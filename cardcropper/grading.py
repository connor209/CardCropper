"""
What each grading company allows for centering, and the best grade a card can
get from each of them on centering alone.

Every company writes centering the same way: the wider of two opposite borders
as a share of the pair, so "55/45" means the wide border is 55% of the two
together. A threshold applies to BOTH axes — left/right and top/bottom — and
the worse axis is the one that counts. Fronts are held much tighter than backs.

How centering turns into a grade differs, and the label says which:

- **A cap** (PSA, SGC): the card cannot grade higher than its centering
  allows, whatever its corners, edges and surface are like.
- **A subgrade** (Beckett, ACE, CGC subgrades): centering is scored on its own
  scale. For Beckett the final grade cannot sit more than a point above the
  lowest subgrade, so centering still acts as a ceiling, just a looser one.

These tables are the published figures as they stood when written. Graders do
change them — PSA tightened its 10 in 2025 — so each table carries a `checked`
date, and the grade shown is "best on centering alone", never a prediction.
"""

from dataclasses import dataclass, field


@dataclass
class Step:
    grade: str           # what the grade is called on the slab, e.g. "10"
    label: str           # e.g. "Gem Mint 10"
    front: float         # max wider-border share on the front, e.g. 55.0
    back: float          # same, on the back


@dataclass
class Grader:
    name: str
    kind: str            # "cap" or "subgrade"
    steps: list          # best first
    note: str = ""
    checked: str = ""
    source: str = ""
    extra: dict = field(default_factory=dict)


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
    ], note="Centering caps the grade. PSA 10 front tightened from 60/40 to 55/45 in 2025.",
        checked="2026-11", source="https://www.psacard.com/gradingstandards"),
    Grader("BGS", "subgrade", [
        Step("10", "Pristine 10 subgrade", 50, 55),
        Step("9.5", "Gem Mint 9.5 subgrade", 55, 60),
        Step("9", "Mint 9 subgrade", 60, 80),
        Step("8.5", "NM-MT+ 8.5 subgrade", 65, 90),
        Step("8", "NM-MT 8 subgrade", 70, 95),
        Step("7", "NM 7 subgrade", 75, 95),
        Step("6", "EX-MT 6 subgrade", 80, 100),
    ], note="Centering subgrade. Final grade can be at most one point above the lowest subgrade.",
        checked="2026-11", source="https://www.beckett.com/grading/scale"),
    Grader("CGC", "subgrade", [
        Step("10P", "Pristine 10", 50, 55),
        Step("10", "Gem Mint 10", 55, 75),
        Step("9.5", "Mint+ 9.5", 55, 75),
        Step("9", "Mint 9", 60, 90),
        Step("8.5", "NM/Mint+ 8.5", 65, 90),
        Step("8", "NM/Mint 8", 65, 90),
        Step("7", "Near Mint 7", 70, 90),
        Step("6", "Excellent/NM 6", 80, 90),
    ], note="Pristine 10 needs centering at or near 50/50.",
        checked="2026-11", source="https://www.cgccards.com/card-grading/grading-scale/"),
    Grader("SGC", "cap", [
        Step("10P", "Pristine 10", 50, 55),
        Step("10", "Gem Mint 10", 55, 70),
        Step("9.5", "Mint+ 9.5", 60, 70),
        Step("9", "Mint 9", 60, 80),
        Step("8.5", "NM-MT+ 8.5", 65, 85),
        Step("8", "NM-MT 8", 65, 85),
        Step("7", "NM 7", 70, 90),
        Step("6", "EX-NM 6", 80, 90),
    ], checked="2026-11", source="https://gosgc.com/card-grading-scale"),
    Grader("ACE", "subgrade", [
        Step("10", "ACE 10 centering", 60, 65),
        Step("9", "ACE 9 centering", 65, 70),
        Step("8", "ACE 8 centering", 70, 75),
        Step("7", "ACE 7 centering", 75, 80),
        Step("6", "ACE 6 centering", 80, 85),
    ], note="UK grader. Centering subgrade; final grade follows the subgrades.",
        checked="2026-11", source="https://acegrading.com/grading-scale"),
]}


def _fmt(pct):
    return f"{pct:g}/{100 - pct:g}"


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
            if front <= step.front + 1e-9 and back <= step.back + 1e-9:
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
            if front > better.front:
                need.append(f"front ≤ {_fmt(better.front)}")
            if back > better.back:
                need.append(f"back ≤ {_fmt(better.back)}")
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
