"""
Centering: how far the printed image sits off the middle of the card, measured
from a scan, and what that does to the grade each grading company can give.

Centering is the one part of a card's condition that is a measurement rather
than a judgement. Every grader publishes it as a ratio of opposite borders —
"55/45" means the wider border is 55% of the two together — and caps the grade
by it. So it can be read off a scan exactly, before a card is sent anywhere,
and a card that cannot make a 10 on centering alone is worth knowing about
before paying to find out.

How a measurement is made:

1. **The card's outer edge** comes from the same deskew and outline detection
   the crops use, then each side is refined to a fraction of a pixel from an
   averaged profile across that edge. A pixel matters: on a 300 DPI scan a
   Pokémon border is about 36px wide, so one pixel is ~1.5 points of ratio —
   the whole gap between a 55/45 and a 56/44.
2. **The inner edge of each border** — where the border colour stops and the
   frame or artwork starts — is found by walking inward from the outer edge
   along averaged profiles and taking the FIRST strong colour change. The first,
   not the strongest: artwork usually has harder edges than a border does, and
   the strongest one would be inside the picture.
3. Each side is measured in several bands along its length, not once. A card
   cut on the skew ("diamond cut") has a border that tapers, and graders
   measure that too; one reading in the middle would hide it.
4. Everything is reported in pixels AND millimetres, using the card's own
   measured width as the ruler (63 mm for a standard card), so a scan with no
   DPI in it still gives real distances.

Detection is a starting point, not the answer. A full-art card has no border
to find, and a card whose art bleeds into the border at one side gives a
wrong edge on that side. Every line can be moved by hand in the centering
window, and the ratios follow it.
"""

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from . import grading, imaging

#: Standard trading-card size (Pokémon, Magic, sports), in millimetres. The
#: card's own measured width is the ruler, so a scan needs no DPI to be read
#: in mm. Japanese/Yu-Gi-Oh small-size cards are 59 x 86.
CARD_MM = (63.0, 88.0)

#: How many bands each side is measured in. Odd, so the median is a reading.
BANDS = 5

#: How much of each edge the bands span. The ends are left out: that is where
#: the rounded corners are, and a profile taken through a corner arc reads the
#: border as narrower than it is.
BAND_SPAN = (0.15, 0.85)

#: Where the search for a border's inner edge starts and stops, as a fraction
#: of the card's dimension across that border. A Pokémon border is ~4.5% of
#: the width; vintage sports borders run to ~10%. 25% leaves room for a badly
#: off-centre card, whose wide side can be two or three times the norm.
SEARCH = (0.006, 0.25)

#: A colour change has to be at least this large (RGB distance across the
#: measuring span, after smoothing) to count as the border's inner edge.
#: Below it is print texture, scanner noise, and the slow darkening some
#: borders have toward their inside.
MIN_STEP = 28.0

#: Bands on one side that disagree by more than this fraction of the median
#: border width make that side "unsure" — usually art bleeding into the border
#: at one band, or no border at all.
SPREAD_LIMIT = 0.18

SIDES = ("left", "right", "top", "bottom")


# ---------------------------------------------------------------- results


@dataclass
class Side:
    """One border: where its outer and inner edges were found."""
    name: str
    outer: float                 # px, in the straightened card image
    inner: float                 # px, same frame
    bands: list = field(default_factory=list)   # border width per band, px
    sure: bool = True
    edge: str = "seen"           # "line" (a bright edge line), "seen", or "estimated"

    @property
    def width(self):
        return abs(self.inner - self.outer)

    @property
    def taper(self):
        """Difference between the widest and narrowest band, px."""
        good = [b for b in self.bands if b is not None]
        return (max(good) - min(good)) if len(good) > 1 else 0.0


@dataclass
class Measurement:
    """
    One face of one card. Coordinates are in the deskewed image `card`, which
    includes a margin of background so the outer edges can be seen and moved.
    """
    card: Image.Image
    sides: dict
    angle: float = 0.0
    card_mm: tuple = CARD_MM
    notes: list = field(default_factory=list)

    # ---- geometry

    @property
    def card_px(self):
        s = self.sides
        return (s["right"].outer - s["left"].outer,
                s["bottom"].outer - s["top"].outer)

    @property
    def mm_per_px(self):
        """From the card's own width — and its height, averaged, as a check."""
        w, h = self.card_px
        if w <= 0 or h <= 0:
            return 0.0
        return (self.card_mm[0] / w + self.card_mm[1] / h) / 2

    @property
    def dpi(self):
        k = self.mm_per_px
        return 25.4 / k if k else 0.0

    def mm(self, side):
        return self.sides[side].width * self.mm_per_px

    # ---- ratios

    @property
    def lr(self):
        return ratio(self.sides["left"].width, self.sides["right"].width)

    @property
    def tb(self):
        return ratio(self.sides["top"].width, self.sides["bottom"].width)

    @property
    def worst(self):
        """The larger share on either axis — what a grade threshold is read against."""
        return max(self.lr[0], self.tb[0])

    @property
    def trusted(self):
        """
        False when the measurement should not be graded from at all: the card
        is cut off, its size disagrees with the scan's DPI, or no border was
        found. Distinct from one uncertain line, which a person can check.
        """
        bad = ("runs off the scan", "edge was probably missed", "no consistent border",
               "could not pick the card out")
        return not any(any(b in n for b in bad) for n in self.notes)

    @property
    def sure(self):
        return all(s.sure for s in self.sides.values())

    def summary(self):
        lr, tb = self.lr, self.tb
        return (f"L/R {fmt_ratio(lr)} ({'left' if lr[1] == 'first' else 'right'} wider)   "
                f"T/B {fmt_ratio(tb)} ({'top' if tb[1] == 'first' else 'bottom'} wider)")

    def recompute(self):
        """Refresh derived notes after a line has been moved by hand."""
        self.notes = [n for n in self.notes if not n.startswith("taper")]
        for s in self.sides.values():
            if s.sure and s.bands and s.width and s.taper / s.width > SPREAD_LIMIT:
                self.notes.append(
                    f"taper on {s.name}: border varies by {s.taper * self.mm_per_px:.2f} mm "
                    "along its length — a skewed cut, or art bleeding into the border")


def ratio(a, b):
    """
    Two opposite borders as a grader writes them: (larger share %, which side).

    Returns (pct, side) where pct is the wider border's share of the pair,
    50.0 for perfect. `side` names the WIDER border — the image has shifted
    away from it.
    """
    total = a + b
    if total <= 0:
        return 50.0, "-"
    if a >= b:
        return 100.0 * a / total, "first"
    return 100.0 * b / total, "second"


def fmt_ratio(r):
    pct = r[0]
    return f"{pct:.1f}/{100 - pct:.1f}"


# ---------------------------------------------------------------- finding the card


def load(path):
    """
    Open an image for measuring, KEEPING any transparency.

    Product photos of slabbed cards are often cut out: the slab's plastic is
    semi-transparent and the card is solid. That alpha channel is the cleanest
    outline of the card there is, and converting to RGB on load would throw it
    away — `imaging.load` does, because a crop does not need it.
    """
    im = ImageOps.exif_transpose(Image.open(path))
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        return im.convert("RGBA")
    return im.convert("RGB")


def _components(mask):
    """Connected regions of a small boolean mask: [(area, (x0, y0, x1, y1))]."""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    out = []
    for y, x in zip(*np.nonzero(mask)):
        if seen[y, x]:
            continue
        seen[y, x] = True
        q = deque([(y, x)])
        area, x0, y0, x1, y1 = 0, x, y, x, y
        while q:
            cy, cx = q.popleft()
            area += 1
            x0, x1, y0, y1 = min(x0, cx), max(x1, cx), min(y0, cy), max(y1, cy)
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    q.append((ny, nx))
        out.append((area, (x0, y0, x1, y1)))
    return out


def _card_region(weight):
    """
    The largest card-shaped region of a 0-1 `weight` map, as a full-size box.

    Card-shaped: about 2.5 x 3.5, and filling most of its bounding box (the
    rounded corners take a little). That is what rules out a slab's label,
    which is solid too but three times as wide as it is tall.
    """
    h, w = weight.shape
    k = max(1, int(max(h, w) / 360))
    small = weight[::k, ::k] > 0.5
    best = None
    for area, (x0, y0, x1, y1) in _components(small):
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        if bw < 10 or bh < 10:
            continue
        aspect = max(bw, bh) / min(bw, bh)
        fill = area / float(bw * bh)
        if 1.2 <= aspect <= 1.65 and fill > 0.8 and (best is None or area > best[0]):
            best = (area, (x0 * k, y0 * k, (x1 + 1) * k, (y1 + 1) * k))
    return best[1] if best else None


def isolate(im):
    """
    The card alone on black, so the scan pipeline can measure it whatever it
    was photographed on.

    A flatbed scan already IS a card on black, and passes through untouched.
    Anything else — a cut-out product photo, a slab on a white table — gets a
    0-1 weight per pixel for "this is card", from transparency where there is
    any and from difference to the background colour where there is not. The
    weight is soft at the outline rather than a hard mask, so the card's edge
    keeps the sub-pixel position the measurement depends on.

    Returns (image, how) — `how` says which route was taken, for the notes.
    """
    arr = np.asarray(im.convert("RGBA")).astype(float)
    rgb, alpha = arr[..., :3], arr[..., 3]
    if im.mode == "RGBA" and (alpha < 250).mean() > 0.02:
        # Slab plastic sits at alpha 50-120; the card and label are opaque.
        weight = np.clip((alpha - 170.0) / 80.0, 0, 1)
        rgb = rgb * (alpha[..., None] / 255.0) + 255.0 * (1 - alpha[..., None] / 255.0)
        how = "cut-out photo"
    else:
        edge = np.concatenate([rgb[:4].reshape(-1, 3), rgb[-4:].reshape(-1, 3),
                               rgb[:, :4].reshape(-1, 3), rgb[:, -4:].reshape(-1, 3)])
        bg = np.median(edge, axis=0)
        if bg.max() <= imaging.INK_THRESHOLD:
            return im.convert("RGB"), "scan"
        dist = np.linalg.norm(rgb - bg, axis=2)
        weight = np.clip((dist - 18.0) / 30.0, 0, 1)
        how = "photo"
    box = _card_region(weight)
    if box is None:
        return Image.fromarray(rgb.astype(np.uint8)), how + " — card not isolated"
    x0, y0, x1, y1 = box
    pad = max(4, (x1 - x0) // 60)
    keep = np.zeros_like(weight)
    keep[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad] = 1.0
    w = weight * keep
    out = rgb * w[..., None]
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)), how


# ---------------------------------------------------------------- detection


def _smooth(profile, k=3):
    """A short box blur along a 1-D profile (rows are positions, cols RGB)."""
    if len(profile) < k:
        return profile
    kernel = np.ones(k) / k
    return np.stack([np.convolve(profile[:, c], kernel, mode="same")
                     for c in range(profile.shape[1])], axis=1)


def _edge_position(values, threshold):
    """
    Sub-pixel position where a 1-D profile first rises through `threshold`.

    Used for the card's OUTER edge: the profile runs from background (low
    value) into the card (high value), and the crossing point is where the
    card starts. Linear interpolation between the two samples either side of
    the crossing gives a fraction of a pixel — the same thing a grader's loupe
    gives them by eye.
    """
    above = np.where(values > threshold)[0]
    if not len(above):
        return None
    i = int(above[0])
    if i == 0:
        return 0.0
    lo, hi = values[i - 1], values[i]
    return (i - 1) + (threshold - lo) / max(hi - lo, 1e-6)


def _midpoint(seg, i, span):
    """
    Sub-pixel position of the colour change centred near `i`: where the
    colour is halfway from the colour before it to the colour after it.

    Halfway along the before-to-after colour line, not halfway in brightness —
    gold to blue changes hue far more than it changes brightness. Unbiased for
    a sharp step and a soft fade alike, which a peak-of-the-gradient estimate
    is not once a second edge sits close by.
    """
    a = max(0, i - span - 1)
    b = min(len(seg) - 1, i + span + 1)
    c0, c1 = seg[a], seg[b]
    d = c1 - c0
    dd = float(d @ d)
    if dd < 1e-6:
        return float(i)
    t = (seg[a:b + 1] - c0) @ d / dd
    for k in range(len(t) - 1):
        if t[k] < 0.5 <= t[k + 1]:
            return a + k + (0.5 - t[k]) / max(t[k + 1] - t[k], 1e-9)
    return float(i)


def _first_step(profile, start, stop, span=2):
    """
    Where the border's colour first changes decisively, walking inward.

    `profile` is an (N, 3) RGB array running from outside the card inward.
    The change is measured across `span` pixels either side of each point,
    not between neighbours: a printed border often fades into the frame over
    ten pixels or more (a Pokémon back's gold into its blue), and pixel to
    pixel that change is no bigger than print texture. Over a span it stands
    out, and a sharp edge still reads as sharp.

    The FIRST qualifying peak wins, not the biggest — artwork deeper in
    usually has harder edges than the border does. Its position is then
    refined by `_midpoint`.
    """
    seg = _smooth(profile.astype(float), 3)
    n = len(seg)
    stop = min(stop, n - span - 1)
    if stop - start < 6:
        return None
    grad = np.zeros(n)
    grad[span:n - span] = np.linalg.norm(seg[2 * span:] - seg[:-2 * span], axis=1)
    window = grad[start:stop]
    if not len(window) or window.max() < MIN_STEP:
        return None
    floor = max(MIN_STEP, 0.3 * window.max())
    for j in range(1, len(window) - 1):
        g = window[j]
        if g >= floor and g >= window[j - 1] and g >= window[j + 1]:
            i = start + j
            return _midpoint(seg, i, span)
    return None


def _profiles(arr, side, outer_px, depth, bands):
    """
    Averaged RGB profiles running from just outside `side` inward, one per
    band along that side. Each is (depth, 3).

    `outer_px` is a rough position of the outer edge, used only to know where
    to start the profile; the profile starts a little outside it so the
    background-to-card step is in view.
    """
    h, w = arr.shape[:2]
    out = []
    lo, hi = BAND_SPAN
    for b in range(bands):
        f0 = lo + (hi - lo) * b / bands
        f1 = lo + (hi - lo) * (b + 1) / bands
        if side in ("left", "right"):
            y0, y1 = int(h * f0), int(h * f1)
            strip = arr[y0:y1]                              # rows, cols, 3
            line = strip.mean(axis=0)                       # (w, 3)
            if side == "right":
                line = line[::-1]
        else:
            x0, x1 = int(w * f0), int(w * f1)
            strip = arr[:, x0:x1]
            line = strip.mean(axis=1)                       # (h, 3)
            if side == "bottom":
                line = line[::-1]
        start = max(0, int(outer_px) - 6)
        out.append((start, line[start:start + depth]))
    return out


def _outer_edge(vals, lines=True):
    """
    Where the card starts in a 1-D value profile running in from the bed.

    Returns (edge, clear): the edge to a fraction of a pixel, and the first
    position past anything that belongs to the edge itself — where the search
    for the border's inner edge may begin.

    Two kinds of edge turn up. Usually the card is simply brighter than the
    bed, and the edge is where the profile is halfway up. But a BLACK border
    on a black bed is not brighter at all — scanners clip both to 0,0,0 — and
    the only trace of the card's edge is a thin bright line where the
    scanner's light catches the cut. That line IS the edge, so its centre is
    taken; and the search for the inner edge starts after it, or the line's
    own falling side would be reported as the border's inner edge.
    """
    bg = float(np.median(vals[:3]))
    lit = np.where(vals > bg + 10)[0]
    if not len(lit):
        return None, None
    i0 = int(lit[0])
    win = vals[i0:i0 + 8]
    j = i0 + int(np.argmax(win))
    pk = float(vals[j])
    after = vals[j + 1:j + 7]
    if lines and len(after) >= 4 and after.min() < bg + 0.4 * (pk - bg) and \
            np.median(vals[j + 4:j + 14]) < bg + 0.4 * (pk - bg):
        half = bg + 0.5 * (pk - bg)
        lo, hi = j, j
        while lo > 0 and vals[lo - 1] > half:
            lo -= 1
        while hi < len(vals) - 1 and vals[hi + 1] > half:
            hi += 1
        idx = np.arange(lo, hi + 1)
        wts = vals[lo:hi + 1] - bg
        return float((idx * wts).sum() / wts.sum()), -(hi + 2)
    # The card's level is read well into it (the upper part of the next 20
    # px), so a faint speck or moulding line just outside the card — which
    # sets i0 — does not drag the halfway point down onto itself.
    level = float(np.percentile(vals[i0:i0 + 20], 75)) if len(vals) > i0 + 20 else pk
    pos = _edge_position(vals, (level + bg) / 2.0)
    return pos, pos


def _outline_box(value):
    """
    The card's extent from its OUTLINE rather than its area.

    A row or column counts as card if a quarter of it is lit — which a filled
    card manages easily, and so does the thin bright line that is all a black
    border leaves on a black bed. `imaging._card_box` asks for 40% of the
    area, and on a black-bordered card finds the artwork frame instead.
    """
    lit = value > imaging.INK_THRESHOLD * 0.6
    cols = np.where(lit.mean(axis=0) > 0.25)[0]
    rows = np.where(lit.mean(axis=1) > 0.25)[0]
    if not len(cols) or not len(rows):
        return None
    return int(cols[0]), int(rows[0]), int(cols[-1]), int(rows[-1])


def _straighten(im):
    """
    Deskew, then crop to the card's outline with a generous margin.

    Returns (padded, angle, clipped): `clipped` names any side where the card
    runs into the edge of the scan — a scanner auto-crop that cut it off, which
    no measurement can recover.
    """
    angle = imaging._skew_degrees(imaging._mask(im))
    rot = im.rotate(-angle, resample=Image.BICUBIC, expand=True, fillcolor=(0, 0, 0))
    value = np.asarray(rot).astype(int).max(axis=2)
    box = _outline_box(value)
    W, H = rot.size
    if box is None:
        return rot, angle, []
    x0, y0, x1, y1 = box
    # A card cut off by the scanner's auto-crop has its artwork running
    # straight into the scan's own edge. Judged on the scan before rotation:
    # a tight crop that leaves only a few pixels of bed is not cut off, and
    # the black wedges a rotation adds would hide the difference.
    src = np.asarray(im).astype(int).max(axis=2) > imaging.INK_THRESHOLD
    clipped = [n for n, strip in (("left", src[:, :2]), ("right", src[:, -2:]),
                                  ("top", src[:2]), ("bottom", src[-2:]))
               if strip.mean() > 0.3]
    m = round(0.04 * (x1 - x0))
    return rot.crop((x0 - m, y0 - m, x1 + 1 + m, y1 + 1 + m)), angle, clipped


def _rebuild_hidden_edges(sides, dpi, card_mm):
    """
    Put back a card edge the scan did not record, from the one opposite it.

    A black border on a black bed is clipped to 0,0,0 along with the bed, and
    the card's edge survives only where the scanner's light rakes across the
    cut — usually two sides, sometimes one. On a side with no such line, the
    "edge" detection found is really the artwork frame: the border's INNER
    edge. With the scan's DPI known, the card's size puts the missing outer
    edge exactly: 63 mm from the other side's line.

    Only done when the pair disagrees with the card's size by more than 3%,
    and only from a side whose edge was seen as a line. The side rebuilt is
    marked "estimated" — card sizes vary by a tenth of a millimetre or so,
    which is a pixel at 300 DPI.
    """
    notes = []
    px_per_mm = dpi / 25.4
    for a, b, mm in (("left", "right", card_mm[0]), ("top", "bottom", card_mm[1])):
        sa, sb = sides[a], sides[b]
        want = mm * px_per_mm
        have = sb.outer - sa.outer
        if abs(have - want) / want <= 0.03:
            continue
        lines = [s for s in (sa, sb) if s.edge == "line"]
        if len(lines) != 1:
            continue
        seen = lines[0]
        hidden = sb if seen is sa else sa
        direction = 1 if hidden is sb else -1
        # What was found as the hidden side's outer edge is the frame —
        # its inner edge. Keep it there; move the outer edge out to size.
        hidden.inner = hidden.outer
        hidden.outer = seen.outer + direction * want
        hidden.bands = []
        hidden.edge = "estimated"
        hidden.sure = True
        notes.append(f"{hidden.name} edge not visible on the scan (black on "
                     f"black) — placed from the {seen.name} edge and the card's "
                     f"{mm:g} mm size")
    return notes


def measure(im, card_mm=CARD_MM):
    """
    Measure one face. `im` is a scan as loaded by `imaging.load`.

    Returns a Measurement whose coordinates are in a deskewed copy of the card
    with a margin of background around it.
    """
    dpi = (im.info.get("dpi") or (0, 0))[0]
    im, how = isolate(im)
    padded, angle, clipped = _straighten(im)
    arr = np.asarray(padded.convert("RGB")).astype(float)
    h, w = arr.shape[:2]
    value = arr.max(axis=2)

    box = _outline_box(value)
    if box is None:
        box = (0, 0, w - 1, h - 1)
    x0, y0, x1, y1 = box
    rough = {"left": x0, "right": w - 1 - x1, "top": y0, "bottom": h - 1 - y1}
    across = {"left": x1 - x0, "right": x1 - x0, "top": y1 - y0, "bottom": y1 - y0}

    sides, notes = {}, []
    for name in SIDES:
        dim = across[name]
        depth = int(dim * SEARCH[1]) + 12
        widths, outers, kinds = [], [], []
        for start, prof in _profiles(arr, name, rough[name], depth, BANDS):
            vals = prof.max(axis=1)
            # Edge LINES only on a scan: black-on-black clipping is a scanner
            # bed's doing, and in a photo a thin line outside the card is far
            # more likely the slab's moulding than the card's cut.
            outer, clear = _outer_edge(vals, lines=(how == "scan"))
            if outer is None:
                widths.append(None)
                continue
            kinds.append("line" if clear < 0 else "seen")
            clear = abs(clear)
            span = max(2, round(dim * 0.006))
            s0 = max(int(math.ceil(outer + dim * SEARCH[0])) + 1,
                     int(math.ceil(clear)) + span + 1)
            s1 = int(outer + dim * SEARCH[1])
            inner = _first_step(prof, s0, s1, span=span)
            outers.append(start + outer)
            widths.append(None if inner is None else inner - outer)

        good = [x for x in widths if x is not None]
        outer_pos = float(np.median(outers)) if outers else float(rough[name])
        if good:
            width = float(np.median(good))
            spread = (max(good) - min(good)) / width if width else 1.0
            sure = len(good) >= (BANDS + 1) // 2 + 1 and spread <= SPREAD_LIMIT * 2
        else:
            width, sure = 0.0, False
            notes.append(f"no border found on the {name} — full art, or set it by hand")

        # Back into image coordinates (the right and bottom were read reversed).
        if name == "left":
            o, i = outer_pos, outer_pos + width
        elif name == "top":
            o, i = outer_pos, outer_pos + width
        elif name == "right":
            o, i = (w - 1) - outer_pos + 1, (w - 1) - outer_pos + 1 - width
        else:
            o, i = (h - 1) - outer_pos + 1, (h - 1) - outer_pos + 1 - width
        edge = "line" if kinds.count("line") > len(kinds) / 2 else "seen"
        sides[name] = Side(name, o, i, widths, sure, edge)

    unsure = [s.name for s in sides.values() if not s.sure]
    if len(unsure) >= 3:
        # Nothing consistent anywhere: a full-art card, or a border the same
        # colour as the art. Per-side notes would just be noise.
        notes = ["no consistent border found — full art? Set the lines by hand"]
    else:
        notes += [f"{n} border is uncertain — check the line" for n in unsure
                  if sides[n].width]
    if clipped:
        notes.insert(0, f"card runs off the scan on the {' and '.join(clipped)} — "
                        "the scanner cut it off; rescan before trusting this")
    if "not isolated" in how:
        notes.insert(0, "could not pick the card out of this photo — the lines "
                        "are likely on the wrong thing")
    if dpi and not clipped:
        notes += _rebuild_hidden_edges(sides, dpi, card_mm)
    m = Measurement(padded, sides, angle, card_mm, notes)
    m.source = how
    m.recompute()
    # The scan's own DPI against the card's measured size is a check on the
    # outer edges: a 63 mm card at 300 DPI is 744 px. Off by more than 3% and
    # an edge was missed — on a black border, usually because the scanner
    # left no trace of it at all.
    if dpi and m.dpi and abs(m.dpi - dpi) / dpi > 0.03 and not clipped:
        m.notes.insert(0, f"card measures as {m.dpi:.0f} DPI but the scan says "
                          f"{dpi:.0f} — the card's edge was probably missed")
    return m


# ---------------------------------------------------------------- reporting


def report(front, back):
    """
    Everything a person reads about one card's centering, as plain values:
    the four ratios, the border widths in mm, and the best grade each company
    could give on centering alone.
    """
    out = {"front": _face(front), "back": _face(back) if back else None}
    out["grades"] = grading.best_grades(
        front.lr[0], front.tb[0],
        back.lr[0] if back else None, back.tb[0] if back else None)
    # A grade read off a measurement that failed its own checks is worse than
    # none: it looks exactly like a real one. Say so on every row.
    out["trusted"] = front.trusted and (back is None or back.trusted)
    if not out["trusted"]:
        for g in out["grades"]:
            g["label"] = f"? {g['label']}"
            g["short"] = "?" + g["short"]
            g["grade"] = None
    return out


def _face(m):
    lr, tb = m.lr, m.tb
    return {
        "lr": lr[0], "lr_heavy": "left" if lr[1] == "first" else "right",
        "tb": tb[0], "tb_heavy": "top" if tb[1] == "first" else "bottom",
        "mm": {s: m.mm(s) for s in SIDES},
        "px": {s: m.sides[s].width for s in SIDES},
        "dpi": m.dpi,
        "angle": m.angle,
        "sure": m.sure,
        "notes": list(m.notes),
    }


# ---------------------------------------------------------------- the sheet

LINE_OUTER = (0, 200, 255)
LINE_INNER = (255, 60, 160)
SHEET_BG = imaging.SHEET_BG
TEXT = (235, 235, 235)
DIM = (150, 150, 150)
GOOD = (90, 210, 120)
WARN = (255, 190, 60)


def draw_lines(m, scale=1.0, width=2):
    """The card with its outer (cyan) and inner (magenta) border lines drawn."""
    im = m.card.copy()
    if scale != 1.0:
        im = im.resize((max(1, round(im.size[0] * scale)),
                        max(1, round(im.size[1] * scale))), Image.LANCZOS)
    d = ImageDraw.Draw(im)
    W, H = im.size
    for s in m.sides.values():
        for pos, colour in ((s.outer, LINE_OUTER), (s.inner, LINE_INNER)):
            p = pos * scale
            if s.name in ("left", "right"):
                d.line([(p, 0), (p, H)], fill=colour, width=width)
            else:
                d.line([(0, p), (W, p)], fill=colour, width=width)
    return im


def sheet(front, back, path, title=""):
    """
    One image per card: both faces with their measured lines, the ratios and
    border widths beside each, and the best grade per company underneath.

    Built to be read, not listed — it carries the measurement so a centering
    claim can be checked against the lines it came from.
    """
    faces = [("FRONT", front)] + ([("BACK", back)] if back else [])
    face_h = 900
    pics = []
    for name, m in faces:
        k = face_h / m.card.size[1]
        pics.append((name, m, draw_lines(m, k, width=max(2, round(3 * k)))))
    text_w = 420
    pad = 30
    col_w = max(p.size[0] for _, _, p in pics) + text_w + pad
    rep = report(front, back)
    grade_rows = len(rep["grades"])
    W = pad + col_w * len(pics)
    H = 70 + face_h + pad + 60 + grade_rows * 34 + pad
    out = Image.new("RGB", (W, H), SHEET_BG)
    d = ImageDraw.Draw(out)
    big, mid, small = imaging._font(30, True), imaging._font(22, True), imaging._font(19)
    d.text((pad, 20), title or "Centering", fill=TEXT, font=big)

    for i, (name, m, pic) in enumerate(pics):
        x = pad + i * col_w
        out.paste(pic, (x, 70))
        tx, ty = x + pic.size[0] + 20, 74
        f = rep["front" if name == "FRONT" else "back"]
        d.text((tx, ty), name, fill=TEXT, font=mid)
        ty += 40
        for label, val, heavy in (("Left / Right", f["lr"], f["lr_heavy"]),
                                  ("Top / Bottom", f["tb"], f["tb_heavy"])):
            d.text((tx, ty), label, fill=DIM, font=small)
            d.text((tx, ty + 24), f"{val:.1f} / {100 - val:.1f}", fill=TEXT, font=mid)
            d.text((tx + 200, ty + 28), f"{heavy} wider", fill=DIM, font=small)
            ty += 68
        ty += 6
        for s in SIDES:
            d.text((tx, ty), f"{s:<7}", fill=DIM, font=small)
            d.text((tx + 90, ty), f"{f['mm'][s]:.2f} mm   {f['px'][s]:.1f} px",
                   fill=TEXT, font=small)
            ty += 28
        ty += 10
        d.text((tx, ty), f"≈{f['dpi']:.0f} DPI   deskew {f['angle']:+.2f}°",
               fill=DIM, font=small)
        ty += 34
        for note in f["notes"][:4]:
            for line in _wrap(note, 38):
                d.text((tx, ty), line, fill=WARN, font=small)
                ty += 24

    y = 70 + face_h + pad
    d.text((pad, y), "Best grade on centering alone", fill=TEXT, font=mid)
    if not rep["trusted"]:
        d.text((pad + 400, y + 2), "— NOT RELIABLE: the measurement failed its own "
                                   "checks (see the notes above)", fill=WARN, font=small)
    y += 44
    for g in rep["grades"]:
        colour = GOOD if g["grade"] else WARN
        d.text((pad, y), g["company"], fill=DIM, font=small)
        d.text((pad + 170, y), g["label"], fill=colour, font=small)
        d.text((pad + 520, y), g["limit"], fill=DIM, font=small)
        y += 34
    out.save(path, quality=94)
    return out.size


def _wrap(text, n):
    words, line, out = text.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > n:
            out.append(line)
            line = w
        else:
            line = (line + " " + w).strip()
    if line:
        out.append(line)
    return out


def measure_path(path, card_mm=CARD_MM):
    return measure(load(path), card_mm)


def csv_row(label, rep):
    f, b = rep["front"], rep["back"] or {}
    row = {
        "card": label,
        "front_lr": f"{f['lr']:.1f}/{100 - f['lr']:.1f}",
        "front_tb": f"{f['tb']:.1f}/{100 - f['tb']:.1f}",
        "back_lr": f"{b['lr']:.1f}/{100 - b['lr']:.1f}" if b else "",
        "back_tb": f"{b['tb']:.1f}/{100 - b['tb']:.1f}" if b else "",
    }
    for face, data in (("front", f), ("back", b)):
        for s in SIDES:
            row[f"{face}_{s}_mm"] = f"{data['mm'][s]:.2f}" if data else ""
    for g in rep["grades"]:
        row[g["company"]] = g["short"]
    row["check"] = "; ".join(
        [f"front: {n}" for n in f["notes"]] + [f"back: {n}" for n in (b.get("notes") or [])])
    return row

