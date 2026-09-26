"""
A run of the whole pipeline on generated scans, with no display and no real
card needed.

It exists so that the Windows build fails on the runner rather than in
somebody's hands. Freezing a broken pipeline into an .exe still produces an
.exe, and the failure then shows up as a dialog on a machine with no Python on
it, which is the worst place to find it.
"""

import datetime
import math
import os
import random
import shutil
import sys
import tempfile

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cardcropper import batch, folders, imaging, stacks  # noqa: E402


def make_card(front):
    """A stand-in card: a bordered rectangle, yellow face up or navy face down."""
    W, H = 1000, 1400
    card = Image.new("RGB", (W, H), (230, 200, 40) if front else (10, 10, 47))
    d = ImageDraw.Draw(card)
    d.rectangle([60, 80, W - 60, H - 160], fill=(120, 150, 90) if front else (180, 180, 190))
    if not front:
        d.ellipse([2, 2, 40, 40], fill=(230, 230, 235))     # a whitening blob
    return card


#: The stand-in card's size, so a test can say what the crop box should have
#: come back as.
CARD_W, CARD_H = 1000, 1400


def make_scan(path, front, angle, glow=0):
    """
    A stand-in scan: one card on a black bed, rotated off-square.

    `glow` adds the halo a real flatbed puts around a card — light bleeding
    sideways under the platen, and the sensor smearing along its travel. It is
    why the crop box has to MEASURE an edge rather than threshold one: a halo
    above the bar is, to any bar, card, and the bar has to sit low enough to
    see a near-black card whole.
    """
    bed = Image.new("RGB", (1400, 1800), (0, 0, 0))
    bed.paste(make_card(front), (200, 200))
    bed = bed.rotate(angle, resample=Image.BICUBIC, fillcolor=(0, 0, 0))
    if glow:
        halo = bed.filter(ImageFilter.GaussianBlur(glow))
        bed = ImageChops.lighter(bed, halo.point(lambda v: int(v * 0.55)))
    bed.save(path, quality=95)


def make_full_art(dark_border=True):
    """
    A full-art card whose near-black artwork reaches the outer border.

    This is the card that broke the deskew: where the artwork is as dark as the
    scanner bed, the edge detector cannot see the border and reports the first
    lit pixel INSIDE the card instead. Those readings are all on one side of
    the truth, so a straight-line fit through them is dragged a long way — a
    square card measured +22 degrees and every crop was cut from a scan rotated
    by that.
    """
    W, H = 1000, 1400
    card = Image.new("RGB", (W, H), (198, 163, 74))
    d = ImageDraw.Draw(card)
    d.rectangle([14, 14, W - 14, H - 14], fill=(150, 60, 40))
    if dark_border:
        d.polygon([(0, int(H * 0.45)), (int(W * 0.5), H), (0, H)], fill=(5, 5, 7))
    return card


def make_dark_card(back=False):
    """
    A card as dark as the scanner bed in places.

    Modern cards are not the navy-bordered Pokemon back this was written for.
    A Lorcana card's lower band measures 12 against a bed of 0, and a League
    back is near-black with a few gold lines on it. Against a fixed threshold
    of 20 both read as background: the card's own dark edge is trimmed off it,
    and a back broken into separate lit patches looks like two cards.
    """
    W, H = 1000, 1400
    card = Image.new("RGB", (W, H), (6, 7, 18) if back else (18, 14, 34))
    d = ImageDraw.Draw(card)
    if back:
        d.ellipse([230, 420, 770, 960], outline=(235, 225, 200), width=14)
    else:
        d.rectangle([0, 0, W, int(H * 0.58)], fill=(40, 90, 110))
        d.rectangle([0, int(H * 0.58), W, H], fill=(7, 6, 12))
        d.text((40, H - 55), "242/207 - EN - 13", fill=(120, 120, 125))
    return card


def make_combined(path, angle, gap=60, stacked=False, flipped=False):
    """
    A scan with BOTH faces on one bed, the way a flatbed gives them: card down,
    card flipped, one pass. `flipped` puts the back on the left instead.
    """
    cards = [make_card(not flipped), make_card(flipped)]
    w, h = cards[0].size
    if stacked:
        bed = Image.new("RGB", (w + 400, h * 2 + gap + 400), (0, 0, 0))
        for i, card in enumerate(cards):
            bed.paste(card, (200, 200 + i * (h + gap)))
    else:
        bed = Image.new("RGB", (w * 2 + gap + 400, h + 400), (0, 0, 0))
        for i, card in enumerate(cards):
            bed.paste(card, (200 + i * (w + gap), 200))
    bed.rotate(angle, resample=Image.BICUBIC, fillcolor=(0, 0, 0)).save(path, quality=95)


def _difference(a, b):
    """
    How far apart two images are, 0 to 255.

    Compared at a common small size because the two faces of a combined scan
    are deskewed separately and land a pixel or two apart. What this is looking
    for is not a subtle difference: it is whether the division handed back the
    same half twice, which reads as 0.
    """
    size = (64, 90)
    pa = np.asarray(a.convert("L").resize(size, Image.BILINEAR)).astype(int)
    pb = np.asarray(b.convert("L").resize(size, Image.BILINEAR)).astype(int)
    return float(np.abs(pa - pb).mean())


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def split_checks(work, scans):
    """
    Splitting a long run into stack folders. It MOVES the scans, so what it
    has to get right is that every card lands whole in the stack it belongs
    to, that nothing moves when it cannot all move, and that it can be undone.
    """
    run = os.path.join(work, "split-run")
    os.makedirs(run)
    # Ten cards, twenty scans, from the four generated ones.
    sources = batch.list_images(scans)
    for i in range(1, 21):
        shutil.copyfile(sources[(i - 1) % 4], os.path.join(run, f"scan{i}.jpg"))
    shutil.copyfile(sources[0], os.path.join(run, "scan21.jpg"))    # an orphan

    plan = stacks.plan_stacks(batch.list_images(run), per_stack=3, split="single")
    check(plan.sizes() == [3, 3, 3, 1], f"cut is wrong: {plan.sizes()}")
    check(len(plan.leftover) == 1, "the orphan scan should be reported")

    # The operator finds the pile disagrees at the second break.
    check(plan.nudge(2, -1) and plan.sizes() == [3, 2, 4, 1],
          f"nudge is wrong: {plan.sizes()}")
    check(not plan.nudge(1, -5) or min(plan.sizes()) >= 1,
          "a nudge emptied a stack")
    plan.rebreak(3)

    names = stacks.folder_names(plan, "Box {n:02d} ({first}-{last})")
    check(names[0] == "Box 01 (1-3)" and names[-1] == "Box 04 (10-10)",
          f"folder names wrong: {names}")
    for bad in ("Stack", "Stack {n}?", "Stack {x}"):
        try:
            stacks.folder_names(plan, bad)
            check(False, f"{bad!r} should be refused")
        except ValueError:
            pass

    # The default is the Scan folders format, numbered on from the day's
    # folders already there.
    check(stacks.folder_names(plan)[0] == f"{plan.date} - 001",
          f"default folder name wrong: {stacks.folder_names(plan)[0]}")
    os.makedirs(os.path.join(run, f"{plan.date} - 004"))
    plan.number_from(run)
    check(stacks.folder_names(plan)[0] == f"{plan.date} - 005",
          f"numbering did not carry on from the day's folders: {stacks.folder_names(plan)}")
    plan.number_from(run, "Stack {n:02d}")
    check(plan.first_number == 1, "a template without {date} should start at 1")
    os.rmdir(os.path.join(run, f"{plan.date} - 004"))
    plan.number_from(run)

    # Something in the way means nothing moves at all.
    second = os.path.join(run, stacks.folder_names(plan)[1])
    os.makedirs(second)
    with open(os.path.join(second, "scan7.jpg"), "w") as fh:
        fh.write("in the way")
    check(stacks.problems(plan, run), "a clash should be reported")
    try:
        stacks.apply(plan, run)
        check(False, "apply should refuse a clash")
    except ValueError:
        pass
    check(len(batch.list_images(run)) == 21, "a refused split moved files")
    shutil.rmtree(second)

    # A failure part-way puts the half-moved card back, and a second run
    # carries on from where it stopped.
    real_move = stacks.shutil.move

    def failing_move(src, dst):
        # The back of card 4, every time — a one-off would just be retried.
        if os.path.basename(src) == "scan8.jpg":
            raise OSError(22, "Invalid argument")
        return real_move(src, dst)

    stacks.shutil.move = failing_move
    first = []
    try:
        stacks.apply(plan, run)
        check(False, "the failure should be raised")
    except stacks.SplitFailed as exc:
        first = exc.moves
        check(len(exc.moves) == 6, f"expected 3 whole cards moved, got {len(exc.moves)}")
        check(os.path.exists(os.path.join(run, "scan7.jpg")),
              "card 4's front was left filed without its back")
    finally:
        stacks.shutil.move = real_move
    moves = stacks.apply(plan, run)
    check(len(moves) == 14, f"the resumed run should move the rest, moved {len(moves)}")

    for k, name in enumerate(stacks.folder_names(plan)):
        folder = os.path.join(run, name)
        refiled = batch.pair_sequential(batch.list_images(folder))
        check([(c.front.name, c.back.name) for c in refiled.cards]
              == [(c.front.name, c.back.name) for c in plan.stack(k)],
              f"{name} does not pair into the cards it was cut from")
    check([os.path.basename(p) for p in batch.list_images(run)] == ["scan21.jpg"],
          "only the orphan should be left behind")

    stuck = stacks.undo(first + moves)
    check(not stuck and len(batch.list_images(run)) == 21,
          "undo should put every scan back")
    check(not [d for d in os.listdir(run) if os.path.isdir(os.path.join(run, d))],
          "undo should remove the emptied stack folders")

    # A scan holding both faces is one card, and moves as its one file.
    both = os.path.join(work, "split-combined")
    os.makedirs(both)
    for i in range(1, 4):
        make_combined(os.path.join(both, f"pair{i}.jpg"), angle=0.3)
    combined = stacks.plan_stacks(batch.list_images(both), per_stack=2, split="auto")
    check(len(combined.cards) == 3 and all(c.combined for c in combined.cards),
          f"combined scans were not read as one card each: {len(combined.cards)}")
    stacks.apply(combined, both, "Stack {n}")
    check(sorted(os.listdir(os.path.join(both, "Stack 1"))) == ["pair1.jpg", "pair2.jpg"]
          and os.listdir(os.path.join(both, "Stack 2")) == ["pair3.jpg"],
          "combined scans were not filed one file per card")
    # Cropping each filed stack: its own cards, into its own folder, and
    # never read back as scans of the stack.
    stack_folders = [os.path.join(both, n) for n in ("Stack 1", "Stack 2")]
    seen = []
    written, failures = stacks.crop_stacks(
        stack_folders, progress=lambda s, n, i, t, *rest: seen.append((s, i, t)))
    check(not failures, f"cropping the stacks failed: {failures}")
    check(seen == [(1, 1, 2), (1, 2, 2), (2, 1, 1)], f"crop progress was {seen}")
    for folder, cards in zip(stack_folders, (2, 1)):
        out = os.listdir(os.path.join(folder, stacks.CROP_FOLDER))
        check(len(out) == cards * batch.PER_CARD,
              f"{folder}: expected {cards} card(s) of crops, got {sorted(out)}")
    check(len(written) == 3 * batch.PER_CARD, f"written list is {len(written)} long")
    check(len(batch.list_images(stack_folders[0])) == 2,
          "a stack's crops were listed as its scans")
    stacks.remove_written(written)
    check(not any(os.path.exists(os.path.join(f, stacks.CROP_FOLDER))
                  for f in stack_folders), "removing the crops left their folders")

    face = combined.cards[0].back
    half = imaging.thumbnail(os.path.join(both, "Stack 1", "pair1.jpg"), (150, 210),
                             face.side)
    check(1.25 < half.size[1] / half.size[0] < 1.55,
          f"a combined scan's thumbnail is not one card: {half.size}")

    thumb = imaging.thumbnail(os.path.join(run, "scan1.jpg"), (150, 210))
    check(thumb.size[0] <= 150 and thumb.size[1] <= 210, f"thumbnail is {thumb.size}")
    # Cropped to the card, so it is card-shaped rather than scanner-bed-shaped.
    check(1.25 < thumb.size[1] / thumb.size[0] < 1.55, f"thumbnail not cropped: {thumb.size}")


def main():
    work = tempfile.mkdtemp(prefix="cardcropper-smoke-")
    scans, out = os.path.join(work, "scans"), os.path.join(work, "out")
    os.makedirs(scans)
    angles = [0.5, -0.4, 0.3, 0.6]
    for i, angle in enumerate(angles, 1):
        make_scan(os.path.join(scans, f"{i:04d}.jpg"), front=(i % 2 == 1), angle=angle)

    try:
        # Natural order, not lexical: 2 sorts before 10.
        mixed = ["a/0010.jpg", "a/2.jpg", "a/1.jpg"]
        check([os.path.basename(p) for p in sorted(mixed, key=batch.natural_key)]
              == ["1.jpg", "2.jpg", "0010.jpg"], "natural sort is wrong")

        plan = batch.pair_sequential(batch.list_images(scans))
        check(len(plan.cards) == 2, f"expected 2 cards, got {len(plan.cards)}")
        check(not plan.leftover, "nothing should be left over from 4 scans")

        # An odd file out is reported, never silently folded into a card.
        odd = batch.pair_sequential(batch.list_images(scans)[:3])
        check(len(odd.cards) == 1 and len(odd.leftover) == 1, "odd scan not reported")

        for style in sorted(imaging.STYLES):
            target = os.path.join(out, style)
            done, failed, failures = batch.run(plan, target, style=style)
            check(done == 2 and failed == 0 and not failures,
                  f"{style}: {done} done, {failed} failed")
            written = sorted(os.listdir(target))
            check(len(written) == 8, f"{style}: expected 8 files, got {written}")
            for name in written:
                check(os.path.getsize(os.path.join(target, name)) > 1024,
                      f"{style}: {name} is suspiciously small")
            # Filename order must be the output order, or a bulk uploader
            # attaches a card's photos to the wrong listing.
            check(written == [n for i in (1, 2) for _, n in
                              batch.output_names(i, plan.cards[i - 1])],
                  f"{style}: files do not sort into the intended sequence")

        # The deskew has to recover the angle actually applied, or every crop
        # below it is of interior artwork rather than the card's corner.
        for i, angle in enumerate(angles, 1):
            im = imaging.load(os.path.join(scans, f"{i:04d}.jpg"))
            _, _, found, _ = imaging.straighten(im)
            check(abs(found - angle) < 0.15,
                  f"deskew found {found:+.2f}° where {angle:+.2f}° was applied")

        # Continuous numbering has to be continuous across cards.
        seq = [n for i in (1, 2, 3) for _, n in batch.output_names(i, None, "sequence")]
        check(seq[:5] == ["0001.jpg", "0002.jpg", "0003.jpg", "0004.jpg", "0005.jpg"],
              f"sequence numbering broken: {seq[:5]}")

        # The front leads in both orders — it is the eBay gallery thumbnail.
        for order in batch.ORDERS:
            first = batch.output_names(1, None, "grouped", order)[0][0]
            check(first == "front", f"{order} does not lead with the front")

        # A transient OSError is retried, not surfaced. This is the Google
        # Drive case: [Errno 22] on a scan that has not been streamed down yet.
        calls = []

        def flaky(path):
            calls.append(path)
            if len(calls) < batch.RETRIES:
                raise OSError(22, "Invalid argument")
            return "recovered"

        batch.RETRY_WAIT = 0                    # do not actually sleep here
        check(batch._io(flaky, "G:/x/scan.jpg") == "recovered",
              "a transient OSError should be retried")
        check(len(calls) == batch.RETRIES, f"expected {batch.RETRIES} attempts")

        # One that never recovers names the file, so "[Errno 22] Invalid
        # argument" does not send someone hunting for a bad filename.
        try:
            batch._io(lambda _p: (_ for _ in ()).throw(OSError(22, "Invalid argument")),
                      "G:/x/scan.jpg")
            check(False, "a permanent OSError should raise")
        except OSError as exc:
            check("scan.jpg" in str(exc), f"error does not name the file: {exc}")

        # A card that fails leaves NOTHING behind — a front with no back would
        # reach a listing looking complete.
        broken = os.path.join(work, "broken")
        os.makedirs(broken)
        shutil.copy(os.path.join(scans, "0001.jpg"), os.path.join(broken, "0001.jpg"))
        with open(os.path.join(broken, "0002.jpg"), "w") as fh:
            fh.write("not an image")
        partial = os.path.join(work, "partial")
        done, failed, failures = batch.run(
            batch.pair_sequential(batch.list_images(broken)), partial)
        check(done == 0 and failed == 1, f"expected 1 failure, got {done}/{failed}")
        check(len(failures) == 1, "the failure should be reported for re-running")
        check(not os.listdir(partial),
              f"a failed card left files behind: {os.listdir(partial)}")

        # Day folders: the format is exact, and a second session the same day
        # carries the numbering on instead of clashing with the first.
        day = os.path.join(work, "days")
        check(folders.date_label(datetime.date(2026, 9, 6)) == "26.09.06",
              "date label is not YY.MM.DD")
        made = folders.create(day, 3, "26.09.06")
        check([os.path.basename(p) for p in made]
              == ["26.09.06 - 001", "26.09.06 - 002", "26.09.06 - 003"],
              f"unexpected folder names: {made}")
        with open(os.path.join(made[0], "scan.jpg"), "w") as fh:
            fh.write("x")
        more = folders.create(day, 2, "26.09.06")
        check([os.path.basename(p) for p in more] == ["26.09.06 - 004", "26.09.06 - 005"],
              f"numbering did not carry on: {more}")
        check(os.listdir(made[0]) == ["scan.jpg"], "an existing folder was touched")
        # Another day's folders do not count towards this one's numbering.
        check(folders.plan(day, 1, "26.09.07") == ["26.09.07 - 001"],
              "another day's folders leaked into the numbering")
        try:
            folders.create(day, 3, "26.09.06", start=5)
            check(False, "a clash with an existing folder should be refused")
        except FileExistsError:
            pass
        check(len(os.listdir(day)) == 5, "a refused run still made folders")

        # ---------------------------------------------- combined scans
        #
        # The failure this whole section guards against is silent: two cards
        # side by side measure 1.43:1 and one card measures 1.40:1, so a
        # combined scan run as a single card passes every geometry check and
        # yields crops of the PAIR's outer corners.
        both = os.path.join(work, "both")
        os.makedirs(both)
        for i, (angle, stacked) in enumerate(
                [(0.5, False), (-0.4, False), (0.3, True)], 1):
            make_combined(os.path.join(both, f"{i:04d}.jpg"), angle, stacked=stacked)

        for name in sorted(os.listdir(both)):
            path = os.path.join(both, name)
            im = imaging.load(path)
            regions = imaging.split_regions(im)
            check(regions is not None and len(regions) == 2,
                  f"{name}: a scan holding two cards was not divided")
            # The halves keep the whole of the other axis, so nothing of either
            # card can have been cut away by the division itself.
            check(sum(r.size[0] for r in regions) == im.size[0]
                  or sum(r.size[1] for r in regions) == im.size[1],
                  f"{name}: the two halves do not add back up to the scan")
            check(imaging.probe(path),
                  f"{name}: the small-decode probe disagrees with the full scan")

        # And the other way: a single-card scan must NOT be divided, or every
        # batch of ordinary scans would be paired into nonsense.
        for i in range(1, 5):
            single = os.path.join(scans, f"{i:04d}.jpg")
            check(imaging.split_regions(imaging.load(single)) is None,
                  f"{i:04d}.jpg: a single card was read as two")
            check(not imaging.probe(single), f"{i:04d}.jpg: probe says two cards")

        # A combined scan is one card on its own, and its two faces are the two
        # halves of the same file.
        plan = batch.plan_scans(batch.list_images(both))
        check(len(plan.cards) == 3 and not plan.leftover,
              f"expected 3 combined cards, got {len(plan.cards)} "
              f"and {len(plan.leftover)} left over")
        for card in plan.cards:
            check(card.combined, "a combined scan was not marked as one")
            check(card.front.path == card.back.path, "faces should share a file")
            check({card.front.side, card.back.side} == {0, 1},
                  "the two faces should be the two halves")
            check(len(card.sources) == 1, "a combined card reads one file")

        # `--front second` puts the other half in front, and Swap is the same
        # operation per row.
        flipped = batch.plan_scans(batch.list_images(both), front="second")
        check(flipped.cards[0].front.side == 1, "--front second was ignored")

        # A stray single scan beside combined ones is reported, never paired
        # across them with another stray from further down the folder.
        mixed_dir = os.path.join(work, "mixed")
        os.makedirs(mixed_dir)
        make_combined(os.path.join(mixed_dir, "0001.jpg"), 0.4)
        shutil.copy(os.path.join(scans, "0001.jpg"), os.path.join(mixed_dir, "0002.jpg"))
        make_combined(os.path.join(mixed_dir, "0003.jpg"), -0.3)
        mixed = batch.plan_scans(batch.list_images(mixed_dir))
        check(len(mixed.cards) == 2 and len(mixed.leftover) == 1,
              f"mixed batch: {len(mixed.cards)} card(s), "
              f"{len(mixed.leftover)} left over — expected 2 and 1")
        check(all(c.combined for c in mixed.cards),
              "the single scan was folded into a combined card")

        # `single` must never divide, whatever the scan holds — it is how an
        # operator overrules the detection.
        forced_single = batch.plan_scans(batch.list_images(both), split="single")
        check(len(forced_single.cards) == 1 and len(forced_single.leftover) == 1,
              "split=single should pair the three files as files, not divide them")

        # `combined` divides even a scan with no gap to find, because there the
        # operator has asserted what the file holds.
        touching = os.path.join(work, "touching")
        os.makedirs(touching)
        make_combined(os.path.join(touching, "0001.jpg"), 0.0, gap=0)
        im = imaging.load(os.path.join(touching, "0001.jpg"))
        check(imaging.split_regions(im) is None,
              "two touching cards have no gap and must not be divided on a guess")
        check(len(imaging.split_regions(im, force=True)) == 2,
              "force should divide a scan with no gap")
        ok, why = imaging.divided_ok(imaging.split_regions(im, force=True))
        check(ok, f"two equal cards divide at the middle cleanly, but: {why}")

        # Touching cards are the normal case for a flatbed, so they must not
        # produce a warning simply for having no gap — only a division that
        # actually came out wrong is worth saying anything about.
        touching_plan = batch.plan_scans(batch.list_images(touching), split="combined")
        touching_out = os.path.join(out, "touching")
        os.makedirs(touching_out, exist_ok=True)
        _, notes = batch.process_card(touching_plan.cards[0], touching_out, 1)
        check(not [n for n in notes if "touching" in n or "did not come out" in n],
              f"a clean division of touching cards should be quiet: {notes}")

        # End to end: four files per combined card, the front leading, and the
        # two faces actually different — the check that catches a division that
        # ran but handed back the same half twice.
        for style in sorted(imaging.STYLES):
            target = os.path.join(out, "combined-" + style)
            done, failed, failures = batch.run(plan, target, style=style)
            check(done == 3 and failed == 0, f"{style}: {done} done, {failed} failed")
            written = sorted(os.listdir(target))
            check(len(written) == 12, f"{style}: expected 12 files, got {written}")
            check(written == [n for i in (1, 2, 3) for _, n in
                              batch.output_names(i, plan.cards[i - 1])],
                  f"{style}: files do not sort into the intended sequence")
            front = imaging.load(os.path.join(target, "0001_1_front.jpg"))
            back = imaging.load(os.path.join(target, "0001_2_back.jpg"))
            # Relative, not a pixel count: the two faces are detected
            # independently and differ by a few tenths of a percent. What this
            # is for is a division that handed back a card and a half — that
            # shows up as tens of percent, never as a handful of pixels.
            check(all(abs(f - b) <= 0.02 * max(f, b)
                      for f, b in zip(front.size, back.size)),
                  f"{style}: the two faces came out {front.size} and {back.size} "
                  "— they should be cut alike")
            check(_difference(front, back) > 10,
                  f"{style}: front and back look the same — the scan was not "
                  "divided, or one half was written twice")
            # A face cut out of a combined scan is written as the card itself,
            # not as the half of the bed it sat on: no scanner bed down one side.
            ratio = max(front.size) / float(min(front.size))
            check(1.15 <= ratio <= 1.75,
                  f"{style}: the written front is {ratio:.2f}:1, not card-shaped")

        # A combined scan run as a single card is called out rather than
        # quietly cropped at the pair's outer corners.
        as_single = batch.plan_scans(batch.list_images(both), split="single")
        warned = os.path.join(out, "warned")
        os.makedirs(warned, exist_ok=True)
        _, notes = batch.process_card(as_single.cards[0], warned, 1)
        check(any("two cards" in n for n in notes),
              f"no warning that the scan holds two cards: {notes}")

        # -------------------------------------------------- dark cards
        #
        # The bar that separates card from bed is read off each scan. A fixed
        # one cannot serve both a bed of 0 and a card whose artwork measures 12
        # — and getting it wrong does not fail loudly, it quietly trims off
        # whichever part of the card happens to be dark.
        bed = 60
        for label, card in (("front", make_dark_card()), ("back", make_dark_card(True))):
            scan = Image.new("RGB", (1000 + bed * 2, 1400 + bed * 2), (0, 0, 0))
            scan.paste(card, (bed, bed))
            box = imaging._card_box(imaging._mask(scan))
            want = (bed, bed, bed + 999, bed + 1399)
            check(box is not None and max(abs(a - b) for a, b in zip(box, want)) <= 3,
                  f"dark {label}: card box {box}, should be about {want}")
            # And it must still read as ONE card, not as its lit patches.
            check(len(imaging._card_runs(imaging._mask(scan), 0)) == 1,
                  f"dark {label} broke into {len(imaging._card_runs(imaging._mask(scan), 0))} "
                  "stretches — a dark patch inside a card is not a gap between two")

        # The learned bar may never be looser than the fixed one, so no scan is
        # read worse than it was before.
        for name in sorted(os.listdir(scans)):
            value = imaging._value(imaging.load(os.path.join(scans, name)))
            learned = min(imaging.INK_THRESHOLD,
                          imaging._bed_level(value) + imaging.BED_MARGIN)
            check(learned <= imaging.INK_THRESHOLD,
                  f"{name}: the learned threshold {learned} is looser than the "
                  f"fixed {imaging.INK_THRESHOLD}, so this scan reads worse than before")

        # End to end: a dark pair comes out as two cards, neither carrying a
        # strip of the other. A 12% strip of the neighbour still passes the
        # loose shape check, so the ratio is held to a real card's here.
        darkdir = os.path.join(work, "dark")
        os.makedirs(darkdir)
        pairscan = Image.new("RGB", (2000 + bed * 2, 1400 + bed * 2), (0, 0, 0))
        pairscan.paste(make_dark_card(), (bed, bed))
        pairscan.paste(make_dark_card(True), (bed + 1000, bed))
        pairscan.save(os.path.join(darkdir, "0001.jpg"), quality=95)
        dark_plan = batch.plan_scans(batch.list_images(darkdir), split="combined")
        darkout = os.path.join(out, "dark")
        done, failed, _ = batch.run(dark_plan, darkout, start=1)
        check(done == 1 and not failed, f"dark pair: {done} done, {failed} failed")
        for name in ("0001_1_front.jpg", "0001_2_back.jpg"):
            face = imaging.load(os.path.join(darkout, name))
            ratio = max(face.size) / float(min(face.size))
            check(1.30 <= ratio <= 1.45,
                  f"dark pair {name} came out {ratio:.2f}:1 at {face.size} — a real "
                  "card is about 1.36:1, so this carries part of the other card "
                  "or lost part of its own")

        # --------------------------------- a real scan's shape, measured
        #
        # Rebuilt from a scan that failed in someone's hands, after fetching it
        # and MEASURING it rather than guessing at it. 1486x1032 at 300dpi: two
        # cards with a 9px seam, and a scan window 87.4mm tall against a card
        # that is 88mm, so the cards run off the top and bottom with no bed
        # there at all.
        #
        # What broke was the back. It is near-black, and down its left third
        # the lit fraction of each column wanders either side of the "40% lit"
        # bar — 0.39, 0.56, 0.41. Every stretch that bar produced there was too
        # short to keep, so the run of card did not resume until 190px into the
        # back, and the gap between two cards was read as 743 to 942 rather
        # than the true 743 to 751. The division landed at its middle, 95px
        # inside the back: the front came out carrying a strip of it at 1.24:1
        # and the back came out narrow at 1.60:1, and BOTH passed the 1.15-1.75
        # shape check. The numbers below reproduce that within a hair.
        W, H, WANDER = 1486, 1032, 200
        real = Image.new("RGB", (W, H), (0, 0, 0))
        front = Image.new("RGB", (743, H), (120, 110, 150))
        ImageDraw.Draw(front).rectangle([0, int(H * 0.55), 743, H], fill=(30, 16, 22))
        real.paste(front, (0, 0))                       # runs off the left edge
        back = np.full((H, 734, 3), (9, 11, 18), dtype=np.uint8)
        back[:, WANDER:] = (14, 17, 52)
        for x in range(WANDER):
            band = int(H * (0.45 + 0.12 * math.sin(x / 13.0)))
            back[(H - band) // 2:(H - band) // 2 + band, x] = (14, 17, 52)
        b = Image.fromarray(back)
        d = ImageDraw.Draw(b)
        d.rectangle([40, 40, 694, 992], outline=(225, 190, 110), width=4)
        d.polygon([(367, 150), (650, 516), (367, 882), (84, 516)],
                  outline=(225, 190, 110), width=4)
        real.paste(b, (752, 0))                         # 9px seam at 743..751
        realdir = os.path.join(work, "real")
        os.makedirs(realdir)
        real.save(os.path.join(realdir, "0001.jpg"), quality=95)

        loaded = imaging.load(os.path.join(realdir, "0001.jpg"))
        check(len(imaging._card_runs(imaging._mask(loaded), 0)) == 2,
              f"the near-black back came out as "
              f"{len(imaging._card_runs(imaging._mask(loaded), 0))} stretches — a "
              "dark patch inside a card is not the gap between two cards")
        halves = imaging.split_regions(loaded)
        check(halves is not None, "a 9px seam between two cards should be found")
        for which, half in zip(("front", "back"), halves):
            exact, _, _, _ = imaging.straighten(half)
            ratio = max(exact.size) / float(min(exact.size))
            check(1.30 <= ratio <= 1.45,
                  f"{which} came out {ratio:.2f}:1 at {exact.size} — a card is about "
                  "1.36:1, so this carries a strip of the other card or lost part "
                  "of its own")

        # A card the scanner cropped flush is called out: with no bed behind it
        # those crops cannot show its outline, which is most of what a corner
        # crop is for.
        off = imaging.clipped_edges(halves[0])
        check("top" in off and "bottom" in off,
              f"a card running off the top and bottom should be reported, got {off}")
        bedded = Image.new("RGB", (900, 1200), (0, 0, 0))
        bedded.paste(front.resize((700, 1000)), (100, 100))
        check(not imaging.clipped_edges(bedded),
              "a card with bed all round it is not clipped")

        # ------------------------------- a back too dark to find its edge
        #
        # A Lorcana back is printed in a black that measures 0 against a
        # scanner bed of 0. The card's own border and the bed are not merely
        # similar, they are the same number, and no threshold separates them.
        # What the detector finds instead is the frame line inset a few
        # millimetres, so the crops show the corners of the frame rather than
        # the corners of the card: a real scan detected the back at 693x987
        # where the front of the same card came out 746x1011.
        BW, BH, INSET = 740, 1030, 22
        blind = Image.new("RGB", (BW * 2 + 120, BH + 120), (0, 0, 0))
        lit_front = Image.new("RGB", (BW, BH), (90, 120, 150))
        ImageDraw.Draw(lit_front).rectangle([0, int(BH * .6), BW, BH], fill=(40, 30, 60))
        blind.paste(lit_front, (40, 40))
        # the back: pure black to its edge, with only an inset frame visible
        black_back = Image.new("RGB", (BW, BH), (0, 0, 0))
        ImageDraw.Draw(black_back).rectangle(
            [INSET, INSET, BW - INSET, BH - INSET], outline=(150, 130, 90), width=5)
        blind.paste(black_back, (40 + BW + 40, 40))
        blinddir = os.path.join(work, "blind")
        os.makedirs(blinddir)
        # PNG on purpose. This is the LIMITING case — a back whose black is bit
        # for bit the bed's black — and JPEG will not hold it: the ringing
        # around the frame line leaks nonzero pixels into the card and hands
        # the detector the very edge this fixture exists to deny it. On the
        # scanner measured, a real black border is not quite the bed and IS
        # found; borrowing is the safety net for when it is not.
        blind.save(os.path.join(blinddir, "0001.png"))

        halves = imaging.split_regions(imaging.load(os.path.join(blinddir, "0001.png")))
        check(halves is not None, "the two cards have bed between them and should divide")
        loose = imaging.straighten(halves[1])[0]
        check(loose.size[0] < BW * 0.95,
              f"the back should detect SHORT on its own ({loose.size}) — if it does "
              "not, this fixture no longer reproduces the problem it guards")

        # Given the front's size, the back is cut to it instead.
        front_exact = imaging.straighten(halves[0])[0]
        tight = imaging.straighten(halves[1], reference=front_exact.size)[0]
        check(tight.size[0] >= front_exact.size[0] * 0.97
              and tight.size[1] >= front_exact.size[1] * 0.97,
              f"the back came out {tight.size} against the front's {front_exact.size}")

        # It may only ever grow, and only as far as the scan goes.
        grown = imaging._expand_to((10, 10, 50, 50), (500, 500), (100, 100))
        check(grown == (0, 0, 99, 99),
              f"expansion must stop at the edge of the scan, got {grown}")
        same = imaging._expand_to((10, 10, 90, 90), (20, 20), (200, 200))
        check(same == (10, 10, 90, 90),
              f"a box already bigger than the reference is left alone, got {same}")

        # And the whole thing end to end: the note names what happened.
        blindout = os.path.join(out, "blind")
        blindplan = batch.plan_scans(batch.list_images(blinddir), split="combined")
        os.makedirs(blindout, exist_ok=True)
        _, blindnotes = batch.process_card(blindplan.cards[0], blindout, 1)
        check(any("too dark to find its own edge" in n for n in blindnotes),
              f"the borrowed size should be reported, got {blindnotes}")
        back_face = imaging.load(os.path.join(blindout, "0001_2_back.jpg"))
        front_face = imaging.load(os.path.join(blindout, "0001_1_front.jpg"))
        # Relative: what is written out carries the crop margin, and that
        # margin is held inside the scan, so two faces sitting differently in
        # their halves keep a few pixels between them even once the CARDS match.
        check(all(abs(b - f) <= 0.02 * max(b, f)
                  for b, f in zip(back_face.size, front_face.size)),
              f"the two faces should come out the same size, got {back_face.size} "
              f"and {front_face.size}")

        # --------------------------------- a real scan, kept byte for byte
        #
        # See tests/fixtures/README.md. A document scanner's duplex output with
        # the pair filling the sheet: no bed anywhere, no gap between the cards,
        # every face clipped on all four sides. It broke two things at once and
        # both came out as hard straight lines through the corner crops.
        #
        # It is a file rather than something generated because it CANNOT be
        # generated: re-encoding it at any quality loses the first fault, so
        # every synthetic stand-in written for this passed on the broken code.
        flush_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "fixtures", "flush-pair.jpg")
        check(os.path.exists(flush_path), f"the fixture is missing: {flush_path}")
        flush_scan = imaging.load(flush_path)
        check(flush_scan.size == (1486, 1037),
              f"the fixture is {flush_scan.size}, not the 1486x1037 it was measured "
              "at — has it been re-encoded? See tests/fixtures/README.md")

        check(imaging.split_regions(flush_scan) is None,
              "the two cards touch, so there is no gap to divide on")
        halves = imaging.split_regions(flush_scan, force=True)
        check(len(halves) == 2, "a touching pair still divides down the middle")
        ok, why = imaging.divided_ok(halves)
        check(ok, f"the middle should land between the two cards, but: {why}")

        for which, half in zip(("front", "back"), halves):
            exact, padded, angle, edges = imaging.straighten(half)
            # One edge is not a consensus. This face measures exactly one, and
            # acting on it rotated a square card by 0.23 degrees.
            check(edges >= imaging.MIN_SKEW_EDGES or angle == 0.0,
                  f"{which}: rotated {angle:+.2f}° on {edges} edge(s) — the fill "
                  "that brings in reads as a hard line through every corner crop")
            # And nothing invented past the edge of what was scanned. Before,
            # this face was padded out by 48px of black that looks like bed.
            check(padded.size[0] <= half.size[0] and padded.size[1] <= half.size[1],
                  f"{which}: the crop area {padded.size} is bigger than the scan "
                  f"{half.size} — that margin is padding, and on a dark bed it "
                  "reads as a background the card was never photographed against")
            ratio = max(exact.size) / float(min(exact.size))
            check(1.30 <= ratio <= 1.45,
                  f"{which} came out {ratio:.2f}:1 at {exact.size}")
            check(imaging.clipped_edges(half),
                  f"{which}: this scan is cropped flush and that should be reported")

        # Where the seam is visible but not EMPTY, a blind division has to snap
        # onto it. Measured across a batch: the seam column runs 0.20 to 0.49
        # lit where the card either side runs 0.79 to 0.96 — a plain local
        # minimum that is simply above the bar deciding where a card stops. The
        # division lands a column short of it, and that column is the first
        # card's edge.
        # The second card is a little narrower, clipped by the edge of the
        # sheet, which is what puts the middle of the pair off the seam. Both
        # cards the same width and the middle lands on it by luck.
        dim_seam = Image.new("RGB", (1484, 1000), (0, 0, 0))
        d = ImageDraw.Draw(dim_seam)
        d.rectangle([0, 0, 742, 1000], fill=(180, 70, 70))      # 743 wide
        d.rectangle([744, 0, 1483, 1000], fill=(70, 70, 180))   # 740, clipped
        # The seam itself: mostly backing, but lit along enough of its length
        # to clear the bar that decides where a card stops — which is what the
        # real ones do, running 0.20 to 0.49 lit. Dark enough to see, not dark
        # enough to have been found as a gap.
        for y in range(0, 1000):
            d.rectangle([743, y, 743, y], fill=(180, 70, 70) if y % 5 < 2 else (2, 2, 2))
        # PNG: a seam one column wide does not survive JPEG. The transform
        # smears it into its neighbours until the dip is gone, and then the
        # fixture tests nothing. Real seams are wider in the underlying scan
        # and survive; a synthetic one has to be written losslessly to stand in
        # for them.
        seam_path = os.path.join(work, "dim-seam.png")
        dim_seam.save(seam_path)
        got = imaging.split_regions(imaging.load(seam_path), force=True)
        opening = np.asarray(got[1].convert("RGB"))[:, 0].mean(axis=0)
        check(opening[0] < 140,
              f"the division did not land on the seam: the second half opens on "
              f"{opening.round().tolist()}, which is the first card's red")

        # And it may only look NEXT DOOR for that seam. A wide search finds an
        # emptier column inside a dark second card and moves onto it, taking a
        # slice of that card into the first half — which is the same bleed the
        # other way round.
        check(imaging.SEAM_SEARCH <= 6,
              f"a {imaging.SEAM_SEARCH}-column search is wide enough to wander off "
              "the seam and into the next card")
        dark_second = Image.new("RGB", (1483, 1000), (0, 0, 0))
        d = ImageDraw.Draw(dark_second)
        d.rectangle([0, 0, 740, 1000], fill=(170, 170, 170))
        d.rectangle([741, 0, 1482, 1000], fill=(26, 26, 26))
        for y in range(0, 1000, 2):     # a patch inside the second card, emptier
            d.rectangle([752, y, 760, y], fill=(4, 4, 4))
        dark_path = os.path.join(work, "dark-second.jpg")
        dark_second.save(dark_path, quality=95)
        got = imaging.split_regions(imaging.load(dark_path), force=True)
        check(abs(got[0].size[0] - 741) <= 4,
              f"the division moved to {got[0].size[0]} where the cards meet at 741 — "
              "it has wandered into the second card")

        # A seam ONE column wide is still a seam. Document-scanner pairs touch
        # along most of their length and leave a single column of backing, and
        # refusing that as too thin falls through to dividing down the middle —
        # which lands a column out whenever the content width is odd, and the
        # column it lands out by is the last column of the first card.
        for seam in (1, 2, 6):
            pair = Image.new("RGB", (740 * 2 + seam, 1000), (0, 0, 0))
            pair.paste(Image.new("RGB", (740, 1000), (190, 60, 60)), (0, 0))
            pair.paste(Image.new("RGB", (740, 1000), (60, 60, 190)), (740 + seam, 0))
            got = imaging.split_regions(pair)
            check(got is not None,
                  f"a {seam}-column seam between two cards should be found")
            check(abs(got[0].size[0] - (740 + seam // 2)) <= 1,
                  f"a {seam}-column seam divided at {got[0].size[0]}, not in its "
                  f"middle at {740 + seam // 2}")
            opening = np.asarray(got[1].convert("RGB"))[:, 0].mean(axis=0)
            check(opening[0] < 120,
                  f"with a {seam}-column seam the second half opens on a red column "
                  f"from the first card: {opening.round().tolist()}")

        # The same arithmetic stated plainly, at three widths: halfway along
        # the content by WIDTH, not the average of its two ends. Those differ
        # by one whenever the width is even, which is most of the time.
        for width in (1486, 1487, 1488):
            pair = Image.new("RGB", (width, 1000), (0, 0, 0))
            half_w = width // 2
            pair.paste(Image.new("RGB", (half_w, 1000), (190, 60, 60)), (0, 0))
            pair.paste(Image.new("RGB", (width - half_w, 1000), (60, 60, 190)),
                       (half_w, 0))
            got = imaging.split_regions(pair, force=True)
            check(abs(got[0].size[0] - half_w) <= 1,
                  f"a {width}px pair divided {got[0].size[0]}/{got[1].size[0]}, "
                  f"but the cards meet at {half_w}")
            opening = np.asarray(got[1].convert("RGB"))[:, 0].mean(axis=0)
            check(opening[2] > opening[0],
                  f"the second half of a {width}px pair opens on a column that is "
                  f"red, not blue: {opening.round().tolist()}")

        # End to end, the crops come out and nothing in them is invented.
        flushout = os.path.join(out, "flush")
        flushplan = batch.Plan(cards=[batch.Card(batch.Face(flush_path, 0),
                                                 batch.Face(flush_path, 1))])
        os.makedirs(flushout, exist_ok=True)
        names, flushnotes = batch.process_card(flushplan.cards[0], flushout, 1)
        check(len(names) == 4, f"expected four images, got {names}")
        check(any("runs off" in n for n in flushnotes),
              f"a flush-cropped scan should say so: {flushnotes}")
        check(not any("deskewed" in n for n in flushnotes),
              f"nothing here has two edges to deskew from: {flushnotes}")

        # ------------------------- the crop box lands ON the edge
        #
        # A scanner leaves a bright card sitting in tens of pixels of bleed,
        # and the card-vs-bed bar is read off the scan precisely so that it can
        # drop low enough to see a near-black border — which is exactly low
        # enough to call that bleed card. An outline that stops where the GLOW
        # dies pushes every crop outward, worse the brighter the card is.
        for front in (True, False):
            face = "front" if front else "back"
            for glow in (0, 9):
                path = os.path.join(work, f"halo-{face}-{glow}.jpg")
                make_scan(path, front=front, angle=0.5, glow=glow)
                exact, padded, _, _ = imaging.straighten(imaging.load(path))
                ref = CARD_W / imaging.CARD_W      # scan px per reference px
                for got, want, axis in ((exact.size[0], CARD_W, "width"),
                                        (exact.size[1], CARD_H, "height")):
                    check(abs(got - want) / ref <= 6,
                          f"{face} glow={glow}: crop box {axis} is off by "
                          f"{abs(got - want) / ref:.0f} reference px")

                # And the consequence of getting that wrong, which is what an
                # operator actually sees: an edge strip keeps only
                # CROP_MARGIN // 3 reference px of bed outside the card, so an
                # outline that drifts outward slides the strip off into black.
                margin, cut, strip = imaging._cuts(padded)
                for name, crop in imaging.edge_crops(padded, cut, strip, margin):
                    v = imaging._value(crop)
                    on_card = (v > v.max() * 0.5).mean()
                    check(on_card > 0.6,
                          f"{face} glow={glow}: {name} strip is only "
                          f"{on_card:.0%} card — it has slid off the edge")

        # ------------------------- a sheet feeder's bed is not black
        #
        # Dust on the sensor draws a line the full length of every page, the
        # feeder's backing shows for the rest of the page once the card has
        # passed, and the lamp throws a glow off the card to one side. All of
        # it clears the bar. Before the bed was cleaned, a line took the box
        # out to it and the backing took it on to the end of the page — on
        # both faces — so every edge strip on that side was scanner bed.
        #
        # The artefacts are added AFTER the card is tilted, because that is
        # how a feeder makes them: the card is skewed on the page, the sensor
        # and the backing are not.
        for front in (True, False):
            face = "front" if front else "back"
            path = os.path.join(work, f"feeder-{face}.jpg")
            make_scan(path, front=front, angle=0.4)
            im = imaging.load(path)
            halo = ImageChops.offset(im.filter(ImageFilter.GaussianBlur(12)), 12, 12)
            im = ImageChops.lighter(im, halo.point(lambda v: int(v * 0.35)))
            a = np.asarray(im).astype(int)
            a[:, [60, 1220, 1221, 1290], 2] += 40               # sensor lines
            a[1610:] += (26, 13, 18)                            # feeder backing
            path = os.path.join(work, f"feeder-{face}-dirty.jpg")
            Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).save(path, quality=95)
            exact, _, found, _ = imaging.straighten(imaging.load(path))
            check(abs(exact.size[0] - CARD_W) <= 4 and abs(exact.size[1] - CARD_H) <= 4,
                  f"{face} on a feeder's bed measured {exact.size}, "
                  f"not ~{(CARD_W, CARD_H)}")
            check(abs(found - 0.4) < 0.25,
                  f"{face} on a feeder's bed deskewed {found:+.2f}°, not +0.40°")

        # A card lying against the scan's border is broad and does not run end
        # to end, so none of that may touch it: a flush-cropped card measures
        # exactly as it did before the bed was cleaned.
        flush_value = imaging._value(make_card(front=True))
        check(np.array_equal(imaging._clean_value(flush_value), flush_value),
              "cleaning the bed altered a scan with no bed in it")

        # A navy back's own edge is short beside the step from its border to
        # the brighter swirl inside, which is the tallest step in reach. Judged
        # only against that, the card's edge did not count as a step at all,
        # and the measurement either landed on the swirl — trimming the border
        # off — or gave up and kept a box still wearing its glow.
        back = Image.new("RGB", (CARD_W, CARD_H), (0, 0, 47))
        ImageDraw.Draw(back).rectangle([36, 36, CARD_W - 37, CARD_H - 37],
                                       fill=(40, 90, 200))
        bed = Image.new("RGB", (1400, 1800), (0, 0, 0))
        bed.paste(back, (200, 200))
        bed = bed.rotate(0.4, resample=Image.BICUBIC, fillcolor=(0, 0, 0))
        noise = np.random.default_rng(7).normal(0, 6, (1800, 1400, 3))
        bed = Image.fromarray(np.clip(np.asarray(bed) + 8 + noise, 0, 255).astype(np.uint8))
        path = os.path.join(work, "swirl-back.jpg")
        bed.save(path, quality=95)
        exact, _, _, _ = imaging.straighten(imaging.load(path))
        check(abs(exact.size[0] - CARD_W) <= 4 and abs(exact.size[1] - CARD_H) <= 4,
              f"a navy back with a bright swirl measured {exact.size}, "
              f"not ~{(CARD_W, CARD_H)} — its border was cut off")

        # ------------------------- a strip of sensor noise at the scan's edge
        #
        # See tests/fixtures/README.md. The front's box ran across a clean band
        # of black to a strip of noise at the edge of the scan, and the back —
        # measured correctly — was grown to match it, so both faces' side
        # strips came out as nothing but backing.
        fixtures = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
        noisy = {}
        for face in ("front", "back"):
            path = os.path.join(fixtures, f"noisy-edge-{face}.jpg")
            check(os.path.exists(path), f"the fixture is missing: {path}")
            noisy[face] = imaging.straighten(imaging.load(path))
        widths = {face: got[0].size[0] for face, got in noisy.items()}
        check(abs(widths["front"] - widths["back"]) <= 6 and widths["front"] <= 752,
              f"noisy-edge card measured {widths} — the front has run out to "
              f"the noise at the edge of the scan")
        gap = imaging.edge_contrast(noisy["front"][1])
        check(gap is not None and gap[0] - gap[1] >= imaging.CONTRAST_FLOOR,
              f"noisy-edge front's grey border read as invisible: {gap}")

        # ------------------------- whether the outline can be SEEN at all
        #
        # Cutting the crop in the right place and being able to read it are two
        # different things. A corner is judged from its profile — the card's
        # outline against what is behind it — and a black border on a black
        # backing has a profile that is exactly right and invisible. Measured
        # on real cards: on a dark bed a Lorcana back reads 0 against its
        # backing and a bright front reads 102.
        CM = round(6 / 25.4 * 300)

        def on_backing(card, bed):
            page = Image.new("RGB", (card.size[0] + CM * 2, card.size[1] + CM * 2), bed)
            page.paste(card, (CM, CM))
            return page

        inky_card = Image.new("RGB", (700, 980), (6, 6, 10))
        ImageDraw.Draw(inky_card).rectangle([60, 60, 640, 920], fill=(40, 40, 60))
        # White-bordered, which is the mirror of the problem: unreadable on a
        # white backing for exactly the same reason the black back is
        # unreadable on a dark bed. It is the CONTRAST that matters, not which
        # way round it is — so no one backing is right for every card, and a
        # scan carrying both kinds has to compromise.
        bright_card = Image.new("RGB", (700, 980), (246, 246, 243))
        ImageDraw.Draw(bright_card).rectangle([60, 60, 640, 920], fill=(120, 150, 170))

        for card, bed, bg, readable in (
                (inky_card, (0, 0, 0), "dark", False),
                (inky_card, (242, 243, 240), "light", True),
                (bright_card, (0, 0, 0), "dark", True),
                (bright_card, (250, 250, 248), "light", False)):
            _, padded, _, _ = imaging.straighten(on_backing(card, bed), background=bg)
            measured = imaging.edge_contrast(padded, bg)
            check(measured is not None,
                  f"6mm of backing is enough to measure against, on {bg}")
            gap = abs(measured[0] - measured[1])
            check((gap >= imaging.CONTRAST_FLOOR) is readable,
                  f"edge {measured[0]:.0f} against backing {measured[1]:.0f} is a gap "
                  f"of {gap:.0f}; expected it to be "
                  f"{'readable' if readable else 'unreadable'}")

        # A scan cropped flush has no margin to judge against, and saying the
        # contrast is fine there would be worse than saying nothing: the only
        # background is a sliver in the corner arcs.
        flush_half = imaging.split_regions(
            imaging.load(flush_path), force=True)[0]
        _, flush_padded, _, _ = imaging.straighten(flush_half)
        check(imaging.edge_contrast(flush_padded) is None,
              "with no margin there is nothing to measure the edge against")

        # And it reaches the operator, in the run, in words.
        inky_dir = os.path.join(work, "inky")
        os.makedirs(inky_dir)
        inky_pair = Image.new("RGB", (700 * 2 + CM * 3, 980 + CM * 2), (0, 0, 0))
        inky_pair.paste(bright_card, (CM, CM))
        inky_pair.paste(inky_card, (CM * 2 + 700, CM))
        inky_pair.save(os.path.join(inky_dir, "0001.jpg"), quality=95)
        inky_out = os.path.join(out, "inky")
        os.makedirs(inky_out, exist_ok=True)
        inky_plan = batch.plan_scans(batch.list_images(inky_dir), split="combined",
                                     front="first")
        _, inky_notes = batch.process_card(inky_plan.cards[0], inky_out, 1)
        check(any("outline cannot be made out" in n for n in inky_notes),
              f"the unreadable face should be called out: {inky_notes}")
        check(any("white backing would show it" in n.lower() for n in inky_notes),
              f"and it should say what would fix it: {inky_notes}")
        check(not any("outline cannot be made out" in n and n.startswith("front")
                      for n in inky_notes),
              f"the bright front reads fine and should not be flagged: {inky_notes}")

        # ------------------------------ telling the front from the back
        #
        # No single card can say which of its two pictures is the front — it has
        # two pictures and nothing to choose between them. A BATCH can: every
        # card has a different front and the same back, so the side that looks
        # like itself across the batch is the back. That is the only honest
        # signal; a back is not reliably darker, or plainer, or symmetrical.
        FW, FH = 740, 1030

        def shared_back():
            card = Image.new("RGB", (FW, FH), (16, 22, 60))
            d = ImageDraw.Draw(card)
            d.ellipse([170, 320, 570, 720], outline=(220, 185, 110), width=14)
            d.rectangle([34, 34, FW - 34, FH - 34], outline=(220, 185, 110), width=6)
            return card

        def unique_front(seed):
            rng = random.Random(seed)
            card = Image.new("RGB", (FW, FH), (rng.randrange(60, 200),) * 3)
            d = ImageDraw.Draw(card)
            for _ in range(14):
                x, y = rng.randrange(0, FW - 200), rng.randrange(0, FH - 200)
                d.rectangle([x, y, x + rng.randrange(60, 200),
                             y + rng.randrange(60, 200)],
                            fill=(rng.randrange(255), rng.randrange(255),
                                  rng.randrange(255)))
            return card

        def a_batch(name, count, back_left, one_card=False, separate=False):
            folder = os.path.join(work, name)
            os.makedirs(folder)
            for i in range(1, count + 1):
                f = unique_front(0 if one_card else i)
                left, right = (shared_back(), f) if back_left else (f, shared_back())
                if separate:
                    left.save(os.path.join(folder, f"{2 * i - 1:04d}.jpg"), quality=93)
                    right.save(os.path.join(folder, f"{2 * i:04d}.jpg"), quality=93)
                else:
                    page = Image.new("RGB", (FW * 2 + 160, FH + 80), (0, 0, 0))
                    page.paste(left, (40, 40))
                    page.paste(right, (40 + FW + 80, 40))
                    page.save(os.path.join(folder, f"{i:04d}.jpg"), quality=93)
            return folder

        def front_taken_from(card, separate):
            if separate:
                return "second" if card.front.path > card.back.path else "first"
            return "second" if card.front.side == 1 else "first"

        for name, count, back_left, one, sep, want in (
                ("fb-right", 8, False, False, False, "first"),
                ("fb-left", 8, True, False, False, "second"),
                ("fb-three", 3, True, False, False, "second"),
                ("fb-two", 2, True, False, False, "first"),
                ("fb-same", 6, True, True, False, "first"),
                ("fb-sep-2nd", 8, False, False, True, "first"),
                ("fb-sep-1st", 8, True, False, True, "second")):
            folder = a_batch(name, count, back_left, one, sep)
            plan = batch.plan_scans(batch.list_images(folder),
                                    split="single" if sep else "combined")
            check(plan.cards, f"{name}: nothing planned")
            taken = {front_taken_from(c, sep) for c in plan.cards}
            check(taken == {want},
                  f"{name}: the front was taken from {taken}, expected {{'{want}'}} "
                  f"— notes: {plan.notes}")
            check(plan.notes, f"{name}: the decision should be reported")

        # The two that must DECLINE rather than guess, and say why.
        two = batch.plan_scans(batch.list_images(os.path.join(work, "fb-two")),
                               split="combined")
        check(any("too few cards" in n for n in two.notes),
              f"two cards is not evidence, and should say so: {two.notes}")
        same = batch.plan_scans(batch.list_images(os.path.join(work, "fb-same")),
                                split="combined")
        check(any("same card scanned over and over" in n for n in same.notes),
              f"one card repeated has no odd side out, and should say so: {same.notes}")

        # Told explicitly, the setting still wins.
        pinned = batch.plan_scans(batch.list_images(os.path.join(work, "fb-left")),
                                  split="combined", front="first")
        check(all(front_taken_from(c, False) == "first" for c in pinned.cards),
              "an explicit --front first should not be second-guessed")
        check(not pinned.notes, "an explicit setting needs no explaining")

        # ---------------------------------------- cards on a white bed
        #
        # A back printed in black measures 0 against a dark bed of 0 and there
        # is nothing there to separate. On something white the same border is 0
        # against 240, and it is the clearest thing on the sheet — which is the
        # only way such a back ever shows an outline.
        BW2, BH2 = 740, 1030
        pale = Image.new("RGB", (BW2, BH2), (90, 120, 150))
        ImageDraw.Draw(pale).rectangle([0, int(BH2 * .6), BW2, BH2], fill=(40, 30, 60))
        inky = Image.new("RGB", (BW2, BH2), (0, 0, 0))
        ImageDraw.Draw(inky).rectangle([22, 22, BW2 - 22, BH2 - 22],
                                       outline=(150, 130, 90), width=5)

        def sheet(bed):
            page = Image.new("RGB", (BW2 * 2 + 160, BH2 + 160), bed)
            page.paste(pale, (40, 80))
            page.paste(inky, (40 + BW2 + 80, 80))
            return page

        dark_sheet, white_sheet = sheet((0, 0, 0)), sheet((242, 243, 240))

        # The black-bordered back is found whole only on the white sheet, and
        # only there does it have edges to take an angle from. The setting is
        # given, not guessed — see BACKGROUNDS for why there is no guessing.
        for label, page, want in (("dark", dark_sheet, False), ("white", white_sheet, True)):
            bg = "light" if want else "dark"
            halves = imaging.split_regions(page, background=bg)
            check(halves is not None, f"{label} sheet: two cards should divide")
            exact, _, _, edges = imaging.straighten(halves[1], background=bg)
            close = abs(exact.size[0] - BW2) <= 20 and abs(exact.size[1] - BH2) <= 20
            check(close is want,
                  f"{label} bed: the inky back came out {exact.size} against its real "
                  f"{BW2}x{BH2} — expected {'the real size' if want else 'short'}")
            if want:
                check(edges >= 3,
                      f"on a white bed the back should have edges to measure, got {edges}")

        # Dark is still the default, so an unset batch behaves as it always did.
        check("dark" == imaging.BACKGROUNDS[0], "dark must remain the default")
        check("auto" not in imaging.BACKGROUNDS,
              "there is no auto: a white bed and a white-bordered card cropped "
              "flush are the same pixels, so guessing can only invert a batch")

        # The measurement that decided that. A yellow-bordered card scanned
        # flush reads EXACTLY like a white backing — bright, flat, brighter than
        # anything inside it — so the suggestion fires on both and neither is
        # ever acted on.
        poke = Image.new("RGB", (600, 840), (250, 210, 60))
        d = ImageDraw.Draw(poke)
        d.rectangle([45, 60, 555, 600], fill=(120, 160, 120))
        d.rectangle([45, 640, 555, 800], fill=(245, 240, 225))
        check(imaging.looks_light_bedded(white_sheet),
              "a white backing should be worth suggesting")
        check(imaging.looks_light_bedded(poke),
              "a flush yellow border looks the same as a white backing — if this "
              "ever stops being true, auto-detection becomes possible")
        check(not imaging.looks_light_bedded(dark_sheet),
              "a black bed is not worth suggesting")

        # Rotation pads with the BED's colour, or a light-bedded scan gets four
        # black corners and black is what a card looks like there.
        check(imaging._bed_fill("light") == (255, 255, 255), "light pads white")
        check(imaging._bed_fill("dark") == (0, 0, 0), "dark pads black")
        tilted = white_sheet.rotate(1.2, resample=Image.BICUBIC,
                                    fillcolor=(255, 255, 255))
        halves = imaging.split_regions(tilted, background="light")
        check(halves is not None and len(halves) == 2,
              "a tilted white-bedded sheet should still divide into two")
        _, _, found, _ = imaging.straighten(halves[1], background="light")
        check(abs(found - 1.2) < 0.25,
              f"white-bedded deskew found {found:+.2f}° where 1.20° was applied")

        # End to end, and the faces come out the size of the card.
        whitedir = os.path.join(work, "whitebed")
        os.makedirs(whitedir)
        white_sheet.save(os.path.join(whitedir, "0001.jpg"), quality=95)
        wplan = batch.plan_scans(batch.list_images(whitedir), split="combined",
                                 background="light")
        wout = os.path.join(out, "whitebed")
        done, failed, _ = batch.run(wplan, wout, start=1, background="light")
        check(done == 1 and not failed, f"white-bed run: {done} done, {failed} failed")
        for name in ("0001_1_front.jpg", "0001_2_back.jpg"):
            face = imaging.load(os.path.join(wout, name))
            check(abs(face.size[0] - BW2) <= 60 and abs(face.size[1] - BH2) <= 60,
                  f"{name} came out {face.size} against the card's {BW2}x{BH2}")

        # ------------------------------------------------ a dark border
        #
        # The angle has to come from the card, not from whichever edge the
        # artwork happens to leave visible.
        art = os.path.join(work, "fullart")
        os.makedirs(art)
        for applied in (0.0, 0.6, -1.5, 3.0):
            bed = Image.new("RGB", (1400, 1800), (0, 0, 0))
            bed.paste(make_full_art(), (200, 200))
            path = os.path.join(art, f"{applied:+.1f}.jpg")
            bed.rotate(applied, resample=Image.BICUBIC, fillcolor=(0, 0, 0))\
               .save(path, quality=95)
            _, _, found, edges = imaging.straighten(imaging.load(path))
            check(abs(found - applied) < 0.2,
                  f"dark border at {applied:+.2f}°: deskew found {found:+.2f}° "
                  f"from {edges} edge(s)")

        # No edge may claim more than MAX_SKEW on its own, however straight a
        # line the artwork happens to make.
        m = imaging._mask(imaging.load(os.path.join(art, "+0.0.jpg")))
        for side, angle in imaging.skew_sides(m).items():
            check(angle is None or abs(angle) <= imaging.MAX_SKEW,
                  f"{side} edge claimed {angle:+.2f}°, past the {imaging.MAX_SKEW}° limit")

        # An edge lying on the image boundary is not a card edge and must not
        # vote: it is a perfectly straight line at zero, and on a scan divided
        # down the middle the cut side is exactly that.
        flush = Image.new("RGB", (1000, 1400), (0, 0, 0))
        flush.paste(make_full_art(dark_border=False).resize((1000, 1400)), (0, 0))
        sides = imaging.skew_sides(imaging._mask(flush))
        check(all(a is None for a in sides.values()),
              f"a card filling the whole scan has no measurable edge: {sides}")
        _, _, found, edges = imaging.straighten(flush)
        check(found == 0.0 and edges == 0,
              f"nothing measurable should leave the scan unrotated, got {found:+.2f}°")

        # A scan divided down the middle still deskews from its real edges.
        touching = os.path.join(work, "touching-art")
        os.makedirs(touching)
        for applied in (0.0, 0.5):
            pair = Image.new("RGB", (2000 + 120, 1400 + 120), (0, 0, 0))
            pair.paste(make_full_art(), (60, 60))
            pair.paste(make_card(front=False), (60 + 1000, 60))
            path = os.path.join(touching, f"{applied:+.1f}.jpg")
            pair.rotate(applied, resample=Image.BICUBIC, fillcolor=(0, 0, 0))\
                .save(path, quality=95)
            halves = imaging.split_regions(imaging.load(path), force=True)
            check(len(halves) == 2, "touching cards should still divide when forced")
            for which, half in zip(("front", "back"), halves):
                _, _, found, _ = imaging.straighten(half)
                check(abs(found - applied) < 0.25,
                      f"touching pair at {applied:+.2f}°: {which} half deskewed "
                      f"{found:+.2f}°")

        # ------------------------------------------- carrying on a folder
        #
        # The failure here is quiet and destructive: a second batch cropped
        # into the folder holding the first is numbered from 0001 again, so
        # every name it writes is one the first batch already used.
        carry = os.path.join(work, "carry")
        check(batch.next_index(carry) == 1, "a folder that does not exist starts at 1")
        os.makedirs(carry)
        check(batch.next_index(carry) == 1, "an empty folder starts at 1")

        first = batch.pair_sequential(batch.list_images(scans))
        done, failed, _ = batch.run(first, carry)
        check(done == 2 and not failed, f"first batch: {done} done, {failed} failed")
        check(batch.next_index(carry) == 3,
              f"2 cards written, so the next is 3, not {batch.next_index(carry)}")

        # The second batch lands after the first instead of on top of it.
        done, failed, _ = batch.run(first, carry)
        check(done == 2 and not failed, f"second batch: {done} done, {failed} failed")
        written = sorted(os.listdir(carry))
        check(len(written) == 16, f"expected 16 files after two batches, got {written}")
        check(written == [n for i in (1, 2, 3, 4) for _, n in
                          batch.output_names(i, None)],
              f"the two batches do not form one continuous run: {written}")

        # start=1 is the explicit overwrite, and must leave the folder no
        # bigger than the batch that overwrote it began with.
        done, failed, _ = batch.run(first, carry, start=1)
        check(done == 2 and not failed, f"restart: {done} done, {failed} failed")
        check(len(os.listdir(carry)) == 16,
              "start=1 should have overwritten cards 1-2, not added more")

        # Continuous numbering is read back the same way, four files to a card.
        seq = os.path.join(work, "carry-seq")
        batch.run(first, seq, naming="sequence")
        check(sorted(os.listdir(seq))[-1] == "0008.jpg", "sequence batch not 1-8")
        check(batch.next_index(seq) == 3,
              f"0008.jpg is the end of card 2, so next is 3, not {batch.next_index(seq)}")
        batch.run(first, seq, naming="sequence")
        check(sorted(os.listdir(seq)) == [f"{i:04d}.jpg" for i in range(1, 17)],
              f"sequence batches do not run on: {sorted(os.listdir(seq))}")

        # A part-written card still reserves its number — rounding DOWN here
        # would hand the next batch a number that is already half used.
        part = os.path.join(work, "carry-part")
        os.makedirs(part)
        open(os.path.join(part, "0009.jpg"), "w").close()
        check(batch.next_index(part) == 4,
              f"0009.jpg is the first file of card 3, so card 3 is taken and the "
              f"next is 4 — got {batch.next_index(part)}")

        # Files the app did not write must not be read as card numbers, or a
        # stray export renumbers the batch.
        junk = os.path.join(work, "carry-junk")
        os.makedirs(junk)
        for name in ("notes.txt", "scan_front.jpg", "IMG_4021.jpg", "0007_1.jpg"):
            open(os.path.join(junk, name), "w").close()
        check(batch.next_index(junk) == 1,
              f"junk filenames were read as card numbers: {batch.next_index(junk)}")

        # A failed card is cleaned up under the number it was WRITTEN as, not
        # its position, or the cleanup deletes a different card's files.
        after = os.path.join(work, "carry-fail")
        os.makedirs(after)
        batch.run(first, after)                         # cards 1-2 land here
        before = sorted(os.listdir(after))
        done, failed, _ = batch.run(
            batch.pair_sequential(batch.list_images(broken)), after)
        check(done == 0 and failed == 1, f"expected 1 failure, got {done}/{failed}")
        check(sorted(os.listdir(after)) == before,
              "a failed card took an earlier card's files with it")

        split_checks(work, scans)

        import cardcropper.gui                   # noqa: F401  (tkinter present?)
        import cardcropper.stacks_tab            # noqa: F401
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("smoke: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
