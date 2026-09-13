"""
Pixel work for CardCropper: straighten a scan, then cut the corner and edge
crops from it.

This is a port of the geometry and sheet-building half of the card-conditioning
skill's `prep_scans.py`, with the CSV, the wear score and the network upload
taken out. Nothing here reads a spreadsheet or makes a grading judgement — it
takes two image files and writes two more.

Two things it keeps from the original because they cost an afternoon to find:

**Scans are not square.** Cards sit 0.3-0.6 degrees off in the sheet feeder.
Crop the bounding box and take its corners and you do not get the card's
corners — you get interior artwork, and it looks plausible enough to grade
from. Everything here runs after a deskew.

**Contrast enhancement manufactures wear.** Auto-contrast on the navy back
border turns JPEG noise into speckle indistinguishable from whitening. The
crops written here are straightened and magnified but NOT enhanced.

It also takes a scan that holds BOTH faces of a card at once and divides it
into two — see the `splitting` section, which explains why such a scan cannot
be recognised from its proportions and has to be recognised from the strip of
scanner bed between the cards.
"""

import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

# ---------------------------------------------------------------- constants

#: Card-vs-background is decided on the VALUE channel — max(R,G,B) — never on
#: luminance. A Pokemon back's navy border measures R0 G0 B47: luminance 5,
#: because blue carries 11% of it, against a scanner bed of 0. By brightness
#: the border and the bed are the same thing, so a luminance mask finds the
#: card's bright INTERIOR and reports the artwork as the border. Value
#: separates them cleanly (0 against 47) and works on a yellow front too.
INK_THRESHOLD = 20

#: The reference card size all crop measurements are expressed against, so one
#: set of numbers holds across scans that framed the card differently. Crops
#: are still cut at the scan's own resolution — this is only the yardstick.
CARD_W, CARD_H = 700, 980

#: How deep an edge strip is cut, in reference pixels. Comfortably thicker than
#: the outer band so there is card either side of any whitening to judge it
#: against — a strip cropped tight gives nothing to compare with.
EDGE_STRIP = 30

#: How much of a corner is cut, in reference pixels.
#:
#: Magnification comes from cropping tighter, not from drawing bigger, but
#: tighter is not automatically better: at 285 DPI a corner is only about 70
#: real pixels, so cutting small and blowing it up past native buys apparent
#: size and pays for it in mush. 140px covers the corner tip, the full border
#: either side of it, and enough interior to judge the border against.
CORNER_CROP = 140

#: How much of a corner a LISTING photo shows — wider than the grading crop on
#: purpose. A grading crop is for someone hunting flaws; a listing photo is
#: seen by a buyer who has not been told what to look for, and at grading
#: magnification paper fibre and scanner noise read as damage. Wider framing
#: keeps the corner legible as the corner of a card, with real wear still plain
#: and nothing invented.
LISTING_CROP = 175

#: How much background to keep OUTSIDE the card in a crop, in reference px.
#:
#: Cropping flush to the card's bounding box seemed obviously right and is not:
#: it puts the corner's arc hard against the tile edge, so the one thing a
#: corner crop should show — the SILHOUETTE, the outline of the card against
#: the scanner's black — is the one thing cut off. A corner that has been
#: rounded off or crushed is read from its profile as much as from whitening
#: on its face.
CROP_MARGIN = 22

#: Sheet width for the labelled grading sheet, chosen to land at the 1568px
#: an image is downscaled to before a model sees it. Four columns.
SHEET_W = 1544

#: Listing sheets are built past 1600px on the long edge because that is where
#: eBay switches zoom on, and zoom is the entire point of a condition photo.
LISTING_W = 1600

#: Sheet palette. Dark, because the subject is a near-black navy border and a
#: white surround drags the eye's adaptation the wrong way — the same crop
#: reads lighter and worn against white than it does against black.
SHEET_BG = (18, 18, 18)
TILE_BG = (32, 32, 32)
LABEL = (255, 224, 66)
TITLE = (245, 245, 245)

#: How far off square a scanned card plausibly is.
#:
#: A sheet feeder leaves 0.3-0.6 degrees and a card laid on a flatbed by hand a
#: couple more. Nothing here is a limit on what a scanner produces — it is a
#: limit on what a MEASUREMENT is allowed to claim, so that an edge the
#: detector could not actually see cannot come back as a confident 22 degrees
#: and rotate a square card into a wonky one. Past this the card is left as it
#: is and the log says so.
MAX_SKEW = 8.0
_MAX_SLOPE = math.tan(math.radians(MAX_SKEW))

#: How many lit pixels in a row mark the card's edge. One is JPEG noise.
EDGE_RUN = 5

#: How much of each end of an edge is ignored — the rounded corners.
ENDS_SKIP = 0.18

#: How far off a line a row may sit and still count as agreeing with it, and
#: how much of the edge has to agree before the line is believed at all.
EDGE_TOLERANCE = 2.0
EDGE_AGREEMENT = 0.35

#: Fewest rows an edge needs before its angle means anything.
MIN_EDGE_POINTS = 20

#: How much of an edge may lie on the image boundary before the edge is thrown
#: away as not being the card's: a card running off the scan, or the cut side
#: of a scan that was divided down the middle.
CLIPPED_EDGE = 0.2

#: Longest edge the mask is measured on when finding the angle.
#:
#: The angle is measured small and applied at full size. A flatbed scan of two
#: cards runs to 16 megapixels and the edge search over four sides of it costs
#: more than every crop taken afterwards, for precision nobody can use: one
#: pixel of quantisation over a thousand rows is 0.06 degrees, and the rotation
#: itself still happens at the scan's own resolution.
SKEW_SIZE = 1600

#: Image files the app will pick up.
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp")


def _font(size, bold=False):
    """A real font where one is installed, falling back to Pillow's bitmap."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf" % ("-Bold" if bold else ""),
        "/usr/share/fonts/truetype/liberation/LiberationSans-%s.ttf"
        % ("Bold" if bold else "Regular"),
        r"C:\Windows\Fonts\%s" % ("arialbd.ttf" if bold else "arial.ttf"),
        r"C:\Windows\Fonts\%s" % ("segoeuib.ttf" if bold else "segoeui.ttf"),
        "/System/Library/Fonts/Supplemental/Arial%s.ttf" % (" Bold" if bold else ""),
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


# ---------------------------------------------------------------- geometry


def _value(im):
    """max(R,G,B) per pixel — see INK_THRESHOLD for why not luminance."""
    return np.asarray(im.convert("RGB")).astype(int).max(axis=2)


def _mask(im):
    return _value(im) > INK_THRESHOLD


def _card_box(m):
    """
    The card's extent, found from how MUCH of each row and column is lit rather
    than from any single lit pixel. JPEG noise in the black margin clears the
    ink threshold on its own, so an any-pixel bounding box reaches well past
    the card — and then every crop is taken against background instead of the
    border.
    """
    lit_cols = m.mean(axis=0) > 0.4
    lit_rows = m.mean(axis=1) > 0.4
    xs = np.where(lit_cols)[0]
    ys = np.where(lit_rows)[0]
    if not len(xs) or not len(ys):
        return None
    return int(xs[0]), int(ys[0]), int(xs[-1]), int(ys[-1])


def _edge_profile(m):
    """
    Where the card's left edge sits on each row, over the straight middle of it.

    Returns (rows, xs). A run of EDGE_RUN lit pixels marks the edge rather than
    a single one, or JPEG noise in the bed is read as the card. The top and
    bottom ENDS_SKIP of the card are left out: those are the rounded corners,
    and including them drags any fit toward zero — the failure that makes a
    deskew look like it ran while leaving the corners unusable.
    """
    rows = np.where(m.mean(axis=1) > 0.4)[0]
    if len(rows) < 100:
        return None, None
    y0, y1 = int(rows[0]), int(rows[-1])
    skip = int((y1 - y0) * ENDS_SKIP)
    band = m[y0 + skip:y1 - skip]
    if band.shape[0] < MIN_EDGE_POINTS:
        return None, None
    # A pixel starts a run when it and the next EDGE_RUN-1 are all lit. Done
    # across the whole band at once: a per-row Python loop over four edges of a
    # 40-megapixel scan costs more than everything else in the pipeline.
    runs = band.copy()
    for k in range(1, EDGE_RUN):
        runs[:, :-k] &= band[:, k:]
    runs[:, -EDGE_RUN:] = False
    found = runs.any(axis=1)
    if found.sum() < MIN_EDGE_POINTS:
        return None, None
    ys = (np.arange(band.shape[0])[found] + y0 + skip).astype(float)
    return ys, runs.argmax(axis=1)[found].astype(float)


def _consensus_slope(ys, xs):
    """
    The slope most of an edge agrees on, ignoring the part that does not.

    A straight-line fit is the obvious thing and is what got this wrong. Where
    dark artwork reaches the card's border — a full-art card, a black cloak
    against the edge — the detector cannot see the border there and reports the
    first lit pixel INSIDE the card instead. Those points are all on one side
    of the truth and they drag a least-squares fit a long way: a card sitting
    square measured +22 degrees, and the crops were cut from a scan rotated by
    that, which looks wonky rather than wrong and passes every other check.

    So the line is chosen by how many rows agree with it, not by the average of
    all of them. Candidate lines steeper than MAX_SKEW are not considered at
    all — a card on a flatbed is off by a fraction of a degree, and treating a
    30-degree "edge" as a serious candidate is how the bad fit won.
    """
    n = len(ys)
    step = max(1, n // 60)
    best = None
    for i in range(0, n, step):
        for j in range(i + step, n, step):
            if ys[j] == ys[i]:
                continue
            slope = (xs[j] - xs[i]) / (ys[j] - ys[i])
            if abs(slope) > _MAX_SLOPE:
                continue
            agree = np.abs(xs - (slope * ys + (xs[i] - slope * ys[i]))) <= EDGE_TOLERANCE
            if best is None or agree.sum() > best[0]:
                best = (int(agree.sum()), agree)
    if best is None or best[0] < max(MIN_EDGE_POINTS, EDGE_AGREEMENT * n):
        return None
    return float(np.polyfit(ys[best[1]], xs[best[1]], 1)[0])


def _side_degrees(m):
    """One edge's angle, or None where that edge cannot be trusted."""
    ys, xs = _edge_profile(m)
    if ys is None:
        return None
    # An edge lying on the image boundary is not the card's edge: the card runs
    # off the scan, or this is the cut side of a scan divided down the middle.
    # It is a perfectly straight line at zero degrees and would vote for zero.
    if (xs <= 0).mean() > CLIPPED_EDGE:
        return None
    slope = _consensus_slope(ys, xs)
    if slope is None:
        return None
    angle = math.degrees(math.atan(slope))
    return None if abs(angle) > MAX_SKEW else angle


#: Each edge seen as a left edge, and the sign that puts its angle back into
#: the left edge's convention.
_SIDES = (("left", lambda m: m, 1),
          ("right", lambda m: m[:, ::-1], -1),
          ("top", lambda m: m.T, -1),
          ("bottom", lambda m: m.T[:, ::-1], 1))


def skew_sides(m):
    """Every edge's angle, None where it could not be measured."""
    out = {}
    for name, view, sign in _SIDES:
        angle = _side_degrees(view(m))
        out[name] = None if angle is None else sign * angle
    return out


def _skew_degrees(m):
    """
    How far the card is off square, from all four of its edges.

    The MEDIAN of the edges that could be measured, not the left edge alone.
    All four carry the same angle, so the median costs nothing when they agree
    and is what saves the result when one of them is wrong — and one of them
    being wrong is the normal case, not the exotic one: artwork reaching the
    border ruins whichever edge it touches, and a scan divided down the middle
    has a cut side that is not a card edge at all.

    Zero when no edge could be measured. Leaving a scan unrotated is a visible,
    recoverable outcome; rotating it by a confident wrong angle is not.
    """
    found = [a for a in skew_sides(m).values() if a is not None]
    return float(np.median(found)) if found else 0.0


def load(path):
    """Open an image, honouring EXIF rotation, in RGB."""
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    return im.convert("RGB")


def _small(im, longest=SKEW_SIZE):
    """A copy no bigger than `longest` on its long edge — see SKEW_SIZE."""
    if max(im.size) <= longest:
        return im
    k = longest / float(max(im.size))
    return im.resize((max(1, round(im.size[0] * k)), max(1, round(im.size[1] * k))),
                     Image.BILINEAR)


def straighten(im):
    """
    Deskew and crop to the card, at the scan's OWN resolution.

    Returns (exact, padded, angle, edges) — the card cropped flush, the same
    card with CROP_MARGIN of background kept around it for the crops to be cut
    from, and how many of the card's four edges the angle was agreed on by.
    Zero edges means the angle could not be measured and the scan was left as
    it is, which is worth saying out loud: a card that is visibly crooked and
    was not straightened otherwise looks like the deskew is broken.
    Normalising to a fixed size here would put a resample between the scan and
    every crop taken from it, and each interpolation costs a little edge
    definition on exactly the fine detail the crop exists to show.
    """
    measured = [a for a in skew_sides(_mask(_small(im))).values() if a is not None]
    angle = float(np.median(measured)) if measured else 0.0
    rotated = im.rotate(-angle, resample=Image.BICUBIC, expand=True, fillcolor=(0, 0, 0))
    box = _card_box(_mask(rotated))
    if box is None:
        return rotated, rotated, angle, len(measured)
    x0, y0, x1, y1 = box
    exact = rotated.crop((x0, y0, x1 + 1, y1 + 1))
    m = round(CROP_MARGIN * exact.size[0] / CARD_W)
    padded = rotated.crop((x0 - m, y0 - m, x1 + 1 + m, y1 + 1 + m))
    return exact, padded, angle, len(measured)


def detection_ok(exact, source):
    """
    Whether the card outline was plausibly found.

    A scan where detection missed still produces a sheet — of the wrong pixels,
    confidently laid out. Cheaper to say so than to let someone list from it.
    Two things give it away: a card that fills almost none, or almost all, of
    the scan, and a card whose aspect ratio is nowhere near a trading card's
    2.5 x 3.5.
    """
    cw, ch = exact.size
    sw, sh = source.size
    if not cw or not ch or not sw or not sh:
        return False, "empty image"
    area = (cw * ch) / float(sw * sh)
    if area < 0.05:
        return False, "card outline covers <5% of the scan — detection missed"
    ratio = max(cw, ch) / float(min(cw, ch))
    if not 1.15 <= ratio <= 1.75:
        return False, f"detected shape is {ratio:.2f}:1, not card-shaped (~1.40:1)"
    return True, ""


# ---------------------------------------------------------------- splitting

#: A scan can hold BOTH faces of one card side by side, which is what a flatbed
#: gives you when you lay the card down, flip it, and scan the sheet once.
#:
#: Such a scan cannot be told from a single-card scan by its proportions, and
#: that is the whole reason this code exists. Two portrait cards side by side
#: measure 5.0 x 3.5 — a 1.43:1 rectangle — and one card measures 2.5 x 3.5,
#: which is 1.40:1 the other way up. `detection_ok` accepts both, so a combined
#: scan run as a single card passes every check and yields four "corners" that
#: are the outer corners of the PAIR: two real, two interior artwork. It looks
#: like a working run. What separates the two cases is not the outline but the
#: strip of scanner bed BETWEEN the cards, so that is what is looked for.

#: How much of the axis each card must span for a division to be believed.
#: Anything shorter is dust, a scanner lid edge, or the card's own interior.
SPLIT_MIN_SPAN = 0.15

#: And how wide the background strip between them must be, as a fraction of the
#: axis. Two cards laid down by hand never touch along their whole length; a
#: few pixels of bed is all this needs, and asking for more would refuse the
#: scans where they were laid close.
SPLIT_MIN_GAP = 0.003

#: Longest edge a scan is decoded to when `probe` is only deciding how many
#: cards are on it. Full-resolution decoding of a folder of scans to plan the
#: batch costs more than the whole crop run afterwards, and the gap between two
#: cards survives the downscale — it is the one feature this needs.
PROBE_SIZE = 900


def _runs(profile, threshold=0.4):
    """Contiguous runs of `profile > threshold`, as inclusive (start, end)."""
    lit = profile > threshold
    runs, start = [], None
    for i, on in enumerate(lit):
        if on and start is None:
            start = i
        elif not on and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(lit) - 1))
    return runs


def _division(m, axis):
    """
    Where a mask divides into exactly two cards along `axis` (0 for a vertical
    cut between side-by-side cards, 1 for a horizontal one between stacked
    cards), or None.

    Measured on how MUCH of each column is lit, for the same reason `_card_box`
    is: JPEG noise in the black bed clears the ink threshold by itself, so a
    gap defined as "no lit pixel at all" is never found on a real scan.
    """
    profile = m.mean(axis=axis)
    n = len(profile)
    runs = [r for r in _runs(profile) if r[1] - r[0] + 1 >= n * SPLIT_MIN_SPAN]
    if len(runs) != 2:
        return None
    if runs[1][0] - runs[0][1] - 1 < max(2, n * SPLIT_MIN_GAP):
        return None
    return (runs[0][1] + runs[1][0]) // 2


def _card_shaped(m):
    """Whether a mask's extent is roughly a trading card's 2.5 x 3.5."""
    box = _card_box(m)
    if box is None:
        return False
    x0, y0, x1, y1 = box
    w, h = x1 - x0 + 1, y1 - y0 + 1
    if w < 40 or h < 40:
        return False
    return 1.15 <= max(w, h) / float(min(w, h)) <= 1.75


def _cut_at(im, cut, vertical):
    """The scan in two, each half keeping the bed around its own card."""
    w, h = im.size
    if vertical:
        return [im.crop((0, 0, cut, h)), im.crop((cut, 0, w, h))]
    return [im.crop((0, 0, w, cut)), im.crop((0, cut, w, h))]


def split_regions(im, force=False):
    """
    A scan holding two cards, cut into two scans of one card each, in reading
    order — left then right, or top then bottom. None where it holds one card.

    The cut is taken at the MIDDLE of the gap rather than at the edge of either
    card, and each half keeps the full width of the other axis. Both matter:
    the lit-fraction boundaries fall slightly inside a tilted card (its extreme
    corner columns are mostly bed), so cutting there would shave the very
    corners these crops exist to show. Splitting down the middle of the bed
    cannot cost a card pixel, and the deskew that follows on each half is the
    same one a separately scanned card gets.

    `force` divides a scan the caller KNOWS holds two cards but on which no gap
    could be found — cards laid touching. Halving the detected content is a
    guess, so it is never made on the app's own initiative.
    """
    m = _mask(im)
    for axis in (0, 1):
        cut = _division(m, axis)
        if cut is None:
            continue
        vertical = axis == 0
        halves = (m[:, :cut], m[:, cut:]) if vertical else (m[:cut], m[cut:])
        if all(_card_shaped(half) for half in halves):
            return _cut_at(im, cut, vertical)
    if not force:
        return None
    box = _card_box(m)
    w, h = im.size
    x0, y0, x1, y1 = box if box else (0, 0, w - 1, h - 1)
    if (x1 - x0) >= (y1 - y0):
        return _cut_at(im, (x0 + x1) // 2, True)
    return _cut_at(im, (y0 + y1) // 2, False)


def divided_ok(regions):
    """
    Whether a scan divided WITHOUT a gap to go on came out as two cards.

    Dividing at the middle of the detected content is a guess, but not a wild
    one: both halves of a combined scan are the same card, so the middle is the
    seam whenever the cards are the same size — which trading cards are. Rather
    than warn every time the guess is made, check it: two card-shaped halves
    mean it landed, and only a half that is not card-shaped is worth saying
    anything about.
    """
    for i, region in enumerate(regions, 1):
        if not _card_shaped(_mask(region)):
            return False, f"half {i} of {len(regions)} is not card-shaped"
    return True, ""


def probe(path):
    """
    Whether the scan at `path` holds two cards, decided from a small decode.

    Planning a batch means answering this for every file before any cropping
    starts, and answering it from full-resolution pixels would mean decoding
    the whole folder twice. JPEG's DCT scaling gives the reduced image almost
    free, and the question — is there a strip of bed through the middle — is
    one a 900px copy answers as well as the original.
    """
    with Image.open(path) as raw:
        raw.draft("RGB", (PROBE_SIZE, PROBE_SIZE))
        im = ImageOps.exif_transpose(raw).convert("RGB")
    im.thumbnail((PROBE_SIZE, PROBE_SIZE), Image.BILINEAR)
    return split_regions(im) is not None


# ---------------------------------------------------------------- sheets


def _crisp(im):
    """
    A light unsharp mask after upscaling.

    Interpolation is a weighted average, so it necessarily softens the edge
    between a white chip and navy card — the boundary these crops exist to
    show. This restores the acutance that enlarging removed; it is not adding
    detail that was not there, and the radius is kept small and the threshold
    non-zero so flat navy (where JPEG noise lives) is left alone rather than
    being crunched into speckle that looks like whitening.
    """
    return im.filter(ImageFilter.UnsharpMask(radius=1.4, percent=85, threshold=3))


def _cuts(padded):
    """The three lengths every crop below is measured in, for this scan."""
    w = padded.size[0]
    scale = w / (CARD_W + 2 * CROP_MARGIN)
    m = round(CROP_MARGIN * scale)
    return m, round(CORNER_CROP * scale) + m, round(EDGE_STRIP * scale) + m // 3


def corner_crops(padded, cut):
    """The four corners, each carrying the card's outline against the black."""
    w, h = padded.size
    return [("TOP-LEFT", padded.crop((0, 0, cut, cut))),
            ("TOP-RIGHT", padded.crop((w - cut, 0, w, cut))),
            ("BOTTOM-LEFT", padded.crop((0, h - cut, cut, h))),
            ("BOTTOM-RIGHT", padded.crop((w - cut, h - cut, w, h)))]


def edge_crops(padded, cut, strip, margin, split=True):
    """
    Each of the four edges, rotated so they all read left-to-right.

    `split` cuts each edge in half. A half is drawn at twice the length a whole
    edge would get, and is still long enough to judge whether wear runs
    continuously — which is the question an edge crop exists to answer, and one
    no corner crop can answer, because "runs the length of the edge" is a
    judgement about the whole edge rather than about any point on it.

    Whole edges are for the listing photo, where the sheet is wide enough to
    give a full edge real length and a buyer should not have to reassemble one
    from two tiles to see that it is clean.
    """
    w, h = padded.size
    off = margin - margin // 3           # skip the margin the strips do not use

    def cut_up(name, img):
        if not split:
            return [(name, img)]
        half = img.size[0] // 2
        return [(f"{name} 1/2", img.crop((0, 0, half, img.size[1]))),
                (f"{name} 2/2", img.crop((half, 0, img.size[0], img.size[1])))]

    return (cut_up("TOP", padded.crop((cut, off, w - cut, off + strip)))
            + cut_up("BOTTOM", padded.crop((cut, h - off - strip, w - cut, h - off))
                     .transpose(Image.FLIP_TOP_BOTTOM))
            + cut_up("LEFT", padded.crop((off, cut, off + strip, h - cut))
                     .transpose(Image.ROTATE_270))
            + cut_up("RIGHT", padded.crop((w - off - strip, cut, w - off, h - cut))
                     .transpose(Image.ROTATE_90)))


def _tile(img, w, h, label, font, fill=False, caption=True):
    """
    One crop on its own dark ground, optionally captioned beneath.

    `fill` stretches to the whole tile rather than fitting proportionally. That
    is right for an EDGE, where the strip is long and shallow and the extra
    height is free magnification across the only axis that carries wear depth;
    a corner keeps its proportions, since its shape is part of what is read.
    """
    tile = Image.new("RGB", (w, h), TILE_BG)
    pad_b = 26 if caption else 8
    iw, ih = w - 10, h - pad_b
    if fill:
        size = (iw, ih)
    else:
        k = min(iw / img.size[0], ih / img.size[1])
        size = (max(1, round(img.size[0] * k)), max(1, round(img.size[1] * k)))
    fit = _crisp(img.resize(size, Image.LANCZOS))
    tile.paste(fit, ((w - size[0]) // 2, (h - pad_b - size[1]) // 2 + 4))
    if caption:
        d = ImageDraw.Draw(tile)
        d.text(((w - d.textlength(label, font=font)) / 2, h - 21), label,
               fill=LABEL, font=font)
    return tile


def grading_sheet(padded, path, face="BACK"):
    """
    The card-conditioning skill's own sheet: four corners across the top, then
    every edge in halves, captioned in yellow under a title.

    Built for a grader looking for flaws, not for a buyer — the captions and
    the title read as a screenshot of somebody's tooling on a listing.
    """
    margin, cut, strip = _cuts(padded)
    corners = corner_crops(padded, cut)
    edges = edge_crops(padded, cut, strip, margin)

    col = SHEET_W // 4
    title_h, corner_h, edge_h = 52, 412, 168
    sheet = Image.new("RGB", (SHEET_W, title_h + corner_h + edge_h * 2), SHEET_BG)
    d = ImageDraw.Draw(sheet)
    d.text((14, 13), f"{face} — corner & edge crops", fill=TITLE, font=_font(26, True))

    small = _font(15, True)
    for i, (name, img) in enumerate(corners):
        sheet.paste(_tile(img, col, corner_h, name, small), (i * col, title_h))
    for i, (name, img) in enumerate(edges):
        x, y = (i % 4) * col, title_h + corner_h + (i // 4) * edge_h
        sheet.paste(_tile(img, col, edge_h, name, small, fill=True), (x, y))
    sheet.save(path, quality=96, subsampling=0)
    return sheet.size


def listing_sheet(padded, path, face="BACK"):
    """
    A clean corners-and-edges photo for the listing itself: no yellow captions,
    no tooling title, wider corner framing.

    Two things the layout has to avoid, both of which the first version did:

    **Four corners butted together read as one card with a seam through it** —
    confusing on a listing, and redundant beside the full scan already in the
    photo set. Generous gutters keep them reading as four separate details.

    **An edge squeezed into a column is not an edge.** Edges go full width and
    whole, one per row, because their whole point is length: a buyer should be
    able to run their eye along the edge, not reassemble it from two tiles.
    They are stretched vertically, which is free magnification on the only axis
    that carries wear depth.

    The case for putting one on a listing at all: a described flaw tells a
    buyer there is corner wear; a zoomable photograph shows it, and a buyer who
    has seen it cannot credibly say the card was not as described.
    """
    margin, cut, strip = _cuts(padded)
    w, h = padded.size
    cw = round(LISTING_CROP * w / (CARD_W + 2 * CROP_MARGIN))
    corners = [padded.crop((0, 0, cw, cw)),
               padded.crop((w - cw, 0, w, cw)),
               padded.crop((0, h - cw, cw, h)),
               padded.crop((w - cw, h - cw, w, h))]
    edges = edge_crops(padded, cut, strip, margin, split=False)

    gutter, edge_h, cap_h = 34, 132, 56
    cell = (LISTING_W - gutter * 3) // 2
    full = LISTING_W - gutter * 2
    height = gutter + cell * 2 + gutter + gutter + (edge_h + gutter) * len(edges) + cap_h
    sheet = Image.new("RGB", (LISTING_W, height), SHEET_BG)

    for i, img in enumerate(corners):
        tile = _crisp(img.resize((cell, cell), Image.LANCZOS))
        sheet.paste(tile, (gutter + (i % 2) * (cell + gutter),
                           gutter + (i // 2) * (cell + gutter)))
    top = gutter + cell * 2 + gutter * 2
    for i, (_, img) in enumerate(edges):
        tile = _crisp(img.resize((full, edge_h), Image.LANCZOS))
        sheet.paste(tile, (gutter, top + i * (edge_h + gutter)))

    d = ImageDraw.Draw(sheet)
    d.text((gutter, height - cap_h + 12),
           f"Corner & edge detail — {face.lower()} of the actual card you will receive",
           fill=(190, 190, 190), font=_font(26))
    sheet.save(path, quality=92)
    return sheet.size


#: Sheet styles offered in the UI. The value is what actually draws it.
STYLES = {
    "grading": grading_sheet,
    "listing": listing_sheet,
}
