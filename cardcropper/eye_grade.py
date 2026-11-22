"""
Grading a CardUploader job by eye: a card at a time, front and back large,
one key per grade.

Opened from the CardUploader page. The pictures are the batch's own scans
when its manifest is beside the job (a batch made with Make batches), and
otherwise the images CardUploader holds. Click a picture to zoom in where you
clicked; click again to see it whole.

A grade is written to grades-<job>.csv beside the job — the same file the
Grade tab writes, so Batch & price prices at that condition and the change
file carries it — and recorded in the grade library. Source "eye": a grade
keyed here replaces this window's earlier grade for the card (so a slip can
be corrected), while a Grade-tab pass is kept and the worse of the two still
wins. No measurements and no panel rules: the one house rule checked is the
crease — a note that mentions one cannot go with a grade better than HP.
"""

import csv
import os
import queue
import threading

import tkinter as tk
from tkinter import simpledialog, ttk

from PIL import Image, ImageTk

from . import conditioning, folders, grading, ui, variants

SOURCE = "eye"
KEYS = {"1": "NM", "2": "LP", "3": "MP", "4": "HP", "5": "DMG", "6": "HOLD"}
ZOOM = 2.5
PHRASES = ("whitening on back corners", "back edge whitening", "front edge wear",
           "corner wear", "surface scratches", "print line", "dent", "crease",
           "off-centre")


def grader_name(parent=None, ask=True):
    """This computer's grader name: remembered in its own settings, asked once."""
    name = folders.load_settings().get("grader", "")
    if not name and ask and parent is not None:
        name = (simpledialog.askstring(
            "Who is grading?", "Your name, for the grades file and the grade library.\n"
            "Asked once on this computer.", parent=parent) or "").strip()
        if name:
            folders.save_settings(grader=name)
    return name


def grades_file(job_path, job):
    name = job.get("jobName") or os.path.splitext(os.path.basename(job_path))[0]
    return os.path.join(os.path.dirname(job_path), f"grades-{name}.csv")


def eye_grades(path):
    """{sku: (grade, note)} keyed in this window."""
    return {r["sku"]: (r.get("grade", ""), r.get("notes", ""))
            for r in grading.read_grades(path) if r.get("source") == SOURCE}


def save_grade(path, sku, grade, note="", grader=""):
    """
    Write this window's grade for `sku`, replacing its earlier one. Returns
    the row it replaced (for undo), or None.
    """
    rows = grading.read_grades(path)
    old = None
    for i in range(len(rows) - 1, -1, -1):
        if rows[i]["sku"] == sku and rows[i].get("source") == SOURCE:
            old = rows.pop(i)
    _write(path, rows)
    grading.record(path, sku, grading.Assessment(grade=grade), grade, [], pass_no=1,
                   grader=grader, notes=note, source=SOURCE)
    return old


def take_back(path, sku, old=None):
    """Undo: drop this window's grade for `sku`, putting `old` back if there was one."""
    rows = [r for r in grading.read_grades(path)
            if not (r["sku"] == sku and r.get("source") == SOURCE)]
    if old:
        rows.append(old)
    _write(path, rows)


def _write(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=grading.GRADE_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def refused(grade, note):
    """Why this grade cannot be saved with this note, or ''."""
    if grade in grading.GRADES and grading.GRADES.index(grade) < grading.GRADES.index(
            conditioning.CREASE_FLOOR) and conditioning.mentions_crease(note):
        return (f"Not saved: the note mentions a crease, and any crease is "
                f"{conditioning.CREASE_FLOOR} or worse. Regrade, or reword if there is "
                "no crease.")
    return ""


def local_scans(job_path, job):
    """{sku: (front, back)} from the batch's manifest beside the job, if there is one."""
    from . import batches
    name = job.get("jobName") or ""
    path = os.path.join(os.path.dirname(job_path), f"manifest-{name}.json")
    if not name or not os.path.exists(path):
        return {}
    try:
        m = batches.read_manifest(path)
    except Exception:                                       # noqa: BLE001
        return {}
    return {c.sku: (c.front, c.back) for c in m.cards}


def game_line(job):
    """(game, grade-library line) for a CardUploader job, from the job's own settings."""
    from . import carduploader_api
    tcg = variants.job_tcg(job)
    game = carduploader_api.game_of_tcg(tcg)
    if not tcg:
        # No game in the job's settings: the SKUs say it (MTG-…, YGO-…).
        skus = [(c.get("sku") or "").upper() for c in variants.job_cards(job)]
        for g, prefix in (("mtg", "MTG-"), ("yugioh", "YGO-")):
            if skus and sum(s.startswith(prefix) for s in skus) * 2 > len(skus):
                game = g
    return game, {"yugioh": "yugioh", "mtg": "mtg"}.get(game, "pokemon-en")


def _open_image(src):
    if not src:
        return None
    if src.startswith(("http://", "https://")):
        from .variant_tab import fetch_image
        return fetch_image(src)
    im = Image.open(src)
    im.load()
    return im.convert("RGB")


def _cached_path(src):
    """A local file for `src` (a fetched URL's cache file), for the grade library."""
    if not src or not src.startswith(("http://", "https://")):
        return src
    import hashlib
    from .variant_tab import CACHE
    p = os.path.join(CACHE, hashlib.sha1(src.encode()).hexdigest() + ".img")
    return p if os.path.exists(p) else ""


class EyeGrader(tk.Toplevel):
    def __init__(self, master, job_path, job, start_sku=None, on_change=None):
        super().__init__(master)
        self.title(f"Grade by eye — {job.get('jobName', '')}")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(1280, sw - 80)}x{min(860, sh - 100)}")
        self.minsize(900, 560)
        self.configure(background=ui.C["bg"])
        self.job_path, self.job = job_path, job
        self.on_change = on_change
        self.cards = variants.job_cards(job)
        self.path = grades_file(job_path, job)
        self.local = local_scans(job_path, job)
        self.grader = grader_name(self)
        self.game, self.line = game_line(job)
        # Grades keyed here before the game was read from the job were filed
        # as Pokémon: put them under the job's own game.
        from . import library
        library.flush(5)
        library.relabel(os.path.abspath(self.path), SOURCE, self.game, self.line)
        self.done = eye_grades(self.path)
        self.others = {s: g for s, (g, _, _) in grading.final_grades(
            [r for r in grading.read_grades(self.path) if r.get("source") != SOURCE]).items()}
        self.undo_stack = []
        self.images = {}            # src -> PIL image (or None when it failed)
        self.events = queue.Queue()
        self.photos = {}
        self.zoom = {}              # face -> (fx, fy) zoomed on that point, or absent
        self.index = 0
        self._build()
        i = next((n for n, c in enumerate(self.cards) if c.get("sku") == start_sku), None)
        if i is None:
            i = next((n for n, c in enumerate(self.cards) if c.get("sku") not in self.done), 0)
        self.go(i)
        self.after(100, self._poll)
        self.bind("<Key>", self._key)
        self.bind("<Left>", lambda _e: self._nav(-1))
        self.bind("<Right>", lambda _e: self._nav(1))
        self.bind("<Control-z>", self.undo)
        self.focus_set()

    # ------------------------------------------------------------ layout

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        pics = ttk.Frame(self, padding=(10, 10, 4, 10))
        pics.grid(row=0, column=0, sticky="nsew")
        pics.rowconfigure(1, weight=1)
        self.canvas = {}
        for col, face in enumerate(("front", "back")):
            pics.columnconfigure(col, weight=1, uniform="pic")
            ttk.Label(pics, text=face.title(), style="Small.TLabel").grid(
                row=0, column=col, sticky="w", padx=4)
            c = tk.Canvas(pics, background="#121212", highlightthickness=0, cursor="hand2")
            c.grid(row=1, column=col, sticky="nsew", padx=4)
            c.bind("<Configure>", lambda _e, f=face: self._draw(f))
            c.bind("<Button-1>", lambda e, f=face: self._click_zoom(f, e))
            ui.tip(c, "Click to zoom in where you click; click again to see it whole.")
            self.canvas[face] = c

        side = ttk.Frame(self, padding=(8, 10, 12, 10), width=330)
        side.grid(row=0, column=1, sticky="ns")
        side.grid_propagate(False)
        side.columnconfigure(0, weight=1)
        self.progress = ttk.Label(side, style="Muted.TLabel")
        self.progress.grid(row=0, column=0, sticky="w")
        self.head = ttk.Label(side, style="Heading.TLabel", wraplength=300, justify="left")
        self.head.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.sub = ttk.Label(side, style="Muted.TLabel", wraplength=300, justify="left")
        self.sub.grid(row=2, column=0, sticky="w", pady=(2, 8))

        grid = ttk.Frame(side)
        grid.grid(row=3, column=0, sticky="ew")
        for i, (k, g) in enumerate(KEYS.items()):
            b = ttk.Button(grid, text=f"{k}  {g}", style=f"{g}.Grade.TButton",
                           command=lambda g=g: self.grade(g))
            b.grid(row=i // 3, column=i % 3, sticky="ew", padx=(0, 4), pady=(0, 4))
            grid.columnconfigure(i % 3, weight=1)
            ui.tip(b, {"NM": "Near Mint", "LP": "Lightly Played", "MP": "Moderately Played",
                       "HP": "Heavily Played — any crease is HP at best", "DMG": "Damaged",
                       "HOLD": "Do not list yet: a rescan, a wrong card, a doubt"}[g]
                   + f". Key {k}: saves and moves on.")
        self.now = ttk.Label(side, style="Muted.TLabel")
        self.now.grid(row=4, column=0, sticky="w", pady=(2, 8))

        ttk.Label(side, text="Flaw note (optional)", style="Small.TLabel").grid(
            row=5, column=0, sticky="w")
        note = ttk.Frame(side)
        note.grid(row=6, column=0, sticky="ew")
        note.columnconfigure(0, weight=1)
        self.note_var = tk.StringVar()
        self.note = ttk.Entry(note, textvariable=self.note_var)
        self.note.grid(row=0, column=0, sticky="ew")
        self.note.bind("<Escape>", lambda _e: self.focus_set())
        self.note.bind("<Return>", lambda _e: self.focus_set())
        ui.tip(self.note, "Type before keying the grade; Enter or Esc to go back to the "
                          "grade keys. Saved with the grade.")
        ui.more_menu(note, [(p, lambda p=p: self._phrase(p)) for p in PHRASES],
                     text="Phrases", tip_text="Add a common flaw phrase to the note.")\
            .grid(row=0, column=1, padx=(4, 0))

        self.only_var = tk.BooleanVar(value=True)
        cb = ttk.Checkbutton(side, text="Move on to the next ungraded card",
                             variable=self.only_var)
        cb.grid(row=7, column=0, sticky="w", pady=(10, 0))
        ui.tip(cb, "After a grade, skip the cards already graded. Off: just the next card.")

        nav = ttk.Frame(side)
        nav.grid(row=8, column=0, sticky="ew", pady=(10, 0))
        for text, cmd, tiptext in (("◀ Back", lambda: self._nav(-1), "Previous card (←)."),
                                   ("Skip ▶", lambda: self._nav(1), "Next card, no grade (→)."),
                                   ("Undo", self.undo, "Take back the last grade (Ctrl+Z).")):
            b = ttk.Button(nav, text=text, style="Ghost.TButton", command=cmd)
            b.pack(side="left", padx=(0, 4))
            ui.tip(b, tiptext)
        self.status = ttk.Label(side, wraplength=300, justify="left")
        self.status.grid(row=9, column=0, sticky="w", pady=(10, 0))

        ttk.Label(side, text="Catalogue", style="Small.TLabel").grid(
            row=10, column=0, sticky="w", pady=(12, 0))
        self.cat = tk.Canvas(side, width=150, height=210, background="#121212",
                             highlightthickness=0)
        self.cat.grid(row=11, column=0, sticky="w")
        ui.tip(self.cat, "CardUploader's catalogue picture, to check it is the right card.")
        ttk.Label(side, style="Muted.TLabel", wraplength=300, justify="left",
                  text="Keys 1-6 grade · ← → move · Ctrl+Z undo · click a picture to "
                       "zoom").grid(row=12, column=0, sticky="w", pady=(12, 0))

    # ------------------------------------------------------------ moving

    def _card(self):
        return self.cards[self.index] if self.cards else {}

    def _sources(self, c):
        sku = c.get("sku")
        imgs = c.get("imageUrls") or {}
        front, back = self.local.get(sku, ("", ""))
        if not (front and os.path.exists(front)):
            front = imgs.get("front") or ""
        if not (back and os.path.exists(back)):
            back = imgs.get("back") or ""
        return {"front": front, "back": back, "catalogue": c.get("databaseImageUrl") or ""}

    def go(self, i):
        if not self.cards:
            self.head.configure(text="This job has no cards.")
            return
        self.index = max(0, min(i, len(self.cards) - 1))
        c = self._card()
        sku = c.get("sku", "")
        self.zoom = {}
        self.head.configure(text=f"{sku}  ·  {c.get('title', '')}")
        bits = [c.get("setName", ""), c.get("variant", ""),
                f"listed as {c.get('condition')}" if c.get("condition") else ""]
        if self.others.get(sku):
            bits.append(f"Grade tab: {self.others[sku]}")
        self.sub.configure(text="  ·  ".join(b for b in bits if b))
        g, note = self.done.get(sku, ("", ""))
        self.note_var.set(note)
        self.now.configure(text=f"Graded here: {g}" if g else "Not graded here yet",
                           foreground=ui.GRADE_COLOURS.get(g, ui.C["muted"]))
        self._progress()
        src = self._sources(c)
        for face in ("front", "back", "catalogue"):
            self._want(src[face])
        for face in ("front", "back"):
            self._draw(face)
        self._draw_cat()
        # The next card's pictures, ready before it is reached.
        if self.index + 1 < len(self.cards):
            for s in self._sources(self.cards[self.index + 1]).values():
                self._want(s)

    def _nav(self, step):
        if isinstance(self.focus_get(), (tk.Entry, ttk.Entry)):
            return
        self.go(self.index + step)

    def _progress(self):
        n = sum(1 for c in self.cards if c.get("sku") in self.done)
        self.progress.configure(text=f"Card {self.index + 1} of {len(self.cards)}  ·  "
                                     f"{n} graded here")

    # ------------------------------------------------------------ pictures

    def _want(self, src):
        if not src or src in self.images:
            return
        self.images[src] = "loading"

        def run():
            try:
                im = _open_image(src)
            except Exception:                               # noqa: BLE001
                im = None
            self.events.put((src, im))
        threading.Thread(target=run, daemon=True).start()

    def _poll(self):
        try:
            while True:
                src, im = self.events.get_nowait()
                self.images[src] = im
                if len(self.images) > 24:
                    keep = set(self._sources(self._card()).values())
                    for k in list(self.images)[:8]:
                        if k not in keep:
                            self.images.pop(k, None)
                cur = self._sources(self._card())
                for face in ("front", "back"):
                    if cur[face] == src:
                        self._draw(face)
                if cur["catalogue"] == src:
                    self._draw_cat()
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._poll)

    def _draw(self, face):
        c = self.canvas[face]
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 10 or h < 10 or not self.cards:
            return
        src = self._sources(self._card())[face]
        im = self.images.get(src)
        if not src or im is None:
            c.create_text(w / 2, h / 2, fill="#888",
                          text="no picture" if not src else "picture unavailable")
            return
        if im == "loading":
            c.create_text(w / 2, h / 2, fill="#888", text="loading…")
            return
        fit = min(w / im.width, h / im.height)
        if face in self.zoom:
            fx, fy = self.zoom[face]
            scale = fit * ZOOM
            vw, vh = w / scale, h / scale
            x0 = min(max(fx * im.width - vw / 2, 0), max(im.width - vw, 0))
            y0 = min(max(fy * im.height - vh / 2, 0), max(im.height - vh, 0))
            part = im.crop((int(x0), int(y0), int(x0 + vw), int(y0 + vh)))
            shown = part.resize((max(1, int(part.width * scale)), max(1, int(part.height * scale))),
                                Image.LANCZOS)
        else:
            shown = im.copy()
            shown.thumbnail((int(im.width * fit), int(im.height * fit)), Image.LANCZOS)
        photo = ImageTk.PhotoImage(shown)
        self.photos[face] = photo
        c.create_image(w / 2, h / 2, image=photo)
        self._shown = getattr(self, "_shown", {})
        self._shown[face] = (fit, im.width, im.height)

    def _click_zoom(self, face, e):
        if face in self.zoom:
            del self.zoom[face]
        else:
            info = getattr(self, "_shown", {}).get(face)
            if not info:
                return
            fit, iw, ih = info
            c = self.canvas[face]
            w, h = c.winfo_width(), c.winfo_height()
            ox, oy = (w - iw * fit) / 2, (h - ih * fit) / 2
            fx = min(max((e.x - ox) / (iw * fit), 0), 1)
            fy = min(max((e.y - oy) / (ih * fit), 0), 1)
            self.zoom[face] = (fx, fy)
        self._draw(face)

    def _draw_cat(self):
        self.cat.delete("all")
        src = self._sources(self._card())["catalogue"] if self.cards else ""
        im = self.images.get(src)
        if not isinstance(im, Image.Image):
            self.cat.create_text(75, 105, fill="#888",
                                 text="loading…" if im == "loading" else "no picture")
            return
        small = im.copy()
        small.thumbnail((150, 210), Image.LANCZOS)
        self.photos["catalogue"] = ImageTk.PhotoImage(small)
        self.cat.create_image(75, 105, image=self.photos["catalogue"])

    # ------------------------------------------------------------ grading

    def _key(self, e):
        if isinstance(self.focus_get(), (tk.Entry, ttk.Entry)):
            return
        if e.char in KEYS:
            self.grade(KEYS[e.char])

    def _phrase(self, p):
        cur = self.note_var.get().strip()
        if p not in cur:
            self.note_var.set(f"{cur}, {p}" if cur else p[0].upper() + p[1:])

    def grade(self, g):
        if not self.cards:
            return
        c = self._card()
        sku = c.get("sku", "")
        note = self.note_var.get().strip()
        why = refused(g, note)
        if why:
            self.status.configure(text=why, foreground=ui.C["bad"])
            return
        try:
            old = save_grade(self.path, sku, g, note, self.grader)
        except PermissionError:
            self.status.configure(foreground=ui.C["bad"],
                                  text="Could not save: the grades file is open in another "
                                       "program (Excel?). Close it there and key it again.")
            return
        self.undo_stack.append((sku, self.index, old, self.done.get(sku)))
        self.done[sku] = (g, note)
        self._to_library(c, g, note)
        self.status.configure(text=f"Saved {sku}: {g}   (Ctrl+Z undoes)",
                              foreground=ui.GRADE_COLOURS.get(g, ui.C["ok"]))
        if self.on_change:
            self.on_change()
        self.go(self._next_index())
        self.focus_set()

    def _next_index(self):
        n = len(self.cards)
        if not self.only_var.get():
            return min(self.index + 1, n - 1)
        for step in range(1, n + 1):
            j = (self.index + step) % n
            if self.cards[j].get("sku") not in self.done:
                return j
        self.status.configure(text=f"All {n} cards are graded. ✓", foreground=ui.C["ok"])
        return self.index

    def _to_library(self, c, g, note):
        if g not in grading.GRADES:
            return
        from . import library
        src = self._sources(c)
        sku = c.get("sku", "")
        library.record_later(
            sku, g, SOURCE, origin=os.path.abspath(self.path), pass_no=1,
            images={"front": _cached_path(src["front"]), "back": _cached_path(src["back"])},
            card=c.get("title", ""), batch=self.job.get("jobName", ""),
            game=self.game, line=self.line,
            keyed=g, grader=self.grader, looked="eye", notes=note)

    def undo(self, _e=None):
        if isinstance(self.focus_get(), (tk.Entry, ttk.Entry)):
            return
        if not self.undo_stack:
            self.status.configure(text="Nothing to undo.", foreground=ui.C["muted"])
            return "break"
        sku, index, old, before = self.undo_stack.pop()
        take_back(self.path, sku, old)
        if before:
            self.done[sku] = before
        else:
            self.done.pop(sku, None)
        from . import library
        library.flush(5)
        library.delete_grade(sku, SOURCE, os.path.abspath(self.path), 1)
        if before and before[0] in grading.GRADES:
            self._to_library(next(c for c in self.cards if c.get("sku") == sku), *before)
        if self.on_change:
            self.on_change()
        self.go(index)
        self.status.configure(text=f"Undone: {sku}. Key it again.", foreground=ui.C["warn"])
        return "break"
