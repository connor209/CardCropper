"""
The "Prices" tab: the working behind every price, without a command prompt.

Pick what to look at:

* **Golden test** — the seven golden cards, priced live through Comp Finder
  (or **Last golden run**, from the saved evidence, without searching again).
* **Open priced batch…** — a `priced-<job>.json` written by Price & report.
* **Look up** — any listing title, priced live.

Select a card to see its sales: every one Comp Finder found, kept or left
out and why (graded, other printing, a lot, outside the UK…), with the
total that counted and the listing title. Below that, the price worked out
step by step. Double-click a sale to open the eBay listing.

Nothing here changes a price. It is for checking one.
"""

import json
import os
import queue
import threading
import traceback
import webbrowser

import tkinter as tk
from tkinter import filedialog, ttk

from . import conditioning, pricing

KEPT, LEFT_OUT = "kept", "left out"


def _money(v):
    return "" if v is None else f"£{float(v):.2f}"


class PricesTab(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=10)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=2)
        self.rowconfigure(4, weight=3)
        self.events = queue.Queue()
        self.worker = None
        self.priced = []
        self.evidence = {}

        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew")
        ttk.Button(bar, text="Golden test", command=self.golden_live).pack(side="left")
        ttk.Button(bar, text="Last golden run", command=self.golden_saved)\
            .pack(side="left", padx=4)
        ttk.Button(bar, text="Open priced batch…", command=self.open_priced)\
            .pack(side="left", padx=(0, 16))
        ttk.Label(bar, text="Look up").pack(side="left")
        self.title_var = tk.StringVar()
        e = ttk.Entry(bar, textvariable=self.title_var, width=46)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda _e: self.lookup())
        self.game_var = tk.StringVar(value="pokemon")
        ttk.Combobox(bar, values=tuple(pricing.FLOORS), width=8, state="readonly",
                     textvariable=self.game_var).pack(side="left")
        self.cond_var = tk.StringVar(value="NM")
        ttk.Combobox(bar, values=tuple(pricing.CONDITION_MULTIPLIERS), width=5,
                     state="readonly", textvariable=self.cond_var).pack(side="left", padx=4)
        ttk.Button(bar, text="Look up", command=self.lookup).pack(side="left")

        self.status = ttk.Label(self, foreground="#666")
        self.status.grid(row=1, column=0, sticky="w", pady=(6, 2))

        cards = ttk.Frame(self)
        cards.grid(row=2, column=0, sticky="nsew")
        cards.columnconfigure(0, weight=1)
        cards.rowconfigure(0, weight=1)
        cols = ("sku", "title", "price", "expected", "sales", "method", "decision")
        self.cards = ttk.Treeview(cards, columns=cols, show="headings", selectmode="browse")
        for c, w, text in (("sku", 80, "SKU"), ("title", 380, "Card"), ("price", 70, "Price"),
                           ("expected", 70, "Expected"), ("sales", 50, "Kept"),
                           ("method", 70, "Method"), ("decision", 260, "Decision")):
            self.cards.heading(c, text=text)
            self.cards.column(c, width=w, stretch=c in ("title", "decision"))
        self.cards.tag_configure("fail", foreground="#b3261e")
        self.cards.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(cards, orient="vertical", command=self.cards.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.cards.configure(yscrollcommand=sb.set)
        self.cards.bind("<<TreeviewSelect>>", lambda _e: self.show())

        self.working = tk.Text(self, height=6, wrap="word", relief="flat",
                               background=self.winfo_toplevel().cget("background"))
        self.working.grid(row=3, column=0, sticky="ew", pady=(8, 4))

        sales = ttk.Frame(self)
        sales.grid(row=4, column=0, sticky="nsew")
        sales.columnconfigure(0, weight=1)
        sales.rowconfigure(0, weight=1)
        cols = ("state", "why", "total", "price", "postage", "date", "title")
        self.sales = ttk.Treeview(sales, columns=cols, show="headings")
        for c, w, text in (("state", 70, "Counted?"), ("why", 230, "Why left out"),
                           ("total", 70, "Total"), ("price", 70, "Price"),
                           ("postage", 70, "Postage"), ("date", 90, "Sold"),
                           ("title", 520, "Listing title")):
            self.sales.heading(c, text=text)
            self.sales.column(c, width=w, stretch=c == "title")
        self.sales.tag_configure("out", foreground="#888")
        self.sales.tag_configure("kept", foreground="#1a7f37")
        self.sales.grid(row=0, column=0, sticky="nsew")
        sb2 = ttk.Scrollbar(sales, orient="vertical", command=self.sales.yview)
        sb2.grid(row=0, column=1, sticky="ns")
        self.sales.configure(yscrollcommand=sb2.set)
        self.sales.bind("<Double-1>", self._open_listing)
        self.urls = {}
        self._poll()

    # ------------------------------------------------------------ sources

    def _background(self, label, fn):
        if self.worker and self.worker.is_alive():
            return
        self.status.configure(text=label)

        def go():
            try:
                self.events.put(("ok", fn()))
            except Exception as exc:                          # noqa: BLE001
                traceback.print_exc()
                self.events.put(("err", exc))
        self.worker = threading.Thread(target=go, daemon=True)
        self.worker.start()

    def _poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "err":
                    self.status.configure(text=f"{type(payload).__name__}: {payload}",
                                          foreground="#b3261e")
                else:
                    priced, expected, evidence, note = payload
                    self._fill(priced, expected, evidence)
                    self.status.configure(text=note, foreground="#666")
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _golden(self, ev, note):
        rows = pricing.golden_priced(ev)
        priced = [p for _, _, p, _ in rows]
        expected = {p.sku: (e, ok) for _, e, p, ok in rows}
        fails = sum(1 for *_, ok in rows if not ok)
        return priced, expected, ev, f"{note}: {len(rows) - fails} of {len(rows)} pass"

    def golden_live(self):
        def run():
            ev = pricing.batch_comps(pricing.golden_cards())
            path = os.path.join(conditioning.settings_dir(), "price-golden-evidence.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"results": list(ev.values())}, fh, indent=1)
            return self._golden(ev, "Golden test, live")
        self._background("Searching sold listings for the golden cards (about a minute)…", run)

    def golden_saved(self):
        path = os.path.join(conditioning.settings_dir(), "price-golden-evidence.json")
        if not os.path.exists(path):
            self.status.configure(text="No saved golden run yet — press Golden test first.")
            return
        with open(path, encoding="utf-8") as fh:
            ev = {r.get("id"): r for r in json.load(fh).get("results", [])}
        self.events.put(("ok", self._golden(ev, f"Last golden run ({path})")))

    def open_priced(self):
        path = filedialog.askopenfilename(title="priced-<job>.json from Price & report",
                                          filetypes=[("Priced batch", "priced-*.json"),
                                                     ("JSON", "*.json")])
        if not path:
            return
        with open(path, encoding="utf-8") as fh:
            priced = [pricing.Priced(**d) for d in json.load(fh)]
        self.events.put(("ok", (priced, {}, {}, f"{len(priced)} card(s) from {path}")))

    def lookup(self):
        title = self.title_var.get().strip()
        if not title:
            return
        game, cond = self.game_var.get(), self.cond_var.get()

        def run():
            ev = pricing.batch_comps([{"id": "lookup", "title": title}])
            r = ev.get("lookup") or {}
            p = pricing.price_card("lookup", title, r.get("sold"), game, cond,
                                   status=r.get("status", "ok"))
            return [p], {}, ev, f"Looked up “{title}” ({game}, {cond})"
        self._background(f"Searching sold listings for “{title}”…", run)

    # ------------------------------------------------------------ display

    def _fill(self, priced, expected, evidence):
        self.priced, self.evidence = priced, evidence
        self.cards.delete(*self.cards.get_children())
        for i, p in enumerate(priced):
            exp, ok = expected.get(p.sku, (None, True))
            price = _money(p.suggested) if p.suggested is not None else "no price"
            self.cards.insert("", "end", iid=str(i), tags=() if ok else ("fail",), values=(
                p.sku, p.title, price, _money(exp), p.n_sales, p.method,
                f"{p.decision}: {p.reason}"))
        if priced:
            self.cards.selection_set("0")

    def show(self):
        sel = self.cards.selection()
        if not sel:
            return
        p = self.priced[int(sel[0])]
        lines = pricing.working(p)
        row = self.evidence.get(p.sku) or {}
        if row.get("query"):
            lines.insert(0, f"Comp Finder searched: {row['query']}")
        self.working.configure(state="normal")
        self.working.delete("1.0", "end")
        self.working.insert("end", "\n".join(lines))
        self.working.configure(state="disabled")

        self.sales.delete(*self.sales.get_children())
        self.urls = {}
        rows = [(KEPT, "", s) for s in p.kept] + [(LEFT_OUT, s.get("why", ""), s)
                                                  for s in p.excluded]
        rows.sort(key=lambda r: (r[0] != KEPT, pricing.sale_total(r[2])))
        for state, why, s in rows:
            iid = self.sales.insert("", "end", tags=("kept" if state == KEPT else "out",), values=(
                state + (" (put back)" if s.get("put_back") else ""), why,
                _money(pricing.sale_total(s)), _money(s.get("price")), _money(s.get("postage")),
                (s.get("date") or "")[:10], s.get("title", "")))
            if s.get("url"):
                self.urls[iid] = s["url"]

    def _open_listing(self, _e):
        sel = self.sales.selection()
        if sel and sel[0] in self.urls:
            webbrowser.open(self.urls[sel[0]])
