"""
The variant review queue (feature 2.6) and the change file every push to
CardUploader goes through (section 3).

After CardUploader matches a batch, every card whose catalog entry ALSO comes
in a Reverse Holofoil / Cosmos / stamped / 1st Edition printing goes in a
queue. A person looks at the scan beside the catalog image and keys one of:
correct, switch variant, or HOLD.

Nothing switches a variant from the scans on its own: in the 21 Nov 2026
audit, 24 of 69 scan-based reverse-holo calls were wrong.

HOLD (no edit pushed):
* a WotC-era front with a modern back (the scanner paired the wrong images);
* a title clearly not matching the card — e.g. a World Championship deck card
  matched to the set print.

The data comes from CardUploader's `GET /jobs/{jobId}/data` (saved by the
extension, or fetched with the user's token); see carduploader_api.
"""

import csv
import datetime as _dt
import json
import os
import re

SPECIAL = (
    ("reverse", re.compile(r"reverse", re.I)),
    ("cosmos", re.compile(r"cosmos", re.I)),
    ("stamped", re.compile(r"stamp|staff|prerelease|pre-release|winner|peelable", re.I)),
    ("1st-edition", re.compile(r"1st\s*ed|first\s+edition", re.I)),
)

#: Title words that say a card is not the set print it was matched to.
NOT_THE_SET_PRINT = re.compile(r"world\s+champ|\bwcd\b|\bwc\s?\d{2}\b|gold\s+border|"
                               r"\bproxy\b|\bjumbo\b|\boversized\b", re.I)

KEYS = ("correct", "switch", "HOLD")
QUEUE_FIELDS = ["sku", "inventoryId", "title", "cardName", "setName", "cardnumber",
                "variant", "variants", "special", "status", "year", "front",
                "catalog_image", "hint", "decision", "target_variant", "note"]


def job_cards(job):
    """The card list out of a `/jobs/{id}/data` response (or just the list)."""
    if isinstance(job, list):
        return job
    res = job.get("results") or {}
    return res.get("cards") or job.get("cards") or []


def job_tcg(job):
    return ((job.get("settings") or {}).get("tcg") or "").strip() if isinstance(job, dict) else ""


def load_job(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def variants_of(card):
    """Every printing the card's catalog entry comes in, from skus[]."""
    seen = []
    for s in card.get("skus") or []:
        v = (s.get("variant") or "").strip()
        if v and v not in seen:
            seen.append(v)
    cur = (card.get("variant") or "").strip()
    if cur and cur not in seen:
        seen.insert(0, cur)
    return seen


def special_of(names):
    return sorted({k for n in names for k, rx in SPECIAL if rx.search(n)})


def build_queue(job, wotc_front_hint=None):
    """
    Rows for every card whose entry has a special printing among its variants.
    Pre-fills `hint` (never `decision`): a title that does not look like the
    set print, or `wotc_front_hint(card)` returning a reason.
    """
    rows = []
    for c in job_cards(job):
        names = variants_of(c)
        special = special_of(names)
        if not special or len(names) < 2:
            continue
        hint = ""
        title = c.get("title") or ""
        set_name = c.get("setName") or ""
        m = NOT_THE_SET_PRINT.search(title)
        if m and not NOT_THE_SET_PRINT.search(set_name):
            hint = f"title says '{m.group(0)}' but matched to {set_name} — HOLD?"
        if wotc_front_hint:
            hint = hint or (wotc_front_hint(c) or "")
        imgs = c.get("imageUrls") or {}
        rows.append({
            "sku": c.get("sku", ""), "inventoryId": c.get("inventoryId", ""),
            "title": title, "cardName": c.get("cardName", ""), "setName": set_name,
            "cardnumber": c.get("cardnumber", ""), "variant": c.get("variant", ""),
            "variants": " | ".join(names), "special": " ".join(special),
            "status": c.get("status", ""), "year": c.get("year", ""),
            "front": imgs.get("front", ""), "catalog_image": c.get("databaseImageUrl", ""),
            "hint": hint, "decision": "", "target_variant": "", "note": ""})
    return rows


def write_queue(rows, path):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=QUEUE_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path


def read_queue(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def queue_changes(rows):
    """
    Changes from a decided queue: `switch` rows become variant changes; the
    rest are recorded as not pushed. Returns (changes, holds, undecided).
    """
    changes, holds, undecided = [], [], []
    for r in rows:
        d = (r.get("decision") or "").strip()
        if not d:
            undecided.append(r["sku"])
        elif d.upper() == "HOLD":
            holds.append((r["sku"], r.get("note") or r.get("hint") or "held"))
        elif d == "switch":
            target = (r.get("target_variant") or "").strip()
            if not target or target == r.get("variant"):
                undecided.append(r["sku"])
                continue
            changes.append(change(r["sku"], "variant", target, old=r.get("variant", ""),
                                  inventory_id=r.get("inventoryId", ""),
                                  listed=r.get("status") == "listed",
                                  why="variant review: scan vs catalog image"))
    return changes, holds, undecided


# ---------------------------------------------------------------- the change file
#
# One JSON file per push: what CardCropper wants changed in ONE CardUploader
# job, by SKU. The extension maps SKU -> inventoryId from the job's data,
# rebuilds title and TCGplayer SKU exactly as the batch page would, shows the
# diff, and only applies on a click. Fields: price, condition, variant.

CHANGE_FIELDS = ("price", "condition", "variant")


def change(sku, field, value, old="", inventory_id="", listed=False, why=""):
    if field not in CHANGE_FIELDS:
        raise ValueError(f"unknown field {field!r}")
    if field == "price":
        value = f"{float(value):.2f}" if value not in ("", None) else ""
    return {"sku": sku, "field": field, "value": value, "old": old,
            "inventoryId": inventory_id, "listed": bool(listed), "why": why}


#: Yu-Gi-Oh prices never go below this, so a sync cannot push £0.99 back.
YGO_MIN_PRICE = 1.99


def validate_changes(changes, game):
    """Refuse anything the push rules forbid. Returns a list of problems."""
    problems, seen = [], set()
    for c in changes:
        key = (c["sku"], c["field"])
        if key in seen:
            problems.append(f"{c['sku']}: {c['field']} changed twice in one file")
        seen.add(key)
        if c["field"] == "price" and c["value"] != "":
            try:
                v = float(c["value"])
            except ValueError:
                problems.append(f"{c['sku']}: price {c['value']!r} is not a number")
                continue
            if game == "yugioh" and v < YGO_MIN_PRICE:
                problems.append(f"{c['sku']}: Yu-Gi-Oh price £{v:.2f} is below "
                                f"£{YGO_MIN_PRICE:.2f}")
            if v <= 0:
                problems.append(f"{c['sku']}: price £{v:.2f}")
        if c["field"] == "condition" and c["value"] not in ("NM", "LP", "MP", "HP", "DMG"):
            problems.append(f"{c['sku']}: condition {c['value']!r}")
    return problems


def write_change_file(path, job_id, job_name, game, changes, tcg=""):
    problems = validate_changes(changes, game)
    if problems:
        raise ValueError("change file refused:\n  " + "\n  ".join(problems))
    data = {"version": 1, "kind": "cardcropper-changes", "jobId": job_id,
            "jobName": job_name, "game": game, "tcg": tcg,
            "created": _dt.datetime.now().isoformat(timespec="seconds"),
            "changes": changes}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path
