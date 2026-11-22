"""
The "Terapeak checks" page: every card a price report sent to a person, from
every batch, in one queue — see terapeak.py.

For the selected card: why it needs a check, the price the rules suggested
and the price it has now, and one click to Terapeak already searched for it
(and to its sales on the Market page). Type the price you settle on and
Save, or Keep current. It is written into that job's terapeak-<job>.csv —
the file the report reads — and when a job's checks are all done, Report
again runs its report so the change file can be written.
"""

import os
import queue
import threading
import webbrowser

import tkinter as tk
from tkinter import ttk

from . import folders, market, terapeak, ui


def _money(v):
    try:
        return f"£{float(v):.2f}"
    except (TypeError, ValueError):
        return ""


class TerapeakTab(ttk.Frame):
    def __init__(self, master, show_sales=None):
        """`show_sales(priced_path, sku)` opens the card's sales on the Market page."""
        super().__init__(master, padding=10)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.show_sales = show_sales
        self.worker = None
        self.events = queue.Queue()
        self.rows = {}
        self.current = None

        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew")
        self.status = ttk.Label(bar, style="Muted.TLabel")
        self.status.pack(side="left")
        self.show_done = tk.BooleanVar(value=False)
        cb = ttk.Checkbutton(bar, text="Show done", variable=self.show_done,
                             command=self.refresh)
        cb.pack(side="right")
        ui.tip(cb, "Also list the checks already made.")
        b = ttk.Button(bar, text="Refresh", style="Ghost.TButton", command=self.refresh)
        b.pack(side="right", padx=(0, 8))
        ui.tip(b, "Look again for terapeak-<job>.csv files under the Batches folder.")

        pane = ttk.PanedWindow(self, orient="horizontal")
        pane.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        left = ttk.Frame(pane)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        wrap, self.tv = ui.tree(left, (
            ("job", "Job", 60), ("sku", "SKU", 70), ("title", "Card", 260, {"stretch": True}),
            ("current", "Now", 60, {"anchor": "e"}), ("suggested", "Rules say", 70,
                                                      {"anchor": "e"}),
            ("done", "Checked", 70, {"anchor": "e"})), height=16)
        wrap.grid(row=0, column=0, sticky="nsew")
        self.tv.tag_configure("done", foreground=ui.C["ok"])
        self.tv.bind("<<TreeviewSelect>>", lambda _e: self._picked())
        ui.tip(self.tv, "Every card waiting on a Terapeak check, from every priced batch "
                        "under the Batches folder.")
        pane.add(left, weight=3)

        right = ttk.Frame(pane, padding=(14, 0, 0, 0))
        right.columnconfigure(1, weight=1)
        self.head = ttk.Label(right, style="Heading.TLabel", wraplength=380, justify="left",
                              text="Select a card")
        self.head.grid(row=0, column=0, columnspan=2, sticky="w")
        self.why = ttk.Label(right, style="Warn.TLabel", wraplength=380, justify="left")
        self.why.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 2))
        self.nums = ttk.Label(right, style="Muted.TLabel", wraplength=380, justify="left")
        self.nums.grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 8))
        links = ttk.Frame(right)
        links.grid(row=3, column=0, columnspan=2, sticky="w")
        self.tp_btn = ttk.Button(links, text="Open Terapeak ↗", style="Accent.TButton",
                                 command=self.open_terapeak, state="disabled")
        self.tp_btn.pack(side="left")
        ui.tip(self.tp_btn, "Terapeak product research for this card: UK, sold, last 90 "
                            "days. Needs you signed in to eBay Seller Hub in the browser.")
        self.sales_btn = ttk.Button(links, text="Sales in Market", command=self.open_sales,
                                    state="disabled")
        self.sales_btn.pack(side="left", padx=(6, 0))
        ui.tip(self.sales_btn, "The sold listings this card's suggested price came from, "
                               "on the Market page.")
        self.ebay_btn = ttk.Button(links, text="eBay sold ↗", style="Ghost.TButton",
                                   command=self.open_ebay, state="disabled")
        self.ebay_btn.pack(side="left", padx=(6, 0))
        ui.tip(self.ebay_btn, "eBay's own UK sold search for the card.")

        ttk.Label(right, text="Price (£)").grid(row=4, column=0, sticky="w", pady=(14, 0))
        self.price_var = tk.StringVar()
        self.price_entry = ttk.Entry(right, textvariable=self.price_var, width=10)
        self.price_entry.grid(row=4, column=1, sticky="w", pady=(14, 0))
        self.price_entry.bind("<Return>", lambda _e: self.save())
        ui.tip(self.price_entry, "The price you settle on after looking at Terapeak. "
                                 "Enter saves it.")
        ttk.Label(right, text="Note").grid(row=5, column=0, sticky="w", pady=(4, 0))
        self.note_var = tk.StringVar()
        e = ttk.Entry(right, textvariable=self.note_var)
        e.grid(row=5, column=1, sticky="ew", pady=(4, 0))
        ui.tip(e, "Why — optional, kept in the file.")
        act = ttk.Frame(right)
        act.grid(row=6, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.save_btn = ttk.Button(act, text="Save price", style="Accent.TButton",
                                   command=self.save, state="disabled")
        self.save_btn.pack(side="left")
        ui.tip(self.save_btn, "Write this price as the check's answer (checked = yes) and "
                              "move to the next card.")
        self.keep_btn = ttk.Button(act, text="Keep current", command=self.keep,
                                   state="disabled")
        self.keep_btn.pack(side="left", padx=(6, 0))
        ui.tip(self.keep_btn, "Terapeak agrees with the price the card has now: keep it.")
        self.saved = ttk.Label(right, wraplength=380, justify="left")
        self.saved.grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.report_btn = ttk.Button(right, text="Report again", command=self.report_again,
                                     state="disabled")
        self.report_btn.grid(row=8, column=0, columnspan=2, sticky="w", pady=(14, 0))
        ui.tip(self.report_btn, "Run this job's price report again with the checks in — "
                                "then Batch & price can write the change file.")
        self.report_out = ttk.Label(right, style="Muted.TLabel", wraplength=380,
                                    justify="left")
        self.report_out.grid(row=9, column=0, columnspan=2, sticky="w", pady=(6, 0))
        pane.add(right, weight=2)
        self.bind("<Map>", lambda _e: self.refresh(), add="+")
        self.after(150, self._poll)

    # ------------------------------------------------------------ queue

    def refresh(self):
        root = folders.load_settings().get("batches_root", "")
        if not root or not os.path.isdir(root):
            self.status.configure(text="Set the Batches folder first (on the Batches page) — "
                                       "the checks are found under it.",
                                  foreground=ui.C["warn"])
            return
        if self.worker and self.worker.is_alive():
            return

        def run():
            try:
                self.events.put(("queue", terapeak.queue(root)))
            except Exception as exc:                          # noqa: BLE001
                self.events.put(("err", exc))
        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _fill(self, items):
        keep = self.current
        self.tv.delete(*self.tv.get_children())
        self.rows = {}
        left = 0
        for n, (path, r) in enumerate(items):
            done = terapeak.is_done(r)
            left += not done
            if done and not self.show_done.get():
                continue
            iid = f"{n}"
            self.tv.insert("", "end", iid=iid, tags=("done",) if done else (), values=(
                terapeak.tag_of(path), r.get("sku", ""), r.get("title", ""),
                _money(r.get("current")), _money(r.get("suggested")) or "none",
                (_money(r.get("terapeak_price")) or "yes") if done else ""))
            self.rows[iid] = (path, r)
        ui.restripe(self.tv)
        jobs = len({terapeak.tag_of(p) for p, _ in items})
        self.status.configure(
            text=f"{left} card(s) waiting on a Terapeak check, across {jobs} job(s)."
            if items else "No Terapeak checks — no priced batch has sent any.",
            foreground=ui.C["muted"])
        target = next((iid for iid, (p, r) in self.rows.items()
                       if keep and (p, r.get("sku")) == keep), None)
        target = target or next(iter(self.rows), None)
        if target:
            self.tv.selection_set(target)
            self.tv.see(target)

    def _picked(self):
        sel = self.tv.selection()
        if not sel or sel[0] not in self.rows:
            return
        path, r = self.rows[sel[0]]
        self.current = (path, r.get("sku"))
        self.head.configure(text=f"{r.get('sku')}  ·  {r.get('title')}")
        self.why.configure(text=f"Why: {r.get('reason') or 'sent for a check'}")
        self.nums.configure(text=f"Now {_money(r.get('current')) or 'no price'}   ·   the "
                                 f"rules suggest {_money(r.get('suggested')) or 'nothing'}"
                                 f"   ·   job {terapeak.tag_of(path)}")
        self.price_var.set(r.get("terapeak_price") or "")
        self.note_var.set(r.get("note") or "")
        for b in (self.tp_btn, self.ebay_btn, self.save_btn, self.keep_btn):
            b.configure(state="normal")
        priced = os.path.join(os.path.dirname(path), f"priced-{terapeak.tag_of(path)}.json")
        self.sales_btn.configure(state="normal" if os.path.exists(priced) and self.show_sales
                                 else "disabled")
        self._job_state(path)
        self.price_entry.focus_set()

    def _job_state(self, path):
        from . import report
        rows = report.read_csv(path)
        left = sum(1 for r in rows if not terapeak.is_done(r))
        self.report_btn.configure(state="normal" if left == 0 else "disabled",
                                  text=f"Report again ({terapeak.tag_of(path)})")
        self.saved.configure(
            text=f"{left} check(s) left in job {terapeak.tag_of(path)}." if left else
            f"Job {terapeak.tag_of(path)}: every check done — Report again.",
            foreground=ui.C["muted"] if left else ui.C["ok"])

    # ------------------------------------------------------------ actions

    def _row(self):
        sel = self.tv.selection()
        return self.rows.get(sel[0]) if sel else None

    def open_terapeak(self):
        row = self._row()
        if row:
            webbrowser.open(terapeak.research_url(row[1].get("title", "")))

    def open_ebay(self):
        row = self._row()
        if row:
            webbrowser.open(market.ebay_sold_url(row[1].get("title", "")))

    def open_sales(self):
        row = self._row()
        if row and self.show_sales:
            path, r = row
            self.show_sales(os.path.join(os.path.dirname(path),
                                         f"priced-{terapeak.tag_of(path)}.json"), r.get("sku"))

    def _record(self, price):
        row = self._row()
        if not row:
            return
        path, r = row
        try:
            terapeak.save(path, r["sku"], price=price, note=self.note_var.get().strip())
        except (OSError, KeyError) as exc:
            self.saved.configure(text=f"Could not save: {exc} (is the file open in Excel?)",
                                 foreground=ui.C["bad"])
            return
        nxt = self.tv.next(self.tv.selection()[0])
        if nxt and nxt in self.rows:
            p2, r2 = self.rows[nxt]
            self.current = (p2, r2.get("sku"))
        self.refresh()

    def save(self):
        text = self.price_var.get().strip().lstrip("£")
        try:
            price = round(float(text), 2)
            if price <= 0:
                raise ValueError
        except ValueError:
            self.saved.configure(text="Type the price you settled on, e.g. 4.99.",
                                 foreground=ui.C["warn"])
            return
        self._record(price)

    def keep(self):
        row = self._row()
        if row:
            self._record(row[1].get("current") or "")

    def report_again(self):
        row = self._row()
        if not row:
            return
        path = row[0]
        self.report_out.configure(text="Running the report…", foreground=ui.C["muted"])

        def run():
            self.events.put(("report", terapeak.report_again(path)))
        threading.Thread(target=run, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "queue":
                    self._fill(payload)
                elif kind == "report":
                    code, text = payload
                    tail = "\n".join(text.strip().splitlines()[-8:])
                    self.report_out.configure(
                        text=("READY — write the change file on Batch & price.\n\n" if code == 0
                              else "Not ready yet:\n\n") + tail,
                        foreground=ui.C["ok"] if code == 0 else ui.C["warn"])
                else:
                    self.status.configure(text=f"Could not read the checks: {payload}",
                                          foreground=ui.C["bad"])
        except queue.Empty:
            pass
        self.after(200, self._poll)
