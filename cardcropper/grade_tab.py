"""
The "Grade" tab: the grading screen (feature 2.4).

One card at a time. Each of the 16 panels (four corners and four edges, front
and back) is shown at native size — magnified by a whole number if asked,
never shrunk — with the same panel of a clean and an LP reference card of the
same game beside it. Stepping onto a panel marks it opened (`looked`).

Keys: 1-6 or N / L / M / H / D / X for NM / LP / MP / HP / DMG / HOLD.
Right / Left (or Space / Backspace) step through the panels. The wear counts
from the crop checks are pre-filled and can be corrected.

The rules in `grading.enforce` decide what is recorded: NM is refused until
every panel has been opened, a capped grade says why, and a crease is never
MP. Each card's passes go into grades-<code>.csv beside the manifest; when
two passes disagree, the worse wins.

Reference cards: put `clean.jpg` and `lp.jpg` (a scan of one face each, or
`clean-front.jpg` / `clean-back.jpg` …) in references/<game line>/ in the
settings folder.
"""

import os
import queue
import threading

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from . import audits, batches, checks, conditioning, grading, imaging, wear

KEYMAP = {"1": "NM", "2": "LP", "3": "MP", "4": "HP", "5": "DMG", "6": "HOLD",
          "n": "NM", "l": "LP", "m": "MP", "h": "HP", "d": "DMG", "x": "HOLD"}
PANEL_NAMES = {"tl": "top-left corner", "tr": "top-right corner",
               "bl": "bottom-left corner", "br": "bottom-right corner",
               "top": "top edge", "right": "right edge", "bottom": "bottom edge",
               "left": "left edge"}
_CORNER_INDEX = {"tl": 0, "tr": 1, "bl": 2, "br": 3}
_EDGE_INDEX = {"top": 0, "bottom": 1, "left": 2, "right": 3}


def face_panels(path, background="dark"):
    """{panel: image} for one face, cut at the scan's own resolution."""
    exact, padded, _, _ = imaging.straighten(imaging.load(path), background=background)
    margin, cut, strip = imaging._cuts(padded)
    corners = imaging.corner_crops(padded, cut)
    edges = imaging.edge_crops(padded, cut, strip, margin, split=False)
    out = {}
    for p, i in _CORNER_INDEX.items():
        out[p] = corners[i][1]
    for p, i in _EDGE_INDEX.items():
        out[p] = edges[i][1]
    return out


def reference_dir(line):
    return os.path.join(conditioning.settings_dir(), "references", line)


def reference_file(line, kind, face):
    d = reference_dir(line)
    for name in (f"{kind}-{face}.jpg", f"{kind}-{face}.png", f"{kind}.jpg", f"{kind}.png"):
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return None


class GradeTab(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=10)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self.batch = None
        self.cards = []          # [(sku, front_path, back_path, front_crops, back_crops)]
        self.checks = {}
        self.grades_path = None
        self.golden = False
        self.index = 0
        self.panel = 0
        self.looked = set()
        self.cache = {}          # (path) -> panels
        self.events = queue.Queue()
        self.photos = []
        self.worker = None

        self._build_top()
        self._build_view()
        self._build_form()
        self.bind_all("<Key>", self._key, add="+")
        self._poll()

    # ------------------------------------------------------------ layout

    def _build_top(self):
        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.columnconfigure(1, weight=1)
        self.src_var = tk.StringVar()
        ttk.Label(bar, text="Manifest / golden picks").grid(row=0, column=0, sticky="w")
        ttk.Entry(bar, textvariable=self.src_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(bar, text="Open…", command=self.pick).grid(row=0, column=2)
        ttk.Label(bar, text="Grader").grid(row=0, column=3, padx=(12, 4))
        self.grader_var = tk.StringVar(value="Connor")
        ttk.Entry(bar, textvariable=self.grader_var, width=10).grid(row=0, column=4)
        ttk.Label(bar, text="Pass").grid(row=0, column=5, padx=(12, 4))
        self.pass_var = tk.StringVar(value="1")
        ttk.Spinbox(bar, from_=1, to=5, width=3, textvariable=self.pass_var,
                    command=self._goto_first_ungraded).grid(row=0, column=6)
        ttk.Label(bar, text="Zoom").grid(row=0, column=7, padx=(12, 4))
        self.zoom_var = tk.StringVar(value="2")
        z = ttk.Combobox(bar, values=("1", "2", "3", "4"), width=3, state="readonly",
                         textvariable=self.zoom_var)
        z.grid(row=0, column=8)
        z.bind("<<ComboboxSelected>>", lambda _e: self._show_panel())

    def _build_view(self):
        view = ttk.Frame(self)
        view.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        view.columnconfigure(0, weight=3)
        view.columnconfigure(1, weight=1)
        view.rowconfigure(1, weight=1)
        self.panel_lbl = ttk.Label(view, font=("TkDefaultFont", 11, "bold"))
        self.panel_lbl.grid(row=0, column=0, sticky="w")
        self.strip = ttk.Label(view, foreground="#666")
        self.strip.grid(row=0, column=0, sticky="e")

        def scroller(parent, row, col, width=900):
            f = ttk.Frame(parent)
            f.grid(row=row, column=col, sticky="nsew", padx=(0, 6))
            f.columnconfigure(0, weight=1)
            f.rowconfigure(0, weight=1)
            c = tk.Canvas(f, background="#121212", highlightthickness=0, width=width,
                          height=300)
            c.grid(row=0, column=0, sticky="nsew")
            xs = ttk.Scrollbar(f, orient="horizontal", command=c.xview)
            ys = ttk.Scrollbar(f, orient="vertical", command=c.yview)
            xs.grid(row=1, column=0, sticky="ew")
            ys.grid(row=0, column=1, sticky="ns")
            c.configure(xscrollcommand=xs.set, yscrollcommand=ys.set)
            return c

        self.canvas = scroller(view, 1, 0)
        refs = ttk.Frame(view)
        refs.grid(row=1, column=1, sticky="nsew")
        refs.columnconfigure(0, weight=1)
        refs.rowconfigure(1, weight=1)
        refs.rowconfigure(3, weight=1)
        ttk.Label(refs, text="Clean reference").grid(row=0, column=0, sticky="w")
        self.ref_clean = scroller(refs, 1, 0, width=280)
        ttk.Label(refs, text="LP reference").grid(row=2, column=0, sticky="w")
        self.ref_lp = scroller(refs, 3, 0, width=280)

    def _build_form(self):
        form = ttk.LabelFrame(self, text="Card", padding=8)
        form.grid(row=1, column=1, sticky="ns", padx=(10, 0), pady=(8, 0))
        self.card_lbl = ttk.Label(form, font=("TkDefaultFont", 12, "bold"))
        self.card_lbl.grid(row=0, column=0, columnspan=3, sticky="w")
        self.looked_lbl = ttk.Label(form)
        self.looked_lbl.grid(row=1, column=0, columnspan=3, sticky="w")
        self.check_lbl = ttk.Label(form, foreground="#b3261e", wraplength=260, justify="left")
        self.check_lbl.grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 6))

        self.points = {f: tk.StringVar(value="0") for f in grading.FACES}
        self.corners = {f: tk.StringVar(value="0") for f in grading.FACES}
        ttk.Label(form, text="").grid(row=3, column=0)
        ttk.Label(form, text="wear points").grid(row=3, column=1)
        ttk.Label(form, text="whitened corners").grid(row=3, column=2)
        for r, f in enumerate(grading.FACES, 4):
            ttk.Label(form, text=f).grid(row=r, column=0, sticky="w")
            ttk.Spinbox(form, from_=0, to=99, width=4, textvariable=self.points[f])\
                .grid(row=r, column=1)
            ttk.Spinbox(form, from_=0, to=4, width=4, textvariable=self.corners[f])\
                .grid(row=r, column=2)
        self.whitening_lbl = ttk.Label(form, foreground="#666", wraplength=260, justify="left")
        self.whitening_lbl.grid(row=6, column=0, columnspan=3, sticky="w", pady=(2, 6))

        ttk.Label(form, text="Crease").grid(row=7, column=0, sticky="w")
        self.crease_var = tk.StringVar(value=grading.CREASE_NONE)
        for i, (val, text) in enumerate(((grading.CREASE_NONE, "none"),
                                         (grading.CREASE_SHORT, "short, edge only, ink intact"),
                                         (grading.CREASE_OTHER, "other"))):
            ttk.Radiobutton(form, text=text, value=val, variable=self.crease_var)\
                .grid(row=8 + i, column=0, columnspan=3, sticky="w")
        ttk.Label(form, text="Note").grid(row=11, column=0, sticky="w", pady=(6, 0))
        self.note_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.note_var, width=32)\
            .grid(row=12, column=0, columnspan=3, sticky="ew")

        keys = ttk.Frame(form)
        keys.grid(row=13, column=0, columnspan=3, pady=(10, 0))
        for i, g in enumerate(grading.KEYS):
            ttk.Button(keys, text=f"{g} ({i + 1})", width=8,
                       command=lambda g=g: self.grade(g)).grid(row=i // 3, column=i % 3, padx=2,
                                                               pady=2)
        nav = ttk.Frame(form)
        nav.grid(row=14, column=0, columnspan=3, pady=(10, 0))
        ttk.Button(nav, text="◀ Card", command=lambda: self.go(self.index - 1)).pack(side="left")
        ttk.Button(nav, text="Card ▶", command=lambda: self.go(self.index + 1)).pack(side="left")
        self.ai_btn = ttk.Button(form, text="Second opinion (Claude)", command=self.second_opinion)
        self.ai_btn.grid(row=15, column=0, columnspan=3, pady=(10, 0), sticky="ew")
        self.status = ttk.Label(form, wraplength=260, justify="left")
        self.status.grid(row=16, column=0, columnspan=3, sticky="w", pady=(10, 0))

    # ------------------------------------------------------------ loading

    def pick(self):
        path = filedialog.askopenfilename(
            title="Manifest (manifest-XX.json) or golden picks (CSV)",
            filetypes=[("Manifest or picks", "*.json *.csv"), ("All files", "*.*")])
        if path:
            self.load(path)

    def load(self, path):
        self.src_var.set(path)
        self.cache.clear()
        if path.lower().endswith(".json"):
            self.batch = batches.read_manifest(path)
            self.golden = False
            self.cards = [(c.sku, c.front, c.back, c.front_crops, c.back_crops)
                          for c in self.batch.cards]
            folder = os.path.dirname(path)
            self.checks = checks.read_checks(os.path.join(folder, f"checks-{self.batch.code}.csv"))
            self.grades_path = grading.grades_path(folder, self.batch.code)
        else:
            rows = audits._read(path)
            self.golden = True
            self.batch = None
            self.cards = [(r["id"], r.get("front", ""), r.get("back", ""),
                           r.get("front_crops", ""), r.get("back_crops", "")) for r in rows]
            self.checks = {}
            self.grades_path = os.path.splitext(path)[0] + "-grades.csv"
        self._goto_first_ungraded()

    def _game(self):
        return self.batch.game if self.batch else "pokemon"

    def _line(self):
        if not self.batch:
            return "pokemon-en"
        return wear.game_line(self.batch.game, self.batch.language)

    def _graded(self):
        p = self.pass_var.get()
        return {r["sku"] for r in grading.read_grades(self.grades_path)
                if str(r.get("pass")) == p and r.get("source") != "ai"}

    def _goto_first_ungraded(self):
        if not self.cards:
            return
        done = self._graded()
        for i, card in enumerate(self.cards):
            if card[0] not in done:
                return self.go(i)
        self.go(0)
        self.status.configure(text=f"every card has a pass {self.pass_var.get()} grade")

    def go(self, i):
        if not self.cards:
            return
        self.index = max(0, min(i, len(self.cards) - 1))
        sku = self.cards[self.index][0]
        self.panel = 0
        self.looked = set()
        chk = self.checks.get(sku, {})
        for f in grading.FACES:
            self.points[f].set(chk.get(f"{f}_points") or "0")
            self.corners[f].set(chk.get(f"{f}_corners_whitened") or "0")
        self.crease_var.set(grading.CREASE_NONE)
        self.note_var.set("")
        self.whitening_lbl.configure(text=chk.get("whitening", ""))
        problems = "; ".join(filter(None, [chk.get("status") if chk.get("status") not in
                                           (None, "", "OK") else "",
                                           chk.get("hold_reasons"), chk.get("notes")]))
        self.check_lbl.configure(text=problems)
        self.card_lbl.configure(text=f"{sku}   ({self.index + 1} of {len(self.cards)})"
                                + ("   GOLDEN — blind" if self.golden else ""))
        self.status.configure(text="")
        self._request(self.index)
        self._request(self.index + 1)       # prefetch
        self._show_panel()

    def _paths(self, i):
        _, front, back, _, _ = self.cards[i]
        return {"front": front, "back": back}

    def _request(self, i):
        if i >= len(self.cards):
            return
        for face, path in self._paths(i).items():
            if path and path not in self.cache:
                self.cache[path] = None
                threading.Thread(target=self._load_face, args=(path,), daemon=True).start()
        line = self._line()
        for kind in ("clean", "lp"):
            for face in grading.FACES:
                ref = reference_file(line, kind, face)
                if ref and ref not in self.cache:
                    self.cache[ref] = None
                    threading.Thread(target=self._load_face, args=(ref,), daemon=True).start()

    def _load_face(self, path):
        try:
            panels = face_panels(path)
        except Exception as exc:                              # noqa: BLE001
            panels = exc
        self.events.put(("face", path, panels))

    def _poll(self):
        try:
            while True:
                kind, *rest = self.events.get_nowait()
                if kind == "face":
                    path, panels = rest
                    self.cache[path] = panels
                    self._show_panel()
                elif kind == "ai":
                    self._show_ai(rest[0])
        except queue.Empty:
            pass
        self.after(100, self._poll)

    # ------------------------------------------------------------ panels

    def _panel_id(self):
        return grading.ALL_PANELS[self.panel]

    def _draw(self, canvas, img):
        canvas.delete("all")
        if img is None:
            return
        z = int(self.zoom_var.get() or 1)
        if z > 1:
            img = img.resize((img.size[0] * z, img.size[1] * z), Image.NEAREST)
        photo = ImageTk.PhotoImage(img)
        self.photos.append(photo)
        canvas.create_image(0, 0, anchor="nw", image=photo)
        canvas.configure(scrollregion=(0, 0, img.size[0], img.size[1]))

    def _show_panel(self):
        if not self.cards:
            return
        self.photos = []
        pid = self._panel_id()
        face, panel = pid.split("-", 1)
        path = self._paths(self.index)[face]
        panels = self.cache.get(path) if path else None
        name = f"{face.upper()} — {PANEL_NAMES[panel]}  ({self.panel + 1}/16)"
        unchecked = (self.checks.get(self.cards[self.index][0], {}).get("unchecked_panels")
                     or "").split()
        if pid in unchecked:
            name += "   UNCHECKED: solid black strip, re-crop from the full scan"
        self.panel_lbl.configure(text=name)
        if not path:
            self._draw(self.canvas, None)
            self.strip.configure(text=f"no {face} image")
        elif panels is None:
            self._draw(self.canvas, None)
            self.strip.configure(text="loading…")
        elif isinstance(panels, Exception):
            self._draw(self.canvas, None)
            self.strip.configure(text=f"could not read: {panels}")
        else:
            self._draw(self.canvas, panels[panel])
            self.strip.configure(text="")
            self.looked.add(pid)
        line = self._line()
        for kind, canvas in (("clean", self.ref_clean), ("lp", self.ref_lp)):
            ref = reference_file(line, kind, face)
            ref_panels = self.cache.get(ref) if ref else None
            self._draw(canvas, ref_panels[panel] if isinstance(ref_panels, dict) else None)
            if not ref:
                canvas.create_text(10, 10, anchor="nw", fill="#aaa",
                                   text=f"no {kind} reference —\nput {kind}.jpg in\n"
                                        f"{reference_dir(line)}")
        self.looked_lbl.configure(text=f"panels opened: {len(self.looked)} / 16")

    def step(self, d):
        self.panel = (self.panel + d) % len(grading.ALL_PANELS)
        self._show_panel()

    # ------------------------------------------------------------ grading

    def _key(self, e):
        if not self.winfo_viewable() or not self.cards:
            return
        if isinstance(e.widget, (tk.Entry, ttk.Entry, ttk.Spinbox, tk.Spinbox)):
            return
        k = (e.keysym or "").lower()
        if k in ("right", "space"):
            self.step(1)
        elif k in ("left", "backspace"):
            self.step(-1)
        elif e.char and e.char.lower() in KEYMAP:
            self.grade(KEYMAP[e.char.lower()])

    def _assessment(self, g):
        sku = self.cards[self.index][0]
        unchecked = set((self.checks.get(sku, {}).get("unchecked_panels") or "").split())

        def num(v):
            try:
                return int(v.get())
            except ValueError:
                return 0
        return grading.Assessment(
            grade=g, looked=set(self.looked), unchecked=unchecked,
            points={f: num(self.points[f]) for f in grading.FACES},
            corners_whitened={f: num(self.corners[f]) for f in grading.FACES},
            crease=self.crease_var.get())

    def grade(self, g):
        if not self.cards:
            return
        a = self._assessment(g)
        try:
            final, reasons = grading.enforce(a)
        except grading.RuleError as exc:
            self.status.configure(text=str(exc), foreground="#b3261e")
            return
        sku = self.cards[self.index][0]
        grading.record(self.grades_path, sku, a, final, reasons,
                       pass_no=self.pass_var.get(), grader=self.grader_var.get(),
                       notes=self.note_var.get())
        if self.golden:
            self._write_blind(sku, final)
        msg = f"{sku}: {final}" + (f" — {'; '.join(reasons)}" if reasons else "")
        self.go(self.index + 1)
        self.status.configure(text=msg, foreground="#1a7f37" if not reasons else "#8a5a00")

    def _write_blind(self, sku, final):
        """A golden blind check also keeps id,grade for `--cli golden --score`."""
        path = os.path.splitext(self.grades_path)[0] + "-score.csv"
        rows = {r["id"]: r for r in audits._read(path)}
        rows[sku] = {"id": sku, "grade": final}
        import csv
        with open(path, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["id", "grade"])
            w.writeheader()
            w.writerows(rows.values())

    # ------------------------------------------------------------ second opinion

    def second_opinion(self):
        if not self.cards or (self.worker and self.worker.is_alive()):
            return
        if self.golden:
            messagebox.showinfo("Second opinion", "Not on a blind golden check.")
            return
        sku, _, _, fc, bc = self.cards[self.index]
        counts = self.checks.get(sku, {}).get("whitening", "")
        self.ai_btn.configure(state="disabled", text="Asking…")

        def run():
            from . import second_opinion
            try:
                res = second_opinion.ask({"front": fc, "back": bc}, self._game(), counts)
                self.events.put(("ai", (sku, res, None)))
            except Exception as exc:                          # noqa: BLE001
                self.events.put(("ai", (sku, None, exc)))
        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _show_ai(self, payload):
        sku, res, exc = payload
        self.ai_btn.configure(state="normal", text="Second opinion (Claude)")
        if exc is not None:
            messagebox.showerror("Second opinion", f"{type(exc).__name__}: {exc}")
            return
        a = grading.Assessment(grade=res.get("grade", ""), crease=res.get("crease", "none")
                               if res.get("crease") in grading.CREASES else "none")
        grading.record(self.grades_path, sku, a, a.grade, [], pass_no=0,
                       grader=res.get("model", "claude"), notes=res.get("evidence", ""),
                       source="ai")
        messagebox.showinfo(
            "Second opinion",
            f"{sku}: {res.get('grade')}  ({res.get('wear_points')} wear points, crease "
            f"{res.get('crease')})\n\n{res.get('evidence')}\n\nAdvice only — key your own grade.")
