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

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cardcropper import batch, imaging, stacks  # noqa: E402


def make_scan(path, front, angle):
    """A stand-in scan: a bordered card on a black bed, rotated off-square."""
    W, H = 1000, 1400
    card = Image.new("RGB", (W, H), (230, 200, 40) if front else (10, 10, 47))
    d = ImageDraw.Draw(card)
    d.rectangle([60, 80, W - 60, H - 160], fill=(120, 150, 90) if front else (180, 180, 190))
    if not front:
        d.ellipse([2, 2, 40, 40], fill=(230, 230, 235))     # a whitening blob
    bed = Image.new("RGB", (1400, 1800), (0, 0, 0))
    bed.paste(card, (200, 200))
    bed.rotate(angle, resample=Image.BICUBIC, fillcolor=(0, 0, 0)).save(path, quality=95)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def split_checks(work, scans):
    """
    Splitting a long run into stack folders. It MOVES the scans, so what it
    has to get right is that every card lands whole in the stack it belongs
    to, that nothing moves when it cannot all move, and that it can be undone.
    """
    run = os.path.join(work, "run")
    os.makedirs(run)
    # Ten cards, twenty scans, from the four generated ones.
    sources = batch.list_images(scans)
    for i in range(1, 21):
        shutil.copyfile(sources[(i - 1) % 4], os.path.join(run, f"scan{i}.jpg"))
    shutil.copyfile(sources[0], os.path.join(run, "scan21.jpg"))    # an orphan

    plan = stacks.plan_stacks(batch.list_images(run), per_stack=3)
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

    # Something in the way means nothing moves at all.
    os.makedirs(os.path.join(run, "Stack 02"))
    with open(os.path.join(run, "Stack 02", "scan7.jpg"), "w") as fh:
        fh.write("in the way")
    check(stacks.problems(plan, run), "a clash should be reported")
    try:
        stacks.apply(plan, run)
        check(False, "apply should refuse a clash")
    except ValueError:
        pass
    check(len(batch.list_images(run)) == 21, "a refused split moved files")
    shutil.rmtree(os.path.join(run, "Stack 02"))

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
        check([(os.path.basename(c.front), os.path.basename(c.back)) for c in refiled.cards]
              == [(os.path.basename(c.front), os.path.basename(c.back))
                  for c in plan.stack(k)],
              f"{name} does not pair into the cards it was cut from")
    check([os.path.basename(p) for p in batch.list_images(run)] == ["scan21.jpg"],
          "only the orphan should be left behind")

    stuck = stacks.undo(first + moves)
    check(not stuck and len(batch.list_images(run)) == 21,
          "undo should put every scan back")
    check(not [d for d in os.listdir(run) if os.path.isdir(os.path.join(run, d))],
          "undo should remove the emptied stack folders")

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

        split_checks(work, scans)

        import cardcropper.gui                   # noqa: F401  (tkinter present?)
        import cardcropper.stacks_tab            # noqa: F401
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("smoke: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
