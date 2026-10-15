"""
The back-border wear score, the triage bands it sorts cards into, and the
calibration loop that is meant to move those bands.

Ported from the card-conditioning skill's `prep_scans.py` without changing a
number, so a triage.csv from the app and one from the script agree.

**The score sorts; it never grades.** It is a count of bright pixels on the
navy BACK border — whitening and corner nicks. It cannot see a crease, a
surface scratch or anything on the front, so a card can score clean and still
be LP. Where the score and the house guide disagree, the guide wins.

**The thresholds are provisional.** They were fitted on six hand-graded cards,
which is a sanity check rather than a measurement. So they live in a file, not
in the code, and `fit()` proposes new ones from grades people actually saw.
"""

import csv
import json
import os
from dataclasses import asdict, dataclass

import numpy as np
from PIL import Image

from . import imaging

#: Every card is resampled to this before scoring, so one set of thresholds
#: holds across scans that framed the card slightly differently.
CARD_W, CARD_H = imaging.CARD_W, imaging.CARD_H

#: The outermost pixels catch scanner light on EVERY card, worn or not, so the
#: measured band starts inside them. Beyond ~18px in you are off the border and
#: onto the artwork.
BAND_INNER, BAND_OUTER = 4, 18

#: A pixel counts as whitening if its value exceeds the card's own border
#: median by this much. Relative, so a darker scan does not read as a cleaner
#: card. The border is a tight distribution — median 50, 99th percentile 59 —
#: so +30 is already well clear of clean navy.
BRIGHT_DELTA = 30

#: Corner squares are measured separately from edges: the guide counts corner
#: nicks, and a corner is where a card is handled.
CORNER_BOX = 90

#: A clean navy back border sits around 50 on the value channel. Well below 40
#: means the border was never found: the card edge detection missed and the
#: band was measured on scanner bed, so every percentage is meaningless.
MIN_BORDER_BASE = 40

#: Grades in the order of the house scale, best first.
GRADES = ("NM", "LP", "MP", "HP", "DMG")


def _band_mask():
    """A ring inside the card outline: the border, minus the lit outer edge."""
    ring = np.zeros((CARD_H, CARD_W), dtype=bool)
    ring[BAND_INNER:CARD_H - BAND_INNER, BAND_INNER:CARD_W - BAND_INNER] = True
    ring[BAND_OUTER:CARD_H - BAND_OUTER, BAND_OUTER:CARD_W - BAND_OUTER] = False
    return ring


BAND = _band_mask()


def _rect(x0, y0, x1, y1):
    r = np.zeros_like(BAND)
    r[y0:y1, x0:x1] = True
    return r


_C = CORNER_BOX
_REGIONS = {
    "corner_tl": _rect(0, 0, _C, _C),
    "corner_tr": _rect(CARD_W - _C, 0, CARD_W, _C),
    "corner_bl": _rect(0, CARD_H - _C, _C, CARD_H),
    "corner_br": _rect(CARD_W - _C, CARD_H - _C, CARD_W, CARD_H),
    "edge_top": _rect(_C, 0, CARD_W - _C, BAND_OUTER),
    "edge_bottom": _rect(_C, CARD_H - BAND_OUTER, CARD_W - _C, CARD_H),
    "edge_left": _rect(0, _C, BAND_OUTER, CARD_H - _C),
    "edge_right": _rect(CARD_W - BAND_OUTER, _C, CARD_W, CARD_H - _C),
}
CORNERS = ("corner_tl", "corner_tr", "corner_bl", "corner_br")


def score_back(card):
    """
    Bright-pixel percentages over the back border of a deskewed, flush-cropped
    card (`imaging.straighten`'s `exact`).

    Measured against the card's OWN border median, so a dark scan and a light
    scan of the same card score the same. Returns a flat dict whose keys are
    the triage.csv column names.
    """
    if card.size != (CARD_W, CARD_H):
        card = card.resize((CARD_W, CARD_H), Image.LANCZOS)
    val = np.asarray(card.convert("RGB")).astype(int).max(axis=2)
    base = float(np.median(val[BAND]))
    bright = (val > base + BRIGHT_DELTA) & BAND

    def pct(region):
        n = int((BAND & region).sum())
        return round(100.0 * float((bright & region).sum()) / n, 2) if n else 0.0

    out = {k: pct(r) for k, r in _REGIONS.items()}
    out["worst_corner_pct"] = max(out[k] for k in CORNERS)
    out["wear_total_pct"] = round(pct(np.ones_like(BAND)), 2)
    out["border_base"] = round(base, 1)
    return out


# ---------------------------------------------------------------- triage


@dataclass
class Thresholds:
    """
    Where the triage bands fall. These sort the batch; they do not grade it.

    The defaults are the skill's, from six hand-graded cards:

        NM  worst corner 0.05   total 0.00
        LP               0.41         0.04
        LP               0.63         0.05
        LP               2.49         0.41
        MP               2.85         0.52
        MP               9.76         1.73

    LP at 2.49 and MP at 2.85 sit next to each other, so the upper edge is soft
    and the bands are deliberately wide: a card is only called clean when BOTH
    measures are near zero, and only called worse when one is far out.
    Everything else is "review" — a needless look costs seconds, a wrong
    unattended NM costs a return.
    """
    clean_corner: float = 0.20
    clean_total: float = 0.02
    worse_corner: float = 3.0
    worse_total: float = 1.0
    #: Where these numbers came from, carried with them so a thresholds file
    #: is never mistaken for a measurement it is not.
    source: str = "provisional — 6 hand-graded cards (card-conditioning skill)"

    @classmethod
    def load(cls, path):
        """Thresholds from a JSON file, or the defaults where there is none."""
        if not path or not os.path.exists(path):
            return cls()
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, indent=2)
            fh.write("\n")


def triage(score, t=None):
    """Sort a card into likely-nm / review / likely-worse. NOT a grade."""
    t = t or Thresholds()
    if score["worst_corner_pct"] > t.worse_corner or score["wear_total_pct"] > t.worse_total:
        return "likely-worse"
    if score["worst_corner_pct"] < t.clean_corner and score["wear_total_pct"] < t.clean_total:
        return "likely-nm"
    return "review"


def sanity_check(records):
    """
    Batch-level warnings that mean the scores cannot be trusted.

    Either symptom means the card edge was not found and the band was measured
    on the wrong pixels. A run like that still produces a neat, sorted
    triage.csv, which is exactly why it has to be called out: grading from it
    is grading from noise.
    """
    scored = [r for r in records if isinstance(r.get("border_base"), (int, float))]
    if not scored:
        return []
    warnings = []
    low = [r for r in scored if r["border_base"] < MIN_BORDER_BASE]
    if len(low) > len(scored) / 2:
        warnings.append(
            f"{len(low)} of {len(scored)} cards have border_base below "
            f"{MIN_BORDER_BASE} (median {np.median([r['border_base'] for r in scored]):.0f}). "
            "Card-edge detection has probably failed and the scores are "
            "meaningless — do not grade from them.")
    elif low:
        warnings.append(
            f"{len(low)} card(s) have border_base below {MIN_BORDER_BASE} — their "
            "scores are unreliable: " + ", ".join(r["sku"] for r in low[:10])
            + ("…" if len(low) > 10 else ""))
    worse = [r for r in scored if r.get("triage") == "likely-worse"]
    if len(scored) >= 10 and len(worse) > 0.8 * len(scored):
        warnings.append(
            f"{len(worse)} of {len(scored)} cards landed in likely-worse. That is "
            "what a failed edge detection looks like, not what a batch looks "
            "like — check a few crops before trusting any of it.")
    return warnings


# ---------------------------------------------------------------- calibration
#
# The loop: a person grades cards from the review sheet, saying which ones
# they actually LOOKED at. Those — and only those — go into a ledger next to
# the score the card got. Re-fitting from the ledger proposes thresholds.
#
# Grades taken from the score (looked = no) are kept out on purpose: fitting
# the score to grades that were themselves read off the score would only ever
# confirm whatever thresholds were already in place.

LEDGER_FIELDS = ["sku", "grade", "worst_corner_pct", "wear_total_pct",
                 "border_base", "recorded_from"]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def record_grades(review_rows, triage_rows, ledger_path, recorded_from=""):
    """
    Append every SEEN grade in a review sheet to the calibration ledger.

    Returns (added, skipped) where skipped is a list of (sku, reason). A SKU
    already in the ledger is replaced rather than duplicated, so recording the
    same review twice does not double its weight in the fit.
    """
    scores = {r["sku"]: r for r in triage_rows}
    existing = read_ledger(ledger_path)
    by_sku = {r["sku"]: r for r in existing}
    added, skipped = 0, []
    for r in review_rows:
        sku = (r.get("sku") or "").strip()
        grade = (r.get("grade") or "").strip().upper()
        if not sku or not grade:
            continue
        if grade not in GRADES:
            skipped.append((sku, f"grade {grade} is not on the scale"))
            continue
        if (r.get("looked") or "").strip().lower() != "yes":
            skipped.append((sku, "not looked at — a grade read off the score "
                                 "cannot calibrate the score"))
            continue
        s = scores.get(sku)
        corner = _num(s and s.get("worst_corner_pct"))
        total = _num(s and s.get("wear_total_pct"))
        base = _num(s and s.get("border_base"))
        if corner is None or total is None:
            skipped.append((sku, "no score in triage.csv"))
            continue
        if base is not None and base < MIN_BORDER_BASE:
            skipped.append((sku, f"border_base {base:.0f} — score unreliable"))
            continue
        by_sku[sku] = {"sku": sku, "grade": grade, "worst_corner_pct": corner,
                       "wear_total_pct": total, "border_base": base,
                       "recorded_from": recorded_from}
        added += 1
    os.makedirs(os.path.dirname(os.path.abspath(ledger_path)), exist_ok=True)
    with open(ledger_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LEDGER_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(by_sku.values())
    return added, skipped


def read_ledger(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        corner, total = _num(r.get("worst_corner_pct")), _num(r.get("wear_total_pct"))
        if corner is None or total is None:
            continue
        r["worst_corner_pct"], r["wear_total_pct"] = corner, total
        r["grade"] = (r.get("grade") or "").upper()
        out.append(r)
    return out


#: Fewest cards each side of a boundary before a fit is offered at all.
MIN_PER_SIDE = 3
#: Below this many graded cards in total, a fit is shown with a warning.
WEAK_SAMPLE = 30
#: Proposed boundaries sit inside the nearest observed card by this factor,
#: rather than on it. Fitting a boundary exactly to the most extreme card seen
#: so far is fitting to that card.
CLEAN_MARGIN, WORSE_MARGIN = 0.5, 1.25


def fit(ledger_rows, current=None):
    """
    Propose thresholds from graded cards, conservatively.

    The bands answer two questions, and each boundary is fitted to never get
    its question wrong on the cards seen so far:

    * likely-nm must contain no card graded LP or worse. So the clean
      boundaries sit below the LOWEST score any LP-or-worse card got.
    * likely-worse must contain no card graded NM or LP (it means "MP or
      worse — look"). So the worse boundaries sit above the HIGHEST score any
      NM or LP card got.

    Both then step inward by a margin. Returns (Thresholds or None, report
    lines). None means there is not enough evidence yet to propose anything.
    """
    current = current or Thresholds()
    lines = []
    by_grade = {}
    for r in ledger_rows:
        by_grade.setdefault(r["grade"], []).append(r)
    for g in GRADES:
        rows = by_grade.get(g, [])
        if not rows:
            continue
        c = sorted(r["worst_corner_pct"] for r in rows)
        t = sorted(r["wear_total_pct"] for r in rows)
        lines.append(f"{g:<4} n={len(rows):<4} worst corner {c[0]:.2f} / "
                     f"{c[len(c) // 2]:.2f} / {c[-1]:.2f}   total {t[0]:.2f} / "
                     f"{t[len(t) // 2]:.2f} / {t[-1]:.2f}   (min / median / max)")

    # How the CURRENT thresholds do on what has been seen.
    wrong_clean = [r for r in ledger_rows if r["grade"] != "NM"
                   and triage(r, current) == "likely-nm"]
    wrong_worse = [r for r in ledger_rows if r["grade"] in ("NM", "LP")
                   and triage(r, current) == "likely-worse"]
    lines.append(f"current thresholds put {len(wrong_clean)} LP-or-worse card(s) in "
                 f"likely-nm and {len(wrong_worse)} NM/LP card(s) in likely-worse")
    for r in wrong_clean + wrong_worse:
        lines.append(f"    {r['sku']}  graded {r['grade']}  corner "
                     f"{r['worst_corner_pct']:.2f}  total {r['wear_total_pct']:.2f}")

    played = [r for r in ledger_rows if r["grade"] != "NM"]
    light = [r for r in ledger_rows if r["grade"] in ("NM", "LP")]
    nm = [r for r in ledger_rows if r["grade"] == "NM"]
    worse = [r for r in ledger_rows if r["grade"] in ("MP", "HP", "DMG")]
    short = [name for name, rows in (("NM", nm), ("LP-or-worse", played),
                                     ("NM/LP", light), ("MP-or-worse", worse))
             if len(rows) < MIN_PER_SIDE]
    if short:
        lines.append(f"not enough graded cards to propose thresholds — need at least "
                     f"{MIN_PER_SIDE} each of: {', '.join(short)}")
        return None, lines

    proposed = Thresholds(
        clean_corner=round(min(r["worst_corner_pct"] for r in played) * CLEAN_MARGIN, 3),
        clean_total=round(min(r["wear_total_pct"] for r in played) * CLEAN_MARGIN, 3),
        worse_corner=round(max(r["worst_corner_pct"] for r in light) * WORSE_MARGIN, 3),
        worse_total=round(max(r["wear_total_pct"] for r in light) * WORSE_MARGIN, 3),
        source=f"fitted from {len(ledger_rows)} looked-at grades",
    )
    if proposed.clean_corner >= proposed.worse_corner:
        lines.append("the fitted bands overlap — the score is not separating the "
                     "grades on this sample; keep the current thresholds")
        return None, lines
    lines.append(f"proposed: clean corner < {proposed.clean_corner} and total < "
                 f"{proposed.clean_total}; worse corner > {proposed.worse_corner} or "
                 f"total > {proposed.worse_total}")
    if len(ledger_rows) < WEAK_SAMPLE:
        lines.append(f"only {len(ledger_rows)} graded cards — treat the proposal as a "
                     "direction, not a measurement")
    return proposed, lines
