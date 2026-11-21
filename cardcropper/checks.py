"""
Completeness and crop checks over a batch manifest (feature 2.2), with the
triage scores (2.3) computed in the same pass so each face is straightened
once.

Per card:

* every one of the four images must exist — a missing one is flagged;
* an edge strip of a face that is solid black means the edge was missed: the
  crop shows bed where the card should be. That panel counts as UNCHECKED
  until it is re-cropped from the full scan, so the grading screen will not
  accept NM over it;
* a second card in frame, or scanner bed showing inside the card's outline
  (a mis-feed, a card short of the frame) is a HOLD.

Then the scores: whitening points per panel on both faces, and for English
Pokémon backs the reference-back marks when a reference is available.
"""

import csv
import os

from . import imaging, wear

#: A strip is "solid black" when this share of it is darker than the ink bar.
BLACK_SHARE = 0.97
#: A corner square of this many reference pixels with more than BED_SHARE of
#: bed inside the card's outline means the card does not fill its own box.
BED_CORNER = 60
BED_SHARE = 0.5

CHECK_FIELDS = ["sku", "status", "missing", "unchecked_panels", "hold_reasons",
                "notes", "whitening", "front_points", "back_points",
                "front_corners_whitened", "back_corners_whitened",
                "back_marks", "back_biggest_mark", "back_longest_mark", "back_flag",
                "may_skip_review"]

#: Edge strip names as imaging.edge_crops returns them, to panel names.
_STRIP_PANEL = {"TOP": "top", "BOTTOM": "bottom", "LEFT": "left", "RIGHT": "right"}


def black_strips(padded):
    """Edges whose crop strip is solid black (all bed, no card)."""
    margin, cut, strip = imaging._cuts(padded)
    out = []
    for name, img in imaging.edge_crops(padded, cut, strip, margin, split=False):
        v = imaging._value(img)
        if v.size and float((v < imaging.INK_THRESHOLD).mean()) >= BLACK_SHARE:
            out.append(_STRIP_PANEL[name])
    return out


def bed_in_corners(exact):
    """Corners of the card's outline that are mostly bed."""
    k = exact.size[0] / float(imaging.CARD_W)
    c = max(4, round(BED_CORNER * k))
    v = imaging._value(exact)
    h, w = v.shape
    boxes = {"tl": v[:c, :c], "tr": v[:c, w - c:], "bl": v[h - c:, :c], "br": v[h - c:, w - c:]}
    dark = {n: float((b < imaging.INK_THRESHOLD).mean()) for n, b in boxes.items()}
    # A card printed black to its edge reads as bed everywhere; that is an
    # unreadable outline, reported by the crop run, not a mis-feed.
    if min(dark.values()) >= BED_SHARE:
        return []
    return [n for n, share in dark.items() if share >= BED_SHARE]


def check_face(path, background="dark", game="pokemon", face="front"):
    """
    Straighten one face and check it. Returns (result, exact, padded) where
    result has unchecked, holds, notes and whitening counts.
    """
    res = {"unchecked": [], "holds": [], "notes": [], "whitening": None}
    im = imaging.load(path)
    if imaging.split_regions(im, background=background) is not None:
        res["holds"].append(f"{face}: a second card is in frame")
    exact, padded, _, _ = imaging.straighten(im, background=background)
    ok, why = imaging.detection_ok(exact, im)
    if not ok:
        res["holds"].append(f"{face}: {why}")
        return res, None, None
    for edge in black_strips(padded):
        res["unchecked"].append(f"{face}-{edge}")
    if res["unchecked"]:
        res["notes"].append(f"{face}: solid black edge strip ({', '.join(res['unchecked'])}) "
                            "— missed edge, re-crop from the full scan")
    bed = bed_in_corners(exact)
    if bed:
        res["holds"].append(f"{face}: scanner bed showing in the {', '.join(bed)} corner(s)")
    res["whitening"] = wear.whitening(exact, padded, game=game, face=face)
    return res, exact, padded


def check_card(card, game="pokemon", language="en", reference=None,
               calibration=None, background="dark", vintage=False):
    """
    One manifest card (batches.ManifestCard) to a row of CHECK_FIELDS.

    `status` is HOLD, INCOMPLETE (images missing), UNCHECKED (some panel cannot
    be read) or OK. OK means only "ready to be looked at".
    """
    row = {"sku": card.sku, "missing": " ".join(card.missing)}
    unchecked, holds, notes = [], [], []
    counts = {}
    for face in ("front", "back"):
        path = getattr(card, face)
        if not path or not os.path.exists(path):
            continue
        try:
            res, exact, _ = check_face(path, background, game, face)
        except Exception as exc:                            # noqa: BLE001
            holds.append(f"{face}: could not be read ({exc})")
            continue
        unchecked += res["unchecked"]
        holds += res["holds"]
        notes += res["notes"]
        if res["whitening"] is not None:
            counts[face] = res["whitening"]
        if (face == "back" and exact is not None and reference is not None
                and game == "pokemon" and language == "en"):
            m = wear.compare_back(exact, reference)
            row.update(back_marks=m["marks"], back_biggest_mark=m["biggest_area"],
                       back_longest_mark=m["longest"], back_flag=m["flag"])
            if m["flag"] == "misaligned?":
                notes.append("back: huge difference from the reference — probably "
                             "misaligned, check by eye")
    for role in card.missing:
        notes.append(f"missing {role}")
    for face in ("front", "back"):
        if face in counts:
            row[f"{face}_points"] = wear.total_points(counts[face])
            row[f"{face}_corners_whitened"] = wear.corners_whitened(counts[face])
    row["whitening"] = wear.format_counts(counts) if counts else ""
    line = wear.game_line(game, language, vintage)
    row["may_skip_review"] = "yes" if wear.may_skip_review(line, calibration) else "no"
    row["unchecked_panels"] = " ".join(unchecked)
    row["hold_reasons"] = "; ".join(holds)
    row["notes"] = "; ".join(notes)
    row["status"] = ("HOLD" if holds else "INCOMPLETE" if card.missing
                     else "UNCHECKED" if unchecked else "OK")
    row["_counts"] = counts
    return row


def check_batch(b, reference=None, calibration=None, background="dark",
                progress=None, should_stop=None):
    rows = []
    for i, card in enumerate(b.cards, 1):
        if should_stop and should_stop():
            break
        rows.append(check_card(card, b.game, b.language, reference, calibration,
                               background))
        if progress:
            progress(i, len(b.cards), rows[-1])
    return rows


def checks_path(b, out_dir=None):
    return os.path.join(out_dir or b.source, f"checks-{b.code}.csv")


def write_checks(rows, path):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CHECK_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path


def read_checks(path):
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return {r["sku"]: r for r in csv.DictReader(fh)}


def summary(rows):
    by = {}
    for r in rows:
        by[r["status"]] = by.get(r["status"], 0) + 1
    return ", ".join(f"{k} {v}" for k, v in sorted(by.items()))


def reference_from_manifest_backs(paths, background="dark"):
    """Build a reference back from scan paths of LP English Pokémon backs."""
    cards = []
    for p in paths:
        exact, _, _, _ = imaging.straighten(imaging.load(p), background=background)
        cards.append(exact)
    if len(cards) < 10:
        raise ValueError(f"a reference back needs ~50 LP backs; got {len(cards)}")
    return wear.build_reference(cards)

