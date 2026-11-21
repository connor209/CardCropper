"""
Local, no-AI triage scores for a card's faces (feature 2.3), and the gate that
decides whether any score may ever stand in for a look.

Two measurements:

* **Reference-back marks** (English Pokémon backs). Every back is the same
  print, so a back is compared pixel for pixel with a reference built from
  ~50 LP cards and what differs is a mark. A mark of MARK_REVIEW_AREA px area
  or MARK_REVIEW_LENGTH px long is flagged for review; MARK_DMG_AREA or
  MARK_DMG_LENGTH is a likely DMG candidate. A huge total usually means the
  two did not line up, not that the card is wrecked — check by eye.
* **Edge whitening per panel** (every game, both faces). Each corner and edge
  is searched for distinct bright points brighter than the scanner's soft halo
  around the card. The output is a panel-by-panel count, e.g.
  `front: corners 0, top 0, R 1, bottom 0, L 0; back: corners 1, edges 0`.

Pixel sizes are in reference pixels, the card resampled to imaging.CARD_W x
CARD_H (700 x 980), so they hold across scan resolutions.

**Scores triage. They never grade, and no game may skip the human/AI look on
a score** until it has been calibrated on that game's golden set with zero
LP-or-worse cards scoring below the clean threshold. Today that is no game;
Yu-Gi-Oh never may (every card is opened). See `may_skip_review`.

Things deliberately NOT counted as wear: off-centring (never measured), the
uniform unbroken scanner hairline along an edge (a line of brightness running
most of an edge's length is removed before counting), and the printed dot near
the top-left inner corner of MTG backs (that area is masked). Scanner streaks
and holo moiré sit away from the border band the points are searched in, which
keeps most of them out; a streak that crosses an edge shows as one point on
that edge and is for the grader to dismiss.
"""

import json
import os

import numpy as np
from PIL import Image

from . import imaging

CARD_W, CARD_H = imaging.CARD_W, imaging.CARD_H

# ---------------------------------------------------------------- reference back

#: A pixel differs from the reference if its brightness (after both are
#: levelled to their own median) is this far apart.
MARK_DELTA = 45
#: Thresholds from the house notes, in reference pixels.
MARK_REVIEW_AREA, MARK_REVIEW_LENGTH = 50, 30
MARK_DMG_AREA, MARK_DMG_LENGTH = 150, 60
#: Above this share of the card differing, the comparison itself is suspect.
MISALIGNED_SHARE = 0.04
#: The outermost pixels catch scanner light on every card.
MARK_SKIP_EDGE = 4
#: How far the card may be nudged to line up with the reference.
ALIGN_SHIFT = 3


def _levelled(card):
    """A card as float brightness at reference size, its median moved to 0."""
    if card.size != (CARD_W, CARD_H):
        card = card.resize((CARD_W, CARD_H), Image.LANCZOS)
    v = np.asarray(card.convert("L")).astype(np.float32)
    return v - float(np.median(v))


def build_reference(cards):
    """
    The per-pixel median of `cards` (flush-cropped, deskewed backs).

    Built from LP cards on purpose: NM copies are rare in a run, and the median
    of 50 throws out any one card's wear anyway.
    """
    stack = np.stack([_levelled(c) for c in cards])
    return np.median(stack, axis=0).astype(np.float32)


def save_reference(ref, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    np.save(path, ref)


def load_reference(path):
    return np.load(path) if path and os.path.exists(path) else None


def components(mask):
    """
    Connected regions (8-connected) of a boolean mask, without scipy.

    Run-length labelling: each row's runs are joined to overlapping runs in
    the row above through a union-find. Returns a list of dicts with area,
    bbox (x0, y0, x1, y1) and length (the longer bbox side, or its diagonal
    for a slanted mark, whichever is longer).
    """
    parent = []

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    runs = []                      # (row, start, end, label)
    prev = []
    for y in range(mask.shape[0]):
        row = mask[y]
        if not row.any():
            prev = []
            continue
        d = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
        starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1
        cur = []
        for s, e in zip(starts.tolist(), ends.tolist()):
            label = None
            for ps, pe, pl in prev:
                if ps <= e + 1 and pe >= s - 1:
                    if label is None:
                        label = find(pl)
                    else:
                        a, b = find(label), find(pl)
                        if a != b:
                            parent[b] = a
                            label = a
            if label is None:
                label = len(parent)
                parent.append(label)
            cur.append((s, e, label))
            runs.append((y, s, e, label))
        prev = cur
    regions = {}
    for y, s, e, label in runs:
        r = regions.setdefault(find(label), [0, s, y, e, y])
        r[0] += e - s + 1
        r[1], r[2] = min(r[1], s), min(r[2], y)
        r[3], r[4] = max(r[3], e), max(r[4], y)
    out = []
    for area, x0, y0, x1, y1 in regions.values():
        w, h = x1 - x0 + 1, y1 - y0 + 1
        # A slanted scratch fills little of its box; its length is the diagonal.
        slanted = min(w, h) > 1 and area <= 0.5 * w * h
        length = (w * w + h * h) ** 0.5 if slanted else max(w, h)
        out.append({"area": area, "bbox": (x0, y0, x1, y1), "length": round(length, 1)})
    return out


def compare_back(card, ref):
    """
    Marks on a back that the reference does not have.

    Returns {marks, biggest_area, longest, differing_share, flag} where flag
    is "" (nothing), "review", "dmg-candidate" or "misaligned?".
    """
    v = _levelled(card)
    best = None
    s = ALIGN_SHIFT
    core = (slice(s + MARK_SKIP_EDGE, CARD_H - s - MARK_SKIP_EDGE),
            slice(s + MARK_SKIP_EDGE, CARD_W - s - MARK_SKIP_EDGE))
    target = v[core]
    for dy in range(-s, s + 1):
        for dx in range(-s, s + 1):
            r = ref[core[0].start + dy:core[0].stop + dy,
                    core[1].start + dx:core[1].stop + dx]
            diff = np.abs(target - r)
            err = float(diff.mean())
            if best is None or err < best[0]:
                best = (err, diff)
    mask = best[1] > MARK_DELTA
    share = float(mask.mean())
    if share > MISALIGNED_SHARE:
        return {"marks": None, "biggest_area": None, "longest": None,
                "differing_share": round(share, 4), "flag": "misaligned?"}
    marks = [m for m in components(mask) if m["area"] >= 4]
    biggest = max((m["area"] for m in marks), default=0)
    longest = max((m["length"] for m in marks), default=0)
    if biggest >= MARK_DMG_AREA or longest >= MARK_DMG_LENGTH:
        flag = "dmg-candidate"
    elif biggest >= MARK_REVIEW_AREA or longest >= MARK_REVIEW_LENGTH:
        flag = "review"
    else:
        flag = ""
    return {"marks": len(marks), "biggest_area": biggest, "longest": longest,
            "differing_share": round(share, 4), "flag": flag}


# ---------------------------------------------------------------- edge whitening

#: Depth of the border band searched, in reference pixels from the card edge.
#: The outermost pixel or two are the edge itself and are kept: whitening IS
#: the edge turning white.
WHITE_BAND = 14
#: Corner square, as in scoring.CORNER_BOX.
CORNER = 90
#: A point must clear its panel's own median by this much...
WHITE_DELTA = 45
#: ...and the brightest of the scanner's halo just outside the card.
HALO_PERCENTILE = 99.5
#: A point smaller than this is sensor noise.
MIN_POINT = 3
#: A line of brightness covering this share of an edge, at one depth, is the
#: scanner hairline and not wear.
HAIRLINE_SHARE = 0.6
#: MTG backs carry a printed dot near the top-left inner corner; pixels deeper
#: than this into that corner are ignored on MTG backs.
MTG_DOT_DEPTH = 8

PANELS = ("tl", "tr", "bl", "br", "top", "right", "bottom", "left")
CORNER_PANELS = ("tl", "tr", "bl", "br")
EDGE_PANELS = ("top", "right", "bottom", "left")
_SHORT = {"top": "top", "right": "R", "bottom": "bottom", "left": "L"}


def _halo_level(padded, exact_size):
    """The brightest the scanner's glow gets in the margin around the card."""
    v = imaging._value(padded).astype(np.int16)
    h, w = v.shape
    ew, eh = exact_size
    mx, my = max(0, (w - ew) // 2), max(0, (h - eh) // 2)
    if mx < 2 and my < 2:
        return 0.0
    ring = np.zeros((h, w), bool)
    ring[:my, :] = ring[h - my:, :] = True
    ring[:, :mx] = ring[:, w - mx:] = True
    vals = v[ring]
    return float(np.percentile(vals, HALO_PERCENTILE)) if vals.size else 0.0


def _strip_hairlines(bright, axis_len_axis):
    """Remove lines of brightness that run most of a strip's length."""
    # axis_len_axis: 1 means the strip runs along x (top/bottom edges).
    share = bright.mean(axis=axis_len_axis)
    out = bright.copy()
    for i, s in enumerate(share):
        if s >= HAIRLINE_SHARE:
            if axis_len_axis == 1:
                out[i, :] = False
            else:
                out[:, i] = False
    return out


def whitening(exact, padded=None, game="pokemon", face="back"):
    """
    Distinct bright points per panel on one face.

    `exact` is the flush-cropped card, `padded` the same with its margin of
    bed (for the halo). Returns {panel: count} over PANELS.
    """
    card = exact.resize((CARD_W, CARD_H), Image.LANCZOS) \
        if exact.size != (CARD_W, CARD_H) else exact
    v = imaging._value(card).astype(np.int16)
    halo = _halo_level(padded, exact.size) if padded is not None else 0.0
    b, c = WHITE_BAND, CORNER
    band = np.zeros_like(v, bool)
    band[:b, :] = band[-b:, :] = True
    band[:, :b] = band[:, -b:] = True

    def bright_in(region):
        vals = v[region & band]
        base = float(np.median(vals)) if vals.size else 0.0
        return (v > max(halo, base + WHITE_DELTA)) & region & band

    def rect(x0, y0, x1, y1):
        r = np.zeros_like(band)
        r[y0:y1, x0:x1] = True
        return r

    regions = {
        "tl": rect(0, 0, c, c), "tr": rect(CARD_W - c, 0, CARD_W, c),
        "bl": rect(0, CARD_H - c, c, CARD_H), "br": rect(CARD_W - c, CARD_H - c, CARD_W, CARD_H),
        "top": rect(c, 0, CARD_W - c, b), "bottom": rect(c, CARD_H - b, CARD_W - c, CARD_H),
        "left": rect(0, c, b, CARD_H - c), "right": rect(CARD_W - b, c, CARD_W, CARD_H - c),
    }
    out = {}
    for name, region in regions.items():
        bright = bright_in(region)
        if name in ("top", "bottom"):
            sub = _strip_hairlines(bright[:, c:CARD_W - c], 1)
            bright[:, c:CARD_W - c] = sub
        elif name in ("left", "right"):
            sub = _strip_hairlines(bright[c:CARD_H - c, :], 0)
            bright[c:CARD_H - c, :] = sub
        if name == "tl" and game == "mtg" and face == "back":
            bright[MTG_DOT_DEPTH:, MTG_DOT_DEPTH:] = False
        out[name] = sum(1 for m in components(bright) if m["area"] >= MIN_POINT)
    return out


def total_points(counts):
    return sum(counts.get(p, 0) for p in PANELS)


def format_counts(by_face):
    """`front: corners 0, top 0, R 1, bottom 0, L 0; back: ...`."""
    parts = []
    for face in ("front", "back"):
        counts = by_face.get(face)
        if counts is None:
            parts.append(f"{face}: not scored")
            continue
        corners = sum(counts[p] for p in CORNER_PANELS)
        edges = ", ".join(f"{_SHORT[p]} {counts[p]}" for p in EDGE_PANELS)
        parts.append(f"{face}: corners {corners}, {edges}")
    return "; ".join(parts)


def corners_whitened(counts):
    """How many of a face's four corners have at least one point."""
    return sum(1 for p in CORNER_PANELS if counts.get(p, 0) > 0)


# ---------------------------------------------------------------- the gate

#: Game lines the golden sets and calibration are kept per.
GAME_LINES = ("pokemon-en", "pokemon-jp-vintage", "yugioh", "mtg")

#: Lines whose every card is opened, whatever the calibration says.
ALWAYS_OPEN = {"yugioh"}


def game_line(game, language="en", vintage=False):
    if game == "pokemon":
        return "pokemon-jp-vintage" if language == "jp" and vintage else (
            "pokemon-jp" if language == "jp" else "pokemon-en")
    return game


def load_calibration(path):
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def calibrate_line(golden, clean_threshold):
    """
    Whether `clean_threshold` (total whitening points below which a card is
    called clean) holds on a golden set: rows with `grade` (true, in-hand)
    and `points`. Returns (ok, report).
    """
    graded = [g for g in golden if g.get("grade") and g.get("points") is not None]
    below = [g for g in graded if g["points"] < clean_threshold]
    bad = [g for g in below if g["grade"] != "NM"]
    ok = len(graded) >= 30 and not bad
    report = (f"{len(graded)} golden cards, {len(below)} below {clean_threshold} points, "
              f"{len(bad)} of those LP or worse"
              + ("" if len(graded) >= 30 else " — a golden set needs 30+ cards"))
    return ok, report


def may_skip_review(line, calibration):
    """
    True only for a line calibrated on its golden set with zero LP-or-worse
    cards below the clean threshold. Today: no line. Yu-Gi-Oh: never.
    """
    if line in ALWAYS_OPEN:
        return False
    c = (calibration or {}).get(line) or {}
    return bool(c.get("calibrated")) and c.get("lp_or_worse_below_clean") == 0 \
        and isinstance(c.get("clean_threshold"), (int, float))
