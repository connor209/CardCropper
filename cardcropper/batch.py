"""
Pairing scans into cards, and writing an organised folder of four images each.

The pixel work lives in `imaging`. This module decides which two FACES are one
card — two separate files, or the two halves of one scan that holds both —
what the output files are called, and in what order they sit, which is the part
that has to be right for a bulk uploader reading the folder in filename order.
"""

import os
import re
import shutil
import time
from dataclasses import dataclass, field

import numpy as np

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

#: Cloud-synced folders — Google Drive for Desktop, OneDrive, a network share
#: — stream files on demand, and both halves of that go wrong transiently.
#:
#: A scan that has not been materialised locally yet can fail to open, and a
#: write can be rejected mid-run; on Windows both surface as OSError [Errno 22]
#: Invalid argument, which reads like a bug in the filename and is not. A
#: second attempt a moment later almost always succeeds.
#:
#: A card that still fails after these attempts has whatever it managed to
#: write removed again, because a card half-written is worse than a card not
#: written: a bulk uploader would attach a front and its crops to a listing
#: with no back at all, and nothing about the folder would show it. A failure
#: is loud; a partial success is not.
RETRIES = 3
RETRY_WAIT = 0.7

#: How the four files per card are named.
#:
#: `grouped` keeps a per-card prefix, so a folder stays readable and a wrong
#: pair is obvious at a glance. `sequence` renumbers the whole batch as one
#: continuous run, which is what an uploader expecting N images per card in
#: strict filename order wants. Both sort into the same sequence — the choice
#: is legibility against a clean run of numbers.
NAMING = ("grouped", "sequence")

#: Images per card, which is what makes a `sequence`-numbered folder readable
#: backwards: file 0009 is card 3 because every card before it took four.
PER_CARD = len(ORDERS["crops-last"])

#: The two shapes `output_names` writes, read back. Both are anchored so that a
#: file the app did not write — a scan the operator dropped in, an export from
#: something else — cannot be mistaken for a card number.
GROUPED_NAME = re.compile(r"^(\d+)_\d+_[a-z-]+$")
SEQUENCE_NAME = re.compile(r"^(\d+)$")

#: How a folder of scans is read: one card per PAIR of files, or one card per
#: file with both faces on it.
#:
#: `auto` asks each scan which it is, and is the default because guessing wrong
#: is silent. A combined scan run as a single card is not rejected by any of the
#: geometry checks — see imaging's `splitting` notes — it simply yields crops of
#: the pair's outer corners, two of which are interior artwork.
#:
#: The explicit settings exist for the two cases `auto` cannot serve: a batch
#: the operator already knows the shape of and does not want examined file by
#: file, and combined scans with the cards laid touching, where there is no
#: strip of bed to find and the division has to be asserted rather than
#: detected.
SPLIT_MODES = ("auto", "single", "combined")


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


def _io(fn, *args):
    """
    Run one filesystem operation, retrying the transient failures a
    cloud-synced folder produces. See RETRIES.

    The failing path is named in the final error. "[Errno 22] Invalid
    argument" on its own sends you looking for a bad filename, which is never
    what it is here.
    """
    for attempt in range(1, RETRIES + 1):
        try:
            return fn(*args)
        except OSError as exc:
            if attempt == RETRIES:
                where = next((a for a in args if isinstance(a, str)), "")
                name = os.path.basename(where)
                raise OSError(f"{name}: {exc}" if name else str(exc)) from exc
            time.sleep(RETRY_WAIT * attempt)


@dataclass
class Face:
    """
    One side of a card, and where to find it.

    `side` is None when the file holds nothing but this face, and 0 or 1 when
    the file holds both and this is the first or second of them in reading
    order — left then right, or top then bottom.

    A face is addressed rather than named because a combined scan gives two
    faces the same filename, and every place that used to assume "one path, one
    face" — the table, the swap, the log — has to be able to tell them apart.
    """
    path: str
    side: int = None

    @property
    def name(self):
        base = os.path.basename(self.path)
        return base if self.side is None else f"{base} ({self.side + 1} of 2)"


def as_face(face):
    """A Face from either a Face or a bare path."""
    return face if isinstance(face, Face) else Face(face)


@dataclass
class Card:
    """One card: the two faces it was scanned into, plus a working label."""
    front: Face
    back: Face
    label: str = ""

    def __post_init__(self):
        self.front = as_face(self.front)
        self.back = as_face(self.back)
        if not self.label:
            stem = os.path.splitext(os.path.basename(self.front.path))[0]
            self.label = stem

    @property
    def combined(self):
        """Whether both faces are halves of one scan."""
        return self.front.side is not None or self.back.side is not None

    @property
    def sources(self):
        """The distinct files this card is read from — one of them, or two."""
        return list(dict.fromkeys([self.front.path, self.back.path]))


@dataclass
class Plan:
    """A pairing of a list of files, plus whatever could not be paired."""
    cards: list = field(default_factory=list)
    leftover: list = field(default_factory=list)
    #: Things worth saying about how the pairing was arrived at, as opposed to
    #: `warnings`, which are things that need acting on.
    notes: list = field(default_factory=list)

    @property
    def warnings(self):
        if self.leftover:
            return [f"{len(self.leftover)} file(s) left unpaired: "
                    + ", ".join(os.path.basename(p) for p in self.leftover)]
        return []


#: Which half of a combined scan, or which file of a pair, is the front.
#:
#: `auto` works it out from the batch rather than from any one card: every card
#: has a different front and the SAME back, so the side that looks like itself
#: on every card is the back. That is the only honest signal. The quick ones —
#: a back is darker, a back is symmetrical — are wrong on enough card games to
#: put the wrong face in the gallery thumbnail without saying so.
FRONTS = ("auto", "first", "second")

#: How many cards are enough to tell a back from a front, how many are worth
#: looking at, and how much tighter the back has to be before the answer is
#: believed. Two cards give one comparison per side and that is not evidence;
#: a dozen is plenty and reading more only costs time.
FACE_MIN_CARDS = 3
FACE_SAMPLE = 12
FACE_CONFIDENCE = 1.6

#: Below this, a set of faces is all the same design. Both sides reading as
#: the same design is what a batch of ONE card repeated looks like — a playset
#: of the same card, a stack of bulk commons — and there the fronts match each
#: other as exactly as the backs do. Neither side is then the odd one out and
#: there is nothing to answer.
FACE_SAME = 0.15

#: Two faces are never bit-identical on a real scan, but a generated one can
#: be, and dividing by zero to report how sure we are helps nobody.
FACE_FLOOR = 0.02

#: The size faces are compared at. Small on purpose: the question is whether
#: two cards carry the same DESIGN, and at 48x67 a shared back is nearly
#: identical while two different fronts are nothing alike. Larger would only
#: add print grain and wear for the comparison to trip over.
FACE_THUMB = (48, 67)


def _descriptor(im):
    """
    A face reduced to something two cards can be compared by.

    Small, grey and normalised for exposure, because none of brightness,
    colour balance or scan-to-scan gain says anything about which design is
    printed on the card.
    """
    grey = np.asarray(im.convert("L").resize(FACE_THUMB, imaging.Image.BILINEAR),
                      dtype=float)
    grey -= grey.mean()
    spread = grey.std()
    return grey / spread if spread > 1e-6 else grey


def _both_faces(card, background):
    """The card's two faces, small and cropped to the card, as planned."""
    if card.combined:
        whole = imaging.load_small(card.front.path)
        regions = imaging.split_regions(whole, force=True, background=background)
        faces = [regions[card.front.side], regions[card.back.side]]
    else:
        faces = [imaging.load_small(card.front.path),
                 imaging.load_small(card.back.path)]
    return [imaging.card_only(face, background) for face in faces]


def _spread(descriptors):
    """How unalike a set of faces are — the median distance between any two."""
    gaps = [float(np.abs(a - b).mean())
            for i, a in enumerate(descriptors) for b in descriptors[i + 1:]]
    return float(np.median(gaps)) if gaps else 0.0


def detect_back(cards, background="dark", progress=None):
    """
    Which side of these cards holds the back, read off the batch as a whole.

    Every card in a batch has a different front and the same back, so of the
    two sides the one that looks like itself across the batch is the back. It
    is a property no single card has — one card has two pictures and nothing to
    say which is which — which is why this is the signal worth using and why it
    declines to answer for a batch too small to show it.

    Returns (side, confidence): 0 where the side currently called the FRONT is
    really the back, 1 where the planning was right, None where the batch
    cannot say — a handful of cards, or the same card scanned repeatedly, where
    both sides look alike and neither reading is supported.
    """
    sample = cards[:FACE_SAMPLE]
    if len(sample) < FACE_MIN_CARDS:
        return None, 0.0
    sides = ([], [])
    for i, card in enumerate(sample, 1):
        if progress:
            progress(i, len(sample))
        try:
            first, second = _both_faces(card, background)
        except Exception:                               # noqa: BLE001
            continue                                    # it fails loudly in the run
        sides[0].append(_descriptor(first))
        sides[1].append(_descriptor(second))
    if len(sides[0]) < FACE_MIN_CARDS:
        return None, 0.0
    spreads = [_spread(side) for side in sides]
    tight = 0 if spreads[0] < spreads[1] else 1
    loose = 1 - tight
    if spreads[loose] < FACE_SAME:
        # Both sides are one design across the batch: the same card, scanned
        # over and over. The backs match, and so do the fronts, so being alike
        # no longer picks anything out.
        return None, 1.0
    confidence = spreads[loose] / max(spreads[tight], FACE_FLOOR)
    return (tight, confidence) if confidence >= FACE_CONFIDENCE else (None, confidence)


def plan_scans(paths, split="auto", front="auto", progress=None, probe=None,
               background="dark", faces=None):
    """
    Turn a sorted run of scans into cards.

    A scan holding both faces is one card on its own. Everything else pairs
    with its neighbour as front, back, front, back — and an odd file out is
    reported rather than dropped or guessed at. A scanner that jams mid-sheet
    leaves exactly one orphan, and every card after it would otherwise be built
    from the back of one card and the front of the next, which looks like a
    working run until someone opens the photos.

    A combined scan ENDS a pairing rather than joining one, so a single file
    stranded beside combined scans is called out instead of being paired across
    them with another stray from further down the folder.

    `front` says which half of a combined scan, or which file of a pair, leads.
    `auto` asks the batch: see `detect_back`. Where the batch cannot say it
    falls back to `first` and says so, because a guess the operator can see is
    worth more than one they cannot.

    `progress(i, total, path)` is called as each file is examined — only in
    `auto`, which is the only mode that opens them. `probe(path)` replaces the
    examination itself, which is how the window caches it: the answer for a
    file does not change, and re-reading a folder every time a setting is
    flipped is the difference between the settings feeling instant and feeling
    broken.
    """
    probe = probe or (lambda path: imaging.probe(path, background))
    ordered = sorted(paths, key=natural_key)
    cards, leftover, pending = [], [], None
    sides = (1, 0) if front == "second" else (0, 1)
    for i, path in enumerate(ordered, 1):
        if progress and split == "auto":
            progress(i, len(ordered), path)
        try:
            combined = split == "combined" or (split == "auto" and probe(path))
        except Exception:                               # noqa: BLE001
            # An unreadable scan is not this function's problem to report — it
            # fails loudly in the run, per card, where it can be retried. Here
            # it is simply not a combined scan.
            combined = False
        if combined:
            if pending is not None:
                leftover.append(pending)
                pending = None
            cards.append(Card(Face(path, sides[0]), Face(path, sides[1])))
        elif pending is None:
            pending = path
        else:
            cards.append(Card(Face(pending), Face(path)))
            pending = None
    if pending is not None:
        leftover.append(pending)

    plan = Plan(cards=cards, leftover=leftover)
    if front != "auto":
        return plan
    side, confidence = detect_back(cards, background, progress=faces)
    if side is None:
        plan.notes.append(
            "could not tell the front from the back across this batch"
            + (" (too few cards to compare)" if not confidence else
               " (both sides look the same on every card — the same card "
               "scanned over and over?)" if confidence <= 1.0 else
               f" (the two sides are only {confidence:.1f}x apart)")
            + " — taking the first of each pair as the front; check the rows")
    elif side == 0:
        for card in plan.cards:
            card.front, card.back = card.back, card.front
        plan.notes.append(
            f"the second of each pair is the front — the first is the same on "
            f"every card, so it is the back ({confidence:.1f}x more alike)")
    else:
        plan.notes.append(
            f"the first of each pair is the front — the second is the same on "
            f"every card, so it is the back ({confidence:.1f}x more alike)")
    return plan


def pair_sequential(paths):
    """Pair a sorted run of one-face scans — `plan_scans` without the probe."""
    return plan_scans(paths, split="single", front="first")


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


def _scans_of(card, notes, background="dark"):
    """
    The card's two faces, each as its own scan, ready to be straightened.

    A combined scan is opened and divided ONCE, not once per face: the halves
    are two crops of one decode, and decoding a 40-megapixel flatbed scan twice
    to throw half of each away is the slowest thing this app could do per card.

    Where the division was detected when the batch was planned but cannot be
    found now, it is forced rather than refused. The plan is made from a 900px
    decode and the run from full resolution, so the two can disagree at the
    margin — on cards laid almost touching — and refusing there would fail a
    card the operator has already told the app the shape of.
    """
    if not card.combined:
        return {"front": _io(imaging.load, card.front.path),
                "back": _io(imaging.load, card.back.path)}
    source = _io(imaging.load, card.front.path)
    regions = imaging.split_regions(source, background=background)
    if regions is None:
        # The cards were laid touching, so there is no strip of bed to cut on.
        # Dividing at the middle is right whenever both halves are the same
        # card, which is every trading card — so check that it came out as two
        # cards rather than warning simply because the gap was missing.
        regions = imaging.split_regions(source, force=True, background=background)
        ok, why = imaging.divided_ok(regions, background)
        if not ok:
            notes.append(f"the two cards are touching and dividing down the "
                         f"middle did not come out right — {why}; check the crops")
    return {"front": regions[card.front.side], "back": regions[card.back.side]}


def next_index(out_dir):
    """
    The card number a batch written into `out_dir` should start at.

    A second batch cropped into the folder that already holds the first would
    otherwise be written over the top of it: the numbering restarts at 1 every
    run, and every name it produces is one the previous run already used. The
    scans survive that — they are only ever copied — but the crops do not, and
    nothing about the folder afterwards shows that a batch went missing.

    Read back from the filenames rather than from a note left in the folder,
    because the folder is the thing the operator edits. Cards get deleted,
    re-cropped, dragged in from another run, and a counter in a dotfile would
    be wrong the moment any of that happened while the filenames never are.

    Both naming schemes are read on every call, not just the one selected now:
    a folder filled with `grouped` names and then added to with `sequence`
    selected still must not land on a name that is already there.
    """
    if not os.path.isdir(out_dir):
        return 1
    highest = 0
    for name in os.listdir(out_dir):
        stem, ext = os.path.splitext(name)
        if ext.lower() not in imaging.IMAGE_EXTS:
            continue
        grouped = GROUPED_NAME.match(stem)
        if grouped:
            highest = max(highest, int(grouped.group(1)))
            continue
        run_of = SEQUENCE_NAME.match(stem)
        if run_of:
            # Round UP: a folder ending at 0009 holds a card whose remaining
            # three files are still to come, and card 3 is taken either way.
            highest = max(highest, -(-int(run_of.group(1)) // PER_CARD))
    return highest + 1


#: How far the two faces of one card may disagree in size before the smaller
#: one is taken to have mis-detected. A percent or two is the deskew and the
#: rounding; fifty pixels is a missed edge.
SIZE_TOLERANCE = 0.03


def _match_sizes(scans, faces, notes, background="dark"):
    """
    Make a card's two faces the same size, because they are the same card.

    Detection only ever under-reports, and on some backs it cannot do better:
    a Lorcana back is printed in a black that measures 0 against a scanner bed
    of 0, so the card's own border and the bed are not merely similar, they are
    the same number. Nothing separates them. What the detector finds instead is
    the gold frame line inset a few millimetres, and the crops then show the
    corners of that frame rather than the corners of the card.

    The front of the same card has artwork to its edge and detects cleanly, and
    it is the same piece of card at the same resolution — so it knows the size
    the back should have been. The smaller face is grown out to the larger,
    which can only ever add back edges that were trimmed, never invent a card
    bigger than the scan.
    """
    sizes = {face: faces[face][0].size for face in ("front", "back")}
    for face, other in (("front", "back"), ("back", "front")):
        small, large = sizes[face], sizes[other]
        if all(s >= l * (1 - SIZE_TOLERANCE) for s, l in zip(small, large)):
            continue
        # Only where the face came out INSET on every side. A box that stops
        # short of the card looks the same as a box stopped short by the edge
        # of the scan, and they want opposite treatment: the first is a missed
        # edge to be grown back, the second is the end of the pixels, and
        # growing it only pads the card with background. Two faces of one card
        # clipped differently by the same scan are both right about their own
        # half and neither is a reference for the other.
        if imaging.clipped_edges(scans[face], background=background):
            continue
        exact, padded, angle, edges = imaging.straighten(
            scans[face], reference=large, background=background)
        notes.append(
            f"{face}: detected {small[0]}x{small[1]} against the {other}'s "
            f"{large[0]}x{large[1]} — too dark to find its own edge, so it was "
            f"cut to the {other}'s size ({exact.size[0]}x{exact.size[1]})")
        faces[face] = (exact, padded)
    return faces


def process_card(card, out_dir, index, naming="grouped", order="crops-last",
                 style="grading", copy_originals=True, quality=95,
                 background="dark"):
    """
    Write one card's four images and return (filenames, notes).

    The originals are COPIED rather than moved. A run is worth being able to
    repeat, and a run that consumes its own input cannot be repeated — if a
    pairing turns out to be off by one, the fix is to pair again, which is only
    possible while the scans are still where the scanner left them.

    A card read from a combined scan has no original to copy — the file holds
    the other face too — so what is written for each face is that face cut out
    of it. See the comment where it happens.
    """
    notes = []
    names = output_names(index, card, naming, order)
    by_role = dict(names)
    scans = _scans_of(card, notes, background)
    faces = {}

    for face in ("front", "back"):
        source = scans[face]
        exact, padded, angle, edges = imaging.straighten(source, background=background)
        ok, why = imaging.detection_ok(exact, source)
        if not ok:
            notes.append(f"{face}: {why}")
            if background == "dark" and imaging.looks_light_bedded(source):
                notes.append(f"{face}: its border is bright and flat, which is what "
                             "a white backing looks like — if these were scanned on "
                             "one, set the background to white backing")
        elif not card.combined and imaging.split_regions(
                source, background=background) is not None:
            # Cheap here, and the failure it catches is the expensive one: a
            # scan holding both faces, run as a single card, passes every check
            # above and produces crops of the PAIR's outer corners.
            notes.append(f"{face}: this scan looks like it holds two cards — "
                         "try the combined setting")
        faces[face] = (exact, padded)
        contrast = imaging.edge_contrast(padded, background)
        if contrast and abs(contrast[0] - contrast[1]) < imaging.CONTRAST_FLOOR:
            other = "a white backing" if background == "dark" else "a dark bed"
            notes.append(
                f"{face}: the card's edge reads {contrast[0]:.0f} against a backing "
                f"of {contrast[1]:.0f} — the crops are cut in the right place, but "
                f"the outline cannot be made out against that. {other.capitalize()} "
                "would show it.")
        clipped = imaging.clipped_edges(source, background=background)
        if clipped:
            notes.append(
                f"{face}: the card runs off the {', '.join(clipped)} of the scan — "
                "no background behind it there, so those crops cannot show its "
                "outline. Scan a little wider than the card.")
        if not edges:
            # Said out loud because the alternative reads as a broken deskew:
            # a visibly crooked card that came out just as crooked, with
            # nothing in the log between the two.
            notes.append(f"{face}: could not measure the angle from any edge — "
                         "left unrotated")
        elif abs(angle) >= 0.05:
            notes.append(f"{face}: deskewed {angle:+.2f}°"
                         + (f" (from {edges} edge)" if edges == 1 else ""))

    _match_sizes(scans, faces, notes, background)

    draw = imaging.STYLES[style]
    for face in ("front", "back"):
        exact, padded = faces[face]
        if copy_originals:
            dest = os.path.join(out_dir, by_role[face])
            source_path = getattr(card, face).path
            if card.combined:
                # There is no original to copy — the file holds the other face
                # too. What is written instead is this face alone, deskewed and
                # cut out with CROP_MARGIN of bed around it, which is what the
                # scan of this card on its own would have been.
                _io(lambda: padded.save(dest, quality=quality, subsampling=0))
            elif os.path.splitext(source_path)[1].lower() == os.path.splitext(dest)[1].lower():
                # copyfile, not copy2. copy2 also copies metadata, which means
                # os.utime on the destination — and a Google Drive or OneDrive
                # folder rejects that with [Errno 22] Invalid argument while
                # having written the bytes perfectly well. The timestamps are
                # worth nothing here and the failure costs a card.
                _io(shutil.copyfile, source_path, dest)
            else:
                _io(lambda: imaging.load(source_path)
                    .save(dest, quality=quality, subsampling=0))
        draw(padded, os.path.join(out_dir, by_role[f"{face}-crops"]), face.upper())

    return [n for _, n in names], notes


def run(plan, out_dir, naming="grouped", order="crops-last", style="grading",
        copy_originals=True, progress=None, should_stop=None, start=None,
        background="dark"):
    """
    Process every card in `plan`, reporting as it goes.

    `start` is the number the first card is written as. None asks
    `next_index`, so a second batch cropped into the same folder carries on
    from the first instead of writing over it; pass 1 to number from the top
    and overwrite whatever is there.

    `progress(index, total, card, filenames, notes, error)` is called once per
    card, and `index` is the card's position in THIS batch — 1, 2, 3 — not the
    number it was written as. The two come apart the moment a batch starts at
    13, and the caller is using it to address its own rows.

    A card that fails does not stop the batch: a hundred-card run that aborts
    on card 3 has wasted the operator's time in a way that reporting card 3 and
    carrying on does not.
    """
    os.makedirs(out_dir, exist_ok=True)
    if start is None:
        start = next_index(out_dir)
    total = len(plan.cards)
    done = failed = 0
    failures = []
    for i, card in enumerate(plan.cards, 1):
        if should_stop is not None and should_stop():
            break
        number = start + i - 1
        try:
            names, notes = process_card(card, out_dir, number, naming, order,
                                        style, copy_originals,
                                        background=background)
            done += 1
            if progress:
                progress(i, total, card, names, notes, None)
        except Exception as exc:                        # noqa: BLE001
            failed += 1
            failures.append((i, card, exc))
            # Leave nothing half-written behind — see RETRIES. Cleared under
            # the number it was WRITTEN as, or the cleanup tidies away some
            # other card's files.
            for _, name in output_names(number, card, naming, order):
                try:
                    os.remove(os.path.join(out_dir, name))
                except OSError:
                    pass
            if progress:
                progress(i, total, card, [], [], exc)
    return done, failed, failures
