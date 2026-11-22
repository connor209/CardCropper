"""
Grading a conditioning batch without leaving the window.

The Condition tab ranks a batch worst-first; this panel sits beside that
ranking so the card selected in it can be looked at and graded on the spot:
its crops at a glance, one click (or key) per grade, a flaw note built from
the usual phrases, and a confidence. Saving writes the row of
conditioning-review.csv — the same file a spreadsheet would — so the
record, calibrate and write steps are unchanged, and records the grade in
the grade library with the card's images.

`looked` is set to yes: the grade was keyed with the crops on screen. The
crease rule is checked as the grade is saved, not at the write step a batch
later: a grade above HP whose note mentions a crease is refused here with the
reason, while the card is still in front of the grader.
"""

import csv
import os

import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from . import conditioning, grading, library, ui

VIEWS = (("corners", "Corners"), ("back", "Back"), ("front", "Front"),
         ("faces", "Both faces"))
FILES = {"corners": ("corners-back.jpg",), "back": ("crops-back.jpg",),
         "front": ("crops-front.jpg",), "faces": ("front.jpg", "back.jpg")}
#: Phrases a flaw note is usually made of — a click adds one.
PHRASES = ("whitening on back corners", "back edge whitening", "front edge wear",
           "corner wear", "surface scratches", "print line", "dent", "crease",
           "off-centre")
#: The review-sheet columns a save changes, and an undo puts back.
UNDO_FIELDS = ("grade", "flaw_note", "confidence", "looked", "evidence")
KEYS = {"1": "NM", "2": "LP", "3": "MP", "4": "HP", "5": "DMG", "6": "HOLD"}
PREVIEW = (360, 240)


def read_sheet(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or conditioning.REVIEW_FIELDS), list(reader)


def write_row(path, sku, values):
    """Update one card's row of a review sheet, keeping every other row and column."""
    fields, rows = read_sheet(path)
    for k in values:
        if k not in fields:
            fields.append(k)
    for r in rows:
        if (r.get("sku") or "").strip() == sku:
            r.update(values)
            break
    else:
        rows.append(dict(values, sku=sku))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


class ReviewPanel(ttk.Frame):
    """
    `out_dir()` gives the batch's output folder; `on_saved(sku, grade)` is
    called after a save (the tab moves to the next card and updates its row).
    """

    def __init__(self, master, out_dir, on_saved=None, on_undone=None):
        super().__init__(master, padding=(12, 0, 0, 0))
        self.out_dir = out_dir
        self.on_saved = on_saved
        self.on_undone = on_undone
        self.undo_stack = []
        self.bind_all("<Control-z>", self.undo, add="+")
        self.rec = None
        self.photos = []
        self.view = tk.StringVar(value="corners")
        self.columnconfigure(0, weight=1)

        self.head = ttk.Label(self, style="Heading.TLabel", wraplength=360, justify="left",
                              text="Select a card to grade it here")
        self.head.grid(row=0, column=0, sticky="w")
        self.sub = ttk.Label(self, style="Muted.TLabel", wraplength=360, justify="left")
        self.sub.grid(row=1, column=0, sticky="w", pady=(2, 6))

        # The controls first and the picture last: on a short screen it is
        # the picture that gets cut, never the buttons.
        grades = ttk.Frame(self)
        grades.grid(row=2, column=0, sticky="w")
        self.grade_var = tk.StringVar()
        self.grade_btns = {}
        for i, g in enumerate(grading.KEYS):
            b = ttk.Button(grades, text=g, width=5 if g == "HOLD" else 4,
                           style=f"{g}.Grade.TButton",
                           command=lambda g=g: self.pick(g))
            b.grid(row=0, column=i, padx=(0, 2))
            ui.tip(b, {"NM": "Near Mint", "LP": "Lightly Played", "MP": "Moderately Played",
                       "HP": "Heavily Played — any crease is HP at best",
                       "DMG": "Damaged",
                       "HOLD": "Do not list yet: a rescan, a wrong pair, a doubt"}[g]
                  + f"  (key {i + 1} with the table selected)")
            self.grade_btns[g] = b
        self.chosen = ttk.Label(self, style="Muted.TLabel")
        self.chosen.grid(row=3, column=0, sticky="w", pady=(2, 4))

        note = ttk.Frame(self)
        note.grid(row=4, column=0, sticky="ew")
        note.columnconfigure(0, weight=1)
        self.note_var = tk.StringVar()
        e = ttk.Entry(note, textvariable=self.note_var)
        e.grid(row=0, column=0, sticky="ew")
        ui.tip(e, "Flaw note: one line a buyer would want to know — it is added to the "
                  "listing's description. Phrases ▾ adds the usual ones.")
        ui.more_menu(note, [(p, lambda p=p: self._add_phrase(p)) for p in PHRASES],
                     text="Phrases", tip_text="Add a common flaw phrase to the note.")\
            .grid(row=0, column=1, padx=(4, 0))

        row = ttk.Frame(self)
        row.grid(row=5, column=0, sticky="ew", pady=(6, 0))
        self.conf_var = tk.StringVar(value="high")
        seg = ui.segmented(row, self.conf_var, (("high", "Sure"), ("medium", "Fairly"),
                                                ("low", "Unsure")))
        seg.pack(side="left")
        ui.tip(seg, "How sure you are. Fairly and Unsure grades are suggested as borderline "
                    "in the grade library — the cards golden sets are short of.")
        self.save_btn = ttk.Button(row, text="Save & next", style="Accent.TButton",
                                   command=self.save, state="disabled")
        self.save_btn.pack(side="right")
        ui.tip(self.save_btn, "Write this grade into conditioning-review.csv (looked = yes), "
                              "add it to the grade library, and move to the next card "
                              "(Enter).")
        self.status = ttk.Label(self, wraplength=360, justify="left")
        self.status.grid(row=6, column=0, sticky="w", pady=(4, 0))
        self.hint = ttk.Label(self, style="Muted.TLabel", wraplength=360, justify="left")
        self.hint.grid(row=7, column=0, sticky="w")

        seg = ui.segmented(self, self.view, VIEWS, command=self._draw)
        seg.grid(row=8, column=0, sticky="w", pady=(6, 0))
        ui.tip(seg, "What to look at. Back corners is where the score looks; open the "
                    "crops for the edges and the front. Click the picture to open it full "
                    "size.")
        self.pic = tk.Canvas(self, width=PREVIEW[0], height=PREVIEW[1], background="#121212",
                             highlightthickness=0, cursor="hand2")
        self.pic.grid(row=9, column=0, sticky="nsew", pady=(4, 0))
        self.rowconfigure(9, weight=1)
        self.pic.bind("<Configure>", lambda _e: self._draw())

    # ------------------------------------------------------------ showing

    def undo(self, _e=None):
        """
        Ctrl+Z: put the last saved card's row of the review sheet back as it
        was, take its grade out of the grade library (or put the old one
        back), and return to the card.
        """
        if not self.winfo_viewable() or not self.undo_stack:
            return
        if isinstance(self.focus_get(), (tk.Entry, ttk.Entry)):
            return
        sku, path, before, rec = self.undo_stack.pop()
        try:
            write_row(path, sku, before)
        except PermissionError:
            self.undo_stack.append((sku, path, before, rec))
            self.status.configure(foreground=ui.C["bad"],
                                  text="Could not undo: the review sheet is open in another "
                                       "program. Close it there and press Ctrl+Z again.")
            return "break"
        library.flush(5)
        library.delete_grade(sku, "review-sheet", os.path.abspath(path), "")
        old = (before.get("grade") or "").upper()
        if old in grading.GRADES:
            library.record_later(sku, old, "review-sheet", origin=os.path.abspath(path),
                                 card=rec.get("title", ""), notes=before.get("flaw_note", ""),
                                 confidence=before.get("confidence", ""), looked="yes")
        if self.on_undone:
            self.on_undone(sku, old)
        self.status.configure(foreground=ui.C["warn"],
                              text=f"Undone: {sku} is back to "
                                   f"{old or 'ungraded'}. Grade it again.")
        return "break"

    def _folder(self):
        return os.path.join(self.out_dir(), "cards", conditioning.safe_name(self.rec["sku"]))

    def review_path(self):
        return os.path.join(self.out_dir(), "conditioning-review.csv")

    def show(self, rec):
        self.rec = rec
        self.head.configure(text=f"{rec['sku']}  ·  {rec.get('title', '')}")

        def num(k):
            v = rec.get(k)
            try:
                return f"{float(v):.2f}%"
            except (TypeError, ValueError):
                return "—"
        self.sub.configure(text=f"{rec.get('triage', '')} · worst corner "
                                f"{num('worst_corner_pct')} · wear {num('wear_total_pct')}"
                                + ("  ·  WotC era — check front and back are one card"
                                   if rec.get("check_back_era") else ""))
        existing = {}
        if os.path.exists(self.review_path()):
            try:
                _, rows = read_sheet(self.review_path())
                existing = next((r for r in rows if (r.get("sku") or "").strip() == rec["sku"]),
                                {})
            except OSError:
                pass
        self.grade_var.set((existing.get("grade") or "").upper())
        self.note_var.set(existing.get("flaw_note") or "")
        self.conf_var.set(existing.get("confidence") or "high")
        self._show_choice()
        self.status.configure(text="")
        self._library_hint()
        self._draw()

    def _draw(self):
        self.pic.delete("all")
        self.pic.unbind("<Button-1>")
        self.photos = []
        if not self.rec:
            return
        paths = [os.path.join(self._folder(), n) for n in FILES[self.view.get()]]
        paths = [p for p in paths if os.path.exists(p)]
        if not paths:
            self.pic.create_text(PREVIEW[0] / 2, PREVIEW[1] / 2, fill="#999", justify="center",
                                 text="No image — crops were off,\nor the scan was unusable.")
            return
        try:
            ims = []
            for p in paths:
                with Image.open(p) as im:
                    ims.append(im.convert("RGB"))
            if len(ims) == 2:
                h = min(i.size[1] for i in ims)
                ims = [i.resize((round(i.size[0] * h / i.size[1]), h)) for i in ims]
                both = Image.new("RGB", (ims[0].size[0] + ims[1].size[0] + 12, h), (18, 18, 18))
                both.paste(ims[0], (0, 0))
                both.paste(ims[1], (ims[0].size[0] + 12, 0))
                im = both
            else:
                im = ims[0]
            size = (max(self.pic.winfo_width(), 120), max(self.pic.winfo_height(), 120))
            im.thumbnail(size, Image.LANCZOS)
            photo = ImageTk.PhotoImage(im)
        except Exception as exc:                            # noqa: BLE001
            self.pic.create_text(PREVIEW[0] / 2, PREVIEW[1] / 2, fill="#999",
                                 text=f"unreadable: {exc}")
            return
        self.photos.append(photo)
        self.pic.create_image(size[0] / 2, size[1] / 2, image=photo)
        from .gui import _reveal
        self.pic.bind("<Button-1>", lambda _e: [_reveal(p) for p in paths])

    def _library_hint(self):
        try:
            wc = float(self.rec.get("worst_corner_pct"))
        except (TypeError, ValueError):
            self.hint.configure(text="")
            return
        try:
            counts, near = library.similar(worst_corner_pct=wc, exclude=self.rec["sku"])
        except Exception:                                   # noqa: BLE001
            near = []
        if len(near) >= 3:
            self.hint.configure(text="Cards that scored like this were graded: " + ", ".join(
                f"{g} {counts[g]}" for g in grading.GRADES if counts.get(g))
                + f" (nearest {len(near)} in the grade library). Advice — the crops decide.")
        else:
            self.hint.configure(text="")

    # ------------------------------------------------------------ grading

    def pick(self, g):
        if not self.rec:
            return
        self.grade_var.set(g)
        self._show_choice()

    def _show_choice(self):
        g = self.grade_var.get()
        self.chosen.configure(text=f"Grade: {g}" if g else "Pick a grade (1-6)",
                              foreground=ui.GRADE_COLOURS.get(g, ui.C["muted"]))
        self.save_btn.configure(state="normal" if g and self.rec else "disabled")

    def _add_phrase(self, p):
        cur = self.note_var.get().strip()
        if p not in cur:
            self.note_var.set(f"{cur}, {p}" if cur else p[0].upper() + p[1:])

    def save(self):
        if not self.rec:
            return
        g = self.grade_var.get()
        if g not in grading.KEYS:
            return
        note = self.note_var.get().strip()
        if g in grading.GRADES and grading.GRADES.index(g) < grading.GRADES.index(
                conditioning.CREASE_FLOOR) and conditioning.mentions_crease(note):
            self.status.configure(foreground=ui.C["bad"], text=(
                f"Not saved: the note mentions a crease, and any crease is "
                f"{conditioning.CREASE_FLOOR} or worse. Regrade, or reword if there is "
                "no crease."))
            return
        path = self.review_path()
        if not os.path.exists(path):
            self.status.configure(foreground=ui.C["bad"],
                                  text="No conditioning-review.csv here — prepare the batch "
                                       "first.")
            return
        sku = self.rec["sku"]
        _, rows = read_sheet(path)
        before = next((r for r in rows if (r.get("sku") or "").strip() == sku), {})
        self.undo_stack.append((sku, path, {k: before.get(k, "") for k in UNDO_FIELDS},
                                dict(self.rec)))
        try:
            write_row(path, sku, {"grade": g, "flaw_note": note,
                                  "confidence": self.conf_var.get(), "looked": "yes",
                                  "evidence": f"graded in CardCropper from "
                                              f"{dict(VIEWS)[self.view.get()].lower()}"})
        except PermissionError:
            self.status.configure(foreground=ui.C["bad"],
                                  text="Could not save: the review sheet is open in another "
                                       "program (Excel?). Close it there and save again.")
            return
        if g in grading.GRADES:
            folder = self._folder()
            library.record_later(
                sku, g, "review-sheet", origin=os.path.abspath(path),
                images={"front": os.path.join(folder, "front.jpg"),
                        "back": os.path.join(folder, "back.jpg"),
                        "front_crops": os.path.join(folder, "crops-front.jpg"),
                        "back_crops": os.path.join(folder, "crops-back.jpg")},
                card=self.rec.get("title", ""), batch=os.path.basename(self.out_dir()),
                game="yugioh" if sku.upper().startswith("YGO-") else "pokemon",
                line="yugioh" if sku.upper().startswith("YGO-") else "pokemon-en",
                looked="yes", notes=note, confidence=self.conf_var.get(),
                worst_corner_pct=_float(self.rec.get("worst_corner_pct")),
                wear_total_pct=_float(self.rec.get("wear_total_pct")),
                border_base=_float(self.rec.get("border_base")),
                front_worst_corner_pct=_float(self.rec.get("front_worst_corner_pct")),
                front_wear_total_pct=_float(self.rec.get("front_wear_total_pct")))
        self.status.configure(foreground=ui.C["ok"], text=f"Saved {sku}: {g}   (Ctrl+Z undoes)")
        if self.on_saved:
            self.on_saved(sku, g)


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
