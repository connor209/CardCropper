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

from cardcropper import batch, centering, grading, imaging   # noqa: E402


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


def make_bordered(borders, angle, front=True):
    """
    A card with a border of known widths (left, right, top, bottom, px), on a
    black bed, rotated, blurred like a scanner and saved through JPEG — so the
    centering measurement is checked against an answer, not against itself.
    """
    import io
    from PIL import ImageFilter
    W, H = 744, 1039                                    # 63 x 88 mm at 300 DPI
    L, R, T, B = borders
    card = Image.new("RGB", (W, H), (235, 205, 50) if front else (15, 30, 110))
    d = ImageDraw.Draw(card)
    d.rectangle([L, T, W - 1 - R, H - 1 - B], fill=(190, 190, 200) if front else (50, 100, 210))
    d.rectangle([L + 40, T + 60, W - 1 - R - 40, T + 400], fill=(60, 120, 50))
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, W - 1, H - 1], radius=30, fill=255)
    bed = Image.new("RGB", (W + 300, H + 300), (3, 3, 3))
    bed.paste(card, (150, 150), mask)
    bed = bed.rotate(angle, resample=Image.BICUBIC, fillcolor=(3, 3, 3))
    bed = bed.filter(ImageFilter.GaussianBlur(1.2))
    buf = io.BytesIO()
    bed.save(buf, "JPEG", quality=88)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def check_centering(work):
    """Centering is a measurement: check it against borders of known width."""
    cases = [((36, 36, 36, 36), 0.0, True),
             ((40, 31, 45, 30), 0.5, True),
             ((48, 26, 33, 40), -0.4, False)]
    for borders, angle, front in cases:
        m = centering.measure(make_bordered(borders, angle, front))
        L, R, T, B = borders
        want_lr = 100.0 * max(L, R) / (L + R)
        want_tb = 100.0 * max(T, B) / (T + B)
        # Half a point of ratio is ~0.35 px on a 36 px border at 300 DPI.
        check(abs(m.lr[0] - want_lr) < 0.5 and abs(m.tb[0] - want_tb) < 0.5,
              f"centering {borders} at {angle:+.1f}°: measured {m.summary()}, "
              f"wanted {want_lr:.1f} L/R, {want_tb:.1f} T/B")
        check(not m.notes, f"centering {borders}: unexpected notes {m.notes}")
        # The card's own width is the ruler: 744 px across 63 mm is 300 DPI.
        check(abs(m.dpi - 300) < 3, f"centering: read {m.dpi:.0f} DPI, not 300")
        # Which side is wider has to be the right way round, or a card
        # shifted left reads as shifted right.
        if L != R:
            check(m.lr[1] == ("first" if L > R else "second"), "L/R reversed")

    # A photo rather than a scan: the same card on a white table with a slab
    # label above it, and as a cut-out with semi-transparent slab plastic.
    # The card has to be picked out (not the label, not the whole photo)
    # before anything is measured.
    import numpy as np
    card = make_bordered((40, 31, 45, 30), 0.0)
    m0 = centering.measure(card)
    _, padded, _ = imaging.straighten(card)
    photo = Image.new("RGB", (padded.size[0] + 200, padded.size[1] + 500), (245, 245, 242))
    ImageDraw.Draw(photo).rectangle([100, 40, photo.size[0] - 100, 240], fill=(40, 90, 200))
    photo.paste(padded.crop((22, 22, padded.size[0] - 22, padded.size[1] - 22)), (122, 342))
    a = np.asarray(photo).copy()
    a[(a.max(axis=2) < 12)] = (245, 245, 242)          # the bed becomes the table
    on_table = Image.fromarray(a)
    for name, img in (("photo", on_table),
                      ("cut-out", Image.merge("RGBA", (*on_table.split(), Image.fromarray(
                          np.where(np.asarray(on_table).min(axis=2) > 240, 70, 255)
                          .astype(np.uint8)))))):
        m = centering.measure(img)
        check(abs(m.lr[0] - m0.lr[0]) < 1.0 and abs(m.tb[0] - m0.tb[0]) < 1.0,
              f"{name}: measured {m.summary()}, the scan reads {m0.summary()}")

    # A black border on a black scanner bed, as a real Magic scan comes out:
    # border and bed both clipped to 0,0,0, the card's edge surviving only as
    # a faint line on the two sides the scanner light rakes across. The
    # hidden edges are placed from the card's size at the scan's DPI.
    def black_scan(borders, glint):
        W, H = 744, 1039
        L, R, T, B = borders
        bed = Image.new("RGB", (W + 40, H + 40), (0, 0, 0))
        d = ImageDraw.Draw(bed)
        d.rectangle([20 + L, 20 + T, 20 + W - 1 - R, 20 + H - 1 - B], fill=(120, 90, 60))
        if glint:
            d.line([(20 + W - 1, 20), (20 + W - 1, 20 + H - 1)], fill=(45, 45, 45), width=2)
            d.line([(20, 20 + H - 1), (20 + W - 1, 20 + H - 1)], fill=(45, 45, 45), width=2)
        bed.info["dpi"] = (300, 300)
        return bed
    m = centering.measure(black_scan((40, 30, 45, 35), True))
    check(m.trusted, f"black-on-black with edge lines should measure: {m.notes}")
    check(abs(m.lr[0] - 57.1) < 1.0 and abs(m.tb[0] - 56.3) < 1.0,
          f"black-on-black measured {m.summary()}, wanted 57.1 L/R, 56.3 T/B")
    blind = centering.measure(black_scan((40, 30, 45, 35), False))
    check(not blind.trusted and not centering.report(blind, blind)["trusted"],
          "black-on-black with NO edge visible must not be graded from")

    # Grades follow the thresholds: a perfectly centred card makes every
    # company's top centering grade, and one at 75/25 makes none of them.
    perfect = grading.best_grades(50, 50, 50, 50)
    check(perfect and all(g["grade"] for g in perfect),
          f"50/50 should make every grader's top grade: {perfect}")
    for g in perfect:
        top = grading.GRADERS[g["company"]].steps[0]
        check(g["grade"] == top.grade, f"{g['company']}: 50/50 gave {g['label']}")
    poor = {g["company"]: g for g in grading.best_grades(75, 50, 50, 50)}
    check(poor["PSA"]["grade"] not in ("10", "9"), "75/25 front should not make a PSA 9")

    # A sheet draws, and the batch writes one per card plus a CSV.
    m = centering.measure(make_bordered((40, 31, 45, 30), 0.5))
    path = os.path.join(work, "centering.jpg")
    centering.sheet(m, m, path, title="smoke")
    check(os.path.getsize(path) > 10_000, "centering sheet is suspiciously small")


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

        check_centering(work)

        # The batch's centering option: a sheet per card in a SUBFOLDER (the
        # top level stays four images per card) and one CSV for the batch.
        measured = os.path.join(work, "measured")
        done, failed, _ = batch.run(plan, measured, measure=True)
        check(done == 2 and failed == 0, "centering batch failed")
        top = sorted(os.listdir(measured))
        check(len([n for n in top if n.endswith(".jpg")]) == 8,
              f"centering added images to the top level: {top}")
        check(batch.CENTERING_CSV in top, "no centering.csv written")
        check(len(os.listdir(os.path.join(measured, batch.CENTERING_DIR))) == 2,
              "expected one centering sheet per card")

        import cardcropper.gui                   # noqa: F401  (tkinter present?)
        import cardcropper.centering_gui         # noqa: F401
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("smoke: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
