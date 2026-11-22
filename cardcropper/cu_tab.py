"""
The "CardUploader" page: your CardUploader batches, card by card, with the
app's tools around them — read-only.

Pick a job (any saved under the batches folder, or fetch one with a token
from the extension) and see every card as CardUploader has it: title,
variant, condition, price, listed or not, its scan beside the catalogue
picture. Beside that, what the app knows: the grade it was given, the price
the house rules suggest and how far that is from the price it has, whether
its variant was checked. One click takes a card to Market, Card lookup or
eBay.

Nothing here changes CardUploader. Changes go through the change file, the
extension's diff, and Apply — as before.
"""

import json
import os
import queue
import threading
import webbrowser

import tkinter as tk
from tkinter import filedialog, ttk

from PIL import Image, ImageTk

from . import cu_jobs, folders, grading, market, ui, variants

THUMB = (140, 196)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _money(v):
    n = _num(v)
    return f"£{n:.2f}" if n is not None else ""


class CardUploaderTab(ttk.Frame):
    def __init__(self, master, actions=None):
        """`actions`: market(title), lookup(title), jobs() — set by the window."""
        super().__init__(master, padding=10)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self.actions = actions or {}
        self.worker = None
        self.events = queue.Queue()
        self.jobs = []
        self.job = None
        self.job_path = ""
        self.cards = {}
        self.photos = []

        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text="Job").grid(row=0, column=0, padx=(0, 6))
        self.job_var = tk.StringVar()
        self.job_box = ttk.Combobox(bar, textvariable=self.job_var, state="readonly")
        self.job_box.grid(row=0, column=1, sticky="ew")
        self.job_box.bind("<<ComboboxSelected>>", lambda _e: self._picked_job())
        ui.tip(self.job_box, "Every CardUploader job saved under the Batches folder, newest "
                             "first. Fetch more with CardUploader jobs….")
        self.grade_btn = ttk.Button(bar, text="Grade by eye…", style="Accent.TButton",
                                    command=lambda: self.grade_by_eye())
        self.grade_btn.grid(row=0, column=5, padx=(6, 0))
        ui.tip(self.grade_btn, "Grade this job's cards by eye: front and back large, keys "
                               "1-6, saved to grades-<job>.csv beside the job and to the "
                               "grade library. Starts at the selected card, or the first "
                               "not graded.")
        self.changes_btn = ttk.Button(bar, text="Write change file", command=self.write_changes)
        self.changes_btn.grid(row=0, column=6, padx=(6, 0))
        ui.tip(self.changes_btn, "Make changes-<job>.json beside the job: the conditions "
                                 "from your grades, plus house prices and variant switches "
                                 "when the job has them. Then, in the extension on the "
                                 "job's CardUploader page: Apply a change file → check the "
                                 "diff → Apply.")
        b = ttk.Button(bar, text="CardUploader jobs…", command=self._fetch)
        b.grid(row=0, column=2, padx=(6, 0))
        ui.tip(b, "Fetch a job from CardUploader with a token from the extension, or collect "
                  "what the extension saved to Downloads.")
        b = ttk.Button(bar, text="Open file…", style="Ghost.TButton", command=self.open_file)
        b.grid(row=0, column=3, padx=(6, 0))
        ui.tip(b, "Open a saved job-<batch>.json from anywhere.")
        b = ttk.Button(bar, text="Refresh", style="Ghost.TButton", command=self.refresh_jobs)
        b.grid(row=0, column=4, padx=(6, 0))
        ui.tip(b, "Look for saved jobs again.")

        self.figs_row = ttk.Frame(self)
        self.figs_row.grid(row=1, column=0, sticky="ew", pady=(10, 4))
        self.figs = {}
        for i, (key, label, tiptext) in enumerate((
                ("cards", "Cards", "Cards in the job."),
                ("listed", "Listed", "Cards live on eBay. Changes to these push to eBay."),
                ("value", "Listed value", "The sum of the cards' current prices."),
                ("graded", "Graded", "Cards with a grade in grades-<job>.csv."),
                ("priced", "Repricing", "Cards whose house price differs from the current "
                                        "one (from priced-<job>.json)."),
                ("variants", "Variants", "Special printings decided on the Variants page."))):
            card = ui.stat_card(self.figs_row, label, "—", "", width=110, compact=True)
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 6, 0))
            self.figs_row.columnconfigure(i, weight=1, uniform="cu")
            ui.tip(card, tiptext)
            self.figs[key] = card

        filt = ttk.Frame(self)
        filt.grid(row=2, column=0, sticky="ew", pady=(6, 4))
        filt.columnconfigure(1, weight=1)
        ttk.Label(filt, text="Find").grid(row=0, column=0, padx=(0, 6))
        self.find_var = tk.StringVar()
        e = ttk.Entry(filt, textvariable=self.find_var)
        e.grid(row=0, column=1, sticky="ew")
        self.find_var.trace_add("write", lambda *_: self._fill())
        ui.tip(e, "Filter by SKU, title or variant.")
        self.show_var = tk.StringVar(value="all")
        seg = ui.segmented(filt, self.show_var, (("all", "All"), ("listed", "Listed"),
                                                 ("move", "Price to move"),
                                                 ("ungraded", "Not graded")),
                           command=self._fill)
        seg.grid(row=0, column=2, padx=(10, 0))
        ui.tip(seg, "Price to move: the house price differs from the price it has.")

        pane = ttk.PanedWindow(self, orient="horizontal")
        pane.grid(row=3, column=0, sticky="nsew")
        left = ttk.Frame(pane)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        wrap, self.tv = ui.tree(left, (
            ("sku", "SKU", 56), ("title", "Title", 190, {"stretch": True}),
            ("variant", "Variant", 86), ("cond", "Cond.", 48),
            ("price", "Price", 60, {"anchor": "e"}), ("status", "Status", 74),
            ("grade", "Graded", 60, {"anchor": "center"}),
            ("suggested", "House price", 84, {"anchor": "e"}),
            ("diff", "Δ", 56, {"anchor": "e"})), height=16)
        wrap.grid(row=0, column=0, sticky="nsew")
        self.tv.tag_configure("up", foreground=ui.C["ok"])
        self.tv.tag_configure("down", foreground=ui.C["bad"])
        self.tv.bind("<<TreeviewSelect>>", lambda _e: self._picked_card())
        self.tv.bind("<Double-1>", lambda _e: self._to_market())
        ui.tip(self.tv, "The job's cards as CardUploader has them, with the grade and house "
                        "price beside each. Double-click for its sold prices on Market.")
        pane.add(left, weight=3)

        right = ttk.Frame(pane, padding=(12, 0, 0, 0))
        right.columnconfigure(0, weight=1)
        self.head = ttk.Label(right, style="Heading.TLabel", wraplength=310, justify="left",
                              text="Select a card")
        self.head.grid(row=0, column=0, sticky="w")
        pics = ttk.Frame(right)
        pics.grid(row=1, column=0, sticky="w", pady=(6, 6))
        self.pics = []
        for i, cap in enumerate(("Your scan", "Catalogue")):
            col = ttk.Frame(pics)
            col.grid(row=0, column=i, padx=(0, 8))
            ttk.Label(col, text=cap, style="Small.TLabel").pack(anchor="w")
            c = tk.Canvas(col, width=THUMB[0], height=THUMB[1], background="#121212",
                          highlightthickness=0)
            c.pack()
            self.pics.append(c)
        self.info = ttk.Frame(right)
        self.info.grid(row=2, column=0, sticky="ew")
        self.info.columnconfigure(1, weight=1)
        act = ttk.Frame(right)
        act.grid(row=3, column=0, sticky="w", pady=(10, 0))
        for i, (text, cmd, tiptext, style) in enumerate((
                ("Sold prices", self._to_market, "This card's eBay UK sales on Market.",
                 "Accent.TButton"),
                ("Card lookup", self._to_lookup, "Printings, Cardmarket and a scan match.",
                 "TButton"),
                ("eBay ↗", self._ebay, "eBay's own UK sold search for it.",
                 "TButton"),
                ("Grade it…", lambda: self.grade_by_eye((self._sel() or {}).get("sku")),
                 "Grade by eye, starting at this card.", "TButton"))):
            b = ttk.Button(act, text=text, command=cmd, style=style)
            b.grid(row=i // 2, column=i % 2, sticky="ew", padx=(0, 6), pady=(0, 6))
            ui.tip(b, tiptext)
        self.note = ttk.Label(right, style="Muted.TLabel", wraplength=310, justify="left",
                              text="Read-only: changes go through the change file and the "
                                   "extension's diff and Apply.")
        self.note.grid(row=4, column=0, sticky="w", pady=(12, 0))
        pane.add(right, weight=1)

        # The table gets the room; the detail panel a fixed share.
        def place(_e=None):
            if pane.winfo_width() > 700 and not getattr(pane, "_placed", False):
                pane._placed = True
                pane.sashpos(0, pane.winfo_width() - 350)
        pane.bind("<Configure>", place, add="+")

        self.bind("<Map>", lambda _e: self.refresh_jobs() if not self.jobs else None, add="+")
        self.after(150, self._poll)

    # ------------------------------------------------------------ jobs

    def refresh_jobs(self):
        def run():
            self.events.put(("jobs", cu_jobs.local_jobs()))
        threading.Thread(target=run, daemon=True).start()

    def _fetch(self):
        fn = self.actions.get("jobs")
        if fn:
            fn()

    def open_file(self):
        path = filedialog.askopenfilename(title="A saved CardUploader job",
                                          filetypes=[("Job data", "*.json")])
        if path:
            self.load(path)

    def _picked_job(self):
        i = self.job_box.current()
        if 0 <= i < len(self.jobs):
            self.load(self.jobs[i][1])

    def load(self, path):
        folders.remember("variants", path)
        self.job_path = path

        def run():
            try:
                with open(path, encoding="utf-8") as fh:
                    job = json.load(fh)
                self.events.put(("job", (path, job, self._extras(path, job))))
            except Exception as exc:                          # noqa: BLE001
                self.events.put(("err", exc))
        threading.Thread(target=run, daemon=True).start()

    def _extras(self, path, job):
        """What the app knows about this job: grades, house prices, variant decisions."""
        folder = os.path.dirname(path)
        name = job.get("jobName") or ""
        try:
            from . import eye_grade, library
            game, line = eye_grade.game_line(job)
            library.relabel(os.path.abspath(eye_grade.grades_file(path, job)),
                            eye_grade.SOURCE, game, line)
        except Exception:                                   # noqa: BLE001
            pass
        grades = {s: g for s, (g, _, _) in grading.final_grades(
            grading.read_grades(os.path.join(folder, f"grades-{name}.csv"))).items()}
        priced = {}
        pp = os.path.join(folder, f"priced-{name}.json")
        if os.path.exists(pp):
            with open(pp, encoding="utf-8") as fh:
                priced = {d.get("sku"): d for d in json.load(fh)}
        decided = {}
        vq = os.path.join(folder, f"variant-queue-{name}.csv")
        if os.path.exists(vq):
            decided = {r["sku"]: r for r in variants.read_queue(vq)}
        return {"grades": grades, "priced": priced, "variants": decided}

    # ------------------------------------------------------------ display

    def _poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "jobs":
                    self.jobs = payload
                    self.job_box.configure(values=[
                        f"{n}   ·   {os.path.basename(os.path.dirname(p))}" for n, p, _ in payload])
                    if payload and not self.job:
                        self.job_box.current(0)
                        self.load(payload[0][1])
                    elif not payload:
                        self.head.configure(text="No saved jobs under the Batches folder yet — "
                                                 "CardUploader jobs… to fetch one.")
                elif kind == "job":
                    path, job, extras = payload
                    self.job, self.extras = job, extras
                    self._summary()
                    self._fill()
                elif kind == "pic":
                    i, img = payload
                    self._show_pic(i, img)
                elif kind == "err":
                    self.head.configure(text=f"Could not read that job: {payload}")
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _rows(self):
        return variants.job_cards(self.job or {})

    def _summary(self):
        rows = self._rows()
        ex = self.extras
        listed = [c for c in rows if (c.get("status") or "").lower() == "listed"]
        value = sum(_num(c.get("price")) or 0 for c in listed)
        moving = [c for c in rows if self._diff(c) not in (None, 0)]
        self.figs["cards"].set(str(len(rows)), self.job.get("jobName", ""))
        self.figs["listed"].set(str(len(listed)), f"{len(rows) - len(listed)} not listed")
        self.figs["value"].set(f"£{value:,.2f}", "sum of listed prices")
        g = sum(1 for c in rows if c.get("sku") in ex["grades"])
        self.figs["graded"].set(f"{g}/{len(rows)}", "" if g else "no grades file")
        self.figs["priced"].set(str(len(moving)) if ex["priced"] else "—",
                                "to reprice" if ex["priced"] else "not priced yet")
        vq = ex["variants"]
        done = sum(1 for r in vq.values() if (r.get("decision") or "").strip())
        self.figs["variants"].set(f"{done}/{len(vq)}" if vq else "—",
                                  "decided" if vq else "not opened on Variants")

    def _diff(self, c):
        p = (self.extras or {}).get("priced", {}).get(c.get("sku"))
        if not p or p.get("suggested") is None:
            return None
        cur = _num(c.get("price"))
        return None if cur is None else round(p["suggested"] - cur, 2)

    def _fill(self):
        if not self.job:
            return
        self.tv.delete(*self.tv.get_children())
        self.cards = {}
        needle = self.find_var.get().strip().lower()
        show = self.show_var.get()
        for i, c in enumerate(self._rows()):
            text = " ".join(str(c.get(k) or "") for k in ("sku", "title", "variant")).lower()
            if needle and needle not in text:
                continue
            listed = (c.get("status") or "").lower() == "listed"
            grade = self.extras["grades"].get(c.get("sku"), "")
            diff = self._diff(c)
            if show == "listed" and not listed or show == "move" and not diff or \
                    show == "ungraded" and grade:
                continue
            p = self.extras["priced"].get(c.get("sku")) or {}
            tag = ("up",) if diff and diff > 0 else ("down",) if diff and diff < 0 else ()
            iid = self.tv.insert("", "end", iid=f"r{i}", tags=tag, values=(
                c.get("sku", ""), c.get("title", ""), c.get("variant", ""),
                c.get("condition", ""), _money(c.get("price")), c.get("status", ""), grade,
                _money(p.get("suggested")), f"{diff:+.2f}" if diff else ""))
            self.cards[iid] = c
        ui.restripe(self.tv)

    def _sel(self):
        s = self.tv.selection()
        return self.cards.get(s[0]) if s else None

    def _picked_card(self):
        c = self._sel()
        if not c:
            return
        self.head.configure(text=f"{c.get('sku')}  ·  {c.get('title')}")
        for w in self.info.winfo_children():
            w.destroy()
        p = self.extras["priced"].get(c.get("sku")) or {}
        vq = self.extras["variants"].get(c.get("sku")) or {}
        rows = [("Set", f"{c.get('setName', '')}  {c.get('cardnumber', '')}"),
                ("Variant", c.get("variant") or "—"),
                ("Condition", c.get("condition") or "—"),
                ("Price", _money(c.get("price")) or "—"),
                ("Status", c.get("status") or "—"),
                ("Graded", self.extras["grades"].get(c.get("sku")) or "not graded"),
                ("House price", (_money(p.get("suggested")) or "—")
                 + (f"  ({p.get('decision')}: {p.get('reason')})" if p.get("decision") else "")),
                ("Variant check", (vq.get("decision") or "not decided") if vq
                 else "no special printing")]
        for r, (k, v) in enumerate(rows):
            ttk.Label(self.info, text=k, style="Small.TLabel").grid(row=r, column=0,
                                                                    sticky="nw", padx=(0, 10))
            ttk.Label(self.info, text=v, wraplength=220, justify="left").grid(
                row=r, column=1, sticky="w")
        imgs = c.get("imageUrls") or {}
        for i, url in enumerate((imgs.get("front") or "", c.get("databaseImageUrl") or "")):
            self.pics[i].delete("all")
            if not url:
                self.pics[i].create_text(THUMB[0] / 2, THUMB[1] / 2, fill="#888",
                                         text="no picture")
                continue
            self.pics[i].create_text(THUMB[0] / 2, THUMB[1] / 2, fill="#888", text="loading…")

            def load(i=i, url=url, sku=c.get("sku")):
                try:
                    from .variant_tab import fetch_image
                    img = fetch_image(url)
                    img.thumbnail(THUMB, Image.LANCZOS)
                except Exception:                             # noqa: BLE001
                    img = None
                self.events.put(("pic", (i, (sku, img))))
            threading.Thread(target=load, daemon=True).start()

    def _show_pic(self, i, payload):
        sku, img = payload
        c = self._sel()
        if not c or c.get("sku") != sku:
            return
        canvas = self.pics[i]
        canvas.delete("all")
        if img is None:
            canvas.create_text(THUMB[0] / 2, THUMB[1] / 2, fill="#888", text="unavailable")
            return
        photo = ImageTk.PhotoImage(img)
        self.photos = [p for p in self.photos[-1:]] + [photo]
        canvas.create_image(THUMB[0] / 2, THUMB[1] / 2, image=photo)

    def grade_by_eye(self, sku=None):
        if not self.job:
            self.head.configure(text="Pick a job first.")
            return
        from .eye_grade import EyeGrader
        if sku is None:
            sel = self._sel()
            sku = sel.get("sku") if sel else None
        EyeGrader(self.winfo_toplevel(), self.job_path, self.job, start_sku=sku,
                  on_change=self._regraded)

    def write_changes(self):
        """changes-<job>.json from the grades (and prices and variants if there are any)."""
        from tkinter import messagebox
        if not self.job or not self.job_path:
            messagebox.showinfo("Write change file", "Pick a job first.", parent=self)
            return
        import contextlib
        import io
        from . import cli_ops
        folder = os.path.dirname(os.path.abspath(self.job_path))
        name = self.job.get("jobName") or ""
        argv = [self.job_path]
        for flag, fname in (("--grades", f"grades-{name}.csv"),
                            ("--variants", f"variant-queue-{name}.csv")):
            if os.path.exists(os.path.join(folder, fname)):
                argv += [flag, os.path.join(folder, fname)]
        if os.path.exists(os.path.join(folder, f"priced-{name}.json")):
            argv += ["--pricing", folder]
        if "--grades" not in argv and len(argv) == 1:
            messagebox.showinfo("Write change file", "Nothing to write yet: this job has no "
                                "grades, prices or variant decisions. Grade by eye first.",
                                parent=self)
            return
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                cli_ops.changes_cmd(argv)
        except (Exception, SystemExit) as exc:              # noqa: BLE001
            messagebox.showerror("Write change file", f"Could not write it: {exc}\n\n"
                                 + out.getvalue(), parent=self)
            return
        path = os.path.join(folder, f"changes-{name or 'job'}.json")
        if messagebox.askyesno(
                "Change file written",
                out.getvalue().strip() + "\n\nNext, in Chrome: open this job on CardUploader → "
                "CardCropper extension → Apply a change file → pick\n" + path
                + "\n→ check the diff → Apply.\n\nOpen the folder now?", parent=self):
            from .gui import _reveal
            _reveal(folder)

    def _regraded(self):
        """A grade was keyed in the grading window: show it here too."""
        if not self.job or not self.job_path:
            return
        keep = self.tv.selection()
        self.extras = self._extras(self.job_path, self.job)
        self._summary()
        self._fill()
        if keep and self.tv.exists(keep[0]):
            self.tv.selection_set(keep[0])

    def _to_market(self):
        c = self._sel()
        if c and self.actions.get("market"):
            self.actions["market"](c.get("title", ""))

    def _to_lookup(self):
        c = self._sel()
        if c and self.actions.get("lookup"):
            self.actions["lookup"](c.get("title", ""))

    def _ebay(self):
        c = self._sel()
        if c:
            webbrowser.open(market.ebay_sold_url(c.get("title", "")))
