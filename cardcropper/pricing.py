"""
Pricing a batch from eBay UK sold comps (feature 2.7).

Comp Finder supplies the EVIDENCE: `POST /api/batch-comps` in its title form,
`{cards: [{id, title}]}`, ~35 cards a call, returns every sold listing it
fetched with the reason it would exclude it (see Comp Finder's
docs/BATCH_COMPS.md). It authenticates with the Comp Finder session cookie,
and calling it from here (server-side, not a browser) has no CORS problem.

This module applies the house rules to that evidence, so every number can be
redone by hand:

1. Variant filter on every sale — our own check on top of Comp Finder's: a
   sale whose title names a different printing (Cosmos, Reverse Holo, 1st
   Edition, a stamp…) or lacks the card's own printing is out.
2. Put-back: a sale Comp Finder excluded only for a reason in PUT_BACK is
   kept (high postage: the total caps postage anyway).
3. Sale total = price + postage, postage capped at POSTAGE_CAP (£4.00).
4. The 40th percentile of the totals; the lowest when there are under 4.
5. Rounded UP onto the .49/.99 ladder, never below the game's floor
   (Pokémon £2.49, Yu-Gi-Oh £1.99).
6. The condition scale (CONDITION_MULTIPLIERS, applied to the LP price and
   rounded up again; MP takes its cut only once LP is £5+).
7. Sensibility flags, low-risk triage and the decision ladder (see `decide`).
   A price RISE on fewer than 4 sales always goes to a human Terapeak check.

The rules match `claude/pricing-standard.md` as far as the feature notes
state them. Where the notes do not give a number, the value here is Comp
Finder's own house rule (apps/app/lib/house-pricing.js) and is marked
PROVISIONAL — check it against the standard.
"""

import json
import math
import os
import re
import sqlite3
import time
import urllib.request
from dataclasses import dataclass, field

COMP_FINDER = "https://comp-finder-alpha.vercel.app"
PER_CALL = 35

POSTAGE_CAP = 4.00
PERCENTILE = 40
MIN_FOR_PERCENTILE = 4
LADDER_STEP = 0.50
FLOORS = {"pokemon": 2.49, "yugioh": 1.99, "mtg": 0.99}   # mtg: PROVISIONAL
#: PROVISIONAL — Comp Finder's house multipliers.
CONDITION_MULTIPLIERS = {"NM": 1.0, "LP": 1.0, "MP": 0.85, "HP": 0.6, "DMG": 0.4}
MP_MIN_LP = 5.00
#: Comp Finder exclusion reasons a sale is put back for. PROVISIONAL.
PUT_BACK = {"high_postage"}

#: Comp counts the run report and the ladder band by.
BANDS = ((10, "10+"), (4, "4-9"), (1, "1-3"), (0, "0"))

#: Sensibility thresholds. PROVISIONAL.
BIG_RISE, BIG_DROP = 2.0, 0.5
WIDE_SPREAD = 3.0
LOW_RISK_CHANGE = 1.00

# ---------------------------------------------------------------- variants

#: Printings told apart by a word in the title. Order matters: the first
#: match names the printing.
VARIANT_WORDS = (
    ("cosmos", re.compile(r"\bcosmos\b", re.I)),
    ("reverse", re.compile(r"\brev(erse)?\.?\s*-?\s*holo(foil)?\b|\breverse\b", re.I)),
    ("1st-edition", re.compile(r"\b1st\s*(ed(ition)?)?\b|\bfirst\s+edition\b", re.I)),
    ("shadowless", re.compile(r"\bshadowless\b", re.I)),
    ("stamped", re.compile(r"\bstamp(ed)?\b|\bprerelease\b|\bstaff\b|\bpeelable\b"
                           r"|\bwinner\b|\bpre-release\b", re.I)),
)


def printing_of(text):
    """The set of printing keys a title or variant field names."""
    return {k for k, rx in VARIANT_WORDS if rx.search(text or "")}


def variant_ok(card_printing, sale_title):
    """
    Whether a sale is the same printing as the card. A sale naming a printing
    the card is not, or missing one the card is, is a different product.
    """
    sale = printing_of(sale_title)
    return sale == set(card_printing)


#: Words that mean more than one card is in the sale.
LOT_WORDS = re.compile(
    r"\b(lots?|job\s*lot|joblot|bundles?|collection|bulk|playsets?|set\s+of|binder)\b"
    r"|\b([2-9]|\d{2,})\s*x\b|\bx\s*([2-9]|\d{2,})\b", re.I)
#: A collector number in a title: 36/100, 013/078, TG05/TG30 — or a promo
#: code such as BW33, SWSH123, SM210, or a Yu-Gi-Oh code such as LOB-EN005.
_FRACTION = re.compile(r"(?<![\w/])([A-Z]{0,4})0*(\d{1,4})[a-z]?\s*/\s*[A-Z]{0,4}\d{1,4}(?![\w/])", re.I)
_CODE = re.compile(r"\b([A-Z]{2,5})-?(?:EN|E)?0*(\d{1,4})\b")


def card_number(title):
    """(prefix, number) of the card's own number, or None if it has none."""
    m = _FRACTION.search(title or "")
    if m:
        return m.group(1).upper(), int(m.group(2))
    m = _CODE.search(title or "")
    if m:
        return m.group(1).upper(), int(m.group(2))
    return None


def names_number(sale_title, number):
    """Whether a sale title carries the card's number (zero-padding ignored)."""
    prefix, n = number
    pat = (rf"(?<![\w]){re.escape(prefix)}-?(?:EN|E)?0*{n}(?!\d)" if prefix
           else rf"(?<![\d]){'0*'}{n}(?!\d)")
    return re.search(pat, sale_title or "", re.I) is not None


def looks_like_lot(card_title, sale_title):
    """
    A sale that is more than one card: it says so (lot, bundle, 3x…), or it
    lists several names — commas, & or + — without the card's own number.
    Comp Finder misses the second kind: "Fearow, Cacturn, Cradilly" sold for
    £22.41 and was kept as a Fearow 36/100 comp (21 Nov 2026).
    """
    t = sale_title or ""
    if LOT_WORDS.search(t):
        return True
    number = card_number(card_title)
    if number and names_number(t, number):
        return False
    return bool(re.search(r",|\s&\s|\s\+\s|\band\b", t, re.I))


# ---------------------------------------------------------------- arithmetic


def percentile(values, p):
    """Linear interpolation (spreadsheet PERCENTILE.INC), as Comp Finder."""
    s = sorted(v for v in values if v is not None)
    if not s:
        return None
    if len(s) == 1:
        return s[0]
    rank = p / 100.0 * (len(s) - 1)
    lo, hi = math.floor(rank), math.ceil(rank)
    return s[lo] + (s[hi] - s[lo]) * (rank - lo)


def round_up(price, floor):
    """Up onto floor, floor+.50, … — £12.50 becomes £12.99, £12.49 stays."""
    if price is None:
        return None
    p = math.ceil(round(price * 100, 6) - 1e-9) / 100.0
    if p <= floor:
        return floor
    return round(floor + math.ceil(round((p - floor) / LADDER_STEP, 6) - 1e-9) * LADDER_STEP, 2)


def sale_total(sale):
    return round(float(sale.get("price") or 0) + min(float(sale.get("postage") or 0),
                                                     POSTAGE_CAP), 2)


# ---------------------------------------------------------------- one card


@dataclass
class Priced:
    sku: str
    title: str
    game: str
    condition: str
    current: float = None
    n_sales: int = 0
    totals: list = field(default_factory=list)
    basis: float = None
    method: str = "none"
    lp_price: float = None
    suggested: float = None
    flags: list = field(default_factory=list)
    decision: str = ""
    reason: str = ""
    kept: list = field(default_factory=list)
    excluded: list = field(default_factory=list)
    status: str = ""
    last_sold: str = ""


def band(n):
    for low, name in BANDS:
        if n >= low:
            return name
    return "0"


def price_card(sku, title, sold, game="pokemon", condition="NM", current=None,
               variant_text="", status="ok"):
    """
    House price for one card from Comp Finder's `sold[]` for it.

    `variant_text` is the card's own variant/finish (e.g. "Reverse Holofoil");
    the printing is read off it and the title together.
    """
    floor = FLOORS.get(game, FLOORS["pokemon"])
    printing = printing_of(title) | printing_of(variant_text)
    kept, excluded = [], []
    for s in sold or []:
        why = s.get("excluded_reason")
        if why and why not in PUT_BACK:
            excluded.append(dict(s, why=why))
            continue
        if not variant_ok(printing, s.get("title", "")):
            excluded.append(dict(s, why="variant_mismatch (cardcropper)"))
            continue
        if looks_like_lot(title, s.get("title", "")):
            excluded.append(dict(s, why="bundle (cardcropper)"))
            continue
        kept.append(dict(s, put_back=bool(why)))
    p = Priced(sku, title, game, condition.upper() or "NM", current=current,
               kept=kept, excluded=excluded, status=status)
    p.totals = sorted(sale_total(s) for s in kept)
    p.n_sales = len(p.totals)
    p.last_sold = max((s.get("date") or "" for s in kept), default="")
    if p.totals:
        if p.n_sales >= MIN_FOR_PERCENTILE:
            p.basis, p.method = round(percentile(p.totals, PERCENTILE), 2), f"p{PERCENTILE}"
        else:
            p.basis, p.method = p.totals[0], "lowest"
        p.lp_price = round_up(p.basis, floor)
        mult = CONDITION_MULTIPLIERS.get(p.condition, 1.0)
        if p.condition == "MP" and p.lp_price < MP_MIN_LP:
            mult = 1.0
        p.suggested = p.lp_price if mult == 1.0 else round_up(p.lp_price * mult, floor)
    decide(p)
    return p


def decide(p):
    """
    Sensibility flags, low-risk triage and the decision ladder.

    Decisions: `apply` (use the suggestion), `terapeak` (a human checks
    Terapeak before anything changes), `keep` (no evidence; the price stays
    and the card is listed under "kept at an old price without fresh comps"),
    `hold` (the comp lookup failed).
    """
    flags = []
    cur = p.current
    if p.status in ("timeout", "error"):
        p.decision, p.reason = "hold", f"comp lookup {p.status}"
        p.flags = ["lookup_failed"]
        return p
    if p.n_sales < MIN_FOR_PERCENTILE:
        flags.append("few_sales")
    if len(p.totals) >= 4 and p.totals[0] > 0 and \
            percentile(p.totals, 90) / max(percentile(p.totals, 10), 0.01) > WIDE_SPREAD:
        flags.append("wide_spread")
    if p.suggested is not None and cur:
        if p.suggested > cur * BIG_RISE and p.suggested - cur >= 2:
            flags.append("big_rise")
        if p.suggested < cur * BIG_DROP and cur - p.suggested >= 2:
            flags.append("big_drop")
    if p.condition == "NM" and p.suggested is not None and p.suggested >= 15:
        flags.append("in_hand_check")
    if any(s.get("put_back") for s in p.kept):
        flags.append("put_back")
    p.flags = flags

    if p.suggested is None:
        p.decision, p.reason = "terapeak", "no sales of this printing"
        if cur:
            p.decision, p.reason = "keep", "no fresh comps — old price kept; check Terapeak"
        return p
    rise = cur is not None and p.suggested > cur
    if p.n_sales < MIN_FOR_PERCENTILE and rise:
        p.decision, p.reason = "terapeak", f"price rise on {p.n_sales} sale(s)"
    elif "big_rise" in flags or "big_drop" in flags or "wide_spread" in flags:
        p.decision, p.reason = "terapeak", ", ".join(f for f in flags if f in
                                                     ("big_rise", "big_drop", "wide_spread"))
    elif p.n_sales < MIN_FOR_PERCENTILE:
        p.decision, p.reason = "apply", f"{p.n_sales} sale(s), not a rise"
    else:
        p.decision = "apply"
        low = cur is not None and abs(p.suggested - cur) <= LOW_RISK_CHANGE
        p.reason = "low risk" if low or p.suggested == FLOORS.get(p.game, 2.49) else \
            f"{band(p.n_sales)} sales"
    return p


# ---------------------------------------------------------------- Comp Finder


class CompFinderError(RuntimeError):
    pass


COOKIE_FILE = "comp-finder-cookie.txt"


def cookie_path():
    from . import conditioning
    return os.path.join(conditioning.settings_dir(), COOKIE_FILE)


def comp_finder_cookie():
    """
    The Comp Finder session cookie: $COMP_FINDER_COOKIE, else the file in the
    settings folder. A file because the cookie is usually longer than Windows'
    `setx` will store (1024 characters) — and setx cuts it off silently. A
    pasted "cookie: …" header line is accepted as it is.

    The file wins over the variable: a variable set with setx may hold a
    cookie that was cut short.
    """
    try:
        with open(cookie_path(), encoding="utf-8-sig") as fh:
            text = " ".join(fh.read().split())
    except OSError:
        text = ""
    if text.lower().startswith("cookie:"):
        text = text[7:].strip()
    return text or os.environ.get("COMP_FINDER_COOKIE", "").strip()


def batch_comps(cards, base=COMP_FINDER, cookie=None, fresh=False, per_call=PER_CALL,
                timeout=300, post=None, progress=None):
    """
    Evidence for `cards` ([{id, title}]) from Comp Finder, `per_call` at a time,
    one call after another (they share SoldComps' rate limit). Returns
    {id: result}. `post(url, body)` replaces the HTTP call in tests.
    """
    cookie = cookie or comp_finder_cookie()
    if not cookie and post is None:
        raise CompFinderError(
            f"no Comp Finder login — paste your session cookie into {cookie_path()} "
            "(or set COMP_FINDER_COOKIE)")

    def http(url, body):
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json",
                                              "Cookie": cookie}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8"))
            except ValueError:
                raise CompFinderError(f"Comp Finder: HTTP {e.code}") from None

    post = post or http
    out = {}
    for i in range(0, len(cards), per_call):
        chunk = [{"id": c["id"], "title": c["title"]} for c in cards[i:i + per_call]]
        body = {"cards": chunk}
        if fresh:
            body["fresh"] = True
        res = post(f"{base}/api/batch-comps", body)
        if not res.get("ok", True) and not res.get("results"):
            raise CompFinderError(res.get("error") or "Comp Finder refused the batch")
        for r in res.get("results", []):
            out[r.get("id")] = r
        if progress:
            progress(min(i + per_call, len(cards)), len(cards))
    return out


# ---------------------------------------------------------------- register

REGISTER_METHOD = "ebay-sold-comps"
REGISTER_MAX_AGE_DAYS = 14   # PROVISIONAL — how recent a row must be to reuse


def card_key(title):
    """A title without its trailing condition code, case- and space-folded."""
    t = re.sub(r"\s+(NM|LP|MP|HP|DMG)\s*$", "", title or "", flags=re.I)
    return re.sub(r"\s+", " ", t).strip().lower()


class Register:
    """The price register, as a SQLite table."""

    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS price_register (
            card_key TEXT NOT NULL, game TEXT, condition TEXT, title TEXT, sku TEXT,
            price REAL, lp_price REAL, method TEXT NOT NULL, n_sales INTEGER,
            basis REAL, last_sold TEXT, decision TEXT, flags TEXT,
            recorded_at REAL NOT NULL, batch TEXT)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS pr_key ON price_register(card_key, condition)")
        self.db.commit()

    def record(self, p, batch="", method=REGISTER_METHOD, now=None):
        self.db.execute(
            "INSERT INTO price_register VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (card_key(p.title), p.game, p.condition, p.title, p.sku, p.suggested,
             p.lp_price, method, p.n_sales, p.basis, p.last_sold, p.decision,
             " ".join(p.flags), now or time.time(), batch))
        self.db.commit()

    def reusable(self, title, condition, now=None, max_age_days=REGISTER_MAX_AGE_DAYS):
        """The newest row for this card, ONLY if it is eBay sold comps and recent."""
        cutoff = (now or time.time()) - max_age_days * 86400
        row = self.db.execute(
            "SELECT price, lp_price, n_sales, recorded_at, method FROM price_register "
            "WHERE card_key=? AND condition=? AND method=? AND recorded_at>=? "
            "AND price IS NOT NULL ORDER BY recorded_at DESC LIMIT 1",
            (card_key(title), condition, REGISTER_METHOD, cutoff)).fetchone()
        return None if row is None else dict(zip(
            ("price", "lp_price", "n_sales", "recorded_at", "method"), row))

    def close(self):
        self.db.close()


# ---------------------------------------------------------------- golden tests

#: Must pass on every change to the rules: (title, variant, expected price).
#: They need live comps, so `golden_check` runs them through Comp Finder.
GOLDEN = (
    ("Riolu (Cosmos Holo) BW33 Black and White Promos Pokemon", "Cosmos Holofoil", 50.49),
    ("Riolu BW33 Black and White Promos Pokemon", "", 14.99),
    ("Numel Peelable Ditto 013/078 Pokemon GO Pokemon", "", 15.99),
    ("Lugia 78/124 Fates Collide Pokemon", "", 4.49),
    ("Exeggutor 109/108 Evolutions Pokemon", "", 5.99),
    ("Aron 44/100 Crystal Guardians Pokemon", "", 2.49),
    ("Fearow 36/100 Majestic Dawn Pokemon", "", 2.49),
)
GOLDEN_TOLERANCE = 0.25   # ± share of the expected price, for comps that move


def golden_priced(evidence):
    """[(title, expected, Priced, ok)] from batch_comps' answer for GOLDEN."""
    out = []
    for i, (title, variant, expected) in enumerate(GOLDEN):
        r = evidence.get(f"golden-{i}") or {}
        p = price_card(f"golden-{i}", title, r.get("sold"), "pokemon", "NM",
                       variant_text=variant, status=r.get("status", "ok"))
        got = p.suggested
        ok = got is not None and abs(got - expected) <= expected * GOLDEN_TOLERANCE
        # No sales of the card at all lists at the floor once a person has
        # checked Terapeak; a golden card expected AT the floor passes then.
        if got is None and p.n_sales == 0 and p.status not in ("timeout", "error") \
                and expected == FLOORS["pokemon"]:
            ok = True
        out.append((title, expected, p, ok))
    return out


def golden_check(evidence):
    """[(title, expected, got, ok)] from batch_comps' answer for GOLDEN."""
    return [(t, e, p.suggested, ok) for t, e, p, ok in golden_priced(evidence)]


def working(p):
    """How the price was reached, step by step, in words a person can redo."""
    floor = FLOORS.get(p.game, FLOORS["pokemon"])
    if p.method == "register":
        return [f"From the price register (recent eBay sold comps): £{p.suggested:.2f}."]
    if p.status in ("timeout", "error"):
        return [f"The Comp Finder lookup failed ({p.status}) — no price; run it again."]
    if not p.totals:
        return [f"No sales of this printing kept — no price. Goes to a Terapeak check; "
                f"the floor for {p.game} is £{floor:.2f}."]
    totals = ", ".join(f"£{t:.2f}" for t in p.totals)
    lines = [f"Totals (price + postage, postage capped at £{POSTAGE_CAP:.2f}), lowest first: "
             f"{totals}"]
    if p.method == "lowest":
        lines.append(f"Fewer than {MIN_FOR_PERCENTILE} sales, so the lowest: £{p.basis:.2f}.")
    else:
        lines.append(f"{PERCENTILE}th percentile of {p.n_sales} sales: £{p.basis:.2f}.")
    lines.append(f"Up onto the .49/.99 ladder (floor £{floor:.2f}): £{p.lp_price:.2f}.")
    mult = CONDITION_MULTIPLIERS.get(p.condition, 1.0)
    if p.condition == "MP" and p.lp_price is not None and p.lp_price < MP_MIN_LP:
        mult = 1.0
    if mult != 1.0:
        lines.append(f"{p.condition} × {mult:.2f}, back up onto the ladder: £{p.suggested:.2f}.")
    else:
        lines.append(f"{p.condition}: no condition cut — £{p.suggested:.2f}.")
    if p.current is not None:
        lines.append(f"Current price £{p.current:.2f}.")
    lines.append(f"Decision: {p.decision} — {p.reason}"
                 + (f"   flags: {', '.join(p.flags)}" if p.flags else ""))
    return lines


def explain(p, evidence_row=None):
    """
    The working behind one price, for a person to check: every sale kept (date,
    total, title), and how many were left out and why. No cookie or account
    detail is in it — only eBay listing titles and prices.
    """
    lines = [f"    method {p.method}, {p.n_sales} sale(s) kept, basis "
             + (f"£{p.basis:.2f}" if p.basis is not None else "none")]
    if evidence_row:
        lines.append(f"    Comp Finder searched: {evidence_row.get('query', '?')}  "
                     f"parsed: {evidence_row.get('parsed', '')}")
    for s in sorted(p.kept, key=sale_total):
        lines.append(f"    kept  £{sale_total(s):6.2f}  {s.get('date', '')[:10]}  "
                     f"{s.get('title', '')}" + ("  (put back)" if s.get("put_back") else ""))
    why = {}
    for s in p.excluded:
        why[s["why"]] = why.get(s["why"], 0) + 1
    if why:
        lines.append("    left out: " + ", ".join(f"{n} {w}" for w, n in sorted(why.items())))
    return lines


def golden_cards():
    return [{"id": f"golden-{i}", "title": t} for i, (t, _, _) in enumerate(GOLDEN)]
