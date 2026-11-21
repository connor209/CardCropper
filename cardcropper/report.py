"""
Pricing a CardUploader job end to end, and the run report that is the
definition of done (feature 2.8).

The report gives: SKUs priced / total; cards with 10+, 4-9, 1-3 and 0 sales;
cards checked on Terapeak; recommendations not applied; the "Connor" list
(everything waiting on a person); and any card kept at an old price without
fresh comps. A batch is never called ready while anything was skipped.
"""

import csv
import os

from . import grading, pricing, variants

PRICING_FIELDS = ["sku", "title", "condition", "current", "suggested", "lp_price",
                  "basis", "method", "n_sales", "band", "last_sold", "decision",
                  "reason", "flags", "source", "n_excluded"]
TERAPEAK_FIELDS = ["sku", "title", "current", "suggested", "reason", "checked",
                   "terapeak_price", "note"]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def price_job(job, game, evidence=None, register=None, grades=None, batch="",
              fetch=None, reuse_register=True):
    """
    Price every card in a CardUploader job.

    `grades` is {sku: condition} (the listing condition, after grading);
    missing cards use the job's own condition. `evidence` is batch_comps'
    {id: result}; when None, `fetch(cards)` is called for the cards the
    register cannot answer. Returns a list of pricing.Priced.
    """
    cards = variants.job_cards(job)
    grades = grades or {}
    out, need = [], []
    for c in cards:
        sku = c.get("sku", "")
        cond = (grades.get(sku) or c.get("condition") or "NM").upper()
        hit = register.reusable(c.get("title", ""), cond) if (register and reuse_register) else None
        if hit:
            p = pricing.Priced(sku, c.get("title", ""), game, cond, current=_num(c.get("price")),
                               n_sales=hit["n_sales"] or 0, suggested=hit["price"],
                               lp_price=hit["lp_price"], method="register")
            pricing.decide(p)
            p.reason = "price register (recent eBay sold comps); " + p.reason
            out.append(p)
        else:
            need.append((c, cond))
            out.append(None)
    if need and evidence is None:
        evidence = fetch([{"id": c.get("sku"), "title": c.get("title", "")} for c, _ in need]) \
            if fetch else {}
    evidence = evidence or {}
    it = iter(need)
    for i, p in enumerate(out):
        if p is not None:
            continue
        c, cond = next(it)
        r = evidence.get(c.get("sku")) or {}
        variant = " ".join(filter(None, [c.get("variant"), c.get("finish")]))
        status = r.get("status", "ok") if r else "error"
        p = pricing.price_card(c.get("sku"), c.get("title", ""), r.get("sold"), game, cond,
                               current=_num(c.get("price")), variant_text=variant,
                               status=status)
        if register and p.suggested is not None and status == "ok":
            register.record(p, batch=batch)
        out[i] = p
    return out


def write_pricing(priced, path):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=PRICING_FIELDS)
        w.writeheader()
        for p in priced:
            w.writerow({"sku": p.sku, "title": p.title, "condition": p.condition,
                        "current": p.current, "suggested": p.suggested,
                        "lp_price": p.lp_price, "basis": p.basis, "method": p.method,
                        "n_sales": p.n_sales, "band": pricing.band(p.n_sales),
                        "last_sold": p.last_sold, "decision": p.decision,
                        "reason": p.reason, "flags": " ".join(p.flags),
                        "source": "register" if p.method == "register" else "comps",
                        "n_excluded": len(p.excluded)})
    return path


def write_terapeak(priced, path):
    """The cards a person checks on Terapeak; fill `checked` and the price."""
    old = {}
    if os.path.exists(path):
        old = {r["sku"]: r for r in read_csv(path)}
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=TERAPEAK_FIELDS)
        w.writeheader()
        for p in priced:
            if p.decision not in ("terapeak", "keep"):
                continue
            prev = old.get(p.sku, {})
            w.writerow({"sku": p.sku, "title": p.title, "current": p.current,
                        "suggested": p.suggested, "reason": p.reason,
                        "checked": prev.get("checked", ""),
                        "terapeak_price": prev.get("terapeak_price", ""),
                        "note": prev.get("note", "")})
    return path


def read_csv(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def price_changes(priced, terapeak_rows=(), job=None):
    """
    Change-file entries: every `apply` that differs from the current price,
    plus Terapeak checks a person has filled in. Returns (changes, not_applied)
    where not_applied lists (sku, why) for every recommendation left out.
    """
    by_card = {c.get("sku"): c for c in variants.job_cards(job or {})}
    tp = {r["sku"]: r for r in terapeak_rows}
    changes, not_applied = [], []
    for p in priced:
        inv = (by_card.get(p.sku) or {}).get("inventoryId", "")
        listed = (by_card.get(p.sku) or {}).get("status") == "listed"
        if p.decision == "apply":
            if p.suggested is not None and p.suggested != p.current:
                if p.game == "yugioh" and p.suggested < variants.YGO_MIN_PRICE:
                    not_applied.append((p.sku, "below the Yu-Gi-Oh minimum"))
                    continue
                changes.append(variants.change(p.sku, "price", p.suggested,
                                               old=p.current if p.current is not None else "",
                                               inventory_id=inv, listed=listed,
                                               why=f"{p.method}, {p.n_sales} sale(s)"))
            continue
        t = tp.get(p.sku)
        if t and (t.get("checked") or "").lower() in ("yes", "y") and _num(t.get("terapeak_price")):
            price = _num(t["terapeak_price"])
            if price != p.current:
                changes.append(variants.change(p.sku, "price", price,
                                               old=p.current if p.current is not None else "",
                                               inventory_id=inv, listed=listed,
                                               why="Terapeak check"))
        elif p.suggested is not None or p.decision != "keep":
            not_applied.append((p.sku, f"{p.decision}: {p.reason}"))
    return changes, not_applied


def run_report(priced, terapeak_rows=(), not_applied=(), connor_extra=(), total=None,
               in_hand=()):
    """
    The report as (lines, ready). `connor_extra` adds (sku, why) items from
    grading/variant review (holds, in-hand checks).
    """
    total = total if total is not None else len(priced)
    tp = {r["sku"]: r for r in terapeak_rows}

    def tp_priced(p):
        t = tp.get(p.sku, {})
        return (t.get("checked") or "").lower() in ("yes", "y") and _num(t.get("terapeak_price"))

    priced_n = sum(1 for p in priced if p.suggested is not None or p.method == "register"
                   or tp_priced(p))
    bands = {"10+": 0, "4-9": 0, "1-3": 0, "0": 0}
    for p in priced:
        bands[pricing.band(p.n_sales)] += 1
    need_tp = [p for p in priced if p.decision in ("terapeak", "keep")]
    checked = [p for p in need_tp if (tp.get(p.sku, {}).get("checked") or "").lower() in ("yes", "y")]
    kept_old = [p for p in priced if p.decision == "keep" and p.sku not in
                {c.sku for c in checked}]
    failed = [p for p in priced if p.decision == "hold"]
    connor = [(p.sku, f"Terapeak: {p.reason}") for p in need_tp if p not in checked]
    connor += [(p.sku, "comp lookup failed — re-run") for p in failed]
    connor += [(s, "NM £15+: in-hand check before listing") for s in in_hand]
    connor += list(connor_extra)

    lines = [f"SKUs priced: {priced_n} / {total}",
             f"sales: 10+ {bands['10+']}, 4-9 {bands['4-9']}, 1-3 {bands['1-3']}, 0 {bands['0']}",
             f"checked on Terapeak: {len(checked)} of {len(need_tp)} needing it",
             f"recommendations not applied: {len(not_applied)}"]
    lines += [f"    {s}: {why}" for s, why in not_applied]
    lines.append(f"kept at an old price without fresh comps: {len(kept_old)}")
    lines += [f"    {p.sku}: {p.title} (£{p.current})" for p in kept_old]
    lines.append(f"the Connor list: {len(connor)}")
    lines += [f"    {s}: {why}" for s, why in connor]
    skipped = (priced_n < total or not_applied or kept_old or connor or failed)
    ready = not skipped
    lines.append("READY" if ready else
                 "NOT READY — something above was skipped or is waiting on a person")
    return lines, ready


def in_hand_list(priced, grades):
    """NM priced £15+ (by its listing condition) needs an in-hand check."""
    return [p.sku for p in priced
            if grading.needs_in_hand(grades.get(p.sku, p.condition), p.suggested)]


def condition_changes(job, final, game, capped=False):
    """
    Condition change-file entries from final grades ({sku: grade}).

    The LISTING condition is what goes up: vintage Pokémon NM lists as LP, and
    a game whose NM is capped (audits.nm_status) lists NM as LP. HOLD and
    ungraded cards are left out and returned as holds.
    """
    from . import audits
    changes, holds = [], []
    for c in variants.job_cards(job):
        sku = c.get("sku", "")
        g = final.get(sku)
        if not g:
            holds.append((sku, "not graded"))
            continue
        if g == grading.HOLD:
            holds.append((sku, "HOLD"))
            continue
        vintage = grading.is_vintage(game, c.get("year"), c.get("setName", ""))
        listed = audits.apply_cap(grading.listing_grade(g, game, vintage), capped)
        if listed != (c.get("condition") or "").upper():
            changes.append(variants.change(
                sku, "condition", listed, old=c.get("condition", ""),
                inventory_id=c.get("inventoryId", ""), listed=c.get("status") == "listed",
                why=f"graded {g}" + (f", lists as {listed}" if listed != g else "")))
    return changes, holds
