"""
The Split tab: one long scanning run in, a folder per stack out.

Built around the walk through the breaks, because that is the only part that
needs a person. Cutting a list every 50 is arithmetic; making sure the 50th
scan is the 50th card in the pile is not, and a stack filed one card out
describes every card in it wrongly. So each break is shown as the two cards
either side of it, the operator splits the physical pile there, and moves the
break when the pile says otherwise.
"""

import os
import queue
import threading
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import ImageTk

from . import batch, imaging, stacks
from .gui import APP_NAME, BACKGROUND_LABELS, SPLIT_LABELS, _reveal

THUMB = (110, 154)

#: How many thumbnails to keep decoded. Paging back and forth across a break is
#: the common move and should be instant; holding every card in a 500-card run
#: is not needed for that.
THUMB_CACHE = 48


class SplitTab(ttk.Frame):
    def __init__(self, master, crop_settings=None):
        """
        `crop_settings()` returns the Crop tab's settings and a line describing
        them — see `App.crop_settings`. Without it, cropping is not offered.
        """
        super().__init__(master, padding=10)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.rowconfigure(6, weight=1)

        self.sources = []               # every scan added, planned as one run
        self.plan = stacks.StackPlan()
        self.planner = None
        self.probed = {}
        # Set once filing starts, and the folder numbers stay put until the run
        # is done or thrown away: the first stack's own folder would otherwise
        # count as already there, and a resumed run would renumber past it.
        self.filing = False
        self.current = 1                # the break on screen: stack k starts here
        self.checked = set()            # breaks the operator has confirmed
        self.last_moves = []
        self.last_written = []          # crops written, so Undo can take them too
        self.crop_settings = crop_settings
        self.stop_flag = threading.Event()
        self.cropped = 0
        self.thumbs = {}
        self.events = queue.Queue()
        self.worker = None

        self._build_sources()
        self._build_body()
        self._build_output()
        self._build_run()
        self._poll()
        self._refresh()

    # ------------------------------------------------------------ layout

    def _build_sources(self):
        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew")
        bar = ttk.Frame(top)
        bar.pack(fill="x")
        ttk.Button(bar, text="Add folder…", command=self.add_folder).pack(side="left")
        ttk.Button(bar, text="Add scans…", command=self.add_files).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="Clear", command=self.clear).pack(side="left", padx=(6, 0))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(bar, text="Cards per stack").pack(side="left")
        self.per_var = tk.StringVar(value=str(stacks.PER_STACK))
        ttk.Spinbox(bar, from_=1, to=1000, width=5, textvariable=self.per_var)\
            .pack(side="left", padx=(4, 4))
        ttk.Button(bar, text="Re-cut", command=self.recut).pack(side="left")
        bar = ttk.Frame(top)
        bar.pack(fill="x", pady=(6, 0))
        ttk.Label(bar, text="Scans").pack(side="left", padx=(0, 4))
        # The same two choices the Crop tab pairs by, so a stack folder pairs
        # there into the cards it was cut as here.
        self.split_var = tk.StringVar(value=list(SPLIT_LABELS)[0])
        self.bg_var = tk.StringVar(value=list(BACKGROUND_LABELS)[0])
        for var, options, width in ((self.split_var, SPLIT_LABELS, 30),
                                    (self.bg_var, BACKGROUND_LABELS, 20)):
            cb = ttk.Combobox(bar, textvariable=var, values=list(options),
                              state="readonly", width=width)
            cb.pack(side="left", padx=(0, 6))
            cb.bind("<<ComboboxSelected>>", lambda _e: self._replan())

        self.hint = ttk.Label(self, foreground="#666")
        self.hint.grid(row=1, column=0, sticky="w", pady=(6, 4))

    def _build_body(self):
        body = ttk.Frame(self)
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        wrap = ttk.Frame(body)
        wrap.grid(row=0, column=0, sticky="nsw")
        wrap.rowconfigure(0, weight=1)
        cols = ("stack", "cards", "count", "checked")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings",
                                 selectmode="browse", height=12)
        for key, text, width in (("stack", "Folder", 110), ("cards", "Cards", 90),
                                 ("count", "Count", 55), ("checked", "Break", 70)):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w" if key == "stack" else "center",
                             stretch=False)
        self.tree.grid(row=0, column=0, sticky="ns")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.tag_configure("odd", foreground="#b3261e")
        self.tree.bind("<<TreeviewSelect>>", self._picked)

        walk = ttk.Frame(body, padding=(14, 0, 0, 0))
        walk.grid(row=0, column=1, sticky="nsew")
        walk.columnconfigure(0, weight=1)
        walk.columnconfigure(1, weight=1)
        self.break_title = ttk.Label(walk, font=("TkDefaultFont", 11, "bold"))
        self.break_title.grid(row=0, column=0, columnspan=2, sticky="w")
        self.break_help = ttk.Label(walk, foreground="#444", wraplength=680, justify="left")
        self.break_help.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 6))

        self.sides = []
        for col in (0, 1):
            box = ttk.LabelFrame(walk, padding=6)
            box.grid(row=3, column=col, sticky="nsew", padx=(0, 8) if col == 0 else 0)
            faces = []
            for f in (0, 1):
                cell = ttk.Frame(box)
                cell.grid(row=0, column=f, padx=4)
                pic = ttk.Label(cell, anchor="center")
                pic.pack()
                name = ttk.Label(cell, foreground="#666")
                name.pack()
                faces.append((pic, name))
            self.sides.append((box, faces))

        # Above the pictures, not below them: on a small screen the bottom of
        # this pane is what gets cut off, and these are what is pressed.
        nav = ttk.Frame(walk)
        nav.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.prev_btn = ttk.Button(nav, text="◀ Previous break",
                                   command=lambda: self.go(self.current - 1))
        self.prev_btn.pack(side="left")
        self.earlier_btn = ttk.Button(nav, text="Break 1 card earlier",
                                      command=lambda: self.nudge(-1))
        self.earlier_btn.pack(side="left", padx=(12, 0))
        self.later_btn = ttk.Button(nav, text="Break 1 card later",
                                    command=lambda: self.nudge(1))
        self.later_btn.pack(side="left", padx=(6, 0))
        self.ok_btn = ttk.Button(nav, text="Matches the pile — next ▶", command=self.confirm)
        self.ok_btn.pack(side="right")

    def _build_output(self):
        box = ttk.LabelFrame(self, text="Folders", padding=8)
        box.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="Create in").grid(row=0, column=0, sticky="w")
        self.dest_var = tk.StringVar()
        self.dest_var.trace_add("write", lambda *_: self._refresh_names())
        ttk.Entry(box, textvariable=self.dest_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(box, text="Browse…", command=self.pick_dest).grid(row=0, column=2)

        ttk.Label(box, text="Named").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.name_var = tk.StringVar(value=stacks.TEMPLATE)
        ttk.Entry(box, textvariable=self.name_var, width=30)\
            .grid(row=1, column=1, sticky="w", padx=6, pady=(6, 0))
        self.name_var.trace_add("write", lambda *_: self._refresh_names())

        self.preview = ttk.Label(box, foreground="#666")
        self.preview.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(box, foreground="#888",
                  text="{date} YY.MM.DD · {n} stack number, on from the day's folders · "
                       "{first} {last} card numbers · {count} cards · {n:03d} = 001")\
            .grid(row=3, column=0, columnspan=3, sticky="w")

        # Off by default: filing is quick, cropping five hundred cards is not,
        # and the breaks being right is worth knowing before committing to it.
        self.crop_var = tk.BooleanVar(value=False)
        crop = ttk.Frame(box)
        crop.grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Checkbutton(crop, variable=self.crop_var, command=self._refresh_crop,
                        text=f"Crop each stack once it is filed, into a "
                             f"'{stacks.CROP_FOLDER}' folder inside it",
                        state="normal" if self.crop_settings else "disabled")\
            .pack(anchor="w")
        self.crop_summary = ttk.Label(crop, foreground="#666")
        self.crop_summary.pack(anchor="w", padx=(22, 0))
        # The settings live on the Crop tab and can change while this one is
        # hidden, so the line saying what they are is redrawn on the way back.
        self.bind("<Map>", lambda _e: self._refresh_crop())

    def _build_run(self):
        bar = ttk.Frame(self)
        bar.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        bar.columnconfigure(1, weight=1)
        self.run_btn = ttk.Button(bar, text="Create folders & move scans", command=self.start)
        self.run_btn.grid(row=0, column=0)
        self.progress = ttk.Progressbar(bar, mode="determinate")
        self.progress.grid(row=0, column=1, sticky="ew", padx=10)
        self.undo_btn = ttk.Button(bar, text="Undo", state="disabled", command=self.undo)
        self.undo_btn.grid(row=0, column=2)
        self.open_btn = ttk.Button(bar, text="Open folder", state="disabled",
                                   command=lambda: _reveal(self.dest_var.get()))
        self.open_btn.grid(row=0, column=3, padx=(6, 0))

        ttk.Label(self, text="Log").grid(row=5, column=0, sticky="w", pady=(10, 2))
        wrap = ttk.Frame(self)
        wrap.grid(row=6, column=0, sticky="nsew")
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        self.log = tk.Text(wrap, height=3, wrap="word", state="disabled")
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=sb.set)

    # ------------------------------------------------------------ state

    def _say(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _refresh_crop(self):
        if not self.crop_settings:
            self.crop_summary.configure(text="")
            return
        _, summary = self.crop_settings()
        self.crop_summary.configure(
            text=f"With the Crop cards tab's settings: {summary}"
            if self.crop_var.get() else "")

    def _names(self):
        """Folder names, or None with the reason shown where the preview goes."""
        dest = self.dest_var.get().strip()
        if not self.filing and dest:
            self.plan.number_from(dest, self.name_var.get())
        try:
            return stacks.folder_names(self.plan, self.name_var.get())
        except ValueError as exc:
            self.preview.configure(foreground="#b3261e", text=str(exc))
            return None

    def _refresh_names(self):
        names = self._names()
        if names is None:
            return
        for k, name in enumerate(names):
            if self.tree.exists(str(k)):
                self.tree.set(str(k), "stack", name)
        shown = ", ".join(names[:3]) + (f" … {names[-1]}" if len(names) > 3 else "")
        self.preview.configure(foreground="#666",
                               text=f"{len(names)} folder(s): {shown}" if names else "")

    def _refresh(self):
        self.tree.delete(*self.tree.get_children())
        per = self._per()
        names = self._names() or [""] * len(self.plan)
        for k in range(len(self.plan)):
            start, end = self.plan.span(k)
            mark = "" if k == 0 else ("✓" if k in self.checked else "to check")
            last = k == len(self.plan) - 1
            # The last stack is allowed to be short — it is whatever is left.
            odd = end - start != per and not (last and end - start < per)
            self.tree.insert("", "end", iid=str(k), tags=("odd",) if odd else (),
                             values=(names[k], f"{start + 1}–{end}", end - start, mark))
        self._refresh_names()

        n = len(self.plan.cards)
        if self.plan.leftover:
            self.hint.configure(
                foreground="#b3261e",
                text=f"{len(self.plan.leftover)} scan(s) without a partner, left where "
                     "they are: " + ", ".join(os.path.basename(p) for p in self.plan.leftover)
                     + " — a front or back is missing.")
        elif n:
            breaks = len(self.plan) - 1
            self.hint.configure(
                foreground="#666",
                text=f"{n} cards in {len(self.plan)} stack(s). "
                     f"{len(self.checked)} of {breaks} break(s) checked against the pile.")
        else:
            self.hint.configure(
                foreground="#666",
                text="Add the whole scanning run. It is cut into stacks, then you check "
                     "each break against the physical pile before anything is moved.")
        self.run_btn.configure(state="normal" if n else "disabled")
        self._show_break()

    def _per(self):
        try:
            return max(1, int(self.per_var.get()))
        except ValueError:
            return stacks.PER_STACK

    def _background(self):
        return BACKGROUND_LABELS[self.bg_var.get()]

    def _thumb(self, face):
        key = (face.path, face.side, self._background())
        if key not in self.thumbs:
            if len(self.thumbs) >= THUMB_CACHE:
                self.thumbs.pop(next(iter(self.thumbs)))
            try:
                self.thumbs[key] = ImageTk.PhotoImage(imaging.thumbnail(
                    face.path, THUMB, face.side, self._background()))
            except Exception:                               # noqa: BLE001
                self.thumbs[key] = None
        return self.thumbs[key]

    def _show_card(self, side, title, card):
        box, faces = self.sides[side]
        box.configure(text=title)
        for (pic, name), face in zip(faces, (card.front, card.back) if card else (None, None)):
            img = self._thumb(face) if face else None
            pic.configure(image=img or "", text="" if img or not face else "cannot open")
            pic.image = img
            name.configure(text=face.name if face else "")

    def _show_break(self):
        plan, k = self.plan, self.current
        breaks = len(plan) - 1
        buttons = (self.prev_btn, self.earlier_btn, self.later_btn, self.ok_btn)
        if breaks < 1:
            self.break_title.configure(
                text="Nothing to split" if plan.cards else "No scans yet")
            self.break_help.configure(
                text="The whole run fits in one stack." if plan.cards else "")
            for side in (0, 1):
                self._show_card(side, "", None)
            for b in buttons:
                b.configure(state="disabled")
            return
        for b in buttons:
            b.configure(state="normal")
        self.prev_btn.configure(state="normal" if k > 1 else "disabled")

        start, _ = plan.span(k)
        before, after = plan.cards[start - 1], plan.cards[start]
        names = self._names() or [f"stack {i + 1}" for i in range(len(plan))]
        count = plan.span(k - 1)[1] - plan.span(k - 1)[0]
        self.break_title.configure(
            text=f"Break {k} of {breaks} — between {names[k - 1]} and {names[k]}"
                 + ("   ✓ checked" if k in self.checked else ""))
        self.break_help.configure(
            text=f"Take the next {count} card(s) off the pile for {names[k - 1]}. "
                 f"The last of them should be the card on the left, and the next card in "
                 f"the pile the one on the right. If the pile does not match, move the "
                 f"break until it does — the scans decide which folder a card goes in.")
        self._show_card(0, f"Last card of {names[k - 1]} — card {start}", before)
        self._show_card(1, f"First card of {names[k]} — card {start + 1}", after)
        if self.tree.exists(str(k)):
            self.tree.see(str(k))

    # ------------------------------------------------------------ actions

    def _load(self, paths):
        if not paths:
            return
        self.sources = sorted(set(self.sources) | set(paths), key=batch.natural_key)
        if not self.dest_var.get():
            # Beside the run's folder rather than inside it, which is where the
            # Scan folders tab puts the day's folders too.
            self.dest_var.set(os.path.dirname(os.path.dirname(os.path.abspath(paths[0]))))
        self._replan()

    def _probe(self, path):
        """imaging.probe, remembered, as the Crop tab does it."""
        key = (path, self._background())
        if key not in self.probed:
            self.probed[key] = imaging.probe(path, key[1])
        return self.probed[key]

    def _replan(self):
        """
        Pair and cut the run afresh. Throws away checked breaks and nudges —
        the cards they were checked against may not be the cards any more.
        """
        if not self.sources or (self.planner and self.planner.is_alive()):
            return
        split = SPLIT_LABELS[self.split_var.get()]
        args = dict(per_stack=self._per(), split=split, background=self._background(),
                    probe=self._probe)
        if split != "auto":
            self._planned(stacks.plan_stacks(self.sources, **args))
            return
        self._say("Examining scans…")
        self.run_btn.configure(state="disabled")
        self.progress.configure(maximum=len(self.sources), value=0)
        sources = list(self.sources)

        def work():
            try:
                plan = stacks.plan_stacks(
                    sources, progress=lambda i, n, p: self.events.put(("examining", i)),
                    **args)
                self.events.put(("planned", plan))
            except Exception:                               # noqa: BLE001
                self.events.put(("crashed", traceback.format_exc()))

        self.planner = threading.Thread(target=work, daemon=True)
        self.planner.start()

    def _planned(self, plan):
        self.plan = plan
        self.filing = False
        self.checked.clear()
        self.last_moves = []
        self.last_written = []
        self.undo_btn.configure(state="disabled")
        self.current = 1
        self.progress.configure(value=0)
        combined = sum(1 for c in plan.cards if c.combined)
        self._say(f"{len(plan.cards)} card(s) from {len(self.sources)} scan(s), "
                  f"cut into {len(plan)} stack(s) of {self._per()}."
                  + (f" {combined} have both faces on one scan." if combined else ""))
        for note in plan.notes:
            self._say("  " + note)
        self._refresh()

    def add_folder(self):
        folder = filedialog.askdirectory(title="Select the folder the scanner wrote to")
        if folder:
            self._load(batch.list_images(folder))

    def add_files(self):
        self._load(filedialog.askopenfilenames(
            title="Select the scans — the whole run, in order",
            filetypes=[("Images", " ".join("*" + e for e in imaging.IMAGE_EXTS)),
                       ("All files", "*.*")]))

    def clear(self):
        self.sources = []
        self.filing = False
        self.plan = stacks.StackPlan()
        self.checked.clear()
        self.current = 1
        self.thumbs.clear()
        self._refresh()

    def recut(self):
        if self.checked and not messagebox.askyesno(
                APP_NAME, "Re-cutting throws away the breaks you have checked. Carry on?"):
            return
        self.plan.rebreak(self._per())
        self.checked.clear()
        self.current = 1
        self._refresh()

    def pick_dest(self):
        folder = filedialog.askdirectory(title="Where should the stack folders go?")
        if folder:
            self.dest_var.set(folder)

    def go(self, k):
        self.current = max(1, min(len(self.plan) - 1, k))
        self._show_break()

    def _picked(self, _event):
        sel = self.tree.selection()
        if sel and len(self.plan) > 1:
            k = int(sel[0])
            if max(1, k) != self.current:
                self.go(k)

    def nudge(self, delta):
        if self.plan.nudge(self.current, delta):
            # This break is unproven again. The others are not: they sit at
            # the same card as before, so what was checked there still holds.
            self.checked.discard(self.current)
            self._refresh()

    def confirm(self):
        self.checked.add(self.current)
        nxt = next((k for k in range(self.current + 1, len(self.plan))
                    if k not in self.checked), None)
        if nxt is None:
            nxt = next((k for k in range(1, len(self.plan)) if k not in self.checked),
                       self.current)
        self.current = nxt
        self._refresh()

    # ------------------------------------------------------------ running

    def start(self):
        if self.worker and self.worker.is_alive():
            # Only the cropping stops part-way. Filing is a card-by-card move
            # that is over in moments, and stopping it would leave the one
            # state a split tries hardest to avoid.
            self.stop_flag.set()
            self._say("Stopping after the card being cropped…")
            return
        dest = self.dest_var.get().strip()
        if not dest:
            messagebox.showwarning(APP_NAME, "Choose where the stack folders should go.")
            return
        issues = stacks.problems(self.plan, dest, self.name_var.get())
        if issues:
            messagebox.showwarning(APP_NAME, "Nothing has been moved.\n\n" + "\n".join(issues))
            return
        unchecked = len(self.plan) - 1 - len(self.checked)
        if unchecked > 0 and not messagebox.askyesno(
                APP_NAME,
                f"{unchecked} break(s) have not been checked against the pile.\n\n"
                "A break that is a card out files every card after it in the wrong "
                "stack. Move the scans anyway?"):
            return

        plan, template = self.plan, self.name_var.get()
        crop = self.crop_settings()[0] if self.crop_var.get() and self.crop_settings \
            else None
        stack_folders = [os.path.join(dest, n) for n in stacks.folder_names(plan, template)]
        split, background = SPLIT_LABELS[self.split_var.get()], self._background()
        self.filing = True
        self.cropped = 0
        self.stop_flag.clear()
        total = len(plan.cards)
        self.progress.configure(maximum=total, value=0)
        self.run_btn.configure(state="disabled")
        self._say(f"\nFiling {total} card(s) into {len(plan)} folder(s) in {dest}")
        if crop:
            self._say(f"  then cropping each, {self.crop_settings()[1]}")

        def work():
            try:
                moves = stacks.apply(plan, dest, template,
                                     progress=lambda d, t: self.events.put(("card", d)))
            except stacks.SplitFailed as exc:
                self.events.put(("finished", exc.moves, exc, [], []))
                return
            except Exception:                               # noqa: BLE001
                self.events.put(("crashed", traceback.format_exc()))
                return
            if not crop:
                self.events.put(("finished", moves, None, [], []))
                return
            self.events.put(("cropping",))
            # The scans were examined before they moved; ask under the name
            # they were examined as rather than reading them all again.
            was = {dst: src for src, dst in moves}
            try:
                written, failures = stacks.crop_stacks(
                    stack_folders, split=split, background=background,
                    probe=lambda p: self._probe(was.get(p, p)),
                    progress=lambda *_: self.events.put(("cropped",)),
                    should_stop=self.stop_flag.is_set, **crop)
                self.events.put(("finished", moves, None, written, failures))
            except Exception:                               # noqa: BLE001
                self.events.put(("finished", moves, None, [], []))
                self.events.put(("crashed", traceback.format_exc()))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def undo(self):
        if not self.last_moves:
            return
        if self.last_written:
            # The crops first: a stack folder still holding them is not empty,
            # and would be left behind when its scans went back.
            stacks.remove_written(self.last_written)
            self._say(f"Removed the {len(self.last_written)} cropped file(s) written.")
            self.last_written = []
        stuck = stacks.undo(self.last_moves)
        self._say(f"Undone: {len(self.last_moves) - len(stuck)} scan(s) moved back.")
        for src, dst, exc in stuck:
            self._say(f"  could not move back {dst}: {exc}")
        self.last_moves = []
        self.filing = False
        self.undo_btn.configure(state="disabled")

    def _poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] in ("card", "examining"):
                    self.progress.configure(value=event[1])
                elif event[0] == "cropping":
                    self._say("Filed. Cropping…")
                    self.progress.configure(value=0)
                    self.run_btn.configure(state="normal", text="Stop")
                elif event[0] == "cropped":
                    self.cropped += 1
                    self.progress.configure(value=self.cropped)
                elif event[0] == "planned":
                    self._planned(event[1])
                elif event[0] == "finished":
                    _, moves, err, written, failures = event
                    self.last_written += written
                    self.run_btn.configure(text="Create folders & move scans")
                    # Added to, not replaced: a run picked up after a failure
                    # should undo as one with the part that went first.
                    self.last_moves += moves
                    self.run_btn.configure(state="normal")
                    self.undo_btn.configure(state="normal" if moves else "disabled")
                    self.open_btn.configure(state="normal")
                    if err:
                        self._say(f"Stopped: {err}")
                        self._say(f"{err.card_index} card(s) were filed before it "
                                  "stopped. Run it again to carry on, or Undo to put "
                                  "them back.")
                        messagebox.showerror(APP_NAME, f"The split stopped part-way.\n\n{err}")
                    else:
                        if written or failures:
                            self._say(f"Done: {len(self.plan.cards)} card(s) filed into "
                                      f"{len(self.plan)} folder(s), {self.cropped} "
                                      f"cropped" + (f", {len(failures)} failed"
                                                    if failures else "")
                                      + (" (stopped early)" if self.stop_flag.is_set()
                                         else "") + ".")
                            # Again at the end, as the Crop tab does: in a run
                            # of five hundred the failures have long scrolled
                            # away, and they are the lines to act on.
                            if failures:
                                self._say("Cards to crop again:")
                            for folder, i, card, exc in failures:
                                self._say(f"  {os.path.basename(folder)}, card {i}: "
                                          f"{card.front.name} + {card.back.name} — {exc}")
                        else:
                            self._say(f"Done: {len(self.plan.cards)} card(s) in "
                                      f"{len(self.plan)} folder(s). Each is ready for "
                                      f"the Crop cards tab.")
                        # The run's paths now point at files that have moved.
                        self.filing = False
                        self.sources = list(self.plan.leftover)
                        self.plan = stacks.StackPlan(leftover=self.plan.leftover)
                        self.checked.clear()
                        self.thumbs.clear()
                        self._refresh()
                elif event[0] == "crashed":
                    self.run_btn.configure(state="normal" if self.plan.cards else "disabled")
                    self._say(event[1])
                    messagebox.showerror(APP_NAME, "The split stopped unexpectedly. "
                                                   "See the log for details.")
        except queue.Empty:
            pass
        self.after(80, self._poll)
