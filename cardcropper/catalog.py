"""
Card catalogue lookup: which card is this, which printings exist, and what
the reference prices say.

Two free public catalogues are asked:

* **Pokémon** — the Pokémon TCG API (api.pokemontcg.io). Its TCGplayer price
  keys (normal, holofoil, reverseHolofoil, 1stEditionHolofoil…) are the best
  free record of which printings of a card were actually made. An API key in
  POKEMONTCG_API_KEY raises the rate limit; none is needed.
* **Yu-Gi-Oh** — YGOPRODeck (db.ygoprodeck.com). A set code such as
  LOB-EN005 pins the print; its rarities are the "printings". The catalogue
  does not record 1st Edition versus Unlimited.

Answers are cached on disk for a week, so a second look at the same card is
instant and works offline.

What this cannot do, and says so: the catalogue knows which printings EXIST,
not which one is in your hand. Reverse holo, Cosmos and 1st Edition stamps
are on the physical card, so `check_variant` raises questions rather than
settling them. Likewise image matching (`fingerprint`, `rank_matches`) tells
you WHICH card a scan is, not which printing — the catalogue's renders of a
normal and a reverse holo are the same picture.

No Tk here: the page lives in lookup_tab.py.
"""

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from . import pricing

POKEMON_API = "https://api.pokemontcg.io/v2/cards"
YGO_API = "https://db.ygoprodeck.com/api/v7/cardinfo.php"
YGO_SETS_API = "https://db.ygoprodeck.com/api/v7/cardsetsinfo.php"
POKEMON_FIELDS = "id,name,number,set,rarity,images,tcgplayer,cardmarket,subtypes,supertype"

USER_AGENT = "CardCropper"
TIMEOUT = 20
RETRIES = 2
CACHE_DAYS = 7

#: PROVISIONAL — tuned on a handful of catalogue renders against simulated
#: scans (rotated, re-lit, blurred, on a black bed). Re-check on real scans.
SAME_CARD, UNCERTAIN = 0.80, 0.60

#: TCGplayer price keys → the printing a person would call it.
POKEMON_PRINTINGS = {
    "normal": "Normal",
    "holofoil": "Holo",
    "reverseHolofoil": "Reverse Holo",
    "1stEditionHolofoil": "1st Edition Holo",
    "1stEditionNormal": "1st Edition",
    "1stEdition": "1st Edition",
    "unlimitedHolofoil": "Unlimited Holo",
    "unlimitedNormal": "Unlimited",
    "unlimited": "Unlimited",
}


@dataclass
class CatalogCard:
    game: str                       # 'pokemon' or 'yugioh'
    id: str
    name: str
    number: str = ""
    set_name: str = ""
    set_id: str = ""
    set_total: int | None = None
    release_date: str = ""
    rarity: str = ""
    image_small: str = ""
    image_large: str = ""
    printings: list[str] = field(default_factory=list)
    prices: dict = field(default_factory=dict)
    url: str = ""
    #: The card's own ids elsewhere, by printing — {"normal": 746438, "holo": …}
    #: for Cardmarket — where the catalogue knows them (TCGdex does).
    ids: dict = field(default_factory=dict)
    language: str = "en"
    #: Every print TCGdex knows of the card, each with its Cardmarket id:
    #: [{type, subtype, stamp, size, cardmarket}] — see tcgdex_variants.
    variants: list = field(default_factory=list)


class Results(list):
    """A list of CatalogCard that also remembers the query that found them."""

    def __init__(self, cards=(), query="", parsed=None):
        super().__init__(cards)
        self.query = query
        self.parsed = parsed or {}


class Finding(str):
    """A variant finding: the text, plus `level` ('ok', 'warn' or 'note')."""

    def __new__(cls, text, level="note"):
        s = super().__new__(cls, text)
        s.level = level
        return s


# ---------------------------------------------------------------- titles

_YGO_CODE = re.compile(r"\b([A-Z0-9]{2,5})-(EN|E|DE|FR|IT|SP|PT|JP|KR)?(\d{2,4})\b", re.I)
_YGO_WORDS = re.compile(r"\byu-?gi-?oh!?\b|\bygo\b", re.I)
_FRACTION = re.compile(r"(?<![\w/])([A-Z]{0,4}\d{1,4}[a-z]?)\s*/\s*([A-Z]{0,4})(\d{1,4})(?![\w/])",
                       re.I)
_PROMO = re.compile(r"\b((?:SWSH|HGSS|SVP|SM|XY|BW|DP|SV|TG|GG|RC|NP)\s?-?\d{1,4})\b", re.I)
_CONDITION = re.compile(r"\b(NM|LP|MP|HP|DMG|near\s*mint|lightly\s*played|"
                        r"moderately\s*played|heavily\s*played|damaged)\b\W*$", re.I)
_PRINT_WORDS = re.compile(
    r"\b(cosmos|reverse|rev|holo(foil)?|non[\s-]?holo|1st|first|edition|ed|shadowless|"
    r"unlimited|stamped|stamp|prerelease|pre-release|staff|foil|full\s*art|promo)\b", re.I)
_JUNK = re.compile(r"\b(pok[eé]mon|tcg|ccg|card|cards|trading|english|eng|japanese|"
                   r"yu-?gi-?oh!?|ygo|konami|wotc|single)\b", re.I)
_YGO_RARITIES = ("Quarter Century Secret Rare", "Prismatic Secret Rare", "Platinum Secret Rare",
                 "Starlight Rare", "Collector's Rare", "Ghost Rare", "Ultimate Rare",
                 "Secret Rare", "Ultra Rare", "Super Rare", "Gold Rare", "Short Print",
                 "Rare", "Common")
_POKEMON_RARITIES = re.compile(r"\b(rare|uncommon|common|ultra|secret|illustration|"
                               r"double|hyper|special)\b", re.I)


def _tidy(s):
    s = re.sub(r"[()\[\]{}|,;:#]+", " ", s or "")
    s = re.sub(r"\s+-\s+", " ", s)
    return re.sub(r"\s+", " ", s).strip(" -")


def parse_title(title):
    """
    Pull the parts a catalogue can search on out of a listing title or a few
    typed words.

    Returns dict(name, number, total, set_name, printing, rarity, code, game).
    `printing` is pricing.printing_of's set, so a page and the pricing rules
    agree on what "Reverse Holo" in a title means. Best effort: the person
    picks from the candidates it leads to.
    """
    raw = (title or "").strip()
    out = {"name": "", "number": "", "total": None, "set_name": "", "printing":
           pricing.printing_of(raw), "rarity": "", "code": "", "game": "pokemon"}
    text = raw
    # Parentheses usually hold the printing: "Riolu (Cosmos Holo)".
    text = re.sub(r"\([^)]*\)", " ", text)
    while True:
        new = _CONDITION.sub("", text).strip()
        if new == text:
            break
        text = new

    code = _YGO_CODE.search(text)
    if code or _YGO_WORDS.search(raw):
        out["game"] = "yugioh"
    if out["game"] == "yugioh":
        for r in _YGO_RARITIES:
            m = re.search(rf"\b{re.escape(r)}\b", text, re.I)
            if m:
                out["rarity"] = r
                text = text[:m.start()] + " " + text[m.end():]
                break

    before, after = text, ""
    if code and out["game"] == "yugioh":
        out["code"] = out["number"] = code.group(0).upper()
        before, after = text[:code.start()], text[code.end():]
    else:
        m = _FRACTION.search(text)
        if m:
            out["number"] = m.group(1).lstrip("0") or "0"
            if not m.group(2) and not re.match(r"[A-Z]", m.group(1), re.I):
                out["total"] = int(m.group(3))
            before, after = text[:m.start()], text[m.end():]
        else:
            m = _PROMO.search(text)
            if m:
                out["number"] = re.sub(r"[\s-]", "", m.group(1)).upper()
                before, after = text[:m.start()], text[m.end():]

    def clean(s, rarities=False):
        s = _JUNK.sub(" ", s)
        s = _PRINT_WORDS.sub(" ", s)
        if rarities:
            s = _POKEMON_RARITIES.sub(" ", s)
        return _tidy(s)

    out["name"] = clean(before)
    out["set_name"] = clean(after, rarities=out["game"] == "pokemon")
    if not out["name"] and out["set_name"] and not out["number"]:
        out["name"], out["set_name"] = out["set_name"], ""
    return out


# ---------------------------------------------------------------- http + cache

_cache_dir = None


def cache_dir():
    """Where answers are kept: the app's settings folder, beside its other state."""
    if _cache_dir:
        return _cache_dir
    from . import conditioning
    return os.path.join(conditioning.settings_dir(), "catalog-cache")


def set_cache_dir(path):
    """Point the cache elsewhere (tests); None goes back to the default."""
    global _cache_dir
    _cache_dir = path


def clear_cache():
    """Delete every cached answer. Returns how many were removed."""
    d, n = cache_dir(), 0
    if os.path.isdir(d):
        for f in os.listdir(d):
            if f.endswith(".json"):
                try:
                    os.remove(os.path.join(d, f))
                    n += 1
                except OSError:
                    pass
    return n


def http_get(url, headers=None):
    """
    GET a JSON answer. A 400/404 is the catalogues' way of saying "no such
    card", so it comes back as an empty answer rather than an error; anything
    else transient is retried RETRIES times.
    """
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
    last = None
    for attempt in range(RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 404):
                return {"data": [], "error": f"HTTP {exc.code}"}
            last = exc
            if exc.code < 500 and exc.code != 429:
                break
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last = exc
        if attempt < RETRIES:
            time.sleep(1.5 * (attempt + 1))
    raise last


def fetch_json(url, headers=None, get=None, max_age=CACHE_DAYS * 86400):
    """
    A cached GET. `get(url, headers)` is the getter (tests pass a fake). An
    answer younger than `max_age` is used without asking; an older one is
    kept for when the network is down, because a week-old price beats none.
    """
    get = get or http_get
    d = cache_dir()
    path = os.path.join(d, hashlib.sha1(url.encode("utf-8")).hexdigest() + ".json")
    stale = None
    try:
        with open(path, encoding="utf-8") as fh:
            saved = json.load(fh)
        if time.time() - saved.get("t", 0) < max_age:
            return saved["data"]
        stale = saved["data"]
    except (OSError, ValueError, KeyError):
        pass
    try:
        data = get(url, headers or {})
    except Exception:
        if stale is not None:
            return stale
        raise
    if isinstance(data, dict) and not data.get("error"):
        try:
            os.makedirs(d, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"t": time.time(), "url": url, "data": data}, fh)
            os.replace(tmp, path)
        except OSError:
            pass
    return data


# ---------------------------------------------------------------- pokémon


def _q(s):
    return re.sub(r'["\\*:]', " ", s).strip()


def pokemon_queries(parts):
    """
    The strict query first, then looser ones: a title's set name is often
    not the catalogue's ("Base Set" is "Base", "Black and White Promos" is
    "BW Black Star Promos"), and its number may be a typo.
    """
    name, number = _q(parts.get("name", "")), _q(parts.get("number", ""))
    set_name = _q(re.sub(r"\s+set$", "", parts.get("set_name", "").strip(), flags=re.I))
    total = parts.get("total")
    terms = {
        "name": f'name:"{name.lower()}*"' if name else "",
        "number": f"number:{number}" if number else "",
        "set": f'set.name:"{set_name.lower()}*"' if set_name else "",
        "total": f"set.printedTotal:{total}" if total else "",
    }
    ladders = (("name", "number", "set", "total"), ("name", "number", "total"),
               ("name", "number"), ("name", "set"), ("name",))
    seen, out = set(), []
    for keys in ladders:
        q = " ".join(terms[k] for k in keys if terms[k])
        if q and q not in seen and terms["name"]:
            seen.add(q)
            out.append(q)
    if not out and terms["number"]:
        out.append(" ".join(t for t in (terms["number"], terms["total"]) if t))
    return out


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, 2) if f > 0 else None


def normalise_pokemon(d):
    """One pokemontcg.io card → CatalogCard."""
    s = d.get("set") or {}
    tcg = (d.get("tcgplayer") or {}).get("prices") or {}
    cm = (d.get("cardmarket") or {}).get("prices") or {}
    printings, market = [], {}
    for key, v in tcg.items():
        label = POKEMON_PRINTINGS.get(key) or re.sub(r"(?<!^)([A-Z])", r" \1", key).title()
        if label not in printings:
            printings.append(label)
        val = _num((v or {}).get("market")) or _num((v or {}).get("mid"))
        if val:
            market[label] = val
    prices = {}
    if market:
        prices["TCGplayer market (USD)"] = market
    for key, label in (("trendPrice", "Cardmarket trend (EUR)"),
                       ("avg30", "Cardmarket avg30 (EUR)"),
                       ("reverseHoloTrend", "Cardmarket reverse holo trend (EUR)")):
        if _num(cm.get(key)):
            prices[label] = _num(cm.get(key))
    imgs = d.get("images") or {}
    return CatalogCard(
        game="pokemon", id=str(d.get("id", "")), name=d.get("name", ""),
        number=str(d.get("number", "")), set_name=s.get("name", ""), set_id=s.get("id", ""),
        set_total=s.get("printedTotal") or s.get("total"),
        release_date=(s.get("releaseDate") or "").replace("/", "-"),
        rarity=d.get("rarity") or "", image_small=imgs.get("small", ""),
        image_large=imgs.get("large", ""), printings=printings, prices=prices,
        url=(d.get("tcgplayer") or {}).get("url") or (d.get("cardmarket") or {}).get("url")
        or "")


def search_pokemon(parts, limit=30, get=None):
    headers = {}
    key = os.environ.get("POKEMONTCG_API_KEY", "").strip()
    if key:
        headers["X-Api-Key"] = key
    for q in pokemon_queries(parts):
        url = POKEMON_API + "?" + urllib.parse.urlencode(
            {"q": q, "pageSize": limit, "select": POKEMON_FIELDS})
        data = fetch_json(url, headers, get=get)
        cards = [normalise_pokemon(d) for d in (data or {}).get("data") or []]
        if cards:
            # Newest first, sorted here so the query stays as simple as it can be.
            cards.sort(key=lambda c: c.release_date, reverse=True)
            return Results(cards, q, parts)
    return Results([], "", parts)


# ---------------------------------------------------------------- yu-gi-oh


def normalise_yugioh(d, code=""):
    """
    One YGOPRODeck card → a CatalogCard per print (set code). With `code`,
    only that print; its rarities are the printings a seller can tell apart.
    """
    imgs = (d.get("card_images") or [{}])[0]
    cp = (d.get("card_prices") or [{}])[0]
    base = {}
    # These are per card, not per print: usually the cheapest print's price.
    for key, label in (("tcgplayer_price", "TCGplayer, any print (USD)"),
                       ("cardmarket_price", "Cardmarket, any print (EUR)"),
                       ("ebay_price", "eBay, any print (USD)")):
        if _num(cp.get(key)):
            base[label] = _num(cp.get(key))
    sets = d.get("card_sets") or []
    by_code = {}
    for s in sets:
        c = (s.get("set_code") or "").upper()
        if code and c != code.upper():
            continue
        by_code.setdefault(c, []).append(s)
    if not by_code:
        by_code = {"": []}
    out = []
    for c, prints in by_code.items():
        rarities = []
        set_prices = {}
        for s in prints:
            r = s.get("set_rarity") or ""
            if r and r not in rarities:
                rarities.append(r)
            if r and _num(s.get("set_price")):
                set_prices[r] = _num(s.get("set_price"))
        prices = dict(base)
        if set_prices:
            prices["Set price (USD)"] = set_prices
        out.append(CatalogCard(
            game="yugioh", id=f"{d.get('id', '')}:{c}" if c else str(d.get("id", "")),
            name=d.get("name", ""), number=c,
            set_name=prints[0].get("set_name", "") if prints else "",
            set_id=c.split("-")[0] if c else "", rarity=" / ".join(rarities),
            image_small=imgs.get("image_url_small", ""), image_large=imgs.get("image_url", ""),
            printings=rarities, prices=prices, url=d.get("ygoprodeck_url", "")))
    return out


def search_yugioh(parts, limit=30, get=None):
    code, name = parts.get("code", ""), parts.get("name", "").strip()
    if code:
        info = fetch_json(YGO_SETS_API + "?" + urllib.parse.urlencode({"setcode": code}),
                          get=get)
        exact = (info or {}).get("name") if isinstance(info, dict) else None
        if exact:
            url = YGO_API + "?" + urllib.parse.urlencode({"name": exact})
            data = fetch_json(url, get=get)
            cards = [c for d in (data or {}).get("data") or []
                     for c in normalise_yugioh(d, code)]
            if cards:
                return Results(cards[:limit], f"{code} ({exact})", parts)
    if not name:
        return Results([], "", parts)
    url = YGO_API + "?" + urllib.parse.urlencode({"fname": name})
    data = fetch_json(url, get=get)
    cards = []
    for d in (data or {}).get("data") or []:
        cards.extend(normalise_yugioh(d, code) if code else normalise_yugioh(d))
        if len(cards) >= limit:
            break
    return Results(cards[:limit], f"fname={name}", parts)


# ---------------------------------------------------------------- search


# ---------------------------------------------------------------- japanese (TCGdex)
#
# pokemontcg.io lists English sets only. TCGdex lists Japanese ones too, and
# better: each print carries its Cardmarket product id, so a Japanese card
# is priced from Cardmarket's guide exactly, with no name matching at all.
# Its names are Japanese, though, and titles are typed in English — so an
# English name is turned into its Pokédex number (from TCGdex's English
# cards), and the Japanese cards are found by that number and the card
# number; the set size and the name's suffix (ex, GX, V…) settle the rest.

TCGDEX = "https://api.tcgdex.net/v2"
_JP_WORD = re.compile(r"\b(japanese|japan|jpn|jp)\b", re.I)
#: A Japanese set code in a title: SV4a, S12a, SM10a, sv2a, S8b, SVLN, SVG…
_JP_CODE = re.compile(r"\b((?:SV|S|SM|XY|BW)\d{1,2}[a-zA-Z]{0,2}|SVLN|SVLS|SVG|SVK)\b", re.I)
_SUFFIXES = ("VMAX", "VSTAR", "V-UNION", "BREAK", "GX", "EX", "ex", "V", "LV.X", "Prime")


def looks_japanese(text):
    """A title that names Japanese, or a Japanese-style set code (sv4a, s12a…)."""
    if _JP_WORD.search(text or ""):
        return True
    m = _JP_CODE.search(text or "")
    return bool(m and re.search(r"\d[a-zA-Z]$", m.group(1)))


def _jp_parts(text):
    """(english name, set code or '', card number or '') from a title."""
    t = _JP_WORD.sub(" ", text or "")
    t = re.sub(r"\b(pok[eé]mon|card|tcg|NM|LP|MP|HP|DMG|holo|rare|promo)\b", " ", t,
               flags=re.I)
    m = _JP_CODE.search(t)
    code = m.group(1) if m else ""
    if m:
        t = t[:m.start()] + " " + t[m.end():]
    n = re.search(r"\b0*(\d{1,3})\s*/\s*\d{1,3}\b", t) or re.search(r"\b0*(\d{1,3})\b", t)
    number = n.group(1) if n else ""
    if n:
        t = t[:n.start()] + " " + t[n.end():]
    name = re.sub(r"\s+", " ", t).strip(" -,")
    total = re.search(r"\d{1,3}\s*/\s*0*(\d{1,3})", text or "")
    return name, code, number, int(total.group(1)) if total else None


def _base_name(name):
    words = name.split()
    while len(words) > 1 and words[-1].upper() in {s.upper() for s in _SUFFIXES}:
        words.pop()
    return " ".join(words)


def english_dex(name, get=None):
    """The Pokédex number of an English card name, via TCGdex's English cards — or None."""
    for n in dict.fromkeys([name, _base_name(name)]):
        if not n:
            continue
        rows = fetch_json(f"{TCGDEX}/en/cards?" + urllib.parse.urlencode(
            {"name": f"eq:{n}", "pagination:itemsPerPage": 5}), get=get,
            max_age=90 * 86400)
        for r in rows if isinstance(rows, list) else []:
            d = fetch_json(f"{TCGDEX}/en/cards/{urllib.parse.quote(r['id'])}", get=get,
                           max_age=90 * 86400)
            if isinstance(d, dict) and d.get("dexId"):
                return d["dexId"][0]
    return None


def normalise_tcgdex(d, english_name=""):
    """One TCGdex card (any language) → a CatalogCard, with its Cardmarket ids."""
    s = d.get("set") or {}
    v = d.get("variants") or {}
    printings = [label for key, label in (("normal", "Normal"), ("holo", "Holo"),
                                          ("reverse", "Reverse Holo"),
                                          ("firstEdition", "1st Edition")) if v.get(key)]
    ids, prices, updated = {}, {}, ""
    for vd in d.get("variants_detailed") or []:
        kind = vd.get("type") or "normal"
        cm_id = (vd.get("thirdParty") or {}).get("cardmarket")
        if cm_id:
            ids.setdefault(kind, cm_id)
        cm = (vd.get("pricing") or {}).get("cardmarket") or {}
        if cm.get("trend"):
            prices.setdefault("Cardmarket via TCGdex (EUR)", {})[kind.capitalize()] = cm["trend"]
            updated = updated or (cm.get("updated") or "")[:10]
    if updated:
        prices = {k.replace("(EUR)", f"{updated} (EUR)"): v for k, v in prices.items()}
    image = d.get("image") or ""
    name = d.get("name", "")
    lang = "ja" if "/ja/" in image else "en"
    variants = _variants_of(d)
    return CatalogCard(
        game="pokemon", id=d.get("id", ""),
        name=f"{english_name} ({name})" if english_name and english_name != name else name,
        number=d.get("localId", ""),
        set_name=f"{s.get('name', '')} ({s.get('id', '')})".strip(),
        set_id=s.get("id", ""), set_total=(s.get("cardCount") or {}).get("official"),
        rarity=d.get("rarity") or "", image_small=image + "/low.png" if image else "",
        image_large=image + "/high.png" if image else "", printings=printings,
        prices=prices, ids=ids, language=lang, variants=variants)


def _variants_of(d):
    """A TCGdex card's prints that Cardmarket sells, each once."""
    out, seen = [], set()
    for v in d.get("variants_detailed") or []:
        cm_id = (v.get("thirdParty") or {}).get("cardmarket")
        if not cm_id or cm_id in seen:
            continue
        seen.add(cm_id)
        cm = (v.get("pricing") or {}).get("cardmarket") or {}
        out.append({"type": v.get("type") or "normal", "subtype": v.get("subtype") or "",
                    "stamp": list(v.get("stamp") or []), "size": v.get("size") or "standard",
                    "cardmarket": cm_id, "tcgdex_trend": cm.get("trend"),
                    "tcgdex_updated": (cm.get("updated") or "")[:10]})
    return out


def variant_label(v):
    """'1st Edition Shadowless', 'Reverse Holo', 'Jumbo', 'Poketour 99 stamp'…"""
    bits = []
    for st in v.get("stamp") or []:
        bits.append("1st Edition" if st == "1st-edition" else f"{st.replace('-', ' ')} stamp")
    sub = (v.get("subtype") or "").replace("-", " ")
    if sub and sub != "unlimited":
        bits.append(sub.capitalize())
    t = {"reverse": "Reverse Holo", "holo": "Holo"}.get(v.get("type"), "")
    if t:
        bits.append(t)
    if v.get("size") not in (None, "", "standard"):
        bits.append(v["size"].capitalize())
    return " ".join(bits) or ("Unlimited" if v.get("subtype") == "unlimited" else "Normal")


def choose_variant(variants, claimed=()):
    """
    The print a title describes, from a card's variants: a plain title gets
    the ordinary print — no stamp, not shadowless, not jumbo, not reverse —
    and printing words (1st Edition, Shadowless, Reverse Holo, a stamp) pick
    theirs. Returns (chosen or None, the others).
    """
    claimed = set(claimed or ())

    def score(v):
        sc = 0
        if v.get("size") not in (None, "", "standard"):
            sc -= 10
        stamps = set(v.get("stamp") or [])
        if "1st-edition" in stamps:
            sc += 5 if "1st-edition" in claimed else -5
        if stamps - {"1st-edition"}:
            sc += 3 if "stamped" in claimed else -6
        sub = v.get("subtype") or ""
        if sub.startswith("shadowless"):
            sc += 4 if "shadowless" in claimed else -4
            if "red-cheek" in sub:
                sc -= 1
        if sub in ("", "unlimited"):
            sc += 1
        if v.get("type") == "reverse":
            sc += 5 if "reverse" in claimed else -3
        return sc
    if not variants:
        return None, []
    ranked = sorted(variants, key=score, reverse=True)
    return ranked[0], ranked[1:]


def tcgdex_variants(card, get=None):
    """
    The prints of an English pokemontcg.io card, from TCGdex — which knows
    each one's Cardmarket product id. Found by name and card number, and
    confirmed by set name or size. [] when TCGdex does not have it.
    """
    if card.variants:
        return card.variants
    if card.game != "pokemon" or not card.number:
        return []
    want_set = ("base set" if card.set_name == "Base" else card.set_name).lower()
    for local in dict.fromkeys([card.number, card.number.zfill(3)]):
        rows = fetch_json(f"{TCGDEX}/en/cards?" + urllib.parse.urlencode(
            {"name": f"eq:{card.name}", "localId": local}), get=get, max_age=30 * 86400)
        for r in rows if isinstance(rows, list) else []:
            d = fetch_json(f"{TCGDEX}/en/cards/{urllib.parse.quote(r['id'])}", get=get,
                           max_age=2 * 86400)
            if not isinstance(d, dict):
                continue
            s = d.get("set") or {}
            same_set = (s.get("name") or "").lower() == want_set or (
                card.set_total and (s.get("cardCount") or {}).get("official") == card.set_total)
            if same_set:
                card.variants = _variants_of(d)
                return card.variants
    return []


def _jp_species(dex, get=None):
    """The Japanese name of a Pokédex number, read off any Japanese card that has it."""
    rows = fetch_json(f"{TCGDEX}/ja/cards?" + urllib.parse.urlencode({"dexId": f"eq:{dex}"}),
                      get=get, max_age=90 * 86400)
    import collections
    names = [r.get("name", "") for r in rows if isinstance(rows, list)] if rows else []
    # Tag teams carry every partner's number ("Reshiram & Charizard GX" has 6),
    # so take the name that turns up most often across all of them.
    parts = collections.Counter()
    for n in names:
        for part in re.split(r"[&＆]", n):
            part = re.sub(r"(ex|EX|GX|V|VMAX|VSTAR|BREAK|LV\.X)$", "", part.strip()).strip()
            if part:
                parts[part] += 1
    return parts.most_common(1)[0][0] if parts else ""


def _jp_set_sizes(get=None):
    rows = fetch_json(f"{TCGDEX}/ja/sets", get=get, max_age=30 * 86400)
    return {r.get("id", "").lower(): (r.get("cardCount") or {}).get("official")
            for r in rows if isinstance(rows, list)} if rows else {}


def _by_number_and_name(dex, number, total, code, get=None, limit=8):
    """
    Some Japanese cards carry no Pokédex number (SV4a-349 Charizard ex has
    none), so the number route misses them. Find them instead by card
    number, set size and the species' Japanese name.
    """
    species = _jp_species(dex, get)
    if not species:
        return []
    rows = fetch_json(f"{TCGDEX}/ja/cards?" + urllib.parse.urlencode(
        {"localId": number.zfill(3)}), get=get)
    rows = [r for r in rows if isinstance(rows, list) and species in r.get("name", "")] \
        if rows else []
    if total or code:
        sizes = _jp_set_sizes(get)
        keep = []
        for r in rows:
            sid = r.get("id", "").rsplit("-", 1)[0].lower()
            if (code and sid == code.lower()) or (total and sizes.get(sid) == total):
                keep.append(r)
        rows = keep or rows
    out = []
    for r in rows[:limit]:
        d = fetch_json(f"{TCGDEX}/ja/cards/{urllib.parse.quote(r['id'])}", get=get)
        if isinstance(d, dict) and d.get("id"):
            out.append(d)
    return out


def search_japanese(text, limit=12, get=None):
    """Japanese Pokémon cards for an English title, best first."""
    name, code, number, total = _jp_parts(text)
    parts = {"name": name, "number": number, "total": total, "set_name": code,
             "printing": pricing.printing_of(text), "rarity": "", "code": code,
             "game": "pokemon"}
    details = []
    if code and number:
        for n in dict.fromkeys([number.zfill(3), number]):
            try:
                d = fetch_json(f"{TCGDEX}/ja/cards/{urllib.parse.quote(code)}-{n}", get=get)
            except Exception:                                # noqa: BLE001
                d = None
            if isinstance(d, dict) and d.get("id"):
                details.append(d)
                break
    if not details and name:
        # The English set name often trails the card's ("Raichu Shiny Treasure
        # ex"): try the longest run of leading words that names a Pokémon.
        words, dex = name.split(), None
        for n in range(min(len(words), 4), 0, -1):
            dex = english_dex(" ".join(words[:n]), get)
            if dex:
                name = " ".join(words[:n])
                break
        if dex:
            q = {"dexId": f"eq:{dex}"}
            if number:
                q["localId"] = number.zfill(3)
            rows = fetch_json(f"{TCGDEX}/ja/cards?" + urllib.parse.urlencode(q), get=get)
            rows = rows if isinstance(rows, list) else []
            if code:
                rows.sort(key=lambda r: not r.get("id", "").lower().startswith(code.lower()))
            for r in rows[:limit]:
                d = fetch_json(f"{TCGDEX}/ja/cards/{urllib.parse.quote(r['id'])}", get=get)
                if isinstance(d, dict) and d.get("id"):
                    details.append(d)
            if not details and number:
                details = _by_number_and_name(dex, number, total, code, get)

    suffix = next((x for x in _SUFFIXES if re.search(rf"\b{re.escape(x)}\b", name)), "")

    def score(d):
        s = d.get("set") or {}
        sc = 0
        if total and (s.get("cardCount") or {}).get("official") == total:
            sc += 3
        if code and (s.get("id") or "").lower() == code.lower():
            sc += 3
        if len(d.get("dexId") or []) == 1:
            sc += 1
        jp = d.get("name", "")
        if suffix and suffix.lower() in jp.lower():
            sc += 1
        if not suffix and re.search(r"(ex|GX|V|VMAX|VSTAR)$", jp):
            sc -= 1
        return sc
    details.sort(key=score, reverse=True)
    english = _base_name(name) if name else ""
    cards = [normalise_tcgdex(d, english + (f" {suffix}" if suffix else "")) for d in details]
    return Results(cards, f"TCGdex (Japanese): {name} {code} {number}".strip(), parts)


def search(text, game="auto", limit=30, get=None):
    """
    Look a card up from a title or a few words. `game` is 'auto', 'pokemon'
    or 'yugioh'. Returns Results (a list of CatalogCard with .query and
    .parsed). Raises on a network failure with nothing cached.
    """
    parts = parse_title(text)
    g = parts["game"] if game in (None, "", "auto") else game
    if g == "pokemon-jp" or (game in (None, "", "auto") and g == "pokemon"
                             and looks_japanese(text)):
        return search_japanese(text, get=get)
    parts["game"] = g
    if g == "yugioh":
        return search_yugioh(parts, limit, get)
    return search_pokemon(parts, limit, get)


# ---------------------------------------------------------------- variants

_CLAIM_LABEL = {"reverse": "Reverse Holo", "1st-edition": "1st Edition", "cosmos": "Cosmos Holo",
                "shadowless": "Shadowless", "stamped": "a stamped print"}


def check_variant(claimed, card, rarity=""):
    """
    What the catalogue says about the printing a title claims (pricing's
    printing keys: reverse, 1st-edition, cosmos, shadowless, stamped).

    Returns Findings ('warn' / 'ok' / 'note'). Honest about its reach: the
    catalogue knows which printings were made, not which one is on the desk.
    """
    claimed = set(claimed or ())
    have = list(card.printings)
    listed = ", ".join(have) if have else "none recorded"
    out = []
    if not have:
        out.append(Finding("The catalogue records no printings for this card, so the printing "
                           "cannot be checked here — go by the card itself.", "note"))
    lower = [p.lower() for p in have]
    has_reverse = any("reverse" in p for p in lower)
    has_first = any("1st edition" in p for p in lower)

    if card.game == "pokemon":
        if "reverse" in claimed and have:
            if has_reverse:
                out.append(Finding("Reverse Holo exists for this card in the catalogue. Confirm "
                                   "the scan shows the foil on the card face, not the art box.",
                                   "ok"))
            else:
                out.append(Finding(f"Title says Reverse Holo, but this card has no Reverse Holo "
                                   f"printing in the catalogue (printings: {listed}).", "warn"))
        if "1st-edition" in claimed and have:
            if has_first:
                out.append(Finding("1st Edition exists for this card. Check the stamp is on the "
                                   "card before listing.", "ok"))
            else:
                out.append(Finding(f"Title says 1st Edition, but the catalogue has no 1st "
                                   f"Edition printing of this card (printings: {listed}).",
                                   "warn"))
        for k in ("cosmos", "shadowless", "stamped"):
            if k in claimed:
                out.append(Finding(f"Title says {_CLAIM_LABEL[k]}. The catalogue does not list "
                                   f"that as a separate printing, so check it on the card "
                                   f"itself.", "note"))
        if "reverse" not in claimed and has_reverse and len(have) > 1:
            out.append(Finding("This card also exists as Reverse Holo — check the scan's foil "
                               "before listing.", "warn"))
        if "1st-edition" not in claimed and has_first:
            out.append(Finding("This card also exists as 1st Edition — check for the stamp "
                               "before listing.", "warn"))
        if not claimed and have and len(have) == 1:
            out.append(Finding(f"Only one printing in the catalogue: {have[0]}.", "ok"))
    else:
        if rarity and have:
            if any(rarity.lower() == p.lower() for p in have):
                out.append(Finding(f"{rarity} is a printing of {card.number or 'this card'}.",
                                   "ok"))
            else:
                out.append(Finding(f"Title says {rarity}, but {card.number or 'this print'} "
                                   f"is listed as {listed}.", "warn"))
        elif len(have) > 1:
            out.append(Finding(f"{card.number or 'This print'} comes in {listed} — check which "
                               "one you have.", "warn"))
        if "1st-edition" in claimed:
            out.append(Finding("The catalogue does not record 1st Edition versus Unlimited for "
                               "Yu-Gi-Oh. Check the text under the art.", "note"))
    return out


# ---------------------------------------------------------------- prices + eBay


def ref_price(card):
    """One short reference price for a table cell: TCGplayer first, else Cardmarket."""
    p = card.prices
    tcg = p.get("TCGplayer market (USD)")
    if isinstance(tcg, dict) and tcg:
        return f"${min(tcg.values()):.2f}"
    sp = p.get("Set price (USD)")
    if isinstance(sp, dict) and sp:
        return f"${min(sp.values()):.2f}"
    for key, sym in (("TCGplayer, any print (USD)", "$"), ("Cardmarket trend (EUR)", "€"),
                     ("Cardmarket, any print (EUR)", "€")):
        if isinstance(p.get(key), (int, float)):
            return f"{sym}{p[key]:.2f}"
    via = next((v for k, v in p.items() if k.startswith("Cardmarket via TCGdex")), None)
    if isinstance(via, dict) and via:
        return f"€{min(via.values()):.2f}"
    return ""


def title_for(card, printing=""):
    """
    A search title the way eBay sellers write it: "Pikachu 58/102 Base Set".
    Modern cards (Sword & Shield on) print their number zero-padded, so it is
    padded here too, or the search would miss them.
    """
    if card.game == "yugioh":
        parts = [card.name, card.number]
        if printing:
            parts.append(printing)
        return " ".join(p for p in parts if p)
    if card.language == "ja":
        # The English name, the number as printed, the set code, and the word
        # sellers use — the Japanese set name would find nothing on eBay UK.
        name = re.sub(r"\s*\(.*?\)\s*$", "", card.name)
        num = f"{card.number}/{card.set_total:03d}" if card.set_total else card.number
        return " ".join(p for p in (name, num, card.set_id, printing, "Japanese") if p)
    num = card.number
    if card.set_total and num.isdigit():
        if card.release_date[:4] >= "2020":
            width = max(3, len(str(card.set_total)))
            num = f"{int(num):0{width}d}/{int(card.set_total):0{width}d}"
        else:
            num = f"{num}/{card.set_total}"
    set_name = "Base Set" if card.set_name == "Base" else card.set_name
    parts = [card.name, num, set_name, printing]
    return " ".join(p for p in parts if p)


def ebay_sold_url(query, site="co.uk"):
    """
    eBay's sold-and-completed search for `query`, most recent first — the
    same search the Market page opens (UK sellers only on ebay.co.uk), so
    the two links never disagree.
    """
    from . import market
    return market.ebay_sold_url(query, site=f"www.ebay.{site}")


# ---------------------------------------------------------------- image matching

#: Where the artwork sits on a card face, as fractions (left, top, right,
#: bottom). The frame is the same on every card of a set; the art is what
#: tells two of them apart.
ART_BOX = (0.08, 0.10, 0.92, 0.50)
#: Differing pHash bits forgiven as scanner noise. PROVISIONAL, like SAME_CARD.
HASH_SLACK = 4


def _flat(img):
    """RGB on black: catalogue PNGs have transparent rounded corners, scans a dark bed."""
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (0, 0, 0))
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


def _looks_raw(img):
    """A raw scan: the card sits on a dark bed, so the outer border is nearly black."""
    a = np.asarray(img.convert("L").resize((64, 64)), dtype=np.float32)
    rim = np.concatenate([a[:3].ravel(), a[-3:].ravel(), a[:, :3].ravel(), a[:, -3:].ravel()])
    return float(np.mean(rim < 40)) > 0.8


def card_only(img):
    """Cut the card out of a raw scan; anything else is returned as it is."""
    img = _flat(img)
    if not _looks_raw(img):
        return img
    try:
        from . import imaging
        exact = imaging.straighten(img)[0]
        if exact.width > 50 and exact.height > 50:
            return exact.convert("RGB")
    except Exception:                                        # noqa: BLE001
        pass
    return img


def _dct_matrix(n):
    k = np.arange(n)[:, None]
    i = np.arange(n)[None, :]
    m = np.cos(np.pi * (2 * i + 1) * k / (2 * n)) * np.sqrt(2.0 / n)
    m[0] /= np.sqrt(2.0)
    return m


_DCT32 = _dct_matrix(32)


def phash_bits(img):
    """
    DCT perceptual hash: 32×32 grey, the 8×8 lowest frequencies without the
    DC term, each bit above or below their median. Survives rescaling, a
    little blur and a change of brightness — what a scan does to a render.
    """
    a = np.asarray(img.convert("L").resize((32, 32), Image.LANCZOS), dtype=np.float64)
    d = _DCT32 @ a @ _DCT32.T
    low = d[:8, :8].ravel()[1:]
    return (low > np.median(low)).astype(np.float32)


def colour_hist(img, bins=4):
    """A coarse RGB histogram (bins³), normalised; brightness shifts mostly stay in-bin."""
    a = np.asarray(img.convert("RGB").resize((48, 48)), dtype=np.int32) * bins // 256
    idx = (a[..., 0] * bins + a[..., 1]) * bins + a[..., 2]
    h = np.bincount(idx.ravel(), minlength=bins ** 3).astype(np.float32)
    return h / max(1.0, h.sum())


def fingerprint(img):
    """
    A card face → a vector: pHash of the whole card (63), pHash of the art
    box (63), colour histogram of the art (64). Raw scans are cut out of
    their bed first.
    """
    card = card_only(img)
    w, h = card.size
    l, t, r, b = ART_BOX
    art = card.crop((int(w * l), int(h * t), int(w * r), int(h * b)))
    return np.concatenate([phash_bits(card), phash_bits(art), colour_hist(art)])


def similarity(a, b):
    """
    0..1 between two fingerprints. A pHash pair of unrelated images differs
    in about half its bits, so 30 differing bits scores 0. A scan of the very
    card a render shows still differs in a few (blur, glare, JPEG), so up to
    HASH_SLACK bits are free. The art counts most, the whole card (mostly
    frame, shared by every card of a set) least.
    """
    a, b = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)

    def bits(x, y):
        ham = float(np.sum(x != y))
        return min(1.0, max(0.0, 1.0 - (ham - HASH_SLACK) / (30.0 - HASH_SLACK)))
    whole = bits(a[:63], b[:63])
    art = bits(a[63:126], b[63:126])
    hist = float(np.minimum(a[126:], b[126:]).sum())
    return round(0.30 * whole + 0.50 * art + 0.20 * hist, 4)


def verdict(score):
    """Words for a similarity. PROVISIONAL thresholds (SAME_CARD, UNCERTAIN)."""
    if score >= SAME_CARD:
        return "likely the same card"
    if score >= UNCERTAIN:
        return "uncertain"
    return "different"


def rank_matches(scan_img, candidates_with_images):
    """
    [(card, image)] → [(score, card)], best first. Image may be a PIL image
    or a fingerprint already worked out; None is skipped.
    """
    fp = scan_img if isinstance(scan_img, np.ndarray) else fingerprint(scan_img)
    out = []
    for card, im in candidates_with_images:
        if im is None:
            continue
        other = im if isinstance(im, np.ndarray) else fingerprint(im)
        out.append((similarity(fp, other), card))
    out.sort(key=lambda r: -r[0])
    return out


# ---------------------------------------------------------------- a whole batch

#: A batch check's verdicts, worst first.
BATCH_VERDICTS = ("mismatch", "check", "not found", "no scan", "ok")


def batch_items(folder):
    """
    The cards of a batch prepared on Condition a CSV: [(sku, title, front scan)]
    from its triage.csv and cards/<sku>/front.jpg.
    """
    import csv
    from . import conditioning
    with open(os.path.join(folder, "triage.csv"), encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return [(r.get("sku", ""), r.get("title", ""),
             os.path.join(folder, "cards", conditioning.safe_name(r.get("sku", "")),
                          "front.jpg")) for r in rows]


def check_card(title, front_path, get=None, load_image=None, limit=8):
    """
    Does this scan look like the card its title names? Searches the catalogue
    for the title, compares the scan with every candidate's picture, and
    returns {verdict, score, best (CatalogCard or None), findings, candidates}.

    * ok — the scan looks like a card the title finds (≥ SAME_CARD);
    * check — the closest is only UNCERTAIN..SAME_CARD alike: look;
    * mismatch — nothing the title finds looks like the scan: a wrong
      title, a wrong match, or the wrong scan paired with this card;
    * not found / no scan — nothing to compare.

    It identifies the card, not the printing: a scan cannot show foil.
    """
    from PIL import Image
    if not front_path or not os.path.exists(front_path):
        return {"verdict": "no scan", "score": None, "best": None, "findings": [],
                "candidates": 0}
    res = search(title, limit=limit, get=get)
    if not res:
        return {"verdict": "not found", "score": None, "best": None, "findings": [],
                "candidates": 0}
    if load_image is None:
        from .variant_tab import fetch_image as load_image
    pairs = []
    for card in res:
        try:
            pairs.append((card, load_image(card.image_small or card.image_large)))
        except Exception:                                   # noqa: BLE001
            pairs.append((card, None))
    with Image.open(front_path) as im:
        scan = card_only(im.convert("RGB"))
    ranked = rank_matches(scan, pairs)
    if not ranked:
        return {"verdict": "not found", "score": None, "best": None,
                "findings": ["no catalogue picture could be loaded"], "candidates": len(res)}
    score, best = ranked[0]
    verdict_ = "ok" if score >= SAME_CARD else "check" if score >= UNCERTAIN else "mismatch"
    parsed = parse_title(title)
    findings = list(check_variant(parsed.get("printing") or set(), best,
                                  parsed.get("rarity") or ""))
    return {"verdict": verdict_, "score": score, "best": best, "findings": findings,
            "candidates": len(res)}
