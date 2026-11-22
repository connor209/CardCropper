"""
eBay sold-listing evidence, kept: what the Market page searches, shows and
remembers.

Comp Finder (`pricing.batch_comps`) is where sold listings come from. This
module is everything around that call:

* **History.** Every search is saved with the listings it found, so a card
  looked up yesterday can be looked at again without searching (or without
  a login) — and so a price can be traced back to the sales that made it.
* **Other evidence.** Sales copied out of Terapeak or eBay's own sold search
  can be imported from a CSV and priced under exactly the same house rules,
  for when Comp Finder is unavailable or a card needs a second look.
* **Figures.** The spread of the sales that counted — the range, middle and
  the percentile the price is built on — and the sale dates, for the chart.
* **Links.** eBay's own sold search for any title, so a person can always
  check by eye.

Nothing here sets a price. `pricing` decides; this keeps the evidence.
"""

import csv
import datetime as _dt
import json
import os
import re
import sqlite3
import time
import urllib.parse

from . import pricing

DB_NAME = "market.sqlite"
EBAY_SITE = "www.ebay.co.uk"


def settings_dir():
    from . import conditioning
    return conditioning.settings_dir()


def default_path():
    return os.path.join(settings_dir(), DB_NAME)


# ---------------------------------------------------------------- links


def ebay_sold_url(query, site=EBAY_SITE):
    """eBay's sold & completed search for `query`, newest first, UK only."""
    q = urllib.parse.urlencode({"_nkw": clean_query(query), "LH_Sold": "1",
                                "LH_Complete": "1", "_sop": "13", "LH_PrefLoc": "1"})
    return f"https://{site}/sch/i.html?{q}"


def ebay_active_url(query, site=EBAY_SITE):
    """eBay's live listings for `query`, cheapest first with postage."""
    q = urllib.parse.urlencode({"_nkw": clean_query(query), "_sop": "15", "LH_PrefLoc": "1",
                                "LH_BIN": "1"})
    return f"https://{site}/sch/i.html?{q}"


def clean_query(title):
    """A listing title as a search: no trailing grade, no 'Pokemon' filler."""
    t = re.sub(r"\s+(NM|LP|MP|HP|DMG)\s*$", "", title or "", flags=re.I)
    t = re.sub(r"\bpok[eé]mon\b(\s+(tcg|card))?", "", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------- pictures

_IMAGE_KEYS = ("image", "imageUrl", "image_url", "img", "thumbnail", "thumbnailUrl",
               "picture", "pictureUrl", "galleryURL", "galleryUrl", "photo", "imageHref")
_IMAGE_URL = re.compile(r"^https?://\S+?(\.(jpe?g|png|webp|gif)(\?\S*)?$|i\.ebayimg\.com/\S+)",
                        re.I)


def _find_image(value, depth=0):
    if depth > 3:
        return ""
    if isinstance(value, str):
        return value if _IMAGE_URL.match(value.strip()) else ""
    if isinstance(value, dict):
        for k in _IMAGE_KEYS:
            got = _find_image(value.get(k), depth + 1)
            if got:
                return got
        for v in value.values():
            got = _find_image(v, depth + 1)
            if got:
                return got
    if isinstance(value, (list, tuple)):
        for v in value:
            got = _find_image(v, depth + 1)
            if got:
                return got
    return ""


def image_in(sale):
    """
    A picture of the listing, if the evidence carries one — under whatever
    name: the usual keys first, then any value that is plainly an image URL
    (eBay's i.ebayimg.com above all). '' when there is none.
    """
    return _find_image({k: v for k, v in (sale or {}).items() if k != "url"})


def sized(image_url, size=500):
    """An eBay picture at another size: their URLs end s-l140.jpg, s-l500.jpg…"""
    return re.sub(r"/s-l\d+(\.\w+)$", rf"/s-l{size}\1", image_url or "")


_OG_IMAGE = re.compile(r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]*'
                       r'content=["\']([^"\']+)', re.I)
_OG_IMAGE_REV = re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*'
                           r'(?:property|name)=["\'](?:og:image|twitter:image)["\']', re.I)
_EBAYIMG = re.compile(r'https://i\.ebayimg\.com/images/g/[\w~-]+/s-l\d+\.(?:jpg|jpeg|png|webp)',
                      re.I)
#: How long a listing that gave no picture is left before it is asked again.
NO_PICTURE_RETRY = 86400


def _listing_page(url):
    import urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CardCropper",
        "Accept": "text/html", "Accept-Language": "en-GB,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read(600_000).decode("utf-8", "replace")


def listing_image(url, path=None, get=None, now=None):
    """
    The main picture of a sold listing, read from its eBay page — for sales
    whose evidence came without one. Remembered in market.sqlite, so each
    listing's page is fetched once; a listing that gave none is left a day
    before asking again. '' when there is no picture to be had.
    """
    if not url or not url.startswith("http"):
        return ""
    now = now or time.time()
    db = connect(path)
    try:
        row = db.execute("SELECT image, checked_at FROM pictures WHERE url=?",
                         (url,)).fetchone()
    finally:
        db.close()
    if row is not None and (row["image"] or now - row["checked_at"] < NO_PICTURE_RETRY):
        return row["image"]
    image = ""
    try:
        html = (get or _listing_page)(url)
        m = _OG_IMAGE.search(html) or _OG_IMAGE_REV.search(html)
        image = m.group(1) if m else ""
        if not image:
            m = _EBAYIMG.search(html)
            image = m.group(0) if m else ""
    except Exception:                                       # noqa: BLE001
        image = ""
    db = connect(path)
    try:
        db.execute("INSERT OR REPLACE INTO pictures (url, image, checked_at) VALUES (?,?,?)",
                   (url, image, now))
        db.commit()
    finally:
        db.close()
    return image


# ---------------------------------------------------------------- Comp Finder login

_AUTH = re.compile(r"not signed in|sign in|signed out|log ?in|unauthori[sz]ed|forbidden|"
                   r"session|cookie|HTTP 40[13]", re.I)


def is_login_error(exc):
    """Whether Comp Finder refused a search because the saved login is not accepted."""
    return isinstance(exc, pricing.CompFinderError) and bool(_AUTH.search(str(exc)))


def _fingerprint(cookie):
    import hashlib
    return hashlib.sha1(cookie.encode("utf-8")).hexdigest()[:16] if cookie else ""


def login_state():
    """
    'none' (no login saved), 'rejected' (Comp Finder refused the saved one —
    usually an expired session) or 'saved'. A cookie being on disk does not
    mean Comp Finder still accepts it, so a refusal is remembered against that
    cookie, and forgotten the moment a different one is saved.
    """
    from . import folders
    cookie = pricing.comp_finder_cookie()
    if not cookie:
        return "none"
    if folders.load_settings().get("comp_finder_rejected") == _fingerprint(cookie):
        return "rejected"
    return "saved"


def mark_login(rejected):
    from . import folders
    folders.save_settings(comp_finder_rejected=_fingerprint(pricing.comp_finder_cookie())
                          if rejected else "")


LOGIN_TEXT = {"none": "Comp Finder: no login", "saved": "Comp Finder: login saved",
              "rejected": "Comp Finder: login expired"}


# ---------------------------------------------------------------- figures


def sale_date(sale):
    """A sale's date as a date, or None."""
    raw = str(sale.get("date") or sale.get("sold_date") or sale.get("endDate") or "")[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return _dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def figures(priced):
    """
    The numbers above the chart: what the price is built on and how wide the
    evidence is. Totals are the house totals (price + postage, postage
    capped), so these match the working line for line.
    """
    t = sorted(priced.totals or [])
    dates = [d for d in (sale_date(s) for s in priced.kept) if d]
    out = {"n": len(t), "excluded": len(priced.excluded),
           "suggested": priced.suggested, "basis": priced.basis, "method": priced.method,
           "min": t[0] if t else None, "max": t[-1] if t else None,
           "median": pricing.percentile(t, 50) if t else None,
           "p25": pricing.percentile(t, 25) if t else None,
           "p75": pricing.percentile(t, 75) if t else None,
           "mean": round(sum(t) / len(t), 2) if t else None,
           "first": min(dates) if dates else None, "last": max(dates) if dates else None}
    if out["last"]:
        out["days_since"] = (_dt.date.today() - out["last"]).days
    return out


def all_sales(priced):
    """[(state, why, sale)] — kept first, then left out, cheapest first in each."""
    rows = [("kept", "", s) for s in priced.kept] + \
        [("left out", s.get("why", ""), s) for s in priced.excluded]
    rows.sort(key=lambda r: (r[0] != "kept", pricing.sale_total(r[2])))
    return rows


def sale_key(sale):
    """Something that names one sale across a recompute."""
    return sale.get("url") or sale.get("itemId") or sale.get("item_id") or \
        f"{sale.get('title', '')}|{sale.get('date', '')}|{sale.get('price', '')}"


def evidence_of(priced):
    """
    The sold list a Priced was made from, as `price_card` takes it — so the
    price can be worked again with a sale left out. CardCropper's own
    exclusions are re-run rather than carried over; Comp Finder's stay.
    """
    sold = []
    for s in priced.kept:
        d = {k: v for k, v in s.items() if k not in ("put_back", "why")}
        if d.get("counted_by_hand"):
            d.pop("counted_by_hand")
        sold.append(d)
    for s in priced.excluded:
        d = {k: v for k, v in s.items() if k != "why"}
        why = s.get("why", "")
        d["excluded_reason"] = None if why.endswith("(cardcropper)") or why == LEFT_OUT \
            else (d.get("excluded_reason") or why)
        sold.append(d)
    return sold


LEFT_OUT = "left out by you"


def reasons(priced):
    """
    Why the sales that did not count were left out, most common first —
    [(reason, n)] — for the one line above the listings.
    """
    out = {}
    for s in priced.excluded:
        why = s.get("why") or "?"
        out[why] = out.get(why, 0) + 1
    return sorted(out.items(), key=lambda kv: -kv[1])


#: Comp Finder's reasons for leaving out a sale in another language. Read as
#: a pattern, not a list: its exact wording is Comp Finder's ("foreign",
#: "not_english", "language: japanese"…) and may change.
_LANGUAGE = re.compile(r"foreign|language|english|japanese|korean|chinese|german|french|"
                       r"italian|spanish|portuguese|\bjp\b|\bjpn\b", re.I)
LANGUAGES = {"English only": "english", "All languages": "all"}


def language_reasons(sold):
    """The exclusion reasons in `sold` that are about the sale's language."""
    return {s.get("excluded_reason") for s in sold or []
            if s.get("excluded_reason") and _LANGUAGE.search(str(s["excluded_reason"]))
            and not str(s["excluded_reason"]).endswith("(cardcropper)")}


def reprice(priced, left_out, variant_text="", counted=(), language="english"):
    """
    `priced` worked again with the sales in `left_out` (sale keys) taken out
    by hand, and those in `counted` counted by hand — the "what if that one
    was a mistake?" a person asks of an odd sale, either way round. Same
    rules, same rounding; only the evidence changes.
    """
    sold = []
    for s in evidence_of(priced):
        s = {k: v for k, v in s.items() if k != "counted_by_hand"}
        key = sale_key(s)
        if key in left_out:
            s = dict(s, excluded_reason=LEFT_OUT)
        elif key in counted:
            s = dict(s, counted_by_hand=True)
        sold.append(s)
    put_back = language_reasons(sold) if language == "all" else ()
    return pricing.price_card(priced.sku, priced.title, sold, priced.game, priced.condition,
                              current=priced.current, variant_text=variant_text,
                              status=priced.status or "ok", put_back=put_back)


# ---------------------------------------------------------------- your prices

#: How long a price chosen on the Market page is used by Batch & price.
DECISION_DAYS = 14


def decide(title, condition, price, priced=None, note="", path=None):
    """
    "Use this price": keep a person's price for a card at a condition. Batch
    & price uses it for that card (by title, grade ignored) for DECISION_DAYS.
    """
    db = connect(path)
    try:
        db.execute("INSERT OR REPLACE INTO decisions (card_key, condition, price, title, "
                   "n_sales, basis, note, decided_at) VALUES (?,?,?,?,?,?,?,?)",
                   (pricing.card_key(title), condition.upper(), float(price), title,
                    getattr(priced, "n_sales", None), getattr(priced, "basis", None), note,
                    time.time()))
        db.commit()
    finally:
        db.close()


def decision_for(title, condition, path=None, now=None):
    """The recent chosen price for a card, as a dict, or None."""
    cutoff = (now or time.time()) - DECISION_DAYS * 86400
    db = connect(path)
    try:
        r = db.execute("SELECT * FROM decisions WHERE card_key=? AND condition=? "
                       "AND decided_at>=?", (pricing.card_key(title), condition.upper(),
                                             cutoff)).fetchone()
    finally:
        db.close()
    if r is None:
        return None
    r = dict(r)
    r["when"] = ago(r["decided_at"])
    return r


def decisions(path=None):
    db = connect(path)
    try:
        return [dict(r) for r in db.execute("SELECT * FROM decisions ORDER BY decided_at DESC")]
    finally:
        db.close()


def undecide(title, condition, path=None):
    db = connect(path)
    try:
        db.execute("DELETE FROM decisions WHERE card_key=? AND condition=?",
                   (pricing.card_key(title), condition.upper()))
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------- history


def connect(path=None):
    path = path or default_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    # Write-ahead logging: background writers (pictures, grades) never make
    # the window wait to read — a locked read on the window's thread is a
    # frozen window.
    try:
        db.execute("PRAGMA journal_mode=WAL")
    except sqlite3.DatabaseError:
        pass
    db.execute("""CREATE TABLE IF NOT EXISTS searches (
        id INTEGER PRIMARY KEY, title TEXT NOT NULL, game TEXT, condition TEXT,
        variant TEXT, source TEXT, searched_at REAL NOT NULL, n_kept INTEGER,
        n_found INTEGER, suggested REAL, query TEXT, evidence TEXT)""")
    db.execute("CREATE INDEX IF NOT EXISTS searches_title ON searches(title)")
    db.execute("""CREATE TABLE IF NOT EXISTS decisions (
        card_key TEXT NOT NULL, condition TEXT NOT NULL, price REAL NOT NULL, title TEXT,
        n_sales INTEGER, basis REAL, note TEXT, decided_at REAL NOT NULL,
        PRIMARY KEY (card_key, condition))""")
    cols = {r[1] for r in db.execute("PRAGMA table_info(searches)")}
    if "language" not in cols:       # added after the first release of the table
        db.execute("ALTER TABLE searches ADD COLUMN language TEXT")
    db.execute("""CREATE TABLE IF NOT EXISTS pictures (
        url TEXT PRIMARY KEY, image TEXT, checked_at REAL NOT NULL)""")
    db.commit()
    return db


def save_search(title, evidence, priced, game="pokemon", condition="NM", variant="",
                source="comp-finder", path=None, language="english"):
    """Keep one search and everything it found. Returns its id."""
    db = connect(path)
    try:
        cur = db.execute(
            "INSERT INTO searches (title, game, condition, variant, source, searched_at, "
            "n_kept, n_found, suggested, query, evidence, language) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (title, game, condition, variant, source, time.time(), priced.n_sales,
             len(priced.kept) + len(priced.excluded), priced.suggested,
             (evidence or {}).get("query", ""), json.dumps(evidence or {}), language))
        db.commit()
        return cur.lastrowid
    finally:
        db.close()


def history(limit=200, search="", path=None):
    db = connect(path)
    try:
        q = "SELECT id, title, game, condition, variant, source, searched_at, n_kept, " \
            "n_found, suggested FROM searches"
        args = []
        if search:
            q += " WHERE title LIKE ?"
            args.append(f"%{search}%")
        q += " ORDER BY searched_at DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in db.execute(q, args)]
    finally:
        db.close()


def load_search(search_id, path=None):
    """(Priced, evidence row, search row) for a saved search, priced again now."""
    db = connect(path)
    try:
        r = db.execute("SELECT * FROM searches WHERE id=?", (search_id,)).fetchone()
    finally:
        db.close()
    if r is None:
        raise KeyError(search_id)
    r = dict(r)
    ev = json.loads(r.get("evidence") or "{}")
    p = pricing.price_card("lookup", r["title"], ev.get("sold"), r.get("game") or "pokemon",
                           r.get("condition") or "NM", variant_text=r.get("variant") or "",
                           status=ev.get("status", "ok"))
    return p, ev, r


def forget(search_id=None, path=None):
    """Delete one saved search, or all of them."""
    db = connect(path)
    try:
        if search_id is None:
            db.execute("DELETE FROM searches")
        else:
            db.execute("DELETE FROM searches WHERE id=?", (search_id,))
        db.commit()
    finally:
        db.close()


def ago(ts):
    """'3 min ago', '2 days ago' — for the history list."""
    s = max(0, time.time() - float(ts or 0))
    for unit, n in (("day", 86400), ("hour", 3600), ("min", 60)):
        if s >= n:
            k = int(s // n)
            return f"{k} {unit}{'s' if k != 1 and unit != 'min' else ''} ago"
    return "just now"


# ---------------------------------------------------------------- imported sales

#: Column names seen in Terapeak / eBay / hand-made sheets, per field.
_COLUMNS = {
    "title": ("title", "listing title", "item title", "name", "listing"),
    "price": ("price", "sold price", "sale price", "item price", "avg sold price",
              "price (gbp)", "total sold price"),
    "postage": ("postage", "shipping", "p&p", "shipping cost", "avg shipping cost",
                "delivery", "postage cost"),
    "date": ("date", "sold date", "date sold", "end date", "last sold", "sale date"),
    "url": ("url", "link", "listing url", "item url"),
    "condition": ("condition", "item condition"),
}


def _money(v):
    s = re.sub(r"[^\d.,\-]", "", str(v or ""))
    if s.count(",") and s.count("."):
        s = s.replace(",", "")
    elif s.count(",") == 1 and len(s.split(",")[1]) == 2:
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def read_sales_csv(path):
    """
    Sales from a spreadsheet saved as CSV — Terapeak's sold research copied
    out, eBay's sold search, or a sheet typed by hand. Needs a title and a
    price column (named anything recognisable); postage, date, URL and
    condition are used when present. Returns the sold list `price_card`
    takes, and a note on what was read.
    """
    with open(path, encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.DictReader(fh, dialect=dialect))
    if not rows:
        raise ValueError(f"{os.path.basename(path)} has no rows")
    headers = {h.strip().lower(): h for h in rows[0] if h}
    cols = {}
    for field, names in _COLUMNS.items():
        for n in names:
            if n in headers:
                cols[field] = headers[n]
                break
    if "title" not in cols or "price" not in cols:
        raise ValueError(f"{os.path.basename(path)}: need a title and a price column — "
                         f"found {', '.join(rows[0])}")
    sold, skipped = [], 0
    for r in rows:
        price = _money(r.get(cols["price"]))
        title = (r.get(cols["title"]) or "").strip()
        if price is None or not title:
            skipped += 1
            continue
        s = {"title": title, "price": price,
             "postage": _money(r.get(cols["postage"])) if "postage" in cols else 0.0,
             "date": "", "excluded_reason": None, "source": "imported"}
        if "date" in cols:
            d = sale_date({"date": (r.get(cols["date"]) or "").strip()})
            s["date"] = d.isoformat() if d else (r.get(cols["date"]) or "")
        for f in ("url", "condition"):
            if f in cols and r.get(cols[f]):
                s[f] = r[cols[f]].strip()
        if s["postage"] is None:
            s["postage"] = 0.0
        sold.append(s)
    used = ", ".join(f"{k} = '{v}'" for k, v in cols.items())
    return sold, f"{len(sold)} sale(s) read ({used})" + \
        (f"; {skipped} row(s) without a title or price skipped" if skipped else "")
