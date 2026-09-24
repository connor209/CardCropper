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
#:
#: The most a pixel may measure and still be called background. A CEILING, not
#: the figure used: the bar that actually separates card from bed is read off
#: each scan. A modern near-black back measures 12 against a bed of 0, so a
#: fixed 20 calls the card background — and it does not fail loudly, it trims
#: whatever part of the card happens to be dark. A Lorcana card's black lower
#: band came off at 60% of its height, cut clean through the artwork.
INK_THRESHOLD = 20

#: How far from the bed a pixel has to sit to count as card — none, and that
#: is deliberate. The bed level below is already the 98th percentile of the
#: bed, so it carries its own headroom for noise: two percent of the bed
#: clears it, against the twelve percent of a row that has to clear it before
#: the row counts as card at all. Adding a margin on top of that counts the
#: same noise twice, and it costs real cards.
#:
#: Measured on a document scanner, which lays cards on a backing that reads as
#: EXACTLY zero — every pixel, no noise at all. A Lorcana back's black border
#: on that backing has a third of its pixels nonzero. That is a clean
#: separation and a margin of 4 stepped straight over it: the card came out
#: 693x987 against a true 724x988, and the crops showed the corners of the
#: frame line printed inside it. At zero it comes out 723x992.
BED_MARGIN = 0

#: How much of the scan's outer border is taken to be bed when measuring it.
BED_RING = 0.02

#: How wide a sensor line can be, in pixels, and still be told apart from a
#: card. Dust on a sheet feeder's sensor draws a line the length of every page:
#: a column a couple of pixels wide, lit from the top of the scan to the
#: bottom. A card is hundreds of pixels wide, so anything the scan's top and
#: bottom borders BOTH show as a narrow bright ridge is the sensor, not paper.
SENSOR_LINE_MAX = 12

#: How far JPEG smears a sensor line into the columns either side of it.
LINE_SMEAR = 4

#: The least a stretch of rows has to read above the bed before it is taken
#: for the feeder's backing rather than noise, and the most it can read and
#: still be backing rather than card. Once a card has passed, a sheet feeder
#: shows its backing for the rest of the page, and that reads brighter than the
#: gap before the card — above the bar, so the card's box ran on down to the
#: end of the page. It spans the scan edge to edge, which a card does not.
BACKING_MIN = 4
BACKING_MAX = 60

#: Which way round a scan is: bed darker than the cards, or lighter.
#:
#: Everything here was written for a dark bed, and for a card with any ink on
#: it that is the right way round. It stops being right on a back printed in
#: black: a Lorcana back's border measures 0 against a bed of 0, the same
#: NUMBER, and no threshold splits a number from itself. Scanned on something
#: white the same border is 0 against 240 and the card's outline is the
#: clearest thing on the sheet.
#:
#: Chosen, not detected, and dark by default. This was measured before it was
#: decided: a white backing sheet reads as a bright, flat border, and so does a
#: yellow-bordered Pokemon card scanned flush to its edges — ring median 250,
#: spread 0, brighter than anything inside it, on BOTH. There is no statistic
#: that separates a white bed from a white border, because there is nothing in
#: the pixels to separate; the difference is which side of the edge the paper
#: belongs to, and the scan does not record that.
#:
#: So there is no `auto`. Guessing would silently inverting every judgement the
#: app makes on a whole batch of flush-cropped light-bordered cards, to save
#: one click on the batches that actually are light-bedded.
BACKGROUNDS = ("dark", "light")

#: How bright and how UNIFORM a border has to read before the app will SUGGEST
#: the light setting — never act on it. See `looks_light_bedded`.
LIGHT_BED_LEVEL = 140
BED_UNIFORMITY = 12

#: The range a learned light-bed threshold is held to. There is no historical
#: fixed value to cap it against, as there is for a dark bed, so it is simply
#: kept somewhere sane.
LIGHT_LIMITS = (60, 250)

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

#: How far either side of the located outline the card's true edge is measured
#: for, as a fraction of the card's own width.
#:
#: A scanner does not render a card as a step from bed to border. Light bleeds
#: sideways under the platen and the sensor smears in its direction of travel,
#: so a card sits in a halo that fades out over tens of pixels — and a halo
#: above the bar is, to any bar, card.
#:
#: Which makes this the other half of reading the threshold off each scan.
#: That bar has to sit low to see a near-black card whole (see INK_THRESHOLD),
#: and low is exactly where the halo lives: on the generated scans here a
#: bright front overshot by 13 reference px with no halo at all, and by 73
#: with a wide one. The bar cannot be both low enough for a black border and
#: high enough to keep a halo out, because those are the same number.
#:
#: So it is not asked to be. The threshold LOCATES the card; each of the four
#: sides is then measured against the brightness profile across it, which does
#: not care where the bar was set.
EDGE_SEARCH_OUT = 0.07
EDGE_SEARCH_IN = 0.05

#: Rows (or columns) sampled to build the profile one edge is measured on, as
#: a fraction to discard at each end. Rounded corners and any residual tilt
#: live at the ends; the middle half of an edge is the part that is straight.
EDGE_BAND = 0.25

#: How many pixels of bed must sit outside a side before it is worth
#: measuring. Below this there is no background left to read a level against —
#: the card runs off the scan, or butts against the next card on the sheet —
#: and the located outline is kept. See `clipped_edges` and `split_regions`
#: for the two ways a side ends up with no bed outside it.
EDGE_BED_MIN = 12

#: How steep a climb, in value per pixel, is always steep enough to be an edge.
#:
#: Steepness is otherwise judged against the steepest climb in the window, and
#: on a navy back that is the WRONG one: the border reads about 47, the swirl
#: just inside it several times that, so the step from border to swirl is the
#: tallest thing in reach and the card's own edge — bed to border, 0 to 47 —
#: falls short of it. The measurement then either landed on the swirl, cutting
#: the border off, or found nothing in its window and kept a box still wearing
#: its halo. A halo climbs a few values per pixel at its steepest; paper, well
#: over ten.
EDGE_STEP_MIN = 8.0

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

#: How much of a row or column has to be lit before it is certainly card, and
#: the looser pair of tests that carry that out to the card's real edge: either
#: still meaningfully lit, or barely lit but with its lit pixels REACHING right
#: across the card. The second is what sees a near-black back, where a column
#: is a few gold lines at the top and the bottom and almost nothing between.
STRONG_LIT = 0.4
WEAK_LIT = 0.12
WEAK_REACH = 0.5

#: How much of an edge may lie on the image boundary before the edge is thrown
#: away as not being the card's: a card running off the scan, or the cut side
#: of a scan that was divided down the middle.
CLIPPED_EDGE = 0.2

#: How many edges have to agree before the scan is rotated at all.
#:
#: Two, because one is not a consensus and the whole reason for reading four
#: edges is that any one of them can be lying. Measured on a scan cropped flush
#: to the cards, where three sides are the scan's own boundary and the fourth
#: is the seam against the next card: the one edge that got through reported
#: 0.23 degrees, the card was rotated by it, and the fill that rotation brought
#: in became a hard straight edge running through the corner crops. There was
#: nothing to gain either — a card that fills its scan has no room to be
#: straightened into.
MIN_SKEW_EDGES = 2

#: And how far off square is worth a resample at all. Rotating is interpolation
#: and interpolation softens exactly the fine detail these crops exist to show,
#: so a hundredth of a degree is not worth paying for.
MIN_SKEW_ANGLE = 0.05

#: How far a blind division may be nudged to land on the seam, in COLUMNS, and
#: how much emptier the seam has to be than where the division would otherwise
#: have fallen.
#:
#: Four, and no more. Halving a pair is only ever wrong by a column or two —
#: rounding, and whatever one card is clipped by — so a wide search is not
#: buying accuracy, it is buying the chance to wander. It did: at fourteen
#: columns a scan whose second card is dark found an emptier column INSIDE that
#: card and moved nine columns onto it, taking eight columns of the second card
#: into the first half. The seam is next door or it is not there.
#:
#: Dividing a touching pair down the middle is right to within a column, and a
#: column is what it costs: the last column of the first card arrives at the
#: inner edge of the second half, where the edge crop magnifies it. The seam is
#: usually visible even when it is not empty enough to have been found as a
#: gap — measured across a batch of these, the seam column runs 0.20 to 0.49
#: lit where the card either side of it runs 0.79 to 0.96. That is a plain
#: local minimum; it is simply above the bar that decides where a card stops.
#: So the middle is where to LOOK, and the emptiest column near it is where to
#: cut.
SEAM_SEARCH = 4
SEAM_DROP = 0.15

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
    """
    max(R,G,B) per pixel — see INK_THRESHOLD for why not luminance.

    Left as bytes. Widening to the platform int first is the obvious way to
    write this and allocates 360MB for a flatbed scan of two cards, to hold
    numbers that never exceed 255 and are only ever compared against a
    threshold.
    """
    return np.asarray(im.convert("RGB")).max(axis=2)


def _border(value):
    """The four sides of the scan's outer ring, which are usually bed."""
    h, w = value.shape
    ry, rx = max(1, int(h * BED_RING)), max(1, int(w * BED_RING))
    return (value[:ry], value[-ry:], value[:, :rx], value[:, -rx:])


def _bed_level(value):
    """
    How bright the scanner bed reads on THIS scan, from its outer border.

    The MINIMUM across the four sides rather than a figure mixing them: on a
    scan divided in two, one of the four sides is the cut through the middle of
    a card, and any statistic that includes it reports a card as the bed.
    """
    return min(float(np.percentile(side, 98)) for side in _border(value))


def _bed_floor(value):
    """
    The same for a light bed: how dark the BACKGROUND gets, not the card.

    Mirrored end to end. The darkest of the bed rather than the brightest, and
    the maximum across the sides rather than the minimum, because on a light
    bed it is a card that drags a side DOWN.
    """
    return max(float(np.percentile(side, 2)) for side in _border(value))


def looks_light_bedded(im):
    """
    Whether this scan's border COULD be a light background.

    Bright and flat, which a white backing sheet is — and which a
    white-bordered card cropped flush to its edges also is, indistinguishably.
    So this is only ever used to suggest the setting to someone whose detection
    has already failed, never to choose it for them.
    """
    ring = np.concatenate([side.ravel() for side in _border(_value(im))])
    spread = np.percentile(ring, 75) - np.percentile(ring, 25)
    return bool(np.median(ring) >= LIGHT_BED_LEVEL and spread <= BED_UNIFORMITY)


def _ridges(level):
    """How far each position stands above its neighbourhood, where that
    neighbourhood is wider than a sensor line — so only narrow ridges remain."""
    n = len(level)
    k = SENSOR_LINE_MAX * 2 + 1
    if n < k * 2:
        return np.zeros(n)
    padded = np.pad(level, k // 2, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, k)
    return np.clip(level - np.median(windows, axis=1), 0, None)


def _backing(level, base):
    """
    How far rows read above the bed where they are the feeder's backing: a
    stretch running in from one end of the scan, reading above the bed but
    too dim to be card. Zero everywhere else.
    """
    excess = level - base
    out = np.zeros(len(level))
    # The last few rows may be something else — a rotated scan's corner fill,
    # a scanner's own dark rim — so the band may start a little way in.
    slack = max(1, int(len(level) * BED_RING))
    for rows in (slice(None), slice(None, None, -1)):   # from each end in turn
        run = excess[rows]
        within = (run >= BACKING_MIN) & (run <= BACKING_MAX)
        if not within[:slack].any():
            continue
        start = int(np.argmax(within))
        stop = len(run) if within[start:].all() else start + int(np.argmin(within[start:]))
        # Half the scan at most: past that it is not backing trailing a card,
        # it is what the card is lying on, and the bed level already has it.
        if stop - start > slack and stop <= len(run) // 2:
            out[rows][start:stop] = run[start:stop]
    return out


def _clean_value(value):
    """
    The value channel with a sheet feeder's two kinds of lit background taken
    off, so the bar measures how far a pixel stands above the bed BEHIND it.

    Sensor lines run down columns and show in the scan's top AND bottom border;
    the backing runs across rows and shows in its left AND right border, at one
    end. Both clear the bar. A line took the box out to it, and the backing took
    it on to the end of the page, on either face — and a navy back, reading
    little above either, is where the crops went wrong first.

    Each is read from BOTH opposite borders and kept only where both see it,
    and each is shaped the way it is on a scanner — narrow and full-length for
    a line, a band from one end for backing — so that a card lying against the
    scan's border, which is broad and does not reach end to end, is left alone.
    """
    h, w = value.shape
    ry, rx = max(1, int(h * BED_RING)), max(1, int(w * BED_RING))
    if h < 8 * ry or w < 8 * rx:
        return value
    v = value.astype(np.int16)
    columns = np.minimum(np.median(v[:ry], axis=0), np.median(v[-ry:], axis=0))
    rows = np.minimum(np.median(v[:, :rx], axis=1), np.median(v[:, -rx:], axis=1))
    base = min(float(np.median(side)) for side in _border(value))
    backing = _backing(rows, base)
    lines = _runs(_ridges(columns) >= BACKING_MIN)
    if not lines and not backing.any():
        return value
    clean = v - backing[:, None].astype(np.int16)
    # A line is FILLED from the columns either side of it rather than having
    # its strength subtracted. How much a line adds depends on what is under
    # it — it hardly shows through a bright card's glow — and subtracting the
    # full amount there digs a dark notch, which the edge measurement then
    # takes for the card's edge. The brighter neighbour keeps card as card.
    # Widened by LINE_SMEAR first: JPEG spreads a line into the columns beside
    # it, below the ridge test but not below the bar.
    for a, b in lines:
        a, b = max(0, a - LINE_SMEAR), min(w - 1, b + LINE_SMEAR)
        fill = np.maximum(clean[:, max(0, a - 1)], clean[:, min(w - 1, b + 1)])
        clean[:, a:b + 1] = np.minimum(clean[:, a:b + 1], fill[:, None])
    return np.clip(clean, 0, 255).astype(np.uint8)


def _runs(flags):
    """(first, last) of each run of True in a 1-D array."""
    edges = np.diff(np.concatenate(([0], flags.astype(np.int8), [0])))
    return list(zip(np.where(edges == 1)[0], np.where(edges == -1)[0] - 1))


def _mask(im, background="dark"):
    """
    Card against background, at a threshold read off this scan.

    On a dark bed the bar is capped at INK_THRESHOLD so no scan is read worse
    than by the old fixed one, and lowered wherever the bed is cleaner than
    that — which is where the dark cards live. A card whose own artwork
    measures 12 needs a bar below 12 to be seen whole, and a bed sitting at 0
    will happily give one.

    On a light bed the whole comparison turns over: the card is what is DARKER
    than the background. There is no historical bar to cap against, so the
    learned one is just held somewhere sane.
    """
    return _mask_value(_value(im), background)


def _mask_value(value, background="dark", cleaned=False):
    """`_mask` on a value channel already in hand; `cleaned` when it has
    already been through `_clean_value`."""
    if background == "light":
        low, high = LIGHT_LIMITS
        return value < min(high, max(low, _bed_floor(value) - BED_MARGIN))
    if not cleaned:
        value = _clean_value(value)
    return value > min(INK_THRESHOLD, _bed_level(value) + BED_MARGIN)


def _bed_fill(background="dark"):
    """What to pad with when rotating: the bed's own colour, not black.

    Filling a light-bedded scan's corners with black would hand `_card_box`
    four black triangles, and on a light bed black is what a card looks like.
    """
    return (255, 255, 255) if background == "light" else (0, 0, 0)


def _lit_runs_mask(m):
    """Where a run of EDGE_RUN lit pixels STARTS on each row of `m`."""
    runs = m.copy()
    for k in range(1, EDGE_RUN):
        runs[:, :-k] &= m[:, k:]
    runs[:, -EDGE_RUN:] = False
    return runs


def _row_stats(m):
    """
    Per row: how much of it is lit, and how far its runs of lit pixels reach.

    The reach matters where the fraction does not. A column crossing a
    near-black back is barely lit at all — a few gold lines — but those lines
    are at the top and the bottom of it, so its reach is the whole card. A
    column of scanner bed has neither.
    """
    runs = _lit_runs_mask(m)
    has = runs.any(axis=1)
    first = runs.argmax(axis=1)
    last = runs.shape[1] - 1 - runs[:, ::-1].argmax(axis=1)
    return m.mean(axis=1), np.where(has, last - first + 1, 0)


def _strong(fraction, weak=None):
    """
    The stretch of rows or columns that is certainly card.

    Without `weak`, first to last: all that is known yet. With it, the
    certainly-card runs are joined only across a gap that still looks like
    card, and the stretch carrying the most card wins. First-to-last took the
    box out to a strip of sensor noise at the edge of the scan — a column of
    faint 1s and 2s down the full height, certainly card against a bed that
    reads exactly 0 — across a clean band of black between it and the card.
    """
    lit = np.where(fraction > STRONG_LIT)[0]
    if not len(lit):
        return None
    if weak is None:
        return int(lit[0]), int(lit[-1])
    runs = [[int(lit[0]), int(lit[0])]]
    for i in lit[1:]:
        if weak[runs[-1][1] + 1:i].all():
            runs[-1][1] = int(i)
        else:
            runs.append([int(i), int(i)])
    return tuple(max(runs, key=lambda r: fraction[r[0]:r[1] + 1].sum()))


def _weak(fraction, reach, across):
    return (fraction > WEAK_LIT) | (reach > WEAK_REACH * across)


def _grow(lo, hi, weak):
    """Extend a stretch of certain card through everything still plausible."""
    while lo > 0 and weak[lo - 1]:
        lo -= 1
    while hi < len(weak) - 1 and weak[hi + 1]:
        hi += 1
    return lo, hi


def _mask_stats(m):
    """
    Row and column statistics for one mask, measured once.

    Deliberately a value that gets passed around rather than something each
    caller works out for itself: a division looks at both axes, checks the
    shape of both halves, and every one of those wants the same two profiles
    off the same mask. Recomputing them is the single most expensive thing in
    the pipeline on a full flatbed scan.
    """
    return _row_stats(m), _row_stats(np.ascontiguousarray(m.T))


def _card_box(m, stats=None):
    """
    The card's extent: rows and columns that are certainly card, grown out
    through the ones that still look like it.

    Found from how MUCH of each row and column is lit rather than from any
    single lit pixel, because JPEG noise in the black margin clears the ink
    threshold on its own and an any-pixel bounding box reaches well past the
    card. But a flat "40% of the row is lit" bar cuts the other way on a card
    whose own edge is dark — a black band across the bottom, a dark cloak down
    one side — and takes the card's real edge off with it. So the bar finds the
    card and a looser test carries the edge out to where the card actually
    stops.
    """
    rows, cols = stats if stats else _mask_stats(m)
    down, across = _strong(rows[0]), _strong(cols[0])
    if down is None or across is None:
        return None
    # Twice: the first pass is only to know how long the card is on each axis,
    # which is what decides whether a row or column still looks like card.
    for _ in range(2):
        weak_rows = _weak(*rows, across[1] - across[0] + 1)
        weak_cols = _weak(*cols, down[1] - down[0] + 1)
        down, across = _strong(rows[0], weak_rows), _strong(cols[0], weak_cols)
    y0, y1 = _grow(*down, weak_rows)
    x0, x1 = _grow(*across, weak_cols)
    return x0, y0, x1, y1


def _card_runs(m, axis, stats=None):
    """
    Stretches of card along `axis`, each grown out of a certainly-card seed.

    This is what separates two cards from one: the bed between them is neither
    certainly card nor plausibly card, so the stretches stop there. A dark
    patch INSIDE a card is plausible and does not stop them, which is the whole
    reason this is not a run of "40% lit" columns — on a near-black back those
    come out as a handful of separate stretches, and two of them look exactly
    like two cards.
    """
    rows, cols = stats if stats else _mask_stats(m)
    along, other = (cols, _strong(rows[0])) if axis == 0 else (rows, _strong(cols[0]))
    if other is None:
        return []
    strong = along[0] > STRONG_LIT
    weak = _weak(*along, other[1] - other[0] + 1)
    out, i, n = [], 0, len(strong)
    while i < n:
        if not strong[i]:
            i += 1
            continue
        lo, hi = _grow(i, i, weak)
        out.append((lo, hi))
        i = hi + 1
    return out


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
    # Done across the whole band at once: a per-row Python loop over four edges
    # of a 40-megapixel scan costs more than everything else in the pipeline.
    runs = _lit_runs_mask(band)
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


def _median(w, a, b):
    """Median of w over an index span given in either order, clipped to w."""
    lo, hi = sorted((a, b))
    lo, hi = max(0, lo), min(len(w) - 1, hi)
    if hi < lo:                             # span fell off the window
        return float(w[0] if a < 0 else w[-1])
    return float(np.median(w[lo:hi + 1]))


def _edge(profile, start, inward, out_span, in_span):
    """
    Where one side of the card really is, given a brightness profile across it.

    `start` is the located outline's guess and `inward` is +1 or -1, the
    direction that travels into the card. The profile is oriented so that
    entering the card RISES, whichever way round the bed is. Two steps, and the
    order matters:

    **Find the step, not the light.** The halo fades; the card's edge is a
    cliff. Scanning outward-in for the first place the profile climbs steeply
    passes over the glow and stops at the paper. Taking the steepest place
    outright would not do — the border-to-artwork boundary inside the card is a
    cliff too, and sometimes a taller one — so it is the OUTERMOST steep place
    rather than the steepest.

    **Then take the half-way level.** Blur is symmetric, so whatever softened
    the edge left its midpoint where the edge was. Reading the bed just outside
    the step and the border just inside it and crossing at the mean of the two
    recovers the original edge to about a pixel, however soft the scan.
    """
    lo = max(0, start - (out_span if inward > 0 else in_span))
    hi = min(len(profile), start + (in_span if inward > 0 else out_span) + 1)
    if hi - lo < 12:
        return start
    w = np.convolve(profile[lo:hi].astype(float), np.ones(3) / 3.0, mode="same")
    gradient = np.diff(w) * inward          # rising as we travel inward
    peak = gradient.max()
    if peak <= 0:
        return start
    steep = np.where(gradient >= min(peak * 0.4, EDGE_STEP_MIN))[0]
    i = int(steep[0]) if inward > 0 else int(steep[-1]) + 1

    near = max(3, (hi - lo) // 12)
    half = (_median(w, i + inward * 2, i + inward * (2 + near))
            + _median(w, i - inward * 2, i - inward * (2 + near))) / 2.0
    j = i
    while 0 <= j < len(w) and w[j] >= half:         # back out below the level
        j -= inward
    while 0 <= j < len(w) and w[j] < half:          # then in to the crossing
        j += inward
    return lo + min(max(j, 0), len(w) - 1)


def _measure_box(value, box, background="dark"):
    """
    The located outline, with each of its four sides measured onto the card's
    actual edge. See EDGE_SEARCH_OUT for why a threshold cannot do this.

    A side is kept as it was found when there is nothing to measure against —
    under EDGE_BED_MIN pixels outside it, which is a card running off the scan
    or butting against its pair — or when the answer lands outside the search
    window, which means what it found was not the edge.
    """
    x0, y0, x1, y1 = box
    h_img, w_img = value.shape
    w, h = x1 - x0, y1 - y0
    if w < 40 or h < 40:
        return box
    # Oriented so that entering the card always rises: on a light bed the card
    # is what is DARKER than the background, and the step runs the other way.
    v = (255 - value) if background == "light" else value

    band_y = slice(y0 + int(h * EDGE_BAND), y1 - int(h * EDGE_BAND) + 1)
    band_x = slice(x0 + int(w * EDGE_BAND), x1 - int(w * EDGE_BAND) + 1)
    cols = np.median(v[band_y, :], axis=0)
    rows = np.median(v[:, band_x], axis=1)

    # EDGE_SEARCH_* is a fraction of the card's WIDTH; the top and bottom get
    # the same distance in pixels rather than the same fraction of the longer
    # side, because a halo does not know which way round the card is.
    out_x, in_x = int(w * EDGE_SEARCH_OUT), int(w * EDGE_SEARCH_IN)
    tall = CARD_W / float(CARD_H)
    out_y, in_y = int(h * EDGE_SEARCH_OUT * tall), int(h * EDGE_SEARCH_IN * tall)

    def side(profile, start, inward, out_span, in_span, limit):
        if start < EDGE_BED_MIN or start > limit - EDGE_BED_MIN:
            return start
        found = _edge(profile, start, inward, out_span, in_span)
        moved = (found - start) * inward
        return found if -out_span <= moved <= in_span else start

    nx0 = side(cols, x0, +1, out_x, in_x, w_img - 1)
    nx1 = side(cols, x1, -1, out_x, in_x, w_img - 1)
    ny0 = side(rows, y0, +1, out_y, in_y, h_img - 1)
    ny1 = side(rows, y1, -1, out_y, in_y, h_img - 1)
    if nx1 - nx0 < w * 0.8 or ny1 - ny0 < h * 0.8:
        return box
    return nx0, ny0, nx1, ny1


def _expand_to(box, reference, bounds):
    """
    Grow a card's box out to a size known from elsewhere, about its own centre.

    Only ever GROWS, and only as far as the scan actually goes. Detection
    under-reports and never over-reports: dark artwork reads as bed, so a box
    smaller than the card is missing edges, while a box the right size is not
    hiding anything. Centred because what a dark back does leave visible — an
    inset frame line, a foil panel — is printed concentric with the card.
    """
    x0, y0, x1, y1 = box
    want_w, want_h = reference
    if want_w > x1 - x0 + 1:
        middle = (x0 + x1) / 2.0
        x0, x1 = round(middle - want_w / 2.0), round(middle + want_w / 2.0)
    if want_h > y1 - y0 + 1:
        middle = (y0 + y1) / 2.0
        y0, y1 = round(middle - want_h / 2.0), round(middle + want_h / 2.0)
    w, h = bounds
    return max(0, x0), max(0, y0), min(w - 1, x1), min(h - 1, y1)


def _small(im, longest=SKEW_SIZE):
    """A copy no bigger than `longest` on its long edge — see SKEW_SIZE."""
    if max(im.size) <= longest:
        return im
    k = longest / float(max(im.size))
    return im.resize((max(1, round(im.size[0] * k)), max(1, round(im.size[1] * k))),
                     Image.BILINEAR)


def straighten(im, reference=None, background="dark"):
    """
    Deskew and crop to the card, at the scan's OWN resolution.

    `reference` is a (width, height) the card is known to be from somewhere
    other than this image — in practice from its other face, which is the same
    piece of card. A box smaller than that is grown out to it. See
    `_expand_to`, and `batch._match_sizes` for where the size comes from.

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
    measured = [a for a in skew_sides(_mask(_small(im), background)).values()
                if a is not None]
    angle = float(np.median(measured)) if len(measured) >= MIN_SKEW_EDGES else 0.0
    if abs(angle) < MIN_SKEW_ANGLE:
        angle = 0.0
    # Padded with the BED's colour. Filling a light-bedded scan's corners with
    # black would hand the next step four black triangles, and on a light bed
    # black is exactly what a card looks like.
    rotated = im.rotate(-angle, resample=Image.BICUBIC, expand=True,
                        fillcolor=_bed_fill(background))
    # On a dark bed the background is cleaned BEFORE rotating and the cleaned
    # map rotated with the scan: a sensor line runs down the scan's columns and
    # the backing across its rows, not the rotated image's.
    if background == "light":
        value = _value(rotated)
    else:
        value = np.asarray(Image.fromarray(_clean_value(_value(im))).rotate(
            -angle, resample=Image.BICUBIC, expand=True, fillcolor=0))
    box = _card_box(_mask_value(value, background, cleaned=True))
    if box is None:
        return rotated, rotated, angle, len(measured)
    # Measured onto the card's edge BEFORE anything is compared against it: a
    # box still carrying its halo is the wrong size to match a face against.
    box = _measure_box(value, box, background)
    if reference:
        box = _expand_to(box, reference, rotated.size)
    x0, y0, x1, y1 = box
    exact = rotated.crop((x0, y0, x1 + 1, y1 + 1))
    m = round(CROP_MARGIN * exact.size[0] / CARD_W)
    # Held inside the scan. Cropping past the edge pads with the bed's colour,
    # and on a scan cropped flush to the card that invents a margin which reads
    # as scanner bed — so the corner crop shows a clean silhouette the card was
    # never photographed against. Better to run the crop to the edge of what
    # was actually scanned and let it look like what it is.
    width, height = rotated.size
    padded = rotated.crop((max(0, x0 - m), max(0, y0 - m),
                           min(width, x1 + 1 + m), min(height, y1 + 1 + m)))
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

#: And how wide the background strip between them must be, in COLUMNS. One is
#: enough, and one is what a lot of scans give: cards fed through a document
#: scanner touch along most of their length and leave a single column of
#: backing between them.
#:
#: Asking for more was costing exactly the pixel it was meant to protect. A
#: seam too narrow to accept falls through to dividing the pair down the
#: middle, that lands a column out whenever the content width is odd, and the
#: column it lands out by is the last column of the first card — which arrives
#: at the inner edge of the second half and is magnified by the edge crop. The
#: seam was right there in the scan, one column wide, being refused for being
#: thin.
#:
#: It is safe to be this permissive because a column only breaks a run when it
#: is neither certainly card nor plausibly card (see `_card_runs`), and because
#: both halves still have to come out card-shaped afterwards.
SPLIT_MIN_GAP = 1

#: Longest edge a scan is decoded to when `probe` is only deciding how many
#: cards are on it. Full-resolution decoding of a folder of scans to plan the
#: batch costs more than the whole crop run afterwards, and the gap between two
#: cards survives the downscale — it is the one feature this needs.
PROBE_SIZE = 900


def _division(m, axis, stats=None):
    """
    Where a mask divides into exactly two cards along `axis` (0 for a vertical
    cut between side-by-side cards, 1 for a horizontal one between stacked
    cards), or None.

    The stretches come from `_card_runs` rather than from a plain run of lit
    columns, because a plain run breaks up inside a dark card: a near-black
    back is a handful of separate lit stretches with unlit artwork between
    them, and any two of those read as two cards. What was actually produced
    was a card and a half in one half and the rest in the other, and both
    passed the shape check.
    """
    n = m.shape[1 - axis]
    runs = [r for r in _card_runs(m, axis, stats)
            if r[1] - r[0] + 1 >= n * SPLIT_MIN_SPAN]
    if len(runs) != 2:
        return None
    if runs[1][0] - runs[0][1] - 1 < SPLIT_MIN_GAP:
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


def _seam_near(profile, cut):
    """
    The emptiest column within reach of a blind division, where one stands out.

    Returns `cut` unchanged when nothing nearby is meaningfully emptier — two
    cards can touch with no seam at all, and then the middle is the best answer
    available and moving off it would be inventing a boundary.
    """
    lo = max(0, cut - SEAM_SEARCH)
    hi = min(len(profile), cut + SEAM_SEARCH + 1)
    if hi - lo < 3:
        return cut
    local = profile[lo:hi]
    best = lo + int(np.argmin(local))
    return best if profile[min(cut, len(profile) - 1)] - profile[best] >= SEAM_DROP else cut


def _cut_at(im, cut, vertical):
    """The scan in two, each half keeping the bed around its own card."""
    w, h = im.size
    if vertical:
        return [im.crop((0, 0, cut, h)), im.crop((cut, 0, w, h))]
    return [im.crop((0, 0, w, cut)), im.crop((0, cut, w, h))]


def split_regions(im, force=False, background="dark"):
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
    m = _mask(im, background)
    stats = _mask_stats(m)
    for axis in (0, 1):
        cut = _division(m, axis, stats)
        if cut is None:
            continue
        vertical = axis == 0
        halves = (m[:, :cut], m[:, cut:]) if vertical else (m[:cut], m[cut:])
        if all(_card_shaped(half) for half in halves):
            return _cut_at(im, cut, vertical)
    if not force:
        return None
    box = _card_box(m, stats)
    w, h = im.size
    x0, y0, x1, y1 = box if box else (0, 0, w - 1, h - 1)
    # Halfway along the content by WIDTH, not the average of its two ends.
    # Those differ by one, and the one is the last column of the first card: it
    # lands at the inner edge of the second half, where the edge crop magnifies
    # it into a visible strip of the wrong card. Cards that touch have no gap
    # to absorb a rounding error.
    rows, cols = stats
    if (x1 - x0) >= (y1 - y0):
        return _cut_at(im, _seam_near(cols[0], x0 + (x1 - x0 + 1) // 2), True)
    return _cut_at(im, _seam_near(rows[0], y0 + (y1 - y0 + 1) // 2), False)


def clipped_edges(im, tolerance=2, background="dark"):
    """
    Which of the card's sides run off the scan instead of ending on it.

    Worth reporting because of what it costs. A corner crop exists to show the
    card's SILHOUETTE against the bed — a corner that has been rounded off or
    crushed is read from its profile as much as from whitening on its face —
    and where the scanner has cropped flush to the card there is no bed behind
    it to read the profile against. The crop still comes out; it just cannot
    answer the question it was cut to answer.

    It costs the deskew too: an edge lying on the image boundary is a straight
    line at zero degrees whatever the card is doing, so it gets no vote, and a
    card clipped on three sides is left measuring its angle from one.

    A scanner set to crop to the card does this, and a card is 88mm tall
    against a scan bed window often set to 87 or so — the two ends go first.
    """
    small = _small(im)
    box = _card_box(_mask(small, background))
    if box is None:
        return []
    x0, y0, x1, y1 = box
    w, h = small.size
    return [name for name, off in (("left", x0 <= tolerance),
                                   ("top", y0 <= tolerance),
                                   ("right", x1 >= w - 1 - tolerance),
                                   ("bottom", y1 >= h - 1 - tolerance)) if off]


#: How thick a band, in reference pixels, counts as the card's EDGE when asking
#: whether it stands out from what is behind it. Thin: it is the outermost ink
#: that has to be told apart from the backing, not the artwork further in.
CONTRAST_BAND = 12

#: How far the card has to sit inside the scan before the question can be asked
#: at all. Backing down one side is not a margin: on a scan cropped flush the
#: only background is a sliver in the corner arcs, and measuring against that
#: reports a healthy contrast for a card that has none anywhere it matters.
#: That case is a different complaint and already has its own warning.
CONTRAST_MIN_INSET = 2

#: Below this much difference the card's outline cannot be made out, whatever
#: the crop is cut to. Calibrated on real scans rather than picked: on a dark
#: bed a Lorcana back measures 0 against its backing and its front 15, a
#: navy One Piece back 24, and a bright front 102. On a white backing every one
#: of those lands between 141 and 243.
CONTRAST_FLOOR = 40


def edge_contrast(padded, background="dark"):
    """
    How far the card's outermost ink stands apart from the backing behind it.

    Returns (edge, backing) as brightness on the value channel, or None where
    there is not enough backing in the scan to compare against.

    This is the difference between a crop that is cut correctly and a crop that
    can be READ. A corner that has been rounded off or crushed is judged from
    its profile — the card's outline against what is behind it — and a black
    border on a black backing has a profile that is cut in exactly the right
    place and invisible. The app cannot fix that from pixels, but it can say so,
    and the fix is a sheet of paper.
    """
    value = _value(padded)
    box = _card_box(_mask(padded, background))
    if box is None:
        return None
    # Measured onto the edge, as the crops are. The located box can sit in the
    # glow a few pixels out, and a band that is mostly backing reads as a card
    # whose edge is as dark as the backing.
    x0, y0, x1, y1 = _measure_box(
        value if background == "light" else _clean_value(value), box, background)
    band = max(2, round(CONTRAST_BAND * (x1 - x0 + 1) / CARD_W))
    if x1 - x0 < band * 4 or y1 - y0 < band * 4:
        return None

    height, width = value.shape
    inset = CONTRAST_MIN_INSET
    if (x0 < inset or y0 < inset
            or x1 > width - 1 - inset or y1 > height - 1 - inset):
        return None
    backing = np.ones(value.shape, bool)
    backing[y0:y1 + 1, x0:x1 + 1] = False
    edge = np.zeros(value.shape, bool)
    edge[y0:y1 + 1, x0:x1 + 1] = True
    edge[y0 + band:y1 + 1 - band, x0 + band:x1 + 1 - band] = False
    return float(np.median(value[edge])), float(np.median(value[backing]))


def divided_ok(regions, background="dark"):
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
        if not _card_shaped(_mask(region, background)):
            return False, f"half {i} of {len(regions)} is not card-shaped"
    return True, ""


def load_small(path, longest=PROBE_SIZE):
    """
    A scan opened at a fraction of its size, for the questions that do not need
    the pixels — how many cards are on it, and which face is which.

    JPEG's DCT scaling gives the reduced image almost free, which is what makes
    it affordable to look at every file in a folder before any cropping starts.
    The alternative is decoding the whole folder twice.
    """
    with Image.open(path) as raw:
        raw.draft("RGB", (longest, longest))
        im = ImageOps.exif_transpose(raw).convert("RGB")
    im.thumbnail((longest, longest), Image.BILINEAR)
    return im


def card_only(im, background="dark"):
    """
    `im` cropped to the card on it, so two scans can be compared like for like.

    Framing is not a property of the card: the same card scanned twice sits
    differently in the frame, and a comparison that includes the bed measures
    the scanner rather than the card.
    """
    box = _card_box(_mask(im, background))
    if box is None:
        return im
    x0, y0, x1, y1 = box
    return im.crop((x0, y0, x1 + 1, y1 + 1))


def probe(path, background="dark"):
    """Whether the scan at `path` holds two cards, from a small decode."""
    return split_regions(load_small(path), background=background) is not None


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
