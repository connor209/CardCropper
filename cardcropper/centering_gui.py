"""
The centering window: one card, front and back, with every measured line
draggable and the ratios and grades following it live.

Auto-detection gets most cards to a fraction of a pixel, but not all of them —
a full-art card has no border to find, and art that bleeds into the border on
one side gives the wrong edge there. The window exists for those cards, and
for checking the rest: a number you cannot see the lines behind is not one to
send a card off on.

Using it:
  - Click a line (cyan = the card's edge, magenta = the border's inner edge)
    to select it; drag it, or nudge it with the arrow keys — 0.25 px a press,
    2 px with Shift. The loupe shows the selected line at 6x against the scan.
  - Re-detect throws away hand adjustments for that face.
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from . import centering, imaging

VIEW_H = 640          # on-screen height of each face
LOUPE = 64            # source pixels across the loupe
LOUPE_ZOOM = 6
GRAB = 7              # how close (screen px) a click must be to grab a line
COLOURS = {"outer": "#00c8ff", "inner": "#ff3ca0"}


class FaceView(ttk.Frame):
    """One face: the scan with its eight lines, draggable."""

    def __init__(self, master, title, on_change, on_select):
        super().__init__(master)
        self.title = title
        self.on_change = on_change
        self.on_select = on_select
        self.m = None
        self.source = None
        self.k = 1.0
        self.photo = None
        self.selected = None              # (side, "outer"|"inner")
        ttk.Label(self, text=title, font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        self.canvas = tk.Canvas(self, width=460, height=VIEW_H, bg="#121212",
                                highlightthickness=0, cursor="crosshair")
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)

    def load(self, image):
        self.source = image
        self.detect()

    def detect(self):
        if self.source is None:
            return
        self.m = centering.measure(self.source)
        self.k = VIEW_H / self.m.card.size[1]
        shown = self.m.card.resize((round(self.m.card.size[0] * self.k), VIEW_H),
                                   Image.LANCZOS)
        self.canvas.configure(width=shown.size[0])
        self.photo = ImageTk.PhotoImage(shown)
        self.selected = None
        self.redraw()
        self.on_change()

    def redraw(self):
        c = self.canvas
        c.delete("all")
        if self.m is None:
            return
        c.create_image(0, 0, image=self.photo, anchor="nw")
        W, H = self.photo.width(), self.photo.height()
        for s in self.m.sides.values():
            for kind in ("outer", "inner"):
                p = getattr(s, kind) * self.k
                width = 3 if self.selected == (s.name, kind) else 1
                if s.name in ("left", "right"):
                    c.create_line(p, 0, p, H, fill=COLOURS[kind], width=width)
                else:
                    c.create_line(0, p, W, p, fill=COLOURS[kind], width=width)

    def _nearest(self, x, y):
        best, dist = None, GRAB + 1
        for s in self.m.sides.values():
            for kind in ("outer", "inner"):
                p = getattr(s, kind) * self.k
                d = abs((x if s.name in ("left", "right") else y) - p)
                if d < dist:
                    best, dist = (s.name, kind), d
        return best

    def _press(self, e):
        if self.m is None:
            return
        self.canvas.focus_set()
        self.selected = self._nearest(e.x, e.y)
        self.redraw()
        self.on_select(self)

    def _drag(self, e):
        if self.m is None or self.selected is None:
            return
        side, kind = self.selected
        pos = (e.x if side in ("left", "right") else e.y) / self.k
        self._set(side, kind, pos)

    def nudge(self, delta):
        if self.selected is None:
            return
        side, kind = self.selected
        self._set(side, kind, getattr(self.m.sides[side], kind) + delta)

    def _set(self, side, kind, pos):
        s = self.m.sides[side]
        setattr(s, kind, float(pos))
        s.bands = []                       # a hand-set line has no bands
        s.sure = True
        self.m.notes = [n for n in self.m.notes
                        if side not in n and "full art" not in n]
        self.m.recompute()
        self.redraw()
        self.on_change()

    def loupe_image(self):
        """The selected line at LOUPE_ZOOM, from the full-resolution scan."""
        if self.m is None or self.selected is None:
            return None
        side, kind = self.selected
        s = self.m.sides[side]
        W, H = self.m.card.size
        pos = getattr(s, kind)
        half = LOUPE // 2
        if side in ("left", "right"):
            cx, cy = pos, H / 2
        else:
            cx, cy = W / 2, pos
        box = (int(cx - half), int(cy - half), int(cx + half), int(cy + half))
        crop = self.m.card.crop(box).resize((LOUPE * LOUPE_ZOOM,) * 2, Image.NEAREST)
        return crop, (pos - (box[0] if side in ("left", "right") else box[1])) * LOUPE_ZOOM, side


class CenteringWindow(tk.Toplevel):
    def __init__(self, master, front_path, back_path=None, label=""):
        super().__init__(master)
        self.title(f"Centering — {label or os.path.basename(front_path)}")
        self.paths = (front_path, back_path)
        self.label = label or os.path.splitext(os.path.basename(front_path))[0]

        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        self.front = FaceView(body, "Front", self._update, self._selected)
        self.front.grid(row=0, column=0, sticky="n", padx=(0, 10))
        self.back = FaceView(body, "Back", self._update, self._selected)
        if back_path:
            self.back.grid(row=0, column=1, sticky="n", padx=(0, 10))

        side = ttk.Frame(body)
        side.grid(row=0, column=2, sticky="nsew")
        self.figures = tk.Text(side, width=46, height=15, wrap="word",
                               font=("Consolas", 10), relief="flat")
        self.figures.pack(fill="x")
        self.grades = ttk.Treeview(side, columns=("company", "grade", "limit"),
                                   show="headings", height=8)
        for key, text, w in (("company", "Grader", 70), ("grade", "Best on centering", 170),
                             ("limit", "Next grade needs", 150)):
            self.grades.heading(key, text=text)
            self.grades.column(key, width=w, anchor="w")
        self.grades.pack(fill="x", pady=(8, 0))
        ttk.Label(side, text="Loupe — selected line, 6x").pack(anchor="w", pady=(10, 2))
        self.loupe = tk.Canvas(side, width=LOUPE * LOUPE_ZOOM, height=LOUPE * LOUPE_ZOOM,
                               bg="#121212", highlightthickness=0)
        self.loupe.pack(anchor="w")
        self.loupe_photo = None

        bar = ttk.Frame(side)
        bar.pack(fill="x", pady=(10, 0))
        ttk.Button(bar, text="Re-detect", command=self._redetect).pack(side="left")
        ttk.Button(bar, text="Save sheet…", command=self._save).pack(side="left", padx=6)
        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right")
        ttk.Label(side, foreground="#666", wraplength=330, justify="left",
                  text="Click a line to select it, then drag or use the arrow "
                       "keys (Shift = bigger steps). Cyan is the card's edge, "
                       "magenta the inside of the border.").pack(anchor="w", pady=(8, 0))

        self.active = None
        for key, step in (("<Left>", -0.25), ("<Right>", 0.25), ("<Up>", -0.25),
                          ("<Down>", 0.25), ("<Shift-Left>", -2), ("<Shift-Right>", 2),
                          ("<Shift-Up>", -2), ("<Shift-Down>", 2)):
            self.bind(key, lambda _e, s=step: self._nudge(s))

        self.front.load(imaging.load(front_path))
        if back_path:
            self.back.load(imaging.load(back_path))

    # -------------------------------------------------------------- events

    def _selected(self, view):
        self.active = view
        for v in (self.front, self.back):
            if v is not view and v.selected:
                v.selected = None
                v.redraw()
        self._draw_loupe()

    def _nudge(self, step):
        if self.active:
            self.active.nudge(step)

    def _redetect(self):
        for v in (self.front, self.back):
            v.detect()

    def _faces(self):
        back = self.back.m if self.paths[1] else None
        return self.front.m, back

    def _update(self):
        front, back = self._faces()
        if front is None or (self.paths[1] and back is None):
            return
        rep = centering.report(front, back)
        t = self.figures
        t.configure(state="normal")
        t.delete("1.0", "end")
        for name, f in (("FRONT", rep["front"]), ("BACK", rep["back"])):
            if not f:
                continue
            t.insert("end", f"{name}\n")
            t.insert("end", f"  L/R  {f['lr']:.1f} / {100 - f['lr']:.1f}  ({f['lr_heavy']} wider)\n")
            t.insert("end", f"  T/B  {f['tb']:.1f} / {100 - f['tb']:.1f}  ({f['tb_heavy']} wider)\n")
            t.insert("end", "  " + "  ".join(f"{s[0].upper()} {f['mm'][s]:.2f}"
                                              for s in centering.SIDES) + " mm\n")
            for n in f["notes"]:
                t.insert("end", f"  ! {n}\n")
        t.configure(state="disabled")
        self.grades.delete(*self.grades.get_children())
        for g in rep["grades"]:
            self.grades.insert("", "end", values=(g["company"], g["label"], g["next"]))
        self._draw_loupe()

    def _draw_loupe(self):
        self.loupe.delete("all")
        view = self.active
        got = view.loupe_image() if view else None
        if not got:
            return
        img, p, side = got
        self.loupe_photo = ImageTk.PhotoImage(img)
        self.loupe.create_image(0, 0, image=self.loupe_photo, anchor="nw")
        n = LOUPE * LOUPE_ZOOM
        colour = COLOURS[view.selected[1]]
        if side in ("left", "right"):
            self.loupe.create_line(p, 0, p, n, fill=colour)
        else:
            self.loupe.create_line(0, p, n, p, fill=colour)

    def _save(self):
        front, back = self._faces()
        path = filedialog.asksaveasfilename(
            parent=self, title="Save centering sheet",
            initialfile=f"{self.label}_centering.jpg", defaultextension=".jpg",
            filetypes=[("JPEG", "*.jpg")])
        if not path:
            return
        try:
            centering.sheet(front, back, path, title=f"Centering — {self.label}")
        except OSError as exc:
            messagebox.showerror("Centering", str(exc), parent=self)
