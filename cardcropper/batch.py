"""
Pairing scans into cards, and writing an organised folder of four images each.

The pixel work lives in `imaging`. This module decides which two files are one
card, what the output files are called, and in what order they sit — which is
the part that has to be right for a bulk uploader reading the folder in
filename order.
"""

import os
import re
import shutil
from dataclasses import dataclass, field

from . import imaging

#: Where a crop sits relative to its card's original scans.
#:
#: `crops-last` puts front, back, then the two crop sheets. That is the order
#: the card-conditioning skill appends photos in, and it exists to protect one
#: thing: the FIRST image is the gallery thumbnail a buyer sees in search
#: results, and it has to be the front of the card, never a magnified corner.
#: `interleaved` keeps each face next to its own crops, which reads better in a
#: file browser and is fine as long as the front still leads.
ORDERS = {
    "crops-last": ("front", "back", "front-crops", "back-crops"),
    "interleaved": ("front", "front-crops", "back", "back-crops"),
}

#: How the four files per card are named.
#:
#: `grouped` keeps a per-card prefix, so a folder stays readable and a wrong
#: pair is obvious at a glance. `sequence` renumbers the whole batch as one
#: continuous run, which is what an uploader expecting N images per card in
#: strict filename order wants. Both sort into the same sequence — the choice
#: is legibility against a clean run of numbers.
NAMING = ("grouped", "sequence")


def natural_key(path):
    """
    Sort filenames the way a person reads them: 2 before 10.

    Plain lexical order puts `0010.jpg` before `002.jpg` the moment a scanner
    changes its zero padding mid-batch, and since pairing is positional that
    does not produce a warning — it produces cards assembled from two different
    cards, silently.
    """
    name = os.path.basename(path)
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def list_images(folder):
    """Every image directly in `folder`, in natural order."""
    entries = [os.path.join(folder, n) for n in os.listdir(folder)]
    return sorted((p for p in entries
                   if os.path.isfile(p)
                   and p.lower().endswith(imaging.IMAGE_EXTS)),
                  key=natural_key)


@dataclass
class Card:
    """One card: the two files it was scanned into, plus a working label."""
    front: str
    back: str
    label: str = ""

    def __post_init__(self):
        if not self.label:
            stem = os.path.splitext(os.path.basename(self.front))[0]
            self.label = stem


@dataclass
class Plan:
    """A pairing of a list of files, plus whatever could not be paired."""
    cards: list = field(default_factory=list)
    leftover: list = field(default_factory=list)

    @property
    def warnings(self):
        if self.leftover:
            return [f"{len(self.leftover)} file(s) left unpaired: "
                    + ", ".join(os.path.basename(p) for p in self.leftover)]
        return []


def pair_sequential(paths):
    """
    Pair a sorted run of scans as front, back, front, back...

    An odd file out is reported rather than dropped or guessed at. A scanner
    that jams mid-sheet leaves exactly one orphan, and every card after it
    would otherwise be built from the back of one card and the front of the
    next — which looks like a working run until someone opens the photos.
    """
    ordered = sorted(paths, key=natural_key)
    cards = [Card(ordered[i], ordered[i + 1])
             for i in range(0, len(ordered) - 1, 2)]
    leftover = ordered[len(cards) * 2:]
    return Plan(cards=cards, leftover=leftover)


def output_names(index, card, naming="grouped", order="crops-last", ext=".jpg"):
    """
    The four filenames for one card, already in the order they should sort in.

    Returns a list of (role, filename). `index` is the card's 1-based position
    in the batch, which is what makes the whole folder one continuous run.
    """
    roles = ORDERS[order]
    if naming == "sequence":
        first = (index - 1) * 4 + 1
        return [(role, f"{first + i:04d}{ext}") for i, role in enumerate(roles)]
    return [(role, f"{index:04d}_{i + 1}_{role}{ext}")
            for i, role in enumerate(roles)]


def process_card(card, out_dir, index, naming="grouped", order="crops-last",
                 style="grading", copy_originals=True, quality=95):
    """
    Write one card's four images and return (filenames, notes).

    The originals are COPIED rather than moved. A run is worth being able to
    repeat, and a run that consumes its own input cannot be repeated — if a
    pairing turns out to be off by one, the fix is to pair again, which is only
    possible while the scans are still where the scanner left them.
    """
    notes = []
    names = output_names(index, card, naming, order)
    by_role = dict(names)
    faces = {}

    for face, path in (("front", card.front), ("back", card.back)):
        source = imaging.load(path)
        exact, padded, angle = imaging.straighten(source)
        ok, why = imaging.detection_ok(exact, source)
        if not ok:
            notes.append(f"{face}: {why}")
        faces[face] = (exact, padded)
        if abs(angle) >= 0.05:
            notes.append(f"{face}: deskewed {angle:+.2f}°")

    draw = imaging.STYLES[style]
    for face in ("front", "back"):
        exact, padded = faces[face]
        if copy_originals:
            dest = os.path.join(out_dir, by_role[face])
            if os.path.splitext(card.front if face == "front" else card.back)[1].lower() \
                    == os.path.splitext(dest)[1].lower():
                shutil.copy2(card.front if face == "front" else card.back, dest)
            else:
                source_path = card.front if face == "front" else card.back
                imaging.load(source_path).save(dest, quality=quality, subsampling=0)
        draw(padded, os.path.join(out_dir, by_role[f"{face}-crops"]), face.upper())

    return [n for _, n in names], notes


def run(plan, out_dir, naming="grouped", order="crops-last", style="grading",
        copy_originals=True, progress=None, should_stop=None):
    """
    Process every card in `plan`, reporting as it goes.

    `progress(index, total, card, filenames, notes, error)` is called once per
    card. A card that fails does not stop the batch: a hundred-card run that
    aborts on card 3 has wasted the operator's time in a way that reporting
    card 3 and carrying on does not.
    """
    os.makedirs(out_dir, exist_ok=True)
    total = len(plan.cards)
    done = failed = 0
    for i, card in enumerate(plan.cards, 1):
        if should_stop is not None and should_stop():
            break
        try:
            names, notes = process_card(card, out_dir, i, naming, order, style,
                                        copy_originals)
            done += 1
            if progress:
                progress(i, total, card, names, notes, None)
        except Exception as exc:                        # noqa: BLE001
            failed += 1
            if progress:
                progress(i, total, card, [], [], exc)
    return done, failed
