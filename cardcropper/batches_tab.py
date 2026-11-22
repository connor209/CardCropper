"""
The "Batches" page: every batch under a folder, and what each still needs.

One row per batch, one column per stage — batched, graded, variants,
priced, change file — each saying how far it has got, and the next step
with a button that opens the page it happens on, with the batch's own file
already loaded. Worked out from the files each step writes (`pipeline`), so
it is never out of step with them.
"""

import os
import queue
import threading

import tkinter as tk
from tkinter import filedialog, ttk

from . import folders, pipeline, ui


class BatchesTab(ttk.Frame):
    def __init__(self, master, actions=None):
        """
        `actions` maps a page key to `open(batch)` — set by the window, it
        shows that page with the batch's file loaded.
        """
        super().__init__(master, padding=10)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.actions = actions or {}
        self.worker = None
        self.events = queue.Queue()
        self.batches = {}

        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text="Batches under").grid(row=0, column=0, padx=(0, 6))
        self.root_var = tk.StringVar(value=folders.load_settings().get("batches_root", ""))
        e = ttk.Entry(bar, textvariable=self.root_var)
        e.grid(row=0, column=1, sticky="ew")
        e.bind("<Return>", lambda _e: self.refresh())
        ui.tip(e, "The folder your batches live under — the scan folders, cropped folders, "
                  "manifests, job data and condition outputs. Searched a few levels deep. "
                  "Remembered.")
        b = ttk.Button(bar, text="Browse…", style="Ghost.TButton", command=self.pick)
        b.grid(row=0, column=2, padx=(6, 0))
        ui.tip(b, "Choose the folder.")
        self.scan_btn = ttk.Button(bar, text="Refresh", style="Accent.TButton",
                                   command=self.refresh)
        self.scan_btn.grid(row=0, column=3, padx=(6, 0))
        ui.tip(self.scan_btn, "Look again at every batch's files. Also done each time this "
                              "page is opened.")

        self.status = ttk.Label(self, style="Muted.TLabel")
        self.status.grid(row=1, column=0, sticky="w", pady=(8, 4))

        cols = [("name", "Batch", 110), ("kind", "From", 80),
                ("cards", "Cards", 50, {"anchor": "e"})]
        cols += [(s, pipeline.STAGE_NAMES[s], 140) for s in pipeline.STAGES]
        cols += [("next", "Next step", 200, {"stretch": True})]
        wrap, self.tv = ui.tree(self, cols, height=16)
        wrap.grid(row=2, column=0, sticky="nsew")
        self.tv.tag_configure("done", foreground=ui.C["ok"])
        self.tv.tag_configure("todo", foreground=ui.C["text"])
        self.tv.bind("<Double-1>", lambda _e: self.go_next())
        self.tv.bind("<<TreeviewSelect>>", lambda _e: self._picked())
        ui.tip(self.tv, "Every batch and how far it has got. ✓ done, — not needed for that "
                        "kind of batch. Double-click to go to its next step.")

        act = ttk.Frame(self)
        act.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.next_btn = ttk.Button(act, text="Go to next step", style="Accent.TButton",
                                   command=self.go_next, state="disabled")
        self.next_btn.pack(side="left")
        ui.tip(self.next_btn, "Open the page the batch's next step happens on, with its file "
                              "loaded.")
        self.folder_btn = ttk.Button(act, text="Open folder", style="Ghost.TButton",
                                     command=self.open_folder, state="disabled")
        self.folder_btn.pack(side="left", padx=(6, 0))
        ui.tip(self.folder_btn, "Open the batch's folder.")
        self.detail = ttk.Label(act, style="Muted.TLabel")
        self.detail.pack(side="left", padx=(12, 0))

        self.bind("<Map>", lambda _e: self.refresh() if self.root_var.get() else None, add="+")
        self.after(150, self._poll)

    def on_show(self):
        pass

    def pick(self):
        d = filedialog.askdirectory(title="The folder your batches live under",
                                    initialdir=self.root_var.get() or None)
        if d:
            self.root_var.set(os.path.normpath(d))
            self.refresh()

    def refresh(self):
        root = self.root_var.get().strip()
        if not root or not os.path.isdir(root):
            self.status.configure(text="Choose the folder your batches live under.",
                                  foreground=ui.C["warn"])
            return
        if self.worker and self.worker.is_alive():
            return
        folders.save_settings(batches_root=root)
        self.status.configure(text="Looking…", foreground=ui.C["muted"])

        def run():
            try:
                self.events.put(("ok", pipeline.scan(root)))
            except Exception as exc:                          # noqa: BLE001
                self.events.put(("err", exc))
        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "err":
                    self.status.configure(text=f"Could not read the batches: {payload}",
                                          foreground=ui.C["bad"])
                else:
                    self._fill(payload)
        except queue.Empty:
            pass
        self.after(200, self._poll)

    def _fill(self, batches):
        keep = self.tv.selection()
        self.tv.delete(*self.tv.get_children())
        self.batches = {}
        for i, b in enumerate(batches):
            cells = []
            for s in pipeline.STAGES:
                done, text = b.stages.get(s, (None, ""))
                cells.append("—" if done is None else ("✓ " if done else "") + text)
            iid = str(i)
            self.tv.insert("", "end", iid=iid, tags=("done" if not b.next_page else "todo",),
                           values=(b.name, "export" if b.kind == "export" else "batch",
                                   b.cards, *cells, b.next_step))
            self.batches[iid] = b
        ui.restripe(self.tv)
        todo = sum(1 for b in batches if b.next_page)
        self.status.configure(
            text=f"{len(batches)} batch(es) — {todo} with something left to do."
            if batches else "No batches found here (no manifest-*.json, and no folder with "
                            "triage.csv and conditioning-review.csv).",
            foreground=ui.C["muted"])
        if keep and keep[0] in self.batches:
            self.tv.selection_set(keep[0])

    def _selected(self):
        sel = self.tv.selection()
        return self.batches.get(sel[0]) if sel else None

    def _picked(self):
        b = self._selected()
        self.folder_btn.configure(state="normal" if b else "disabled")
        can = bool(b and b.next_page and b.next_page in self.actions)
        self.next_btn.configure(state="normal" if can else "disabled",
                                text=f"Go to: {b.next_step}" if can else "Go to next step")
        self.detail.configure(text=b.folder if b else "")

    def go_next(self):
        b = self._selected()
        if b and b.next_page in self.actions:
            self.actions[b.next_page](b)

    def open_folder(self):
        b = self._selected()
        if b:
            from .gui import _reveal
            _reveal(b.folder)
