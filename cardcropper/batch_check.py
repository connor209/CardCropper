"""
Checking a whole prepared batch against the catalogue, card by card.

Each card's front scan is compared with the catalogue pictures its title
finds (`catalog.check_card`), so a card whose scan does not look like its
title — a wrong match in CardUploader, a mistyped title, the wrong scans
paired with a card — is caught before it is listed, not after a buyer
says so. It identifies the card, not the printing: a scan cannot show foil.

A window of its own, working in the background; the worst cards sort first.
Double-click one to open it in Card lookup with its scan matched.
"""

import os
import queue
import threading

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import catalog, ui

LABELS = {"mismatch": "MISMATCH", "check": "check", "not found": "not in catalogue",
          "no scan": "no scan", "ok": "ok"}


class BatchCheck(tk.Toplevel):
    def __init__(self, master, folder=None, on_open=None):
        super().__init__(master)
        self.title("Check a batch against the catalogue")
        self.geometry("1000x620")
        self.configure(background=ui.C["bg"])
        self.on_open = on_open
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.rows = {}
        self.worker = None

        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(2, weight=1)
        bar = ttk.Frame(f)
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text="Prepared batch").grid(row=0, column=0, padx=(0, 6))
        self.folder_var = tk.StringVar(value=folder or "")
        e = ttk.Entry(bar, textvariable=self.folder_var)
        e.grid(row=0, column=1, sticky="ew")
        ui.tip(e, "A batch's output folder from Condition a CSV — the one holding triage.csv "
                  "and cards/<sku>/front.jpg.")
        ttk.Button(bar, text="Browse…", style="Ghost.TButton",
                   command=self.pick).grid(row=0, column=2, padx=(6, 0))
        self.go_btn = ttk.Button(bar, text="Check every card", style="Accent.TButton",
                                 command=self.start)
        self.go_btn.grid(row=0, column=3, padx=(6, 0))
        ui.tip(self.go_btn, "Search the catalogue for each card's title and compare its front "
                            "scan with what comes back. About a second a card; press again "
                            "to stop.")
        self.status = ttk.Label(f, style="Muted.TLabel")
        self.status.grid(row=1, column=0, sticky="w", pady=(8, 4))
        wrap, self.tv = ui.tree(f, (
            ("sku", "SKU", 80), ("verdict", "Verdict", 110), ("score", "Likeness", 70,
                                                              {"anchor": "e"}),
            ("title", "Title", 280, {"stretch": True}),
            ("best", "Closest catalogue card", 240), ("notes", "Notes", 220)), height=16)
        wrap.grid(row=2, column=0, sticky="nsew")
        self.tv.tag_configure("mismatch", foreground=ui.C["bad"])
        self.tv.tag_configure("check", foreground=ui.C["warn"])
        self.tv.tag_configure("ok", foreground=ui.C["ok"])
        self.tv.bind("<Double-1>", lambda _e: self.open_selected())
        ui.tip(self.tv, "MISMATCH: nothing the title finds looks like the scan — check the "
                        "title, the CardUploader match and the scan pairing. check: only "
                        "somewhat alike. Double-click to look at it in Card lookup.")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(100, self._poll)

    def pick(self):
        d = filedialog.askdirectory(title="A prepared batch's output folder", parent=self)
        if d:
            self.folder_var.set(d)

    def close(self):
        self.stop.set()
        self.destroy()

    def start(self):
        if self.worker and self.worker.is_alive():
            self.stop.set()
            return
        folder = self.folder_var.get().strip()
        try:
            items = catalog.batch_items(folder)
        except OSError:
            messagebox.showinfo("Check a batch", "No triage.csv there — choose the output "
                                                 "folder of a batch prepared on Condition a "
                                                 "CSV.", parent=self)
            return
        self.tv.delete(*self.tv.get_children())
        self.rows = {}
        self.stop.clear()
        self.go_btn.configure(text="Stop")

        def run():
            for i, (sku, title, front) in enumerate(items, 1):
                if self.stop.is_set():
                    break
                try:
                    r = catalog.check_card(title, front)
                except Exception as exc:                    # noqa: BLE001
                    r = {"verdict": "not found", "score": None, "best": None,
                         "findings": [f"{type(exc).__name__}: {exc}"]}
                self.events.put(("row", (i, len(items), sku, title, front, r)))
            self.events.put(("done", None))
        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "row":
                    i, n, sku, title, front, r = payload
                    best = r.get("best")
                    iid = self.tv.insert("", "end", tags=(r["verdict"],), values=(
                        sku, LABELS.get(r["verdict"], r["verdict"]),
                        f"{r['score']:.0%}" if r.get("score") is not None else "",
                        title, f"{best.name} {best.number} · {best.set_name}" if best else "",
                        "; ".join(r.get("findings") or [])))
                    self.rows[iid] = (title, front)
                    self.status.configure(text=f"Checked {i} of {n}…")
                else:
                    self.go_btn.configure(text="Check every card")
                    self._sort()
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(150, self._poll)

    def _sort(self):
        order = {v: n for n, v in enumerate(catalog.BATCH_VERDICTS)}
        rows = sorted(self.tv.get_children(), key=lambda iid: order.get(
            next((k for k, v in LABELS.items() if v == self.tv.set(iid, "verdict")), "ok"), 9))
        for n, iid in enumerate(rows):
            self.tv.move(iid, "", n)
        counts = {}
        for iid in rows:
            v = self.tv.set(iid, "verdict")
            counts[v] = counts.get(v, 0) + 1
        self.status.configure(text="Done: " + ", ".join(f"{n} {v}" for v, n in counts.items())
                              + ". Worst first.")

    def open_selected(self):
        sel = self.tv.selection()
        if sel and sel[0] in self.rows and self.on_open:
            self.on_open(*self.rows[sel[0]])
