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
        sold.append(d)
    for s in priced.excluded:
        d = {k: v for k, v in s.items() if k != "why"}
        why = s.get("why", "")
        d["excluded_reason"] = None if why.endswith("(cardcropper)") or why == LEFT_OUT \
            else (d.get("excluded_reason") or why)
        sold.append(d)
    return sold


LEFT_OUT = "left out by you"


def reprice(priced, left_out, variant_text=""):
    """
    `priced` worked again with the sales in `left_out` (sale keys) taken out
    by hand — the "what if that one was a mistake?" a person asks of an odd
    sale. Same rules, same rounding; only the evidence changes.
    """
    sold = []
    for s in evidence_of(priced):
        if sale_key(s) in left_out:
            s = dict(s, excluded_reason=LEFT_OUT)
        sold.append(s)
    return pricing.price_card(priced.sku, priced.title, sold, priced.game, priced.condition,
                              current=priced.current, variant_text=variant_text,
                              status=priced.status or "ok")


# ---------------------------------------------------------------- history


def connect(path=None):
    path = path or default_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS searches (
        id INTEGER PRIMARY KEY, title TEXT NOT NULL, game TEXT, condition TEXT,
        variant TEXT, source TEXT, searched_at REAL NOT NULL, n_kept INTEGER,
        n_found INTEGER, suggested REAL, query TEXT, evidence TEXT)""")
    db.execute("CREATE INDEX IF NOT EXISTS searches_title ON searches(title)")
    db.commit()
    return db


def save_search(title, evidence, priced, game="pokemon", condition="NM", variant="",
                source="comp-finder", path=None):
    """Keep one search and everything it found. Returns its id."""
    db = connect(path)
    try:
        cur = db.execute(
            "INSERT INTO searches (title, game, condition, variant, source, searched_at, "
            "n_kept, n_found, suggested, query, evidence) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (title, game, condition, variant, source, time.time(), priced.n_sales,
             len(priced.kept) + len(priced.excluded), priced.suggested,
             (evidence or {}).get("query", ""), json.dumps(evidence or {})))
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
