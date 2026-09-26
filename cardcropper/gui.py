"""
The CardCropper window.

Tkinter on purpose: it ships with CPython, so the frozen .exe needs no
third-party UI toolkit and stays one file a person can double-click.

The whole design is one screen. Add scans, look at how they paired, fix the
pairs that are wrong, press Crop. There is no wizard because there is only one
decision on it worth pausing over — whether the front and back on each row
belong to the same card — and a wizard would hide exactly that behind a step.

A second tab makes the day's empty scan folders before a session starts —
`26.09.26 - 001`, `26.09.26 - 002`, … — which is where the scans this screen
reads come from.
"""

import os
import queue
import subprocess
import sys
import threading
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import batch, folders, imaging

APP_NAME = "CardCropper"

#: First entry is the default. The labelled sheet is what the card-conditioning
#: skill produces and what these crops are actually read as — captions on every
#: tile, so a flaw can be named by where it is rather than pointed at.
STYLE_LABELS = {
    "Labelled sheet — corners & edges": "grading",
    "Clean photo, no labels": "listing",
}
NAMING_LABELS = {
    "Grouped — 0001_1_front.jpg": "grouped",
    "Continuous — 0001.jpg, 0002.jpg…": "sequence",
}
ORDER_LABELS = {
    "front, back, then both crops": "crops-last",
    "each face followed by its crops": "interleaved",
}


def _reveal(path):
    """Open the output folder in the platform's file manager."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)                              # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:                                       # noqa: BLE001
        pass


class App(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=10)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=3)
        self.rowconfigure(6, weight=2)

        self.cards = []
        self.leftover = []
        self.events = queue.Queue()
        self.worker = None
        self.stop_flag = threading.Event()

        self._build_sources()
        self._build_table()
        self._build_options()
        self._build_run()
        self._poll()
        self._refresh()

    # ------------------------------------------------------------ layout

    def _build_sources(self):
        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew")
        ttk.Button(bar, text="Add scans…", command=self.add_files).pack(side="left")
        ttk.Button(bar, text="Add folder…", command=self.add_folder).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="Clear", command=self.clear).pack(side="left", padx=(6, 0))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(bar, text="Swap front/back", command=self.swap).pack(side="left")
        ttk.Button(bar, text="Remove", command=self.remove).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="▲", width=3, command=lambda: self.move(-1)).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="▼", width=3, command=lambda: self.move(1)).pack(side="left", padx=(2, 0))

        self.hint = ttk.Label(
            self, foreground="#666",
            text="Scans pair in filename order: first is the front, second is the back. "
                 "Check the pairs below before cropping.")
        self.hint.grid(row=1, column=0, sticky="w", pady=(8, 4))

    def _build_table(self):
        wrap = ttk.Frame(self)
        wrap.grid(row=2, column=0, sticky="nsew")
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)

        cols = ("card", "front", "back", "status")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="extended")
        for key, text, width, anchor in (("card", "Card", 60, "center"),
                                         ("front", "Front scan", 260, "w"),
                                         ("back", "Back scan", 260, "w"),
                                         ("status", "Output", 300, "w")):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor=anchor,
                             stretch=(key in ("front", "back", "status")))
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.tag_configure("done", foreground="#1a7f37")
        self.tree.tag_configure("failed", foreground="#b3261e")

    def _build_options(self):
        box = ttk.LabelFrame(self, text="Output", padding=8)
        box.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="Folder").grid(row=0, column=0, sticky="w")
        self.out_var = tk.StringVar()
        ttk.Entry(box, textvariable=self.out_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(box, text="Browse…", command=self.pick_out).grid(row=0, column=2)

        row = ttk.Frame(box)
        row.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self.style_var = tk.StringVar(value=list(STYLE_LABELS)[0])
        self.naming_var = tk.StringVar(value=list(NAMING_LABELS)[0])
        self.order_var = tk.StringVar(value=list(ORDER_LABELS)[0])
        for label, var, options, width in (("Crops", self.style_var, STYLE_LABELS, 28),
                                           ("Naming", self.naming_var, NAMING_LABELS, 28),
                                           ("Order", self.order_var, ORDER_LABELS, 30)):
            ttk.Label(row, text=label).pack(side="left", padx=(0, 4))
            cb = ttk.Combobox(row, textvariable=var, values=list(options),
                              state="readonly", width=width)
            cb.pack(side="left", padx=(0, 14))
            cb.bind("<<ComboboxSelected>>", lambda _e: self._refresh())

        self.copy_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, text="Copy the original scans into the output folder too",
                        variable=self.copy_var, command=self._refresh)\
            .grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))

        self.preview = ttk.Label(box, foreground="#666")
        self.preview.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))

    def _build_run(self):
        bar = ttk.Frame(self)
        bar.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        bar.columnconfigure(1, weight=1)
        self.run_btn = ttk.Button(bar, text="Crop cards", command=self.start)
        self.run_btn.grid(row=0, column=0)
        self.progress = ttk.Progressbar(bar, mode="determinate")
        self.progress.grid(row=0, column=1, sticky="ew", padx=10)
        self.count = ttk.Label(bar, text="")
        self.count.grid(row=0, column=2)
        self.open_btn = ttk.Button(bar, text="Open folder", state="disabled",
                                   command=lambda: _reveal(self.out_var.get()))
        self.open_btn.grid(row=0, column=3, padx=(10, 0))

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

    # ------------------------------------------------------------ state

    def _say(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _opts(self):
        return (STYLE_LABELS[self.style_var.get()],
                NAMING_LABELS[self.naming_var.get()],
                ORDER_LABELS[self.order_var.get()])

    def _refresh(self):
        self.tree.delete(*self.tree.get_children())
        style, naming, order = self._opts()
        for i, card in enumerate(self.cards, 1):
            names = [n for _, n in batch.output_names(i, card, naming, order)]
            if not self.copy_var.get():
                names = [n for (role, n) in batch.output_names(i, card, naming, order)
                         if role.endswith("crops")]
            self.tree.insert("", "end", iid=str(i), values=(
                i, os.path.basename(card.front), os.path.basename(card.back),
                ", ".join(names)))
        self.count.configure(text=f"{len(self.cards)} card(s)")
        self.run_btn.configure(state="normal" if self.cards else "disabled")
        if self.leftover:
            self.hint.configure(
                foreground="#b3261e",
                text=f"{len(self.leftover)} scan(s) left over and ignored: "
                     + ", ".join(os.path.basename(p) for p in self.leftover)
                     + " — an odd number of files means a pair is missing.")
        else:
            self.hint.configure(
                foreground="#666",
                text="Scans pair in filename order: first is the front, second is the "
                     "back. Check the pairs below before cropping.")
        if self.cards:
            names = [n for _, n in batch.output_names(1, self.cards[0], naming, order)]
            self.preview.configure(text="Card 1 will be written as:  " + "   ".join(names))
        else:
            self.preview.configure(text="")

    # ------------------------------------------------------------ actions

    def _adopt(self, paths):
        if not paths:
            return
        plan = batch.pair_sequential(list(paths))
        self.cards.extend(plan.cards)
        self.leftover = plan.leftover
        if not self.out_var.get() and plan.cards:
            self.out_var.set(os.path.join(os.path.dirname(plan.cards[0].front), "cropped"))
        self._say(f"Added {len(plan.cards)} card(s) from {len(paths)} file(s).")
        for w in plan.warnings:
            self._say("  " + w)
        self._refresh()

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="Select card scans — fronts and backs, in order",
            filetypes=[("Images", " ".join("*" + e for e in imaging.IMAGE_EXTS)),
                       ("All files", "*.*")])
        self._adopt(paths)

    def add_folder(self):
        folder = filedialog.askdirectory(title="Select a folder of card scans")
        if folder:
            self._adopt(batch.list_images(folder))

    def pick_out(self):
        folder = filedialog.askdirectory(title="Where should the cropped cards go?")
        if folder:
            self.out_var.set(folder)

    def clear(self):
        self.cards, self.leftover = [], []
        self._refresh()

    def _selected(self):
        return sorted(int(i) - 1 for i in self.tree.selection())

    def swap(self):
        for i in self._selected():
            c = self.cards[i]
            c.front, c.back = c.back, c.front
        self._refresh()

    def remove(self):
        for i in reversed(self._selected()):
            del self.cards[i]
        self._refresh()

    def move(self, delta):
        idx = self._selected()
        if not idx:
            return
        order = idx if delta < 0 else list(reversed(idx))
        moved = []
        for i in order:
            j = i + delta
            if 0 <= j < len(self.cards):
                self.cards[i], self.cards[j] = self.cards[j], self.cards[i]
                moved.append(j)
        self._refresh()
        self.tree.selection_set([str(j + 1) for j in moved])

    # ------------------------------------------------------------ running

    def start(self):
        if self.worker and self.worker.is_alive():
            self.stop_flag.set()
            return
        out = self.out_var.get().strip()
        if not out:
            messagebox.showwarning(APP_NAME, "Choose an output folder first.")
            return
        # Writing crops into the folder being read would feed the app its own
        # output on the next run, and the pairing is positional — every card
        # after the first would be assembled from two different cards.
        sources = {os.path.dirname(os.path.abspath(c.front)) for c in self.cards}
        sources |= {os.path.dirname(os.path.abspath(c.back)) for c in self.cards}
        if os.path.abspath(out) in sources:
            messagebox.showwarning(
                APP_NAME,
                "The output folder is one of the folders the scans came from.\n\n"
                "Crops written there would be picked up as scans next time and "
                "paired into the batch. Choose a separate folder.")
            return
        existing = os.path.isdir(out) and os.listdir(out)
        if existing and not messagebox.askyesno(
                APP_NAME, f"{out}\n\nis not empty. Files with the same names will be "
                          "overwritten. Carry on?"):
            return

        style, naming, order = self._opts()
        plan = batch.Plan(cards=list(self.cards))
        self.progress.configure(maximum=len(plan.cards), value=0)
        self.run_btn.configure(text="Stop")
        self.open_btn.configure(state="disabled")
        self.stop_flag.clear()
        self._say(f"\nCropping {len(plan.cards)} card(s) into {out}")

        def work():
            try:
                done, failed, failures = batch.run(
                    plan, out, naming=naming, order=order, style=style,
                    copy_originals=self.copy_var.get(),
                    progress=lambda *a: self.events.put(("card",) + a),
                    should_stop=self.stop_flag.is_set)
                self.events.put(("finished", done, failed, failures))
            except Exception:                               # noqa: BLE001
                self.events.put(("crashed", traceback.format_exc()))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "card":
                    _, i, total, card, names, notes, err = event
                    self.progress.configure(value=i)
                    self.count.configure(text=f"{i} / {total}")
                    if err:
                        self.tree.item(str(i), tags=("failed",))
                        self.tree.set(str(i), "status", f"failed — {err}")
                        self._say(f"[{i}/{total}] FAILED {os.path.basename(card.front)}: {err}")
                    else:
                        self.tree.item(str(i), tags=("done",))
                        self.tree.set(str(i), "status", ", ".join(names))
                        for n in notes:
                            self._say(f"[{i}/{total}] {n}")
                elif event[0] == "finished":
                    _, done, failed, failures = event
                    self.run_btn.configure(text="Crop cards")
                    self.open_btn.configure(state="normal")
                    self._say(f"Done: {done} card(s) written"
                              + (f", {failed} failed" if failed else "")
                              + (" (stopped early)" if self.stop_flag.is_set() else ""))
                    # The failures again at the end. After a hundred cards the
                    # one that failed has scrolled away, and it is the only
                    # line left that needs acting on.
                    if failures:
                        self._say("Cards to run again:")
                        for i, card, exc in failures:
                            self._say(f"  card {i}: "
                                      f"{os.path.basename(card.front)} + "
                                      f"{os.path.basename(card.back)} — {exc}")
                elif event[0] == "crashed":
                    self.run_btn.configure(text="Crop cards")
                    self._say(event[1])
                    messagebox.showerror(APP_NAME, "The run stopped unexpectedly. "
                                                   "See the log for details.")
        except queue.Empty:
            pass
        self.after(80, self._poll)


class FoldersTab(ttk.Frame):
    """Make the next N `YY.MM.DD - 001` folders for a scanning session."""

    def __init__(self, master, settings):
        super().__init__(master, padding=10)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        box = ttk.LabelFrame(self, text="Scan folders for the day", padding=8)
        box.grid(row=0, column=0, sticky="ew")
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="Location").grid(row=0, column=0, sticky="w")
        self.loc_var = tk.StringVar(value=settings.get("folders_location", ""))
        ttk.Entry(box, textvariable=self.loc_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(box, text="Browse…", command=self.pick_location).grid(row=0, column=2)

        ttk.Label(box, text="Date").grid(row=1, column=0, sticky="w", pady=(8, 0))
        row = ttk.Frame(box)
        row.grid(row=1, column=1, columnspan=2, sticky="w", padx=6, pady=(8, 0))
        self.date_var = tk.StringVar(value=folders.date_label())
        ttk.Entry(row, textvariable=self.date_var, width=10).pack(side="left")
        ttk.Button(row, text="Today", command=lambda: self.date_var.set(folders.date_label()))\
            .pack(side="left", padx=(6, 0))
        ttk.Label(row, text="YY.MM.DD", foreground="#666").pack(side="left", padx=(8, 0))

        ttk.Label(box, text="How many").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.count_var = tk.StringVar(value=str(settings.get("folders_count", 5)))
        ttk.Spinbox(box, from_=1, to=999, textvariable=self.count_var, width=6)\
            .grid(row=2, column=1, sticky="w", padx=6, pady=(8, 0))

        self.preview = ttk.Label(box, foreground="#666")
        self.preview.grid(row=3, column=0, columnspan=3, sticky="w", pady=(10, 0))

        bar = ttk.Frame(self)
        bar.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.make_btn = ttk.Button(bar, text="Create folders", command=self.make)
        self.make_btn.pack(side="left")
        ttk.Button(bar, text="Open location",
                   command=lambda: _reveal(self.loc_var.get().strip()))\
            .pack(side="left", padx=(10, 0))

        ttk.Label(self, text="Log").grid(row=2, column=0, sticky="w", pady=(10, 2))
        self.log = tk.Text(self, height=8, wrap="word", state="disabled")
        self.log.grid(row=3, column=0, sticky="nsew")

        for var in (self.loc_var, self.date_var, self.count_var):
            var.trace_add("write", lambda *_: self._refresh())
        self._refresh()

    def _say(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _inputs(self):
        """(location, count, label) — or raise ValueError saying what is wrong."""
        location = self.loc_var.get().strip()
        if not location:
            raise ValueError("Choose a location for the folders.")
        try:
            count = int(self.count_var.get())
        except ValueError:
            raise ValueError("How many folders? Enter a whole number.") from None
        if not 1 <= count <= 999:
            raise ValueError("Make between 1 and 999 folders at a time.")
        label = self.date_var.get().strip()
        try:
            folders.parse_date_label(label)
        except ValueError:
            raise ValueError(f"'{label}' is not a date in YY.MM.DD form.") from None
        return location, count, label

    def _refresh(self):
        try:
            location, count, label = self._inputs()
        except ValueError as exc:
            self.preview.configure(text=str(exc), foreground="#b3261e")
            self.make_btn.configure(state="disabled")
            return
        names = folders.plan(location, count, label)
        span = names[0] if count == 1 else f"{names[0]}  to  {names[-1]}"
        already = len(folders.existing_numbers(location, label))
        self.preview.configure(
            foreground="#666",
            text=f"Will create {span}"
                 + (f"   ({already} already exist for {label}, so numbering carries on)"
                    if already else ""))
        self.make_btn.configure(state="normal")

    def pick_location(self):
        folder = filedialog.askdirectory(title="Where should the day's scan folders go?",
                                         initialdir=self.loc_var.get() or None)
        if folder:
            self.loc_var.set(os.path.normpath(folder))

    def make(self):
        try:
            location, count, label = self._inputs()
            made = folders.create(location, count, label)
        except (ValueError, OSError) as exc:
            messagebox.showwarning(APP_NAME, str(exc))
            return
        folders.save_settings(folders_location=location, folders_count=count)
        self._say(f"Created {len(made)} folder(s) in {location}:")
        for path in made:
            self._say("  " + os.path.basename(path))
        self._refresh()


def main():
    root = tk.Tk()
    root.title(APP_NAME)
    root.geometry("1080x760")
    root.minsize(880, 620)
    try:
        ttk.Style().theme_use("vista" if sys.platform.startswith("win") else "clam")
    except tk.TclError:
        pass

    settings = folders.load_settings()
    tabs = ttk.Notebook(root)
    tabs.grid(row=0, column=0, sticky="nsew")
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)
    crop_tab, folders_tab = ttk.Frame(tabs), ttk.Frame(tabs)
    tabs.add(crop_tab, text="Crop cards")
    tabs.add(folders_tab, text="Scan folders")
    App(crop_tab)
    FoldersTab(folders_tab, settings)
    # Open on whichever tab was in use last — someone who starts the day by
    # making folders should not have to click across to them every time.
    if settings.get("tab") == "folders":
        tabs.select(folders_tab)
    tabs.bind("<<NotebookTabChanged>>", lambda _e: folders.save_settings(
        tab="folders" if tabs.select() == str(folders_tab) else "crop"))
    root.mainloop()
