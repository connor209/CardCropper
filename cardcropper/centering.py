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
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw

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

#: A colour change has to be at least this large (RGB distance per pixel,
#: after smoothing) to count as the border's inner edge. Below it is print
#: texture and scanner noise.
MIN_STEP = 9.0

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


def _first_step(profile, start, stop, depth_hint=None):
    """
    Where the border's colour first changes decisively, walking inward.

    `profile` is an (N, 3) RGB array running from outside the card inward.
    The border colour is taken just past `start`; the inner edge is the first
    place the colour departs from it by more than MIN_STEP per pixel, located
    to sub-pixel precision at the peak of that step.
    """
    seg = _smooth(profile.astype(float), 3)
    stop = min(stop, len(seg) - 2)
    if stop - start < 6:
        return None
    # Gradient magnitude: distance between neighbours either side.
    grad = np.linalg.norm(seg[2:] - seg[:-2], axis=1) / 2.0
    grad = np.concatenate([[0.0], grad, [0.0]])
    window = grad[start:stop]
    if not len(window) or window.max() < MIN_STEP:
        return None
    # The first peak that is both above the floor and a decent fraction of the
    # strongest in the window — so print texture inside the border does not
    # win, but artwork deeper in does not either.
    floor = max(MIN_STEP, 0.35 * window.max())
    for j in range(1, len(window) - 1):
        g = window[j]
        if g >= floor and g >= window[j - 1] and g >= window[j + 1]:
            i = start + j
            # Parabolic refinement of the peak.
            a, b, c = grad[i - 1], grad[i], grad[i + 1]
            den = a - 2 * b + c
            off = 0.5 * (a - c) / den if abs(den) > 1e-9 else 0.0
            return i + max(-0.5, min(0.5, off))
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


def measure(im, card_mm=CARD_MM):
    """
    Measure one face. `im` is a scan as loaded by `imaging.load`.

    Returns a Measurement whose coordinates are in a deskewed copy of the card
    with a margin of background around it.
    """
    _exact, padded, angle = imaging.straighten(im)
    arr = np.asarray(padded.convert("RGB")).astype(float)
    h, w = arr.shape[:2]
    value = arr.max(axis=2)

    box = imaging._card_box(value > imaging.INK_THRESHOLD)
    if box is None:
        box = (0, 0, w - 1, h - 1)
    x0, y0, x1, y1 = box
    rough = {"left": x0, "right": w - 1 - x1, "top": y0, "bottom": h - 1 - y1}
    across = {"left": x1 - x0, "right": x1 - x0, "top": y1 - y0, "bottom": y1 - y0}

    sides, notes = {}, []
    for name in SIDES:
        dim = across[name]
        depth = int(dim * SEARCH[1]) + 12
        widths, outers = [], []
        for start, prof in _profiles(arr, name, rough[name], depth, BANDS):
            vals = prof.max(axis=1)
            # Halfway between the bed and the border is where the card starts.
            card_level = np.median(vals[min(len(vals) - 1, 14):min(len(vals), 24)]) \
                if len(vals) > 24 else vals.max()
            outer = _edge_position(vals, (card_level + vals[:3].mean()) / 2.0)
            if outer is None:
                widths.append(None)
                continue
            s0 = int(math.ceil(outer + dim * SEARCH[0])) + 1
            s1 = int(outer + dim * SEARCH[1])
            inner = _first_step(prof, s0, s1)
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
        sides[name] = Side(name, o, i, widths, sure)

    unsure = [s.name for s in sides.values() if not s.sure]
    if len(unsure) >= 3:
        # Nothing consistent anywhere: a full-art card, or a border the same
        # colour as the art. Per-side notes would just be noise.
        notes = ["no consistent border found — full art? Set the lines by hand"]
    else:
        notes += [f"{n} border is uncertain — check the line" for n in unsure
                  if sides[n].width]
    m = Measurement(padded, sides, angle, card_mm, notes)
    m.recompute()
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
    return measure(imaging.load(path), card_mm)


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

