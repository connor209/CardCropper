"""
The "Market" page (formerly Prices): eBay sold listings for any card, and the
working behind every price.

Search a title — or open a priced batch, or run the golden test — and for
each card see:

* the **figures**: the suggested price, what it is built on, the middle and
  range of the sales that counted, how many, and how recent;
* a **chart** of every sale over time — the ones that counted filled, the
  ones left out hollow — with the price lines across it;
* the **listings**, each with why it counted or why it was left out (graded,
  another printing, a lot, abroad…). Select one for its details and a link
  to the listing on eBay;
* the **working**, step by step, in words a person can redo.

A sale that looks wrong can be **left out by hand** to see what the price
would have been without it. That is a what-if: nothing here changes a price
on a listing.

Every search is kept (**History**), with the listings it found, so it can be
looked at again later without searching. Sales copied out of Terapeak or
eBay's sold search can be imported from a CSV and priced under the same rules.
"""

import json
import os
import queue
import threading
import traceback
import webbrowser

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import conditioning, market, pricing, ui

KEPT, LEFT_OUT = "kept", "left out"
GAMES = {"Pokémon": "pokemon", "Yu-Gi-Oh": "yugioh", "MTG": "mtg"}


def _money(v):
    return "" if v is None else f"£{float(v):.2f}"


def _image_url(sale):
    for k in ("image", "imageUrl", "image_url", "img", "thumbnail", "picture", "galleryURL"):
        v = sale.get(k)
        if isinstance(v, dict):
            v = v.get("imageUrl") or v.get("url")
        if v:
            return str(v)
    return ""


class PricesTab(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=10)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.events = queue.Queue()
        self.worker = None
        self.priced = []
        self.expected = {}
        self.evidence = {}
        self.variant_text = {}
        self.current = None          # index into self.priced
        self.left_out = {}           # card index -> set of sale keys left out by hand
        self.rows = []               # [(state, why, sale)] for the card on screen
        self.urls = {}
        self.photos = []
        self.history_ids = {}

        self._build_search()
        self._build_body()
        self._poll()
        self.after(200, self.refresh_history)

    # ------------------------------------------------------------ layout

    def _build_search(self):
        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text="Card").grid(row=0, column=0, padx=(0, 6))
        self.title_var = tk.StringVar()
        e = ttk.Entry(bar, textvariable=self.title_var)
        e.grid(row=0, column=1, sticky="ew")
        e.bind("<Return>", lambda _e: self.lookup())
        ui.tip(e, "A listing title or just the card: name, number and set — "
                  "e.g. \"Lugia 78/124 Fates Collide\". Printing words (Reverse Holo, "
                  "1st Edition, Cosmos…) matter: sales of other printings are left out.")
        self.game_var = tk.StringVar(value="Pokémon")
        g = ttk.Combobox(bar, values=tuple(GAMES), width=9, state="readonly",
                         textvariable=self.game_var)
        g.grid(row=0, column=2, padx=(6, 0))
        ui.tip(g, "Which game — it sets the price floor (Pokémon £2.49, Yu-Gi-Oh £1.99).")
        self.cond_var = tk.StringVar(value="NM")
        c = ttk.Combobox(bar, values=tuple(pricing.CONDITION_MULTIPLIERS), width=5,
                         state="readonly", textvariable=self.cond_var)
        c.grid(row=0, column=3, padx=6)
        ui.tip(c, "The condition to price for. LP and NM share a price; MP, HP and DMG "
                  "take the house cut off the LP price.")
        self.search_btn = ttk.Button(bar, text="Search sold", style="Accent.TButton",
                                     command=self.lookup)
        self.search_btn.grid(row=0, column=4)
        ui.tip(self.search_btn, lambda: (
            "Find this card's recent UK sold listings through Comp Finder and price it "
            "under the house rules. (Enter)" if pricing.comp_finder_cookie() else
            "Needs your Comp Finder login first — More → Comp Finder login…  "
            "Until then, eBay ↗ opens the sold search in your browser."))
        b = ttk.Button(bar, text="eBay ↗", command=self.open_ebay)
        b.grid(row=0, column=5, padx=(6, 0))
        ui.tip(b, "Open eBay's own sold & completed listings for this title in your "
                  "browser — always works, no login needed.")
        more = ui.more_menu(bar, [
            ("Golden test (live)", self.golden_live),
            ("Last golden run", self.golden_saved),
            ("Open priced batch…", self.open_priced),
            None,
            ("Import sales from CSV…", self.import_csv),
            ("eBay live listings ↗", self.open_ebay_active),
            None,
            ("Comp Finder login…", self.set_login),
            ("Clear search history", self.clear_history),
        ], tip_text="Golden pricing test, priced batches, importing sales from Terapeak "
                    "or a sheet, and the Comp Finder login.")
        more.grid(row=0, column=6, padx=(6, 0))

        self.status = ttk.Label(self, style="Muted.TLabel")
        self.status.grid(row=1, column=0, sticky="w", pady=(6, 4))
        self._login_hint()

    def _build_body(self):
        pane = ttk.PanedWindow(self, orient="horizontal")
        pane.grid(row=2, column=0, sticky="nsew")

        # Left: what is being looked at — this run's cards, or past searches.
        left = ttk.Frame(pane)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        self.lists = ttk.Notebook(left)
        self.lists.grid(row=0, column=0, sticky="nsew")
        cards_page = ttk.Frame(self.lists, padding=(0, 4, 0, 0))
        cards_page.columnconfigure(0, weight=1)
        cards_page.rowconfigure(0, weight=1)
        wrap, self.cards = ui.tree(cards_page, (
            ("title", "Card", 220, {"stretch": True}), ("price", "Price", 64, {"anchor": "e"}),
            ("expected", "Expected", 64, {"anchor": "e"}), ("sales", "Kept", 40,
                                                            {"anchor": "e"}),
            ("decision", "Decision", 110)), height=12)
        wrap.grid(row=0, column=0, sticky="nsew")
        self.cards.tag_configure("fail", foreground=ui.C["bad"])
        self.cards.bind("<<TreeviewSelect>>", lambda _e: self._picked_card())
        self.lists.add(cards_page, text="Cards")
        hist_page = ttk.Frame(self.lists, padding=(0, 4, 0, 0))
        hist_page.columnconfigure(0, weight=1)
        hist_page.rowconfigure(1, weight=1)
        self.hist_filter = tk.StringVar()
        hf = ttk.Entry(hist_page, textvariable=self.hist_filter)
        hf.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ui.tip(hf, "Filter past searches by title.")
        self.hist_filter.trace_add("write", lambda *_: self.refresh_history())
        wrap, self.hist = ui.tree(hist_page, (
            ("title", "Searched", 210, {"stretch": True}), ("price", "Price", 60,
                                                            {"anchor": "e"}),
            ("kept", "Kept", 40, {"anchor": "e"}), ("when", "When", 80)), height=12)
        wrap.grid(row=1, column=0, sticky="nsew")
        self.hist.bind("<<TreeviewSelect>>", lambda _e: self._picked_history())
        self.lists.add(hist_page, text="History")
        ui.tip(self.lists, "Cards: what you just searched, opened or tested.  History: every "
                           "search kept with its listings — look again without searching.")
        pane.add(left, weight=1)

        # Right: one card in depth.
        right = ttk.Frame(pane, padding=(10, 0, 0, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)
        self.card_title = ttk.Label(right, style="Heading.TLabel", text="Search for a card",
                                    wraplength=760, justify="left")
        self.card_title.grid(row=0, column=0, sticky="w")

        stats = ttk.Frame(right)
        stats.grid(row=1, column=0, sticky="ew", pady=(6, 6))
        self.figs = {}
        for i, (key, label, tiptext) in enumerate((
                ("suggested", "Suggested", "The house price for this condition: the basis "
                                           "rounded up onto the .49/.99 ladder, never below "
                                           "the floor."),
                ("basis", "Basis", "What the price is built on: the 40th percentile of the "
                                   "sale totals — or the lowest, with fewer than 4 sales."),
                ("median", "Middle sale", "Half the counted sales went for less than this, "
                                          "half for more."),
                ("range", "Cheapest", "Cheapest and dearest counted sale (price + postage, "
                                   "postage capped at £4)."),
                ("n", "Sales counted", "Sales of this printing that counted, out of all "
                                       "found. Fewer than 4 is thin evidence."),
                ("last", "Last sold", "Date of the most recent counted sale."))):
            card = ui.stat_card(stats, label, "—", "", width=110, compact=True)
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 6, 0))
            stats.columnconfigure(i, weight=1, uniform="fig")
            ui.tip(card, tiptext)
            self.figs[key] = card

        self.chart = tk.Canvas(right, height=170, background=ui.C["surface"],
                               highlightthickness=1, highlightbackground=ui.C["border"])
        self.chart.grid(row=2, column=0, sticky="ew")
        self.chart.bind("<Configure>", lambda _e: self._draw_chart())
        ui.tip(self.chart, "Every sale found, by date and total. Filled dots counted, hollow "
                           "ones were left out. The green line is the suggested price, the "
                           "dashed one its basis. Click a dot to see that listing.")

        lower = ttk.PanedWindow(right, orient="horizontal")
        lower.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
        sales = ttk.Frame(lower)
        sales.columnconfigure(0, weight=1)
        sales.rowconfigure(1, weight=1)
        top = ttk.Frame(sales)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(top, text="Sold listings", style="Heading.TLabel").pack(side="left")
        self.show_out = tk.BooleanVar(value=True)
        cb = ttk.Checkbutton(top, text="Show left out", variable=self.show_out,
                             command=self._fill_sales)
        cb.pack(side="left", padx=(12, 0))
        ui.tip(cb, "Also list the sales that did not count, with the reason each was left "
                   "out.")
        self.reset_btn = ttk.Button(top, text="Reset what-if", style="Ghost.TButton",
                                    command=self.reset_whatif, state="disabled")
        self.reset_btn.pack(side="right")
        ui.tip(self.reset_btn, "Put back every sale you left out by hand.")
        wrap, self.sales = ui.tree(sales, (
            ("state", "Counted?", 70), ("total", "Total", 62, {"anchor": "e"}),
            ("date", "Sold", 88), ("title", "Listing title", 300, {"stretch": True}),
            ("why", "Why left out", 150)), height=8)
        wrap.grid(row=1, column=0, sticky="nsew")
        self.sales.tag_configure("out", foreground=ui.C["faint"])
        self.sales.tag_configure("kept", foreground=ui.C["ok"])
        self.sales.tag_configure("mine", foreground=ui.C["bad"])
        self.sales.bind("<<TreeviewSelect>>", lambda _e: self._picked_sale())
        self.sales.bind("<Double-1>", lambda _e: self.open_listing())
        self.sales.bind("<Delete>", lambda _e: self.toggle_sale())
        ui.tip(self.sales, "Select a sale for its details. Double-click to open it on eBay; "
                           "Delete leaves it out (or puts it back) to see the price without it.")
        lower.add(sales, weight=3)

        detail = ttk.Frame(lower, padding=(10, 0, 0, 0), width=300)
        detail.columnconfigure(0, weight=1)
        ttk.Label(detail, text="Listing", style="Heading.TLabel").grid(row=0, column=0,
                                                                       sticky="w")
        self.pic = tk.Canvas(detail, width=260, height=180, background=ui.C["sunken"],
                             highlightthickness=0)
        self.pic.grid(row=1, column=0, sticky="ew", pady=(4, 6))
        self.sale_title = ttk.Label(detail, wraplength=270, justify="left")
        self.sale_title.grid(row=2, column=0, sticky="w")
        self.sale_info = ttk.Label(detail, style="Muted.TLabel", wraplength=270,
                                   justify="left")
        self.sale_info.grid(row=3, column=0, sticky="w", pady=(4, 6))
        btns = ttk.Frame(detail)
        btns.grid(row=4, column=0, sticky="w")
        self.open_btn = ttk.Button(btns, text="Open on eBay ↗", command=self.open_listing,
                                   state="disabled")
        self.open_btn.pack(side="left")
        ui.tip(self.open_btn, lambda: "Open this listing on eBay." if self._sale_url() else
               "This sale came without a link — use eBay ↗ above to find it.")
        self.toggle_btn = ttk.Button(btns, text="Leave out", command=self.toggle_sale,
                                     state="disabled")
        self.toggle_btn.pack(side="left", padx=(6, 0))
        ui.tip(self.toggle_btn, "What if this sale had not counted? Works the price again "
                                "without it (or with it back). Nothing on a listing changes.")
        copy = ttk.Button(btns, text="Copy title", style="Ghost.TButton",
                          command=lambda: self._copy_sale_title())
        copy.pack(side="left", padx=(6, 0))
        ui.tip(copy, "Copy the listing title — handy for searching the same wording.")
        lower.add(detail, weight=1)

        work = ttk.Frame(right)
        work.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        work.columnconfigure(0, weight=1)
        self.work_open = tk.BooleanVar(value=False)
        self.work_btn = ttk.Checkbutton(work, text="How this price was worked out ▸",
                                        variable=self.work_open, command=self._toggle_working)
        self.work_btn.grid(row=0, column=0, sticky="w")
        ui.tip(self.work_btn, "Every step from the sales to the price, in words you can redo "
                              "by hand.")
        self.working = tk.Text(work, height=7, wrap="word", font="TkDefaultFont")
        pane.add(right, weight=3)

    # ------------------------------------------------------------ background

    def _background(self, label, fn):
        if self.worker and self.worker.is_alive():
            self.status.configure(text="Still working on the last search…")
            return
        self.status.configure(text=label, foreground=ui.C["muted"])
        self.search_btn.configure(state="disabled")

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
                    self.search_btn.configure(state="normal")
                    msg = f"{payload}" if isinstance(payload, pricing.CompFinderError) \
                        else f"{type(payload).__name__}: {payload}"
                    self.status.configure(text=msg, foreground=ui.C["bad"])
                elif kind == "pic":
                    key, img = payload
                    if key == self._sale_key_selected():
                        self._show_pic(img)
                else:
                    self.search_btn.configure(state="normal")
                    priced, expected, evidence, note = payload[:4]
                    variants = payload[4] if len(payload) > 4 else {}
                    self._fill(priced, expected, evidence, variants)
                    self.status.configure(text=note, foreground=ui.C["muted"])
                    self.refresh_history()
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _login_hint(self):
        if not pricing.comp_finder_cookie():
            self.status.configure(
                text="Not logged in to Comp Finder — searching needs it (More → Comp Finder "
                     "login…). History, imported sales and eBay ↗ work without it.",
                foreground=ui.C["warn"])

    # ------------------------------------------------------------ sources

    def _golden(self, ev, note):
        rows = pricing.golden_priced(ev)
        priced = [p for _, _, p, _ in rows]
        expected = {p.sku: (e, ok) for _, e, p, ok in rows}
        variants = {f"golden-{i}": v for i, (_, v, _) in enumerate(pricing.GOLDEN)}
        fails = sum(1 for *_, ok in rows if not ok)
        return priced, expected, ev, f"{note}: {len(rows) - fails} of {len(rows)} pass", \
            variants

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
            self.status.configure(text="No saved golden run yet — run the golden test first.",
                                  foreground=ui.C["warn"])
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

    def _game(self):
        return GAMES.get(self.game_var.get(), "pokemon")

    def lookup(self, title=None):
        """Search sold listings for a title — also called by Card lookup."""
        if title:
            self.title_var.set(title)
        title = self.title_var.get().strip()
        if not title:
            self.status.configure(text="Type a card or listing title first.",
                                  foreground=ui.C["warn"])
            return
        if not pricing.comp_finder_cookie():
            self._login_hint()
            if messagebox.askyesno("Market", "Searching sold listings needs your Comp Finder "
                                             "login.\n\nSet it now? (Or press No and use "
                                             "eBay ↗ to search in your browser.)"):
                self.set_login()
            return
        game, cond = self._game(), self.cond_var.get()

        def run():
            ev = pricing.batch_comps([{"id": "lookup", "title": title}])
            r = ev.get("lookup") or {}
            p = pricing.price_card("lookup", title, r.get("sold"), game, cond,
                                   status=r.get("status", "ok"))
            market.save_search(title, r, p, game, cond)
            return [p], {}, ev, f"Searched “{title}” ({game}, {cond}) — " \
                                f"{len(p.kept) + len(p.excluded)} sale(s) found, " \
                                f"{p.n_sales} counted"
        self._background(f"Searching sold listings for “{title}”…", run)

    def import_csv(self):
        path = filedialog.askopenfilename(
            title="Sales copied from Terapeak or eBay, saved as CSV",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        try:
            sold, note = market.read_sales_csv(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Import sales", str(exc))
            return
        title = self.title_var.get().strip() or os.path.splitext(os.path.basename(path))[0]
        p = pricing.price_card("imported", title, sold, self._game(), self.cond_var.get())
        ev = {"status": "ok", "sold": sold, "query": f"imported from {os.path.basename(path)}"}
        market.save_search(title, ev, p, self._game(), self.cond_var.get(), source="csv")
        self.events.put(("ok", ([p], {}, {"imported": ev}, note)))

    def set_login(self):
        """Paste the Comp Finder session cookie into its file — no setx, no truncation."""
        top = tk.Toplevel(self)
        top.title("Comp Finder login")
        top.transient(self.winfo_toplevel())
        f = ttk.Frame(top, padding=14)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Comp Finder login", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(f, style="Muted.TLabel", wraplength=520, justify="left", text=(
            "Sign in to Comp Finder in your browser, open the developer tools (F12) → "
            "Network, click any request to comp-finder, and copy the whole Cookie request "
            "header. Paste it below. It is saved in the settings folder, not sent anywhere "
            "but Comp Finder.")).pack(anchor="w", pady=(4, 8))
        box = tk.Text(f, height=6, width=70, wrap="char")
        box.pack(fill="both", expand=True)
        box.insert("1.0", pricing.comp_finder_cookie())

        def save():
            text = " ".join(box.get("1.0", "end").split())
            os.makedirs(os.path.dirname(pricing.cookie_path()), exist_ok=True)
            with open(pricing.cookie_path(), "w", encoding="utf-8") as fh:
                fh.write(text)
            top.destroy()
            self.status.configure(text="Comp Finder login saved." if text else
                                  "Comp Finder login cleared.", foreground=ui.C["ok"])
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(10, 0))
        ttk.Button(row, text="Save", style="Accent.TButton", command=save).pack(side="right")
        ttk.Button(row, text="Cancel", style="Ghost.TButton", command=top.destroy)\
            .pack(side="right", padx=6)
        box.focus_set()

    def open_ebay(self):
        title = self.title_var.get().strip() or (
            self.priced[self.current].title if self.current is not None else "")
        if title:
            webbrowser.open(market.ebay_sold_url(title))

    def open_ebay_active(self):
        title = self.title_var.get().strip() or (
            self.priced[self.current].title if self.current is not None else "")
        if title:
            webbrowser.open(market.ebay_active_url(title))

    # ------------------------------------------------------------ history

    def refresh_history(self):
        try:
            rows = market.history(search=self.hist_filter.get().strip())
        except Exception:                                     # noqa: BLE001
            return
        self.hist.delete(*self.hist.get_children())
        self.history_ids = {}
        for r in rows:
            iid = self.hist.insert("", "end", values=(
                r["title"], _money(r["suggested"]) or "—", r["n_kept"] or 0,
                market.ago(r["searched_at"])))
            self.history_ids[iid] = r["id"]
        ui.restripe(self.hist)

    def _picked_history(self):
        sel = self.hist.selection()
        if not sel or sel[0] not in self.history_ids:
            return
        try:
            p, ev, row = market.load_search(self.history_ids[sel[0]])
        except Exception as exc:                              # noqa: BLE001
            self.status.configure(text=f"Could not open that search: {exc}",
                                  foreground=ui.C["bad"])
            return
        self.title_var.set(row["title"])
        self._fill([p], {}, {"lookup": ev}, {"lookup": row.get("variant") or ""}, keep_tab=True)
        self.status.configure(
            text=f"From history — searched {market.ago(row['searched_at'])}, priced again "
                 "now under today's rules.", foreground=ui.C["muted"])

    def clear_history(self):
        if messagebox.askyesno("Market", "Forget every saved search and its listings?"):
            market.forget()
            self.refresh_history()

    # ------------------------------------------------------------ display

    def _fill(self, priced, expected, evidence, variants=None, keep_tab=False):
        self.priced, self.expected, self.evidence = priced, expected, evidence
        self.variant_text = variants or {}
        self.left_out = {}
        self.cards.delete(*self.cards.get_children())
        for i, p in enumerate(priced):
            exp, ok = expected.get(p.sku, (None, True))
            price = _money(p.suggested) if p.suggested is not None else "no price"
            self.cards.insert("", "end", iid=str(i), tags=() if ok else ("fail",), values=(
                p.title, price, _money(exp), p.n_sales, p.decision))
        ui.restripe(self.cards)
        if not keep_tab:
            self.lists.select(0)
        if priced:
            self.cards.selection_set("0")
            self.show(0)

    def _picked_card(self):
        sel = self.cards.selection()
        if sel:
            self.show(int(sel[0]))

    def _shown(self):
        """The card on screen, worked again if sales were left out by hand."""
        p = self.priced[self.current]
        out = self.left_out.get(self.current)
        if out:
            return market.reprice(p, out, self.variant_text.get(p.sku, ""))
        return p

    def show(self, index=None):
        if index is not None:
            self.current = index
        if self.current is None or self.current >= len(self.priced):
            return
        p = self._shown()
        base = self.priced[self.current]
        whatif = bool(self.left_out.get(self.current))
        self.card_title.configure(text=p.title + (f"   ·   {p.condition}" if p.condition else ""))
        f = market.figures(p)
        price_note = f"{p.decision}: {p.reason}" if p.decision else ""
        if whatif and base.suggested != p.suggested:
            price_note = f"what-if — was {_money(base.suggested) or 'no price'}"
        self.figs["suggested"].set(_money(p.suggested) or "none", price_note,
                                   colour=ui.C["warn"] if whatif else ui.C["accent"])
        self.figs["basis"].set(_money(f["basis"]) or "—",
                               {"lowest": "lowest sale (fewer than 4)",
                                "none": ""}.get(f["method"], f"{f['method']} of totals"))
        self.figs["median"].set(_money(f["median"]) or "—",
                                f"mean {_money(f['mean'])}" if f["mean"] else "")
        self.figs["range"].set(_money(f["min"]) or "—",
                               (f"to {_money(f['max'])}" + (
                                   f" · middle half {_money(f['p25'])}–{_money(f['p75'])}"
                                   if f["n"] >= 4 else "")) if f["n"] else "")
        thin = f["n"] < pricing.MIN_FOR_PERCENTILE
        self.figs["n"].set(str(f["n"]), f"of {f['n'] + f['excluded']} found",
                           colour=ui.C["warn"] if thin else None)
        if f["last"]:
            self.figs["last"].set(f["last"].strftime("%d %b"),
                                  f"{f.get('days_since', 0)} day(s) ago")
        else:
            self.figs["last"].set("—", "")
        self.reset_btn.configure(state="normal" if whatif else "disabled")

        lines = pricing.working(p)
        row = self.evidence.get(base.sku) or {}
        if row.get("query"):
            lines.insert(0, f"Searched: {row['query']}")
        if whatif:
            lines.insert(0, f"WHAT-IF: {len(self.left_out[self.current])} sale(s) left out by "
                            "hand. Nothing on a listing has changed.")
        self.working.configure(state="normal")
        self.working.delete("1.0", "end")
        self.working.insert("end", "\n".join(lines))
        self.working.configure(state="disabled")
        self.rows = market.all_sales(p)
        self._fill_sales()
        self._draw_chart()

    def _toggle_working(self):
        if self.work_open.get():
            self.working.grid(row=1, column=0, sticky="ew", pady=(4, 0))
            self.work_btn.configure(text="How this price was worked out ▾")
        else:
            self.working.grid_remove()
            self.work_btn.configure(text="How this price was worked out ▸")

    def _fill_sales(self):
        self.sales.delete(*self.sales.get_children())
        self.urls = {}
        for i, (state, why, s) in enumerate(self.rows):
            if state != KEPT and not self.show_out.get():
                continue
            mine = why == market.LEFT_OUT
            tag = "kept" if state == KEPT else ("mine" if mine else "out")
            iid = self.sales.insert("", "end", iid=str(i), tags=(tag,), values=(
                state + (" (put back)" if s.get("put_back") else ""),
                _money(pricing.sale_total(s)), (s.get("date") or "")[:10], s.get("title", ""),
                why))
            if s.get("url"):
                self.urls[iid] = s["url"]
        self._clear_sale()

    # ------------------------------------------------------------ chart

    def _draw_chart(self, selected=None):
        c = self.chart
        c.delete("all")
        w, h = max(c.winfo_width(), 200), max(c.winfo_height(), 120)
        if not self.rows:
            c.create_text(w / 2, h / 2, text="Search for a card to see its sales here",
                          fill=ui.C["faint"])
            return
        pts = []
        for i, (state, why, s) in enumerate(self.rows):
            pts.append((i, market.sale_date(s), pricing.sale_total(s), state, why))
        dated = [d for _, d, *_ in pts if d]
        l, r, t, b = 52, 14, 12, 24
        p = self._shown()
        # Scaled to the sales that counted: one graded slab at £60 left out
        # would otherwise flatten every real sale onto the bottom line.
        # Anything above the scale is pinned to the top with a marker.
        kept = [tot for _, _, tot, state, _ in pts if state == KEPT]
        top = max(kept or [tot for _, _, tot, _, _ in pts]) * 1.25 or 1
        for line in (p.suggested, p.basis):
            if line:
                top = max(top, line * 1.15)

        def y(v):
            return h - b - (v / top) * (h - t - b)

        if dated:
            d0, d1 = min(dated), max(dated)
            span = max((d1 - d0).days, 1)

            def x(i, d):
                if d is None:
                    return l + 4
                return l + ((d - d0).days / span) * (w - l - r)
        else:
            n = max(len(pts) - 1, 1)

            def x(i, d):
                return l + (i / n) * (w - l - r)

        for k in range(5):
            v = top * k / 4
            yy = y(v)
            c.create_line(l, yy, w - r, yy, fill=ui.C["sunken"])
            c.create_text(l - 6, yy, text=f"£{v:.0f}" if top >= 40 else f"£{v:.2f}",
                          anchor="e", fill=ui.C["faint"], font="CCSmall")
        if dated:
            for d, anchor in ((min(dated), "w"), (max(dated), "e")):
                c.create_text(x(0, d), h - b + 12, text=d.strftime("%d %b %y"), anchor=anchor,
                              fill=ui.C["faint"], font="CCSmall")
        if p.basis:
            c.create_line(l, y(p.basis), w - r, y(p.basis), fill=ui.C["accent"], dash=(4, 3))
        if p.suggested:
            c.create_line(l, y(p.suggested), w - r, y(p.suggested), fill=ui.C["ok"], width=2)
            c.create_text(w - r, y(p.suggested) - 8, anchor="e", fill=ui.C["ok"],
                          text=f"suggested {_money(p.suggested)}", font="CCSmall")
        for i, d, tot, state, why in pts:
            if state != KEPT and not self.show_out.get():
                continue
            cx, cy = x(i, d), y(min(tot, top))
            big = 6 if str(i) == selected else 4
            if tot > top:
                c.create_text(cx, cy + 10, text="▲", fill=ui.C["faint"], font="CCSmall")
            if state == KEPT:
                kw = {"fill": ui.C["accent"], "outline": ui.C["accent"]}
            elif why == market.LEFT_OUT:
                kw = {"fill": "", "outline": ui.C["bad"], "width": 2}
            else:
                kw = {"fill": "", "outline": ui.C["faint"]}
            dot = c.create_oval(cx - big, cy - big, cx + big, cy + big, tags=(f"s{i}",), **kw)
            c.tag_bind(dot, "<Button-1>", lambda _e, i=i: self._select_sale(i))
            c.tag_bind(dot, "<Enter>", lambda _e, i=i, cx=cx, cy=cy: self._hover(i, cx, cy))
            c.tag_bind(dot, "<Leave>", lambda _e: c.delete("hover"))

    def _hover(self, i, cx, cy):
        state, why, s = self.rows[i]
        text = f"{_money(pricing.sale_total(s))}  {(s.get('date') or '')[:10]}\n" \
               f"{(s.get('title') or '')[:60]}" + (f"\n{why}" if why else "")
        c = self.chart
        c.delete("hover")
        anchor = "sw" if cx < c.winfo_width() * 0.6 else "se"
        tid = c.create_text(cx + (8 if anchor == "sw" else -8), cy - 8, text=text,
                            anchor=anchor, fill=ui.C["tip_fg"], font="CCSmall", tags=("hover",))
        x0, y0, x1, y1 = c.bbox(tid)
        bg = c.create_rectangle(x0 - 6, y0 - 4, x1 + 6, y1 + 4, fill=ui.C["tip_bg"],
                                outline="", tags=("hover",))
        c.tag_lower(bg, tid)

    def _select_sale(self, i):
        if self.sales.exists(str(i)):
            self.sales.selection_set(str(i))
            self.sales.see(str(i))

    # ------------------------------------------------------------ one sale

    def _sale_key_selected(self):
        sel = self.sales.selection()
        if not sel:
            return None
        return market.sale_key(self.rows[int(sel[0])][2])

    def _sale_url(self):
        sel = self.sales.selection()
        return self.urls.get(sel[0]) if sel else None

    def _clear_sale(self):
        self.sale_title.configure(text="Select a sale to see the listing.")
        self.sale_info.configure(text="")
        self.open_btn.configure(state="disabled")
        self.toggle_btn.configure(state="disabled", text="Leave out")
        self.pic.delete("all")

    def _picked_sale(self):
        sel = self.sales.selection()
        if not sel:
            return self._clear_sale()
        i = int(sel[0])
        state, why, s = self.rows[i]
        self.sale_title.configure(text=s.get("title", ""))
        info = [f"Total {_money(pricing.sale_total(s))} = {_money(s.get('price'))} + "
                f"{_money(s.get('postage') or 0)} postage"
                + (" (capped at £4 in the total)" if float(s.get("postage") or 0) >
                   pricing.POSTAGE_CAP else ""),
                f"Sold {(s.get('date') or 'date unknown')[:10]}",
                "Counted" + (" — put back after Comp Finder's high-postage flag"
                             if s.get("put_back") else "") if state == KEPT
                else f"Left out — {why}"]
        skip = {"title", "price", "postage", "date", "url", "excluded_reason", "why",
                "put_back"}
        for k, v in s.items():
            if k not in skip and v not in (None, "", [], {}) and not _image_url({k: v}):
                info.append(f"{k.replace('_', ' ').capitalize()}: {v}")
        self.sale_info.configure(text="\n".join(info))
        self.open_btn.configure(state="normal" if s.get("url") else "disabled")
        mine = why == market.LEFT_OUT
        self.toggle_btn.configure(state="normal" if state == KEPT or mine else "disabled",
                                  text="Put back" if mine else "Leave out")
        self._draw_chart(selected=str(i))
        self.pic.delete("all")
        url = _image_url(s)
        if url:
            self.pic.create_text(130, 90, text="loading picture…", fill=ui.C["faint"])
            key = market.sale_key(s)

            def load():
                try:
                    from .variant_tab import fetch_image
                    img = fetch_image(url)
                except Exception:                             # noqa: BLE001
                    img = None
                self.events.put(("pic", (key, img)))
            threading.Thread(target=load, daemon=True).start()
        else:
            self.pic.create_text(130, 90, fill=ui.C["faint"], justify="center",
                                 text="No picture came with this sale.\n"
                                      "Open it on eBay to see the card.")

    def _show_pic(self, img):
        from PIL import Image, ImageTk
        self.pic.delete("all")
        if img is None:
            self.pic.create_text(130, 90, text="picture unavailable", fill=ui.C["faint"])
            return
        w, h = max(self.pic.winfo_width(), 200), max(self.pic.winfo_height(), 150)
        k = min(w / img.size[0], h / img.size[1])
        img = img.resize((max(1, int(img.size[0] * k)), max(1, int(img.size[1] * k))),
                         Image.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        self.photos = [photo]
        self.pic.create_image(w / 2, h / 2, image=photo)

    def open_listing(self):
        url = self._sale_url()
        if url:
            webbrowser.open(url)

    def _copy_sale_title(self):
        sel = self.sales.selection()
        if sel:
            ui.copy_text(self, self.rows[int(sel[0])][2].get("title", ""))
            self.status.configure(text="Listing title copied.", foreground=ui.C["muted"])

    def toggle_sale(self):
        sel = self.sales.selection()
        if not sel or self.current is None:
            return
        state, why, s = self.rows[int(sel[0])]
        if state != KEPT and why != market.LEFT_OUT:
            return
        key = market.sale_key(s)
        out = self.left_out.setdefault(self.current, set())
        if key in out:
            out.discard(key)
        else:
            out.add(key)
        self.show()
        for i, (_, _, s2) in enumerate(self.rows):
            if market.sale_key(s2) == key and self.sales.exists(str(i)):
                self.sales.selection_set(str(i))
                self.sales.see(str(i))
                break

    def reset_whatif(self):
        if self.current is not None:
            self.left_out.pop(self.current, None)
            self.show()
