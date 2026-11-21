"""
The "Batch & price" tab: the command-line batch workflow behind buttons.

1. Make batches — cropped folders in, 50-card batches with their CardUploader
   codes, manifests and crop checks out (`--cli batch`).
2. Price — a CardUploader job's data (saved by the extension) priced from
   Comp Finder's sold comps under the house rules, with the run report
   (`--cli price`). Needs COMP_FINDER_COOKIE (the Comp Finder session cookie).
3. Write change file — conditions from the grades, prices, and variant
   switches, for the extension to apply (`--cli changes`).

Each button runs the same code as the command line, and prints what it would.
"""

import contextlib
import io
import os
import queue
import shlex
import threading
import traceback

import tkinter as tk
from tkinter import filedialog, ttk

from . import batches, cli_ops


class _Writer(io.TextIOBase):
    def __init__(self, q):
        self.q = q

    def write(self, s):
        self.q.put(s)
        return len(s)


class OpsTab(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=10)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)
        self.events = queue.Queue()
        self.worker = None

        b = ttk.LabelFrame(self, text="1. Make batches", padding=8)
        b.grid(row=0, column=0, sticky="ew")
        b.columnconfigure(1, weight=1)
        self.folders_var = tk.StringVar()
        self.history_var = tk.StringVar()
        self.codes_var = tk.StringVar()
        self.game_var = tk.StringVar(value="pokemon")
        self.lang_var = tk.StringVar(value="en")
        rows = (("Cropped folders", self.folders_var, self.pick_folder),
                ("CardUploader history (JSON)", self.history_var, self.pick_history))
        for r, (label, var, cmd) in enumerate(rows):
            ttk.Label(b, text=label).grid(row=r, column=0, sticky="w")
            ttk.Entry(b, textvariable=var).grid(row=r, column=1, sticky="ew", padx=6)
            ttk.Button(b, text="Add…" if r == 0 else "Browse…", command=cmd).grid(row=r, column=2)
        opts = ttk.Frame(b)
        opts.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(opts, text="Game").pack(side="left")
        ttk.Combobox(opts, values=batches.GAMES, width=9, state="readonly",
                     textvariable=self.game_var).pack(side="left", padx=4)
        ttk.Label(opts, text="Language").pack(side="left", padx=(10, 0))
        ttk.Combobox(opts, values=batches.LANGUAGES, width=4, state="readonly",
                     textvariable=self.lang_var).pack(side="left", padx=4)
        ttk.Label(opts, text="Codes (optional)").pack(side="left", padx=(10, 0))
        ttk.Entry(opts, textvariable=self.codes_var, width=14).pack(side="left", padx=4)
        ttk.Button(opts, text="Preview", command=lambda: self.batch(True)).pack(side="left",
                                                                                padx=(10, 0))
        ttk.Button(opts, text="Make batches & check", command=self.batch).pack(side="left",
                                                                               padx=4)

        p = ttk.LabelFrame(self, text="2. Price  ·  3. Change file", padding=8)
        p.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        p.columnconfigure(1, weight=1)
        self.job_var = tk.StringVar()
        self.grades_var = tk.StringVar()
        self.queue_var = tk.StringVar()
        for r, (label, var, types) in enumerate((
                ("Job data (JSON)", self.job_var, ("JSON", "*.json")),
                ("Grades (grades-XX.csv)", self.grades_var, ("CSV", "*.csv")),
                ("Variant queue (optional)", self.queue_var, ("CSV", "*.csv")))):
            ttk.Label(p, text=label).grid(row=r, column=0, sticky="w")
            ttk.Entry(p, textvariable=var).grid(row=r, column=1, sticky="ew", padx=6)
            ttk.Button(p, text="Browse…", command=lambda v=var, t=types: self._file(v, t))\
                .grid(row=r, column=2)
        act = ttk.Frame(p)
        act.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.fresh_var = tk.BooleanVar()
        ttk.Checkbutton(act, text="Fresh comps (skip Comp Finder's 24h cache)",
                        variable=self.fresh_var).pack(side="left")
        ttk.Button(act, text="Price & report", command=self.price).pack(side="left", padx=8)
        ttk.Button(act, text="Report again", command=self.report).pack(side="left")
        ttk.Button(act, text="Write change file", command=self.changes).pack(side="left", padx=8)

        ttk.Label(self, foreground="#666",
                  text="Comp Finder needs COMP_FINDER_COOKIE set to your signed-in session "
                       "cookie. Terapeak checks go in terapeak-<job>.csv beside the job; "
                       "then Report again.").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.log = tk.Text(self, height=18, wrap="word")
        self.log.grid(row=4, column=0, sticky="nsew", pady=(6, 0))
        self._poll()

    def pick_folder(self):
        d = filedialog.askdirectory(title="A cropped folder")
        if d:
            cur = self.folders_var.get().strip()
            self.folders_var.set((cur + " " if cur else "") + shlex.quote(d))

    def pick_history(self):
        self._file(self.history_var, ("JSON", "*.json"))

    def _file(self, var, types):
        p = filedialog.askopenfilename(filetypes=[types, ("All", "*.*")])
        if p:
            var.set(p)

    def _run(self, cmd, argv):
        if self.worker and self.worker.is_alive():
            return
        self.log.insert("end", f"\n$ cardcropper --cli {cmd} {' '.join(argv)}\n")

        def go():
            w = _Writer(self.events)
            with contextlib.redirect_stdout(w), contextlib.redirect_stderr(w):
                try:
                    code = cli_ops.main(cmd, argv)
                    print(f"[done{'' if not code else f', exit {code}'}]")
                except SystemExit as e:
                    print(f"[stopped: {e}]")
                except Exception:                              # noqa: BLE001
                    traceback.print_exc()
        self.worker = threading.Thread(target=go, daemon=True)
        self.worker.start()

    def _poll(self):
        try:
            while True:
                self.log.insert("end", self.events.get_nowait())
                self.log.see("end")
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def batch(self, dry=False):
        argv = shlex.split(self.folders_var.get(), posix=True)
        if not argv:
            return
        argv += ["--game", self.game_var.get(), "--language", self.lang_var.get()]
        if self.history_var.get():
            argv += ["--history", self.history_var.get()]
        if self.codes_var.get():
            argv += ["--codes", self.codes_var.get()]
        if dry:
            argv.append("--dry-run")
        self._run("batch", argv)

    def _job_args(self):
        if not self.job_var.get():
            return None
        argv = [self.job_var.get()]
        if self.grades_var.get():
            argv += ["--grades", self.grades_var.get()]
        return argv

    def price(self):
        argv = self._job_args()
        if argv:
            self._run("price", argv + (["--fresh"] if self.fresh_var.get() else []))

    def report(self):
        argv = self._job_args()
        if argv:
            self._run("report", argv)

    def changes(self):
        argv = self._job_args()
        if not argv:
            return
        argv += ["--pricing", os.path.dirname(os.path.abspath(self.job_var.get()))]
        if self.queue_var.get():
            argv += ["--variants", self.queue_var.get()]
        self._run("changes", argv)
