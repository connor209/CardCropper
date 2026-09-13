"""
A run of the whole pipeline on generated scans, with no display and no real
card needed.

It exists so that the Windows build fails on the runner rather than in
somebody's hands. Freezing a broken pipeline into an .exe still produces an
.exe, and the failure then shows up as a dialog on a machine with no Python on
it, which is the worst place to find it.
"""

import os
import shutil
import sys
import tempfile

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cardcropper import batch, imaging          # noqa: E402


def make_card(front):
    """A stand-in card: a bordered rectangle, yellow face up or navy face down."""
    W, H = 1000, 1400
    card = Image.new("RGB", (W, H), (230, 200, 40) if front else (10, 10, 47))
    d = ImageDraw.Draw(card)
    d.rectangle([60, 80, W - 60, H - 160], fill=(120, 150, 90) if front else (180, 180, 190))
    if not front:
        d.ellipse([2, 2, 40, 40], fill=(230, 230, 235))     # a whitening blob
    return card


def make_scan(path, front, angle):
    """A stand-in scan: one card on a black bed, rotated off-square."""
    bed = Image.new("RGB", (1400, 1800), (0, 0, 0))
    bed.paste(make_card(front), (200, 200))
    bed.rotate(angle, resample=Image.BICUBIC, fillcolor=(0, 0, 0)).save(path, quality=95)


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
            _, _, found = imaging.straighten(im)
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
        flipped = batch.plan_scans(batch.list_images(both), front_first=False)
        check(flipped.cards[0].front.side == 1, "front_first=False was ignored")

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
            check(abs(front.size[0] - back.size[0]) <= 4
                  and abs(front.size[1] - back.size[1]) <= 4,
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

        import cardcropper.gui                   # noqa: F401  (tkinter present?)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("smoke: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
