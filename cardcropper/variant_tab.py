"""
The "Variants" tab: the variant review queue (feature 2.6).

Open a CardUploader job's data (saved by the extension as JSON). Every card
whose catalog entry also comes in a Reverse Holofoil / Cosmos / stamped /
1st Edition printing is listed; for each, the scan is shown beside the
catalog image and one key decides it:

    C  correct          S  switch to the chosen variant          X  HOLD

Nothing is switched from the scans alone (24 of 69 scan-based reverse calls
were wrong in the 21 Nov 2026 audit). HOLD a WotC-era front with a modern
back, or a title that is plainly not the card it was matched to.

Decisions are saved to variant-queue-<job>.csv as you go; "Write change
file" turns the switches into changes-<job>-variants.json for the extension.
"""

import hashlib
import os
import queue
import tempfile
import threading
import urllib.request

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from . import carduploader_api, variants

VIEW = 520
CACHE = os.path.join(tempfile.gettempdir(), "cardcropper-images")


def fetch_image(url):
    if not url:
        return None
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest() + ".img")
    if not os.path.exists(path):
        req = urllib.request.Request(url, headers={"User-Agent": "CardCropper"})
        with urllib.request.urlopen(req, timeout=30) as resp, open(path, "wb") as fh:
            fh.write(resp.read())
    im = Image.open(path)
    im.load()
    return im.convert("RGB")


class VariantTab(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=10)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.job = None
        self.job_path = ""
        self.rows = []
        self.queue_path = ""
        self.index = 0
        self.events = queue.Queue()
        self.images = {}
        self.photos = []

        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)
        self.src_var = tk.StringVar()
        ttk.Label(bar, text="Job data (JSON)").grid(row=0, column=0)
        ttk.Entry(bar, textvariable=self.src_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(bar, text="Open…", command=self.pick).grid(row=0, column=2)
        ttk.Button(bar, text="Write change file", command=self.write_changes)\
            .grid(row=0, column=3, padx=(8, 0))

        self.title_lbl = ttk.Label(self, font=("TkDefaultFont", 12, "bold"), wraplength=1000)
        self.title_lbl.grid(row=1, column=0, sticky="w", pady=(8, 4))

        body = ttk.Frame(self)
        body.grid(row=2, column=0, sticky="nsew")
        for c in (0, 1):
            body.columnconfigure(c, weight=1)
        body.rowconfigure(1, weight=1)
        ttk.Label(body, text="Scan").grid(row=0, column=0, sticky="w")
        ttk.Label(body, text="Catalog image").grid(row=0, column=1, sticky="w")
        self.scan = tk.Canvas(body, width=VIEW, height=VIEW, background="#121212",
                              highlightthickness=0)
        self.scan.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        self.cat = tk.Canvas(body, width=VIEW, height=VIEW, background="#121212",
                             highlightthickness=0)
        self.cat.grid(row=1, column=1, sticky="nsew")

        form = ttk.Frame(self)
        form.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.info = ttk.Label(form, wraplength=1000, justify="left")
        self.info.grid(row=0, column=0, columnspan=8, sticky="w")
        ttk.Label(form, text="Switch to").grid(row=1, column=0, sticky="w")
        self.target = ttk.Combobox(form, state="readonly", width=28)
        self.target.grid(row=1, column=1, padx=6)
        ttk.Button(form, text="Correct (C)", command=lambda: self.decide("correct"))\
            .grid(row=1, column=2)
        ttk.Button(form, text="Switch (S)", command=lambda: self.decide("switch"))\
            .grid(row=1, column=3)
        ttk.Button(form, text="HOLD (X)", command=lambda: self.decide("HOLD"))\
            .grid(row=1, column=4)
        ttk.Button(form, text="◀", width=3, command=lambda: self.go(self.index - 1))\
            .grid(row=1, column=5, padx=(12, 0))
        ttk.Button(form, text="▶", width=3, command=lambda: self.go(self.index + 1))\
            .grid(row=1, column=6)
        self.note_var = tk.StringVar()
        ttk.Label(form, text="Note").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(form, textvariable=self.note_var, width=60)\
            .grid(row=2, column=1, columnspan=6, sticky="w", pady=(6, 0))
        self.status = ttk.Label(form)
        self.status.grid(row=3, column=0, columnspan=8, sticky="w", pady=(6, 0))
        self.bind_all("<Key>", self._key, add="+")
        self._poll()

    def pick(self):
        path = filedialog.askopenfilename(title="CardUploader job data",
                                          filetypes=[("JSON", "*.json"), ("All", "*.*")])
        if path:
            self.load(path)

    def load(self, path):
        self.src_var.set(path)
        self.job_path = path
        self.job = variants.load_job(path)
        name = self.job.get("jobName") or self.job.get("jobId") or "job"
        self.queue_path = os.path.join(os.path.dirname(path), f"variant-queue-{name}.csv")
        fresh = variants.build_queue(self.job)
        if os.path.exists(self.queue_path):
            old = {r["sku"]: r for r in variants.read_queue(self.queue_path)}
            for r in fresh:
                o = old.get(r["sku"])
                if o:
                    r.update(decision=o.get("decision", ""),
                             target_variant=o.get("target_variant", ""), note=o.get("note", ""))
        self.rows = fresh
        variants.write_queue(self.rows, self.queue_path)
        first = next((i for i, r in enumerate(self.rows) if not r["decision"]), 0)
        if not self.rows:
            self.title_lbl.configure(text="No card in this job has a special printing to check.")
            return
        self.go(first)

    def go(self, i):
        if not self.rows:
            return
        self.index = max(0, min(i, len(self.rows) - 1))
        r = self.rows[self.index]
        self.title_lbl.configure(text=f"{r['sku']}  {r['title']}  ({self.index + 1} of "
                                      f"{len(self.rows)})")
        others = [v for v in r["variants"].split(" | ") if v and v != r["variant"]]
        self.target.configure(values=others)
        self.target.set(r.get("target_variant") or (others[0] if others else ""))
        self.note_var.set(r.get("note", ""))
        listed = "LISTED — a switch ends/creates eBay listings. " if r["status"] == "listed" else ""
        self.info.configure(
            text=f"{listed}Matched as: {r['variant']}   Also exists as: {', '.join(others)}   "
                 f"Set: {r['setName']} {r['cardnumber']} ({r['year']})"
                 + (f"\nHint: {r['hint']}" if r["hint"] else "")
                 + (f"\nDecided: {r['decision']} {r['target_variant']}" if r["decision"] else ""),
            foreground="#b3261e" if listed or r["hint"] else "")
        for url in (r["front"], r["catalog_image"]):
            if url and url not in self.images:
                self.images[url] = None
                threading.Thread(target=self._fetch, args=(url,), daemon=True).start()
        self._draw()

    def _fetch(self, url):
        try:
            im = fetch_image(url)
        except Exception as exc:                              # noqa: BLE001
            im = exc
        self.events.put((url, im))

    def _poll(self):
        try:
            while True:
                url, im = self.events.get_nowait()
                self.images[url] = im
                self._draw()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _draw(self):
        if not self.rows:
            return
        r = self.rows[self.index]
        self.photos = []
        for canvas, url in ((self.scan, r["front"]), (self.cat, r["catalog_image"])):
            canvas.delete("all")
            im = self.images.get(url)
            if isinstance(im, Image.Image):
                w = max(canvas.winfo_width(), VIEW)
                h = max(canvas.winfo_height(), VIEW)
                im = im.copy()
                im.thumbnail((w, h), Image.LANCZOS)
                photo = ImageTk.PhotoImage(im)
                self.photos.append(photo)
                canvas.create_image(0, 0, anchor="nw", image=photo)
            else:
                canvas.create_text(10, 10, anchor="nw", fill="#aaa",
                                   text="no image" if not url else
                                   ("loading…" if im is None else f"could not load: {im}"))

    def _key(self, e):
        if not self.winfo_viewable() or not self.rows:
            return
        if isinstance(e.widget, (tk.Entry, ttk.Entry)):
            return
        k = (e.char or "").lower()
        if k == "c":
            self.decide("correct")
        elif k == "s":
            self.decide("switch")
        elif k == "x":
            self.decide("HOLD")
        elif e.keysym == "Right":
            self.go(self.index + 1)
        elif e.keysym == "Left":
            self.go(self.index - 1)

    def decide(self, d):
        if not self.rows:
            return
        r = self.rows[self.index]
        if d == "switch" and not self.target.get():
            self.status.configure(text="no other variant to switch to")
            return
        r["decision"] = d
        r["target_variant"] = self.target.get() if d == "switch" else ""
        r["note"] = self.note_var.get()
        variants.write_queue(self.rows, self.queue_path)
        left = sum(1 for x in self.rows if not x["decision"])
        self.status.configure(text=f"{r['sku']}: {d} {r['target_variant']} — {left} left")
        nxt = next((i for i in range(self.index + 1, len(self.rows))
                    if not self.rows[i]["decision"]), None)
        self.go(nxt if nxt is not None else self.index)

    def write_changes(self):
        if not self.rows:
            return
        changes, holds, undecided = variants.queue_changes(self.rows)
        if undecided and not messagebox.askyesno(
                "Variants", f"{len(undecided)} card(s) are undecided. Write the change file "
                            "for the decided ones anyway?"):
            return
        name = self.job.get("jobName") or self.job.get("jobId") or "job"
        out = os.path.join(os.path.dirname(self.job_path), f"changes-{name}-variants.json")
        game = carduploader_api.game_of_tcg(variants.job_tcg(self.job))
        variants.write_change_file(out, self.job.get("jobId", ""), name, game, changes,
                                   variants.job_tcg(self.job))
        messagebox.showinfo("Variants", f"{len(changes)} switch(es), {len(holds)} held -> {out}\n\n"
                                        "Open it in the CardUploader extension to review the "
                                        "diff and apply.")
