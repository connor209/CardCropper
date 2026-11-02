"""
What each grading company allows for centering, and the best grade a card can
get from each of them on centering alone.

Every company writes centering the same way: the wider of two opposite borders
as a share of the pair, so "55/45" means the wide border is 55% of the two
together. A threshold applies to BOTH axes — left/right and top/bottom — and
the worse axis is the one that counts. Fronts are held much tighter than backs.

How centering turns into a grade differs, and `kind` says which:

- **cap** (PSA, CGC, SGC): one overall grade, and the card cannot get a grade
  whose centering limit it fails, whatever its corners, edges and surface.
- **subgrade** (Beckett, ACE): centering is scored on its own. Beckett's final
  grade rarely sits more than a point above the lowest subgrade; ACE's never
  does ("no more than one grade higher than the lowest subgrade").
- **score** (TAG): machine-measured, feeding a 1000-point score; the bands
  below are the published centering limits for each grade band.

Sources and confidence, as checked in November 2026:

- ACE, CGC, TAG: read from the companies' own grading-scale pages.
- PSA: the 10 is PSA's own wording; 9 and below via a source quoting PSA.
- Beckett, SGC: secondary sources only (their pages refuse automated reads).
  SGC publishes no back tolerance at all, so backs are unlimited here.
- Where a company publishes a front figure but no back one, the back is left
  unlimited (100) rather than guessed — the tool should never claim a limit a
  grader has not set.

Graders change these — PSA tightened its 10 from 60/40 to 55/45 in 2025 — so
the grade shown is "best on centering alone", never a prediction, and each
grader's `source` is the page to recheck.
"""

from dataclasses import dataclass, field


@dataclass
class Step:
    grade: str           # what the grade is called on the slab, e.g. "10"
    label: str           # e.g. "Gem Mint 10"
    front: float         # max wider-border share on the front, e.g. 55.0
    back: float          # same, on the back; 100 = no published limit
    strict: bool = False  # True when the limit is "less than", not "up to"


@dataclass
class Grader:
    name: str
    kind: str            # "cap", "subgrade" or "score"
    steps: list          # best first
    note: str = ""
    source: str = ""
    extra: dict = field(default_factory=dict)


NO_LIMIT = 100.0

GRADERS = {g.name: g for g in [
    Grader("PSA", "cap", [
        Step("10", "Gem Mint 10", 55, 75),
        Step("9", "Mint 9", 60, 90),
        Step("8", "NM-MT 8", 65, 90),
        Step("7", "NM 7", 70, 90),
        Step("6", "EX-MT 6", 80, 90),
        Step("5", "EX 5", 85, 90),
        Step("4", "VG-EX 4", 85, 90),
        Step("3", "VG 3", 90, 90),
        Step("2", "Good 2", 90, 90),
    ], note="Caps the grade. Limits are 'approximately'; small variance at the "
            "grader's discretion for eye appeal.",
        source="https://www.psacard.com/gradingstandards"),
    Grader("BGS", "subgrade", [
        Step("10", "Pristine 10 subgrade", 50, 55),
        Step("9.5", "Gem Mint 9.5 subgrade", 55, 60),
        Step("9", "Mint 9 subgrade", 55, 70),
        Step("8", "NM-MT 8 subgrade", 60, 80),
        Step("7", "NM 7 subgrade", 65, 90),
        Step("6", "EX-MT 6 subgrade", 70, 95),
        Step("5", "EX 5 subgrade", 75, 95),
        Step("4", "VG-EX 4 subgrade", 80, NO_LIMIT),
        Step("3", "VG 3 subgrade", 85, NO_LIMIT),
        Step("2", "Good 2 subgrade", 90, NO_LIMIT),
    ], note="Centering subgrade; the final grade rarely exceeds the lowest "
            "subgrade by more than a point. Half-grade limits are not published.",
        source="https://www.beckett.com/grading/scale"),
    Grader("CGC", "cap", [
        Step("10P", "Pristine 10", 50, 75),
        Step("10", "Gem Mint 10", 55, 75),
        Step("9", "Mint 9", 60, 90),
        Step("8", "NM/Mint 8", 65, NO_LIMIT),
        Step("7", "Near Mint 7", 70, NO_LIMIT),
        Step("6", "Fine/Excellent 6", 75, NO_LIMIT),
        Step("4.5", "VG/Excellent+ 4.5", 85, NO_LIMIT),
        Step("3.5", "VG+ 3.5", 90, NO_LIMIT),
    ], note="Pristine needs 50/50 front; no separate back figure, so Gem "
            "Mint's 75/25 is used. Below 9 only front limits are published.",
        source="https://www.cgccards.com/card-grading/grading-scale/"),
    Grader("SGC", "cap", [
        Step("10P", "Pristine 10", 50, NO_LIMIT),
        Step("10", "Gem Mint 10", 55, NO_LIMIT),
        Step("9", "Mint 9", 60, NO_LIMIT),
        Step("8", "NM-MT 8", 65, NO_LIMIT),
        Step("7", "NM 7", 70, NO_LIMIT),
        Step("6", "EX-NM 6", 75, NO_LIMIT),
        Step("5", "EX 5", 80, NO_LIMIT),
        Step("4", "VG-EX 4", 85, NO_LIMIT),
        Step("3", "VG 3", 90, NO_LIMIT),
    ], note="Front guidelines only — SGC publishes no back tolerance. "
            "Secondary sources; check before relying on it.",
        source="https://gosgc.com/card-grading/scale"),
    Grader("ACE", "subgrade", [
        Step("10", "Gem Mint 10 centering", 60, 60, strict=True),
        Step("9", "Mint 9 centering", 65, 70),
        Step("8", "NM-MT 8 centering", 70, 75),
        Step("7", "NM 7 centering", 75, 80),
        Step("6", "EX-MT 6 centering", 80, 80),
        Step("3", "Good 3 centering", 85, 85),
    ], note="UK. Centering subgrade; the total is never more than one grade "
            "above the lowest subgrade. 10 needs LESS than 60/40, front and back.",
        source="https://acegrading.com/grading-scale"),
    Grader("TAG", "score", [
        Step("10P", "Pristine 10", 51, 52),
        Step("10", "Gem Mint 10", 55, 65),
        Step("9", "Mint 9", 60, 75),
        Step("8.5", "NM-MT+ 8.5", 62.5, 85),
        Step("8", "NM-MT 8", 65, 95),
        Step("7", "NM 7", 70, NO_LIMIT),
        Step("6", "EX-MT 6", 75, NO_LIMIT),
        Step("5", "EX 5", 80, NO_LIMIT),
        Step("4", "VG-EX 4", 85, NO_LIMIT),
        Step("3", "VG 3", 90, NO_LIMIT),
    ], note="Machine-measured, scored out of 1000. Backs are TCG figures "
            "(sports backs are looser).",
        source="https://taggrading.com/pages/rubric"),
]}


def _fmt(pct):
    if pct >= NO_LIMIT:
        return "any"
    return f"{pct:g}/{100 - pct:g}"


def _within(share, limit, strict):
    if limit >= NO_LIMIT:
        return True
    return share < limit if strict else share <= limit + 1e-9


def best_grades(front_lr, front_tb, back_lr=None, back_tb=None):
    """
    The best grade each company allows on centering alone.

    Arguments are the wider-border shares (50-100) for each axis of each face.
    A back of None is treated as perfect, so a front-only measurement still
    reads — but a real grade depends on the back too.
    """
    front = max(front_lr, front_tb)
    back = max(back_lr or 50.0, back_tb or 50.0)
    out = []
    for g in GRADERS.values():
        hit, better = None, None
        for step in g.steps:
            if _within(front, step.front, step.strict) and \
                    _within(back, step.back, step.strict):
                hit = step
                break
            better = step
        if hit:
            label = hit.label
            limit = f"front {_fmt(hit.front)}, back {_fmt(hit.back)}"
        else:
            label = f"below {g.steps[-1].label}"
            limit = ""
        if better:
            need = []
            op = "<" if better.strict else "≤"
            if not _within(front, better.front, better.strict):
                need.append(f"front {op} {_fmt(better.front)}")
            if not _within(back, better.back, better.strict):
                need.append(f"back {op} {_fmt(better.back)}")
            nxt = f"{better.grade}: " + ", ".join(need)
        else:
            nxt = "top grade"
        out.append({
            "company": g.name,
            "kind": g.kind,
            "grade": hit.grade if hit else None,
            "label": label,
            "short": hit.grade if hit else "<" + g.steps[-1].grade,
            "limit": limit,
            "next": nxt,
        })
    return out
