"""
The "CardUploader jobs" window (Tools menu, and the Batches page): list your
jobs with a token copied from the extension, fetch one into its batch folder,
or collect what the extension saved to Downloads. Read-only — see cu_jobs.
"""

import os
import queue
import threading

import tkinter as tk
from tkinter import ttk

from . import carduploader_api, cu_jobs, ui

#: The token, for this run of the app only. Never written anywhere.
_TOKEN = {"value": ""}


class JobsWindow(tk.Toplevel):
    def __init__(self, master, on_fetched=None):
        """`on_fetched(path)` is called after a job's data is saved."""
        super().__init__(master)
        self.title("CardUploader jobs")
        self.geometry("820x520")
        self.configure(background=ui.C["bg"])
        self.on_fetched = on_fetched
        self.events = queue.Queue()
        self.jobs = {}
        f = ttk.Frame(self, padding=14)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(3, weight=1)

        ttk.Label(f, text="CardUploader jobs", style="Heading.TLabel")\
            .grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(f, style="Muted.TLabel", wraplength=780, justify="left", text=(
            "In your CardUploader tab, open the CardCropper panel and press Copy token for "
            "CardCropper, then paste it here. It works until you sign out of CardUploader "
            "and is kept only while the app is open. Nothing is changed on CardUploader "
            "from here.")).grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(2, 8))
        ttk.Label(f, text="Token").grid(row=2, column=0, sticky="w")
        self.token_var = tk.StringVar(value=_TOKEN["value"])
        e = ttk.Entry(f, textvariable=self.token_var, show="•")
        e.grid(row=2, column=1, sticky="ew", padx=6)
        ui.tip(e, "The session token the extension copied. Paste with Ctrl+V.")
        self.list_btn = ttk.Button(f, text="List my jobs", style="Accent.TButton",
                                   command=self.list_jobs)
        self.list_btn.grid(row=2, column=2)
        ui.tip(self.list_btn, "Read your recent CardUploader jobs (GET /jobs/history).")
        b = ttk.Button(f, text="Collect from Downloads", command=self.collect)
        b.grid(row=2, column=3, padx=(6, 0))
        ui.tip(b, "File the job-<batch>.json and carduploader-history.json the extension "
                  "saved to Downloads: each job beside its batch's manifest, the history "
                  "into the settings folder. No token needed.")

        wrap, self.tv = ui.tree(f, (("name", "Job", 120), ("created", "Created", 150),
                                    ("cards", "Cards", 60, {"anchor": "e"}),
                                    ("where", "Will be saved in", 380, {"stretch": True})),
                                height=12)
        wrap.grid(row=3, column=0, columnspan=4, sticky="nsew", pady=(10, 0))
        self.tv.bind("<Double-1>", lambda _e: self.fetch())
        ui.tip(self.tv, "Your jobs, newest first. Double-click one to fetch its data.")
        row = ttk.Frame(f)
        row.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        self.fetch_btn = ttk.Button(row, text="Fetch job data", style="Accent.TButton",
                                    command=self.fetch)
        self.fetch_btn.pack(side="left")
        ui.tip(self.fetch_btn, "Save the selected job's data as job-<name>.json beside its "
                               "batch, ready for Variants, Batch & price and Batches.")
        self.status = ttk.Label(row, style="Muted.TLabel", wraplength=620, justify="left")
        self.status.pack(side="left", padx=(12, 0))
        self.after(120, self._poll)

    def _token(self):
        t = " ".join(self.token_var.get().split())
        _TOKEN["value"] = t
        return t

    def _run(self, label, fn):
        self.status.configure(text=label, foreground=ui.C["muted"])

        def go():
            try:
                self.events.put(("ok", fn()))
            except Exception as exc:                          # noqa: BLE001
                self.events.put(("err", exc))
        threading.Thread(target=go, daemon=True).start()

    def list_jobs(self):
        token = self._token()
        if not token:
            self.status.configure(text="Paste a token from the extension first.",
                                  foreground=ui.C["warn"])
            return

        def run():
            jobs = cu_jobs.jobs_in(cu_jobs.fetch_history(token))
            for j in jobs:
                j["where"] = cu_jobs.destination(j["name"])
            return ("jobs", jobs)
        self._run("Reading your jobs…", run)

    def fetch(self):
        sel = self.tv.selection()
        if not sel:
            self.status.configure(text="Select a job first.", foreground=ui.C["warn"])
            return
        job, token = self.jobs[sel[0]], self._token()
        self._run(f"Fetching {job['name']}…",
                  lambda: ("saved", cu_jobs.fetch_job(job, token)))

    def collect(self):
        self._run("Looking in Downloads…", lambda: ("collected", cu_jobs.collect_downloads()))

    def _poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "err":
                    text = str(payload)
                    if isinstance(payload, carduploader_api.NotSignedIn):
                        old = "=" not in self._token()
                        text = ("CardUploader did not accept the token. " + (
                            "That is an old-style token, which CardUploader no longer "
                            "takes: update the extension (reload it in chrome://extensions, "
                            "then reload the CardUploader tab) and copy again."
                            if old else
                            "You may have signed out — reload the CardUploader tab, sign "
                            "in if asked, and copy a fresh one."))
                    self.status.configure(text=text, foreground=ui.C["bad"])
                    continue
                what, data = payload
                if what == "jobs":
                    self.tv.delete(*self.tv.get_children())
                    self.jobs = {}
                    for j in data:
                        iid = self.tv.insert("", "end", values=(
                            j["name"], j["created"].replace("T", " "), j["cards"], j["where"]))
                        self.jobs[iid] = j
                    ui.restripe(self.tv)
                    self.status.configure(text=f"{len(data)} job(s).",
                                          foreground=ui.C["muted"])
                elif what == "saved":
                    self.status.configure(text=f"Saved {data}", foreground=ui.C["ok"])
                    if self.on_fetched:
                        self.on_fetched(data)
                else:
                    self.status.configure(
                        text=(f"Filed {len(data)} file(s): " + "; ".join(
                            os.path.basename(t) + " → " + os.path.dirname(t)
                            for _, t in data[:3])) if data else
                        "Nothing from the extension in Downloads.",
                        foreground=ui.C["ok"] if data else ui.C["muted"])
                    for _, t in data:
                        if self.on_fetched and os.path.basename(t).startswith("job-"):
                            self.on_fetched(t)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(150, self._poll)
