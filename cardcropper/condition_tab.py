"""
The "Condition a CSV" tab: the card-conditioning workflow behind a window.

Pick a CardUploader export, press Prepare, and the batch comes back ranked
worst corner first. Double-click a card to open the image worth looking at for
it. Grade in conditioning-review.csv, then record the grades (which teaches the
triage) and write them into a copy of the export.

Nothing on this tab grades a card. It ranks them, so that a person looks at as
few as possible, and it writes down what that person decided.
"""

import os
import queue
import threading
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import carduploader, conditioning, scoring

APP_NAME = "CardCropper"

BAND_COLOURS = {"likely-worse": "#b3261e", "review": "#8a5a00", "likely-nm": "#1a7f37",
                "no-scan": "#555", "error": "#555"}


def _open(path):
    from .gui import _reveal
    _reveal(path)


class ConditionTab(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=10)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=3)
        self.rowconfigure(6, weight=2)
        self.paths = conditioning.default_paths()
        self.events = queue.Queue()
        self.worker = None
        self.stop_flag = threading.Event()
        self.records = []
        self.by_row = {}
        self.grades = {}
        self._auto_out = ""

        self._build_inputs()
        self._build_run()
        self._build_table()
        self._build_after()
        self._build_log()
        self._show_thresholds()
        self._poll()

    # ------------------------------------------------------------ layout

    def _build_inputs(self):
        box = ttk.LabelFrame(self, text="Batch", padding=8)
        box.grid(row=0, column=0, sticky="ew")
        box.columnconfigure(1, weight=1)

        self.csv_var = tk.StringVar()
        self.out_var = tk.StringVar()
        for r, (label, var, cmd) in enumerate((
                ("CardUploader export", self.csv_var, self.pick_csv),
                ("Output folder", self.out_var, self.pick_out))):
            ttk.Label(box, text=label).grid(row=r, column=0, sticky="w", pady=2)
            ttk.Entry(box, textvariable=var).grid(row=r, column=1, sticky="ew", padx=6)
            ttk.Button(box, text="Browse…", command=cmd).grid(row=r, column=2)

        opts = ttk.Frame(box)
        opts.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Label(opts, text="First").pack(side="left")
        self.limit_var = tk.StringVar()
        ttk.Spinbox(opts, from_=0, to=10000, width=6,
                    textvariable=self.limit_var).pack(side="left", padx=4)
        ttk.Label(opts, text="cards (blank = all)").pack(side="left", padx=(0, 14))
        self.crops_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Write crops", variable=self.crops_var)\
            .pack(side="left", padx=(0, 14))
        self.refresh_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Re-download scans", variable=self.refresh_var)\
            .pack(side="left", padx=(0, 14))
        self.upload_var = tk.BooleanVar(value=False)
        self.upload_chk = ttk.Checkbutton(
            opts, text="Host corner photos for the listings (Supabase)",
            variable=self.upload_var)
        self.upload_chk.pack(side="left")
        if conditioning.SupabaseUploader.from_env() is None:
            self.upload_chk.configure(state="disabled")

        self.thresh_lbl = ttk.Label(box, foreground="#666")
        self.thresh_lbl.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(box, text="Settings folder",
                   command=self.open_settings).grid(row=3, column=2, pady=(6, 0))

    def _build_run(self):
        bar = ttk.Frame(self)
        bar.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        bar.columnconfigure(1, weight=1)
        self.run_btn = ttk.Button(bar, text="Prepare batch", command=self.start)
        self.run_btn.grid(row=0, column=0)
        self.progress = ttk.Progressbar(bar, mode="determinate")
        self.progress.grid(row=0, column=1, sticky="ew", padx=10)
        self.count = ttk.Label(bar, text="")
        self.count.grid(row=0, column=2)
        self.open_btn = ttk.Button(bar, text="Open folder", state="disabled",
                                   command=lambda: _open(self.out_var.get()))
        self.open_btn.grid(row=0, column=3, padx=(10, 0))

        ttk.Label(self, foreground="#666",
                  text="Work down from the top: select a card and grade it on the right "
                       "(keys 1-6, Enter saves and moves on). Double-click to open the "
                       "image full size. Stop when the cards stop being interesting.")\
            .grid(row=2, column=0, sticky="w", pady=(8, 2))

    def _build_table(self):
        # The ranking on the left, the card selected in it on the right —
        # graded there and then, without a spreadsheet (see review_panel).
        from .review_panel import KEYS, ReviewPanel
        pane = ttk.PanedWindow(self, orient="horizontal")
        pane.grid(row=3, column=0, sticky="nsew")
        wrap = ttk.Frame(pane)
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        pane.add(wrap, weight=3)
        self.panel = ReviewPanel(pane, out_dir=lambda: self.out_var.get().strip(),
                                 on_saved=self._graded_here)
        pane.add(self.panel, weight=2)

        # The table asks for every column's width and would squeeze the panel
        # to a sliver, so the divider is placed once the window has a size.
        def place(_e=None):
            if pane.winfo_width() > 700 and not getattr(pane, "_placed", False):
                pane._placed = True
                pane.sashpos(0, pane.winfo_width() - 400)
        pane.bind("<Configure>", place, add="+")
        cols = ("sku", "triage", "grade", "corner", "total", "copies", "title", "note")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        for key, text, width, stretch in (("sku", "SKU", 110, False),
                                          ("triage", "Triage", 90, False),
                                          ("grade", "Grade", 56, False),
                                          ("corner", "Worst corner %", 115, False),
                                          ("total", "Wear %", 70, False),
                                          ("copies", "Copies", 55, False),
                                          ("title", "Title", 220, True),
                                          ("note", "Note", 160, True)):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, stretch=stretch,
                             anchor="e" if key in ("corner", "total", "copies") else "w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        for band, colour in BAND_COLOURS.items():
            self.tree.tag_configure(band, foreground=colour)
        self.tree.bind("<Double-1>", self.open_card)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._picked())
        for key, g in KEYS.items():
            self.tree.bind(key, lambda _e, g=g: self.panel.pick(g))
        self.tree.bind("<Return>", lambda _e: self.panel.save())

    def _build_after(self):
        box = ttk.LabelFrame(self, text="After grading conditioning-review.csv", padding=8)
        box.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(box, text="Open prepared batch…",
                   command=self.open_prepared).pack(side="left", padx=(0, 6))
        ttk.Button(box, text="Open review sheet",
                   command=self.open_review).pack(side="left")
        #: Set by the window: check this batch's scans against the catalogue.
        self.check_catalogue = None
        ttk.Button(box, text="Check against catalogue…",
                   command=lambda: self.check_catalogue and self.check_catalogue(
                       self.out_var.get().strip())).pack(side="left", padx=(6, 0))
        ttk.Button(box, text="Record grades & re-fit triage",
                   command=self.calibrate).pack(side="left", padx=(6, 0))
        ttk.Button(box, text="Write grades to a copy of the CSV",
                   command=self.apply).pack(side="left", padx=(6, 0))

    def _build_log(self):
        ttk.Label(self, text="Log").grid(row=5, column=0, sticky="w", pady=(10, 2))
        wrap = ttk.Frame(self)
        wrap.grid(row=6, column=0, sticky="nsew")
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        self.log = tk.Text(wrap, height=8, wrap="word", state="disabled")
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=sb.set)

    # ------------------------------------------------------------ helpers

    def _say(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _show_thresholds(self):
        t = scoring.Thresholds.load(self.paths["thresholds"])
        self.thresh_lbl.configure(text=f"Triage thresholds: {t.source}")

    def _review_path(self):
        return os.path.join(self.out_var.get().strip(), "conditioning-review.csv")

    def _row(self, rec):
        def num(k):
            v = rec.get(k)
            return f"{v:.2f}" if isinstance(v, (int, float)) else ""
        note = rec.get("note", "")
        if rec.get("check_back_era"):
            note = "WotC era — open front and back together. " + note
        return (rec["sku"], rec["triage"], self.grades.get(rec["sku"], ""),
                num("worst_corner_pct"), num("wear_total_pct"),
                rec.get("copies", ""), rec["title"], note.strip())

    # ------------------------------------------------------------ actions

    def pick_csv(self):
        path = filedialog.askopenfilename(
            title="Select a CardUploader export",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
        if path:
            self.csv_var.set(path)
            # Follow the export unless a folder was chosen by hand: a folder
            # named after the PREVIOUS export would mix two batches' crops and
            # put this batch's review sheet under the wrong name.
            current = self.out_var.get().strip()
            if not current or current == self._auto_out:
                stem = os.path.splitext(os.path.basename(path))[0]
                self._auto_out = os.path.join(os.path.dirname(path),
                                              f"{stem}-conditioning")
                self.out_var.set(self._auto_out)

    def pick_out(self):
        folder = filedialog.askdirectory(title="Where should the run's files go?")
        if folder:
            self.out_var.set(folder)

    def open_settings(self):
        os.makedirs(conditioning.settings_dir(), exist_ok=True)
        carduploader.ensure_condition_map(self.paths["conditions"])
        _open(conditioning.settings_dir())

    def open_card(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        rec = self.by_row.get(sel[0])
        if rec is None:
            return
        target = conditioning.look_at(rec) or \
            f"cards/{conditioning.safe_name(rec['sku'])}/crops-back.jpg"
        for part in target.split(" + "):
            path = os.path.join(self.out_var.get().strip(), *part.split("/"))
            if os.path.exists(path):
                _open(path)
            else:
                self._say(f"{rec['sku']}: no {part} (crops off, or no usable scan)")

    def open_review(self):
        path = self._review_path()
        if os.path.exists(path):
            _open(path)
        else:
            messagebox.showinfo(APP_NAME, "Prepare the batch first — the review sheet "
                                          "is written into the output folder.")

    # ------------------------------------------------------------ running

    def start(self):
        if self.worker and self.worker.is_alive():
            self.stop_flag.set()
            return
        csv_path, out = self.csv_var.get().strip(), self.out_var.get().strip()
        if not csv_path or not os.path.exists(csv_path):
            messagebox.showwarning(APP_NAME, "Choose a CardUploader export first.")
            return
        if not out:
            messagebox.showwarning(APP_NAME, "Choose an output folder first.")
            return
        try:
            limit = int(self.limit_var.get()) if self.limit_var.get().strip() else None
        except ValueError:
            messagebox.showwarning(APP_NAME, "The card count must be a number.")
            return
        uploader = conditioning.SupabaseUploader.from_env() if self.upload_var.get() else None
        thresholds = scoring.Thresholds.load(self.paths["thresholds"])
        # Read every option here, on the main thread. Tk variables belong to
        # the thread running the event loop, and reading one from the worker
        # raises on Tk builds that are not thread-aware.
        refresh, crops = self.refresh_var.get(), self.crops_var.get()

        self.records, self.by_row = [], {}
        self.tree.delete(*self.tree.get_children())
        self.run_btn.configure(text="Stop")
        self.open_btn.configure(state="disabled")
        self.stop_flag.clear()
        self._say(f"\nPreparing {os.path.basename(csv_path)} into {out}")

        def work():
            try:
                result = conditioning.prepare(
                    csv_path, out, thresholds=thresholds, limit=limit or None,
                    refresh=refresh, crops=crops,
                    uploader=uploader,
                    progress=lambda *a: self.events.put(("card",) + a),
                    should_stop=self.stop_flag.is_set)
                self.events.put(("finished", result))
            except Exception:                           # noqa: BLE001
                self.events.put(("crashed", traceback.format_exc()))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "card":
                    _, i, total, rec = event
                    self.progress.configure(maximum=total, value=i)
                    self.count.configure(text=f"{i} / {total}")
                    if rec["triage"] in ("no-scan", "error"):
                        self._say(f"[{i}/{total}] {rec['sku']} {rec['triage']}: {rec['note']}")
                elif event[0] == "finished":
                    self._finished(event[1])
                elif event[0] == "crashed":
                    self.run_btn.configure(text="Prepare batch")
                    self._say(event[1])
                    messagebox.showerror(APP_NAME, "The run stopped unexpectedly. "
                                                   "See the log for details.")
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def _load_grades(self):
        self.grades = {}
        path = self._review_path()
        if os.path.exists(path):
            try:
                for r in conditioning.read_review(path):
                    g = (r.get("grade") or "").strip().upper()
                    if g:
                        self.grades[(r.get("sku") or "").strip()] = g
            except OSError:
                pass

    def _show_records(self, records):
        self.records = records
        self._load_grades()
        self.tree.delete(*self.tree.get_children())
        self.by_row = {}
        for rec in records:
            row = self.tree.insert("", "end", values=self._row(rec), tags=(rec["triage"],))
            self.by_row[row] = rec
        kids = self.tree.get_children()
        todo = [k for k in kids if not self.grades.get(self.by_row[k]["sku"])]
        if todo or kids:
            first = (todo or kids)[0]
            self.tree.selection_set(first)
            self.tree.see(first)

    def _picked(self):
        sel = self.tree.selection()
        if sel and sel[0] in self.by_row:
            self.panel.show(self.by_row[sel[0]])

    def _graded_here(self, sku, grade):
        """After a save in the panel: mark the row, and go to the next card."""
        self.grades[sku] = grade
        sel = self.tree.selection()
        if not sel:
            return
        self.tree.set(sel[0], "grade", grade)
        nxt = self.tree.next(sel[0])
        if nxt:
            self.tree.selection_set(nxt)
            self.tree.see(nxt)
            self.tree.focus(nxt)
        left = sum(1 for r in self.records if not self.grades.get(r["sku"]))
        self._say(f"{sku}: {grade}  ({left} card(s) still to grade)")

    def open_prepared(self):
        """A batch prepared earlier: its triage.csv, ranked as it was, ready to grade."""
        folder = filedialog.askdirectory(title="A prepared batch's output folder "
                                               "(the one holding triage.csv)")
        if not folder:
            return
        path = os.path.join(folder, "triage.csv")
        if not os.path.exists(path):
            messagebox.showinfo(APP_NAME, "No triage.csv in that folder — choose the output "
                                          "folder a Prepare run wrote to.")
            return
        import csv
        with open(path, encoding="utf-8-sig", newline="") as fh:
            records = list(csv.DictReader(fh))
        for r in records:
            for k in ("worst_corner_pct", "wear_total_pct", "border_base"):
                try:
                    r[k] = float(r[k])
                except (KeyError, TypeError, ValueError):
                    pass
        self.out_var.set(folder)
        self._auto_out = folder
        self.open_btn.configure(state="normal")
        self._show_records(records)
        self._say(f"\nOpened {len(records)} card(s) from {path}. "
                  f"{sum(1 for r in records if self.grades.get(r['sku']))} already graded.")

    def _finished(self, result):
        self.run_btn.configure(text="Prepare batch")
        self.open_btn.configure(state="normal")
        self._show_records(result.records)
        for line in result.summary():
            self._say(line)
        if result.warnings:
            messagebox.showwarning(APP_NAME, "\n\n".join(result.warnings))

    # ------------------------------------------------------------ after grading

    def calibrate(self):
        review = self._review_path()
        triage = os.path.join(self.out_var.get().strip(), "triage.csv")
        if not (os.path.exists(review) and os.path.exists(triage)):
            messagebox.showinfo(APP_NAME, "Prepare the batch and fill in the review "
                                          "sheet first.")
            return
        self._say("")
        lines = conditioning.calibrate(review, triage, self.paths["ledger"],
                                       self.paths["thresholds"], adopt=False)
        for line in lines:
            self._say(line)
        self._to_library(review, triage)
        proposal = next((ln for ln in lines if ln.startswith("proposed:")), None)
        if proposal and messagebox.askyesno(
                APP_NAME, f"{proposal}\n\nTriage future batches with these thresholds? "
                          "This changes which cards get called likely-nm without a look."):
            for line in conditioning.calibrate(review, triage, self.paths["ledger"],
                                               self.paths["thresholds"], adopt=True)[-1:]:
                self._say(line)
            self._show_thresholds()

    def _to_library(self, review, triage=None):
        """Every grade in the review sheet also goes into the grade library."""
        from . import library
        try:
            n = library.import_review(review, triage, self.out_var.get().strip())
            self._say(f"{n} grade(s) recorded in the grade library")
        except Exception as exc:                            # noqa: BLE001
            self._say(f"could not record grades in the grade library: {exc}")

    def apply(self):
        review = self._review_path()
        csv_path = self.csv_var.get().strip()
        if not (os.path.exists(review) and os.path.exists(csv_path)):
            messagebox.showinfo(APP_NAME, "Prepare the batch and fill in the review "
                                          "sheet first.")
            return
        carduploader.ensure_condition_map(self.paths["conditions"])
        # Prefer the copy with hosted photos when this run made one, so the
        # grades and the photos end up in the same upload file.
        stem = os.path.splitext(os.path.basename(csv_path))[0]
        with_photos = os.path.join(self.out_var.get().strip(), f"{stem}-with-photos.csv")
        source = with_photos if os.path.exists(with_photos) else csv_path
        self._say(f"\nWriting grades from the review sheet into a copy of "
                  f"{os.path.basename(source)}")
        try:
            result = conditioning.apply_grades(source, review, self.out_var.get().strip(),
                                               self.paths["conditions"])
        except ValueError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        for line in result.summary():
            self._say(line)
        self._to_library(review)
        if result.missing_grades:
            messagebox.showinfo(
                APP_NAME,
                "Some grades could not be written because their eBay condition strings "
                f"have not been seen yet: {', '.join(result.missing_grades)}.\n\n"
                "Condition one card at each of those grades by hand in CardUploader, "
                "export, and copy its three condition cells into condition-map.json "
                "(Settings folder). Until then those cards are in the -held.csv.")
