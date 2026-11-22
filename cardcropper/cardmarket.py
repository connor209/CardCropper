"""
Cardmarket's own price guide, downloaded daily, matched to catalogue cards.

Cardmarket publishes, per game, a product list and a price guide as plain
JSON, rebuilt every day:

    downloads.s3.cardmarket.com/productCatalog/productList/products_singles_<n>.json
    downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_<n>.json

(n = 6 for Pokémon, 3 for Yu-Gi-Oh). The guide has trend, average, low and
1/7/30-day averages per product, and the same again for reverse holos
(Pokémon) or foils (Yu-Gi-Oh). That is fresher than the copy the catalogue
APIs carry — pokemontcg.io's Cardmarket figures were five months old when
this was written.

**Matching.** The product list names a product ("Weedle [Multiply]") and an
expansion by number only: no set name, no card number, and no public list of
what the numbers are. So the expansion is recognised by its cards: for a
catalogue card, every expansion holding a product of that name is compared
with the catalogue's list of the card's set (pokemontcg.io / YGOPRODeck), by
the overlap of their card names. On 22 Nov 2026 the right expansion won
clearly — Base Set 96% against 66% for the next, Paldean Fates 89/54, Crown
Zenith 81/39, Phantom Nightmare 98/58 — and a set reprinted often (Legend of
Blue-Eyes, 74/64) less clearly, so a close call is marked unsure rather
than passed off as certain. Each set's answer is kept, so it is worked out
once.

Within a set, cards sharing a name (alternate arts, several rarities) are
taken in card-number order against the products in id order — a fair
guess, marked as one.

**Reference only.** These prices are shown beside the eBay UK evidence; no
price is worked out from them.
"""

import collections
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

GAMES = {"pokemon": 6, "yugioh": 3}
BASE = "https://downloads.s3.cardmarket.com/productCatalog"
SEARCH = {"pokemon": "https://www.cardmarket.com/en/Pokemon/Products/Search?searchString=",
          "yugioh": "https://www.cardmarket.com/en/YuGiOh/Products/Search?searchString="}
#: Refresh the downloaded files when older than this (they change daily).
MAX_AGE = 20 * 3600
#: An expansion is a sure match for a set when it overlaps this much, and
#: this far ahead of the next best. Below either, it is shown as unsure.
SURE_SCORE, SURE_MARGIN = 0.50, 0.08
#: Below this the best expansion is not used at all.
MIN_SCORE = 0.30

#: Price-guide fields shown, and what to call them.
FIELDS = (("trend", "Trend"), ("avg30", "30-day average"), ("avg7", "7-day average"),
          ("low", "From"))
VARIANT = {"pokemon": ("holo", "reverse holo"), "yugioh": ("foil", "foil")}

_lock = threading.Lock()
_loaded = {}            # game -> (mtimes, Index)


def folder():
    from .conditioning import settings_dir
    return os.path.join(settings_dir(), "cardmarket")


def _paths(game):
    n = GAMES[game]
    return (os.path.join(folder(), f"products_singles_{n}.json"),
            os.path.join(folder(), f"price_guide_{n}.json"))


def _urls(game):
    n = GAMES[game]
    return (f"{BASE}/productList/products_singles_{n}.json",
            f"{BASE}/priceGuide/price_guide_{n}.json")


def norm(name):
    """A card name as compared: no [attacks] or (versions), case and quotes folded."""
    n = re.sub(r"\s*\[.*?\]|\s*\(.*?\)", "", name or "")
    n = n.replace("’", "'").replace("é", "e").replace("É", "E")
    return re.sub(r"\s+", " ", n).strip().lower()


# ---------------------------------------------------------------- the files


def age(game):
    """Seconds since the files were downloaded, or None if they never were."""
    try:
        return time.time() - min(os.path.getmtime(p) for p in _paths(game))
    except OSError:
        return None


def created(game):
    """When Cardmarket built the price guide on disk (its createdAt), or ''."""
    idx = _index(game, download=False)
    return idx.created if idx else ""


def download(game, fetch=None):
    """
    Fetch both files for `game`, checking each is whole JSON before it
    replaces the copy on disk. `fetch(url)` returns bytes (tests pass a fake).
    """
    fetch = fetch or _fetch
    os.makedirs(folder(), exist_ok=True)
    for url, path in zip(_urls(game), _paths(game)):
        data = fetch(url)
        parsed = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        if not (parsed.get("products") or parsed.get("priceGuides")):
            raise ValueError(f"{url} has no products or prices")
        tmp = path + ".part"
        with open(tmp, "wb") as fh:
            fh.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        os.replace(tmp, path)
    with _lock:
        _loaded.pop(game, None)


def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "CardCropper"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def refresh(games=GAMES, force=False, fetch=None):
    """Download whichever games' files are missing or a day old. Returns what failed."""
    failed = {}
    for g in games:
        a = age(g)
        if force or a is None or a > MAX_AGE:
            try:
                download(g, fetch)
            except Exception as exc:                        # noqa: BLE001
                failed[g] = exc
    return failed


def refresh_later(done=None, force=False):
    """`refresh`, on a background thread — at start-up, and from the Tools menu."""
    def run():
        failed = refresh(force=force)
        if done:
            done(failed)
    threading.Thread(target=run, name="cardmarket-download", daemon=True).start()


class Index:
    """The two files, boiled down to what matching and pricing need."""

    def __init__(self, products, guide, created=""):
        self.created = created
        self.by_name = collections.defaultdict(list)      # name -> [(idProduct, idExpansion)]
        self.expansions = collections.defaultdict(collections.Counter)
        self.product_name = {}
        added = collections.defaultdict(list)
        for p in products:
            n = norm(p.get("name"))
            pid, exp = p.get("idProduct"), p.get("idExpansion")
            self.by_name[n].append((pid, exp))
            self.expansions[exp][n] += 1
            self.product_name[pid] = p.get("name", "")
            d = (p.get("dateAdded") or "")[:10]
            if d and not d.startswith("0000"):
                added[exp].append(d)
        #: When each expansion's products were first added — Cardmarket adds a
        #: set's cards around its release; a later re-release comes years on.
        self.added = {e: min(ds) for e, ds in added.items()}
        self.guide = {g.get("idProduct"): g for g in guide}


def _index(game, download=True):
    paths = _paths(game)
    if not all(os.path.exists(p) for p in paths):
        if not download:
            return None
        refresh([game])
        if not all(os.path.exists(p) for p in paths):
            return None
    mtimes = tuple(os.path.getmtime(p) for p in paths)
    with _lock:
        have = _loaded.get(game)
        if have and have[0] == mtimes:
            return have[1]
    with open(paths[0], encoding="utf-8") as fh:
        products = json.load(fh).get("products") or []
    with open(paths[1], encoding="utf-8") as fh:
        g = json.load(fh)
    idx = Index(products, g.get("priceGuides") or [], g.get("createdAt", ""))
    with _lock:
        _loaded[game] = (mtimes, idx)
    return idx


# ---------------------------------------------------------------- sets


def _map_path(game):
    return os.path.join(folder(), f"expansions-{game}.json")


def _load_map(game):
    try:
        with open(_map_path(game), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_map(game, data):
    os.makedirs(folder(), exist_ok=True)
    tmp = _map_path(game) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, _map_path(game))


def set_cards(card, get=None):
    """
    The catalogue's list of the card's set: [(name, number)] — every card in
    it, for recognising the expansion and ordering same-named cards.
    """
    from . import catalog
    if card.game == "pokemon":
        out, page = [], 1
        while page < 10:
            url = catalog.POKEMON_API + "?" + urllib.parse.urlencode(
                {"q": f"set.id:{card.set_id}", "pageSize": 250, "page": page,
                 "select": "name,number"})
            data = catalog.fetch_json(url, get=get, max_age=90 * 86400) or {}
            rows = data.get("data") or []
            out += [(r.get("name", ""), r.get("number", "")) for r in rows]
            if len(rows) < 250:
                break
            page += 1
        return out
    url = catalog.YGO_API + "?" + urllib.parse.urlencode({"cardset": card.set_name})
    data = catalog.fetch_json(url, get=get, max_age=90 * 86400) or {}
    out = []
    for d in data.get("data") or []:
        for s in d.get("card_sets") or []:
            if s.get("set_name") == card.set_name:
                out.append((d.get("name", ""), s.get("set_code", "")))
    return out


def _set_key(card):
    return card.set_id if card.game == "pokemon" else card.set_name


def expansion_for(card, idx, get=None):
    """
    Which Cardmarket expansion is the card's set: {expansion, score, second,
    sure}, or None. Worked out once per set and kept.
    """
    key = _set_key(card)
    if not key:
        return None
    saved = _load_map(card.game)
    if key in saved and saved[key].get("expansion") in idx.expansions:
        return saved[key]
    try:
        names = collections.Counter(norm(n) for n, _ in set_cards(card, get))
    except Exception:                                       # noqa: BLE001
        return None          # the catalogue is down: try again next time, keep nothing
    if not names:
        return None
    candidates = {exp for _, exp in idx.by_name.get(norm(card.name), [])}
    scores = []
    for exp in candidates:
        have = idx.expansions[exp]
        inter = sum((names & have).values())
        union = sum(names.values()) + sum(have.values()) - inter
        score = inter / union if union else 0.0
        if _near_release(idx.added.get(exp), getattr(card, "release_date", "")):
            score += DATE_BONUS
        scores.append((score, exp))
    if not scores:
        return None
    scores.sort(reverse=True)
    best, exp = scores[0]
    second = scores[1][0] if len(scores) > 1 else 0.0
    if best < MIN_SCORE:
        return None
    found = {"expansion": exp, "score": round(min(best, 1.0), 3), "second": round(second, 3),
             "sure": best >= SURE_SCORE and best - second >= SURE_MARGIN}
    saved[key] = found
    _save_map(card.game, saved)
    return found


#: Added to an expansion's score when Cardmarket added its cards within
#: RELEASE_WINDOW days of the set's release. Only ever a bonus: an old set's
#: expansion may have been re-created years later (Base Set's was, in 2021).
DATE_BONUS, RELEASE_WINDOW = 0.15, 120


def _near_release(added, released):
    import datetime as dt
    try:
        a = dt.date.fromisoformat(added[:10])
        r = dt.date.fromisoformat(str(released)[:10].replace("/", "-"))
    except (TypeError, ValueError):
        return False
    return abs((a - r).days) <= RELEASE_WINDOW


def _natural(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s or "")]


# ---------------------------------------------------------------- prices


def price_for(card, get=None):
    """
    Cardmarket's prices for a catalogue card (catalog.CatalogCard), or None
    when there is no guide or no match. A dict: product, created, sure,
    score, approx (same-named cards guessed by order), prices [(label,
    euros)], variant [(label, euros)] for reverse holo / foil, search URL.
    """
    if card.game not in GAMES:
        return None
    idx = _index(card.game)
    if getattr(card, "ids", None):
        return _exact(card, idx)
    if idx is None:
        return None
    found = expansion_for(card, idx, get)
    if not found:
        return None
    exp = found["expansion"]
    products = sorted(pid for pid, e in idx.by_name.get(norm(card.name), []) if e == exp)
    if not products:
        return None
    approx = False
    if len(products) > 1 and card.game == "yugioh":
        # Same-named prints of one card in one set (rarities, 1st/unlimited)
        # carry identical names in the file and nothing else to tell them
        # apart — and their prices differ tenfold. So no guess: the range.
        trends = sorted(t for t in ((idx.guide.get(pid) or {}).get("trend")
                                    for pid in products) if t)
        return {"product": card.name, "id": None, "created": idx.created,
                "sure": found["sure"], "score": found["score"], "approx": True,
                "prices": [], "variant": [], "versions": len(products),
                "range": (trends[0], trends[-1]) if trends else None,
                "url": SEARCH[card.game] + urllib.parse.quote(card.name)}
    if len(products) > 1:
        same = sorted((num for n, num in set_cards(card, get) if norm(n) == norm(card.name)),
                      key=_natural)
        rank = same.index(card.number) if card.number in same else 0
        approx = True
        pid = products[min(rank, len(products) - 1)]
    else:
        pid = products[0]
    g = idx.guide.get(pid) or {}
    var_key, var_name = VARIANT[card.game]
    prices = [(label, g.get(k)) for k, label in FIELDS if g.get(k)]
    variant = [(f"{var_name.capitalize()} {label.lower()}", g.get(f"{k}-{var_key}"))
               for k, label in FIELDS if g.get(f"{k}-{var_key}")]
    return {"product": idx.product_name.get(pid, card.name), "id": pid,
            "created": idx.created, "sure": found["sure"], "score": found["score"],
            "approx": approx, "prices": prices, "variant": variant,
            "url": SEARCH[card.game] + urllib.parse.quote(card.name)}


def _exact(card, idx):
    """
    A card whose catalogue knows its Cardmarket ids (TCGdex does, for every
    print — Japanese ones included): today's guide by id, no matching at all.
    Where today's guide is not on disk or lacks the id, TCGdex's own copy
    of Cardmarket's prices (a day or so old) stands in.
    """
    kinds = sorted(card.ids, key=lambda k: (k != "normal", k != "holo", k))
    var_key, var_name = VARIANT[card.game]
    first = kinds[0]
    g = (idx.guide.get(card.ids[first]) if idx else None) or {}
    if g:
        prices = [(label, g.get(k)) for k, label in FIELDS if g.get(k)]
        variant = [(f"{var_name.capitalize()} {label.lower()}", g.get(f"{k}-{var_key}"))
                   for k, label in FIELDS if g.get(f"{k}-{var_key}")]
        for k in kinds[1:]:
            other = idx.guide.get(card.ids[k]) or {}
            if other.get("trend"):
                variant.append((f"{k} print trend", other["trend"]))
        created = idx.created
        product = idx.product_name.get(card.ids[first], card.name)
    else:
        via = next((v for key, v in (card.prices or {}).items()
                    if key.startswith("Cardmarket via TCGdex")), None)
        if not via:
            return None
        date = next((key for key in card.prices if key.startswith("Cardmarket via TCGdex")),
                    "")
        created = (re.search(r"\d{4}-\d{2}-\d{2}", date) or [""])[0] + " via TCGdex"
        vals = list(via.items()) if isinstance(via, dict) else [(first, via)]
        prices = [("Trend", vals[0][1])]
        variant = [(f"{k.lower()} print trend", v) for k, v in vals[1:]]
        product = card.name
    return {"product": product, "id": card.ids[first], "created": created, "sure": True,
            "score": 1.0, "approx": False, "prices": prices, "variant": variant,
            "exact": True, "url": SEARCH[card.game] + urllib.parse.quote(
                re.sub(r"\s*\(.*?\)", "", card.name))}


def describe(found):
    """One line for the Market page and Card lookup."""
    if not found:
        return ""
    if found.get("range"):
        lo, hi = found["range"]
        when = (found.get("created") or "")[:10]
        return (f"Cardmarket{f' ({when})' if when else ''}: {found['versions']} versions of "
                f"this card in the set, trend €{lo:.2f}–€{hi:.2f} — the file does not say "
                "which rarity is which; check on Cardmarket"
                + ("   [set match unsure]" if not found["sure"] else ""))
    bits = [f"{label} €{v:.2f}" for label, v in found["prices"][:3]]
    bits += [f"{label} €{v:.2f}" for label, v in found["variant"][:1]]
    when = (found.get("created") or "")[:10]
    caveat = []
    if not found["sure"]:
        caveat.append("set match unsure")
    if found["approx"]:
        caveat.append("one of several same-named cards — check")
    return (f"Cardmarket{f' ({when})' if when else ''}: " + " · ".join(bits)
            + (f"   [{'; '.join(caveat)}]" if caveat else ""))
