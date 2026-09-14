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

#: How far either side of the rough outline the card's true edge is hunted
#: for, as a fraction of the card's own width and height.
#:
#: A scanner does not render a card as a step from bed to border. Light bleeds
#: sideways under the platen and the sensor smears in the direction of travel,
#: so a bright card sits in a halo that fades out over tens of pixels — and a
#: halo above INK_THRESHOLD is, to a threshold, card. That is the whole reason
#: the crops here drifted: the outline was landing where the GLOW died rather
#: than where the card ends, which pushes every crop outward and can walk an
#: edge strip clean off the card into black.
#:
#: The halo is far worse on a yellow front (value ~230) than on a navy back
#: (value ~47), which is why fronts drifted further than backs on the same
#: scanner — the giveaway that it was bleed and not geometry.
EDGE_SEARCH_OUT = 0.07
EDGE_SEARCH_IN = 0.05

#: Rows (or columns) sampled to build the profile one edge is measured on,
#: as a fraction to discard at each end. Rounded corners and any residual tilt
#: live at the ends; the middle half of an edge is the part that is straight.
EDGE_BAND = 0.25

#: How many pixels of scanner bed must sit outside an edge before it is worth
#: measuring. Below this the halo is clipped by the edge of the scan and there
#: is no background left to read a level against — and a card framed that
#: tightly has nowhere for the glow to spread anyway, so the rough outline is
#: already close and is left alone.
EDGE_BED_MIN = 12

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


def _mask(v):
    return v > INK_THRESHOLD


def _coarse_box(v):
    """
    Roughly where the card is, found from how MUCH of each row and column is
    lit rather than from any single lit pixel. JPEG noise in the black margin
    clears the ink threshold on its own, so an any-pixel bounding box reaches
    well past the card.

    Rough is all this is. It finds the card but not its edge: a threshold
    cannot tell the card from the halo around it, and lands somewhere out in
    the glow. `_card_box` measures the real edge from here.
    """
    m = _mask(v)
    lit_cols = m.mean(axis=0) > 0.4
    lit_rows = m.mean(axis=1) > 0.4
    xs = np.where(lit_cols)[0]
    ys = np.where(lit_rows)[0]
    if not len(xs) or not len(ys):
        return None
    return int(xs[0]), int(ys[0]), int(xs[-1]), int(ys[-1])


def _median(w, a, b):
    """Median of w over an index span given in either order, clipped to w."""
    lo, hi = sorted((a, b))
    lo, hi = max(0, lo), min(len(w) - 1, hi)
    if hi < lo:                             # span fell off the window
        return float(w[0] if a < 0 else w[-1])
    return float(np.median(w[lo:hi + 1]))


def _edge(profile, start, inward, out_span, in_span):
    """
    Where one edge of the card really is, given a brightness profile across it.

    `start` is the rough outline's guess and `inward` is +1 or -1, the
    direction that travels into the card. Two steps, and the order matters:

    **Find the step, not the light.** The halo fades; the card's edge is a
    cliff. Scanning outward-in for the first place the profile climbs steeply
    passes straight over the glow and stops at the paper. Taking the steepest
    point outright would not do — the border-to-artwork boundary inside the
    card is a cliff too, and sometimes a taller one, so it has to be the
    OUTERMOST steep place rather than the steepest.

    **Then take the half-way level.** Blur is symmetric, so whatever softened
    the edge left its midpoint sitting exactly where the edge was. Reading the
    background just outside the step and the border just inside it and
    crossing at the mean of the two recovers the original edge to about a
    pixel, however soft the scan is.
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
    steep = np.where(gradient >= peak * 0.4)[0]
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


def _card_box(v):
    """
    The card's extent: the rough outline, then each of its four sides measured
    against the brightness profile across it. See EDGE_SEARCH_OUT.

    A side is left as the rough outline found it when there is nothing to
    measure against — the card runs off that end of the scan — or when the
    measurement comes back further than the search window should have allowed,
    which means it found something other than the card's edge.
    """
    coarse = _coarse_box(v)
    if coarse is None:
        return None
    x0, y0, x1, y1 = coarse
    h_img, w_img = v.shape
    w, h = x1 - x0, y1 - y0
    if w < 40 or h < 40:
        return coarse

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
        # A few pixels of bed outside the edge is enough to read a level
        # against; none at all means the card runs off the scan on this side,
        # and there is nothing to measure. The window itself is clipped to the
        # scan, so a tightly framed scan is narrowed rather than skipped.
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
        return coarse
    return nx0, ny0, nx1, ny1


def _left_edge(m, y, run=5):
    """First x where the card actually starts: a run of lit pixels, not one."""
    lit = m[y]
    idx = np.where(lit)[0]
    for x in idx:
        if x + run < len(lit) and lit[x:x + run].all():
            return int(x)
    return None


def _skew_degrees(v):
    """
    Angle of the card's left edge, from a line fit over the straight middle of
    it. The rounded corners are excluded (the top and bottom 18%) or they drag
    the fit toward zero — which is exactly the failure that makes a deskew look
    like it ran while leaving the corners unusable.

    The threshold is good enough here where it is not good enough for the crop
    box: a halo shifts every row's left edge by about the same amount, and a
    constant offset does not tilt the line that is fitted through them.
    """
    box = _coarse_box(v)
    if box is None:
        return 0.0
    _, y0, _, y1 = box
    height = y1 - y0
    if height < 100:
        return 0.0
    m = _mask(v)
    ys, xs = [], []
    for y in range(y0 + int(height * 0.18), y1 - int(height * 0.18)):
        x = _left_edge(m, y)
        if x is not None:
            ys.append(y)
            xs.append(x)
    if len(ys) < 20:
        return 0.0
    slope = np.polyfit(ys, xs, 1)[0]
    return math.degrees(math.atan(slope))


def load(path):
    """Open an image, honouring EXIF rotation, in RGB."""
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    return im.convert("RGB")


def straighten(im):
    """
    Deskew and crop to the card, at the scan's OWN resolution.

    Returns (exact, padded, angle) — the card cropped flush, and the same card
    with CROP_MARGIN of background kept around it for the crops to be cut from.
    Normalising to a fixed size here would put a resample between the scan and
    every crop taken from it, and each interpolation costs a little edge
    definition on exactly the fine detail the crop exists to show.
    """
    angle = _skew_degrees(_value(im))
    rotated = im.rotate(-angle, resample=Image.BICUBIC, expand=True, fillcolor=(0, 0, 0))
    box = _card_box(_value(rotated))
    if box is None:
        return rotated, rotated, angle
    x0, y0, x1, y1 = box
    exact = rotated.crop((x0, y0, x1 + 1, y1 + 1))
    m = round(CROP_MARGIN * exact.size[0] / CARD_W)
    padded = rotated.crop((x0 - m, y0 - m, x1 + 1 + m, y1 + 1 + m))
    return exact, padded, angle


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
