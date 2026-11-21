"""
The grading rules (feature 2.4), enforced in code so the grading screen
cannot record a grade the house guide forbids.

Grade rules:

* NM needs every panel of both faces opened (looked = yes) — and none of them
  unchecked by the crop checks — and fewer than NM_MAX_POINTS wear points
  across both faces.
* Whitened tips on 3 or 4 corners of either face: LP at best.
* Any crease: never MP. HP if it is short, runs in from an edge only, and the
  ink is intact; otherwise DMG. (House decision, replacing the guide's
  "a single light crease: MP".)
* Two passes that disagree: the worse grade wins.

Listing rules, applied after grading:

* Pokémon vintage — WotC (1999-2003) and EX era (2003-2007) — graded NM lists
  as LP. Modern Pokémon NM lists as NM.
* Yu-Gi-Oh and MTG NM stays NM.
* NM priced £15 or more goes to an in-hand check before listing.

Full rules: project doc `claude/conditioning-guide-v2.md`.
"""

import csv
import datetime as _dt
import os
import re
from dataclasses import dataclass, field

GRADES = ("NM", "LP", "MP", "HP", "DMG")
HOLD = "HOLD"
KEYS = GRADES + (HOLD,)

#: Wear points across both faces at which NM is no longer possible.
NM_MAX_POINTS = 3
#: Corners whitened on one face at which a card is LP at best.
CORNERS_FOR_LP = 3
#: NM at or above this price is checked in hand before listing.
IN_HAND_PRICE = 15.0

FACES = ("front", "back")
PANELS = ("tl", "tr", "bl", "br", "top", "right", "bottom", "left")
ALL_PANELS = tuple(f"{f}-{p}" for f in FACES for p in PANELS)

#: Crease kinds the screen offers.
CREASE_NONE, CREASE_SHORT, CREASE_OTHER = "none", "short-edge-ink-intact", "other"
CREASES = (CREASE_NONE, CREASE_SHORT, CREASE_OTHER)


def worse(a, b):
    """The worse of two grades; HOLD beats everything, blank loses to anything."""
    if HOLD in (a, b):
        return HOLD
    if not a:
        return b
    if not b:
        return a
    return GRADES[max(GRADES.index(a), GRADES.index(b))]


def worst(grades):
    out = ""
    for g in grades:
        out = worse(out, g)
    return out


@dataclass
class Assessment:
    """What a grader recorded for one card in one pass."""
    grade: str
    looked: set = field(default_factory=set)        # panel ids from ALL_PANELS
    unchecked: set = field(default_factory=set)     # panels the crop checks could not read
    points: dict = field(default_factory=dict)      # {"front": n, "back": n}
    corners_whitened: dict = field(default_factory=dict)
    crease: str = CREASE_NONE


class RuleError(ValueError):
    """A grade the rules refuse outright (rather than cap)."""


def enforce(a):
    """
    Apply the rules to an Assessment. Returns (grade, reasons) where reasons
    say why the grade differs from what was keyed. Raises RuleError for NM
    over panels that were not all opened.
    """
    g = a.grade.upper()
    if g not in KEYS:
        raise RuleError(f"{a.grade!r} is not one of {', '.join(KEYS)}")
    if g == HOLD:
        return HOLD, []
    reasons = []
    if g == "NM":
        unopened = [p for p in ALL_PANELS if p not in a.looked]
        if unopened:
            raise RuleError(f"NM needs every panel opened — not yet: {', '.join(unopened)}")
        bad = sorted(a.unchecked)
        if bad:
            raise RuleError(f"NM needs every panel readable — re-crop first: {', '.join(bad)}")
        pts = sum(int(a.points.get(f) or 0) for f in FACES)
        if pts >= NM_MAX_POINTS:
            g = "LP"
            reasons.append(f"{pts} wear points across both faces (NM needs fewer "
                           f"than {NM_MAX_POINTS})")
    for f in FACES:
        n = int(a.corners_whitened.get(f) or 0)
        if n >= CORNERS_FOR_LP and GRADES.index(g) < GRADES.index("LP"):
            g = "LP"
            reasons.append(f"{n} whitened corners on the {f}: LP at best")
    if a.crease not in CREASES:
        raise RuleError(f"unknown crease kind {a.crease!r}")
    if a.crease != CREASE_NONE:
        floor = "HP" if a.crease == CREASE_SHORT else "DMG"
        if GRADES.index(g) < GRADES.index(floor):
            reasons.append(f"crease ({a.crease}): {floor}, never MP or better")
            g = floor
    return g, reasons


# ---------------------------------------------------------------- listing rules

#: WotC (1999-2003) and EX era (2003-2007) English set names, for when the
#: year is not known. The year from CardUploader's job data is preferred.
VINTAGE_SETS = {
    "base set", "base set 2", "jungle", "fossil", "team rocket", "gym heroes",
    "gym challenge", "neo genesis", "neo discovery", "neo revelation",
    "neo destiny", "legendary collection", "expedition", "aquapolis", "skyridge",
    "southern islands", "wizards black star promos", "best of game",
    "ruby & sapphire", "sandstorm", "dragon", "team magma vs team aqua",
    "hidden legends", "firered & leafgreen", "team rocket returns", "deoxys",
    "emerald", "unseen forces", "delta species", "legend maker",
    "holon phantoms", "crystal guardians", "dragon frontiers", "power keepers",
    "nintendo black star promos", "pop series 1", "pop series 2", "pop series 3",
    "pop series 4", "pop series 5",
}
VINTAGE_YEARS = (1999, 2007)


def _norm_set(name):
    s = (name or "").lower().strip()
    s = re.sub(r"^(ex|pokemon|pokémon)\s*[:\-]?\s+", "", s)
    return s.replace(" and ", " & ")


def is_vintage(game, year=None, set_name=""):
    if game != "pokemon":
        return False
    try:
        y = int(str(year)[:4])
        return VINTAGE_YEARS[0] <= y <= VINTAGE_YEARS[1]
    except (TypeError, ValueError):
        return _norm_set(set_name) in VINTAGE_SETS


def listing_grade(grade, game, vintage=False):
    """The condition a graded card is LISTED at."""
    if grade == "NM" and game == "pokemon" and vintage:
        return "LP"
    return grade


def needs_in_hand(grade, price):
    """NM priced £15+ is checked in hand before listing."""
    try:
        return grade == "NM" and float(price) >= IN_HAND_PRICE
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- the grades file

GRADE_FIELDS = ["sku", "pass", "grader", "keyed", "grade", "reasons", "looked",
                "unchecked", "front_points", "back_points", "front_corners",
                "back_corners", "crease", "notes", "source", "time"]


def grades_path(manifest_dir, code):
    return os.path.join(manifest_dir, f"grades-{code}.csv")


def read_grades(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def record(path, sku, a, final, reasons, pass_no=1, grader="", notes="",
           source="human"):
    """Append one pass for one card to the grades file."""
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=GRADE_FIELDS)
        if new:
            w.writeheader()
        w.writerow({
            "sku": sku, "pass": pass_no, "grader": grader, "keyed": a.grade,
            "grade": final, "reasons": "; ".join(reasons),
            "looked": " ".join(sorted(a.looked)),
            "unchecked": " ".join(sorted(a.unchecked)),
            "front_points": a.points.get("front", ""), "back_points": a.points.get("back", ""),
            "front_corners": a.corners_whitened.get("front", ""),
            "back_corners": a.corners_whitened.get("back", ""),
            "crease": a.crease, "notes": notes, "source": source,
            "time": _dt.datetime.now().isoformat(timespec="seconds"),
        })


def final_grades(rows):
    """
    {sku: (grade, passes, disagreed)} from a grades file: the worse of every
    pass. An AI second opinion counts as a pass only if a human accepted it
    (source "ai-accepted"); a bare "ai" row is advice.
    """
    by = {}
    for r in rows:
        if r.get("source") == "ai":
            continue
        by.setdefault(r["sku"], []).append(r["grade"])
    return {sku: (worst(gs), len(gs), len(set(gs)) > 1) for sku, gs in by.items()}
