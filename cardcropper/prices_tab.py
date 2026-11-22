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


#: Thumbnails in the Pictures view, and how many listings' pictures are
#: fetched per card — each one without a picture in the evidence costs a
#: look at its eBay page.
THUMB = 112
MAX_PICTURES = 60
#: Longest side a listing photo is kept at. eBay pages often give 1600px;
#: anything resized on the window's own thread must already be small.
PICTURE_MAX = 400


def _prepared(img):
    """
    A listing photo made ready on the loading thread, so the window only has
    to show it: shrunk to PICTURE_MAX, with its grid thumbnail cut too.
    """
    from PIL import Image
    if img is None:
        return None
    img = img.convert("RGB")
    img.thumbnail((PICTURE_MAX, PICTURE_MAX), Image.LANCZOS)
    thumb = img.copy()
    thumb.thumbnail((THUMB, THUMB), Image.LANCZOS)
    img.thumb = thumb
    return img


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
        self.counted = {}            # card index -> set of sale keys counted by hand
        self.rows = []               # [(state, why, sale)] for the card on screen
        self.urls = {}
        self.photos = []
        self.history_ids = {}
        #: Listing pictures by sale key: a PIL image, or None for "none to be
        #: had". Shared by the Pictures view and the listing panel.
        self.pictures = {}
        self.thumbs = []
        from concurrent.futures import ThreadPoolExecutor
        self._pool = ThreadPoolExecutor(max_workers=2)
        self._asked = set()

        self._build_search()
        self._build_body()
        self._poll()
        self.after(200, self.refresh_history)
        self.after(250, self.refresh_watch)

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
        from . import folders
        saved = folders.load_settings().get("market_language", "English only")
        self.lang_var = tk.StringVar(value=saved if saved in market.LANGUAGES
                                     else "English only")
        lang = ttk.Combobox(bar, values=tuple(market.LANGUAGES), width=13, state="readonly",
                            textvariable=self.lang_var)
        lang.grid(row=0, column=4, padx=(0, 6))
        lang.bind("<<ComboboxSelected>>", lambda _e: self._language_changed())
        ui.tip(lang, "Which sales count, by language. English only leaves out the sales "
                     "Comp Finder marks as another language (foreign); All languages counts "
                     "them too — for Japanese and other non-English cards, or a card that "
                     "sells the same in any language. Switching re-prices what is on screen "
                     "from the sales already found: no new search.")
        self.search_btn = ttk.Button(bar, text="Search sold", style="Accent.TButton",
                                     command=self.lookup)
        self.search_btn.grid(row=0, column=5)
        ui.tip(self.search_btn, lambda: (
            "Find this card's recent UK sold listings through Comp Finder and price it "
            "under the house rules. (Enter)" if pricing.comp_finder_cookie() else
            "Needs your Comp Finder login first — More → Comp Finder sign-in…  "
            "Until then, eBay ↗ opens the sold search in your browser."))
        b = ttk.Button(bar, text="eBay ↗", command=self.open_ebay)
        b.grid(row=0, column=6, padx=(6, 0))
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
            ("Comp Finder sign-in…", self.set_login),
            ("Clear search history", self.clear_history),
        ], tip_text="Golden pricing test, priced batches, importing sales from Terapeak "
                    "or a sheet, and Comp Finder sign-in.")
        more.grid(row=0, column=7, padx=(6, 0))

        self.status = ttk.Label(self, style="Muted.TLabel", justify="left")
        self.status.grid(row=1, column=0, sticky="ew", pady=(6, 4))
        # Wrapped to the window, so a long message is read whole, not cut off.
        ui.wrap_to_width(self.status)
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
        watch_page = ttk.Frame(self.lists, padding=(0, 4, 0, 0))
        watch_page.columnconfigure(0, weight=1)
        watch_page.rowconfigure(1, weight=1)
        wbar = ttk.Frame(watch_page)
        wbar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.check_btn = ttk.Button(wbar, text="Check prices now", command=self.check_watch)
        self.check_btn.pack(side="left")
        ui.tip(self.check_btn, "Search every watched card again and show which prices "
                               "moved. Also happens by itself once a week when the app opens "
                               "(signed in to Comp Finder).")
        rm = ttk.Button(wbar, text="Stop watching", style="Ghost.TButton",
                        command=self.unwatch_selected)
        rm.pack(side="left", padx=(6, 0))
        ui.tip(rm, "Take the selected cards off the watch list.")
        wrap, self.watch_tv = ui.tree(watch_page, (
            ("title", "Card", 180, {"stretch": True}), ("cond", "Cond.", 44),
            ("price", "Price", 58, {"anchor": "e"}), ("change", "Change", 64, {"anchor": "e"}),
            ("checked", "Checked", 80)), height=12, selectmode="extended")
        wrap.grid(row=1, column=0, sticky="nsew")
        self.watch_tv.tag_configure("up", foreground=ui.C["ok"])
        self.watch_tv.tag_configure("down", foreground=ui.C["bad"])
        self.watch_tv.bind("<Double-1>", lambda _e: self._open_watched())
        ui.tip(self.watch_tv, "Cards you watch — ones you hold several of, or valuable ones. "
                              "Green rose, red fell by enough to act on. Double-click to look "
                              "at the card's latest search.")
        self.lists.add(watch_page, text="Watch list")
        ui.tip(self.lists, "Cards: what you just searched, opened or tested.  History: every "
                           "search kept with its listings — look again without searching.  "
                           "Watch list: cards re-checked weekly, with how far each moved.")
        pane.add(left, weight=1)

        # Right: one card in depth.
        right = ttk.Frame(pane, padding=(10, 0, 0, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)
        head = ttk.Frame(right)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        self.card_title = ttk.Label(head, style="Heading.TLabel", text="Search for a card",
                                    wraplength=620, justify="left")
        self.card_title.grid(row=0, column=0, sticky="w")
        self.chosen_lbl = ttk.Label(head, style="Ok.TLabel")
        self.chosen_lbl.grid(row=1, column=0, sticky="w")
        self.use_btn = ttk.Button(head, text="Use this price…", command=self.use_price,
                                  state="disabled")
        self.use_btn.grid(row=0, column=2, rowspan=2, sticky="e")
        self.watch_btn = ttk.Button(head, text="☆ Watch", style="Ghost.TButton",
                                    command=self.toggle_watch, state="disabled")
        self.watch_btn.grid(row=0, column=1, rowspan=2, sticky="e", padx=(0, 6))
        ui.tip(self.watch_btn, "Add this card to the watch list: checked again every week, "
                               "and flagged when its price moves.")
        self.trend_lbl = ttk.Label(head, style="Muted.TLabel")
        self.trend_lbl.grid(row=2, column=0, sticky="w")
        self.ref_lbl = ttk.Label(head, style="Muted.TLabel", justify="left")
        self.ref_lbl.grid(row=3, column=0, columnspan=3, sticky="ew")
        ui.wrap_to_width(self.ref_lbl)
        ui.tip(self.ref_lbl, "Reference prices for the card its title finds: Cardmarket's own "
                             "price guide (euros, downloaded daily) and TCGplayer (US). Not "
                             "eBay UK — a cross-check, worth most when there are few UK "
                             "sales. 'check' means the card has same-named versions in its "
                             "set; 'unsure' that its set was a close match.")
        self.refs = {}
        ui.tip(self.use_btn, "Keep this price for this card at this condition. Batch & price "
                             "uses it for that card for the next "
                             f"{market.DECISION_DAYS} days instead of working one out — "
                             "labelled 'your price' in the report.")

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
        ui.tip(self.reset_btn, "Undo every sale you left out or counted by hand.")
        self.why_lbl = ttk.Label(sales, style="Muted.TLabel", justify="left")
        self.why_lbl.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        ui.wrap_to_width(self.why_lbl)
        self.sales_views = ttk.Notebook(sales)
        self.sales_views.grid(row=1, column=0, sticky="nsew")
        wrap, self.sales = ui.tree(self.sales_views, (
            ("state", "Counted?", 96), ("why", "Why left out", 150),
            ("total", "Total", 62, {"anchor": "e"}), ("date", "Sold", 88),
            ("title", "Listing title", 300, {"stretch": True})), height=8)
        self.sales_views.add(wrap, text="List")
        gallery = ttk.Frame(self.sales_views)
        gallery.columnconfigure(0, weight=1)
        gallery.rowconfigure(0, weight=1)
        self.gallery = tk.Canvas(gallery, background=ui.C["surface"], highlightthickness=0)
        self.gallery.grid(row=0, column=0, sticky="nsew")
        gsb = ttk.Scrollbar(gallery, orient="vertical", command=self.gallery.yview)
        gsb.grid(row=0, column=1, sticky="ns")
        self.gallery.configure(yscrollcommand=gsb.set)
        self.gallery.bind("<Configure>", lambda _e: self._draw_gallery())
        self.gallery.bind("<MouseWheel>", lambda e: self.gallery.yview_scroll(
            -1 if e.delta > 0 else 1, "units"))
        self.gallery.bind("<Button-4>", lambda _e: self.gallery.yview_scroll(-1, "units"))
        self.gallery.bind("<Button-5>", lambda _e: self.gallery.yview_scroll(1, "units"))
        self.sales_views.add(gallery, text="Pictures")
        # Drawn when the tab is actually on screen: the tab-changed event
        # comes before the canvas is mapped.
        self.gallery.bind("<Map>", lambda _e: self.after_idle(self._draw_gallery))
        ui.tip(self.sales_views, "List: every sale with why it counted. Pictures: the same "
                                 "sales as photos — green border counted, grey left out. "
                                 "Click a picture to see that listing.")
        self.sales.tag_configure("out", foreground=ui.C["faint"])
        self.sales.tag_configure("kept", foreground=ui.C["ok"])
        self.sales.tag_configure("mine", foreground=ui.C["bad"])
        self.sales.tag_configure("yours", foreground=ui.C["accent"])
        self.sales.bind("<<TreeviewSelect>>", lambda _e: self._picked_sale())
        self.sales.bind("<Double-1>", lambda _e: self.open_listing())
        self.sales.bind("<Delete>", lambda _e: self.toggle_sale())
        ui.tip(self.sales, "Select a sale for its details. Double-click to open it on eBay; "
                           "Delete leaves a counted sale out, or counts a left-out one — "
                           "a what-if, to see the price either way.")
        lower.add(sales, weight=3)

        detail = ttk.Frame(lower, padding=(10, 0, 0, 0), width=300)
        detail.columnconfigure(0, weight=1)
        ttk.Label(detail, text="Listing", style="Heading.TLabel").grid(row=0, column=0,
                                                                       sticky="w")
        self.pic = tk.Canvas(detail, width=260, height=220, background=ui.C["sunken"],
                             highlightthickness=0, cursor="hand2")
        self.pic.bind("<Button-1>", lambda _e: self.open_listing())
        ui.tip(self.pic, "The listing's photo. Click to open the listing on eBay.")
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
        ui.tip(self.toggle_btn, "What if this sale had (or had not) counted? Leave out a "
                                "counted sale, or count one that was left out when you can "
                                "see it is the same card — the price is worked again. "
                                "Nothing on a listing changes.")
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
        # A few events per tick, not the whole queue: sixty pictures landing
        # at once must not hold the window for seconds before it can redraw.
        budget = 12
        try:
            while budget:
                budget -= 1
                kind, payload = self.events.get_nowait()
                if kind == "err":
                    self.search_btn.configure(state="normal")
                    self.check_btn.configure(state="normal", text="Check prices now")
                    if market.is_login_error(payload):
                        self._login_refused(payload)
                        continue
                    msg = f"Comp Finder: {payload}" if isinstance(
                        payload, pricing.CompFinderError) else \
                        f"{type(payload).__name__}: {payload}"
                    self.status.configure(text=msg, foreground=ui.C["bad"])
                elif kind == "pic":
                    self._got_pic(*payload)
                elif kind == "ref":
                    title, text = payload
                    self.refs[title] = text
                    self._show_ref(title)
                else:
                    self.search_btn.configure(state="normal")
                    priced, expected, evidence, note = payload[:4]
                    variants = payload[4] if len(payload) > 4 else {}
                    self._fill(priced, expected, evidence, variants)
                    self.status.configure(text=note, foreground=ui.C["muted"])
                    self.refresh_history()
                    self.refresh_watch()
                    self.check_btn.configure(state="normal", text="Check prices now")
        except queue.Empty:
            pass
        self.after(150 if budget else 15, self._poll)

    def _login_refused(self, exc):
        """
        Comp Finder answered, but would not take the saved login — almost
        always a session that has expired. Say so in words, and offer the fix
        and the way round it, rather than passing on its bare "Not signed in."
        """
        market.mark_login(rejected=True)
        self.status.configure(
            foreground=ui.C["bad"],
            text=f"Comp Finder did not accept your saved login (it said: “{exc}”). The "
                 "session has probably expired — sign in again (More → Comp Finder "
                 "sign-in…). eBay ↗ still opens the sold listings in your browser.")
        if messagebox.askyesno(
                "Comp Finder sign-in expired",
                f"Comp Finder said: “{exc}”\n\nYour saved login is no longer accepted — "
                "sessions expire after a while.\n\nPaste a new login now?\n\n"
                "(No: use eBay ↗ to see the sold listings in your browser instead.)"):
            self.set_login(retry=True)

    def _login_hint(self):
        state = market.login_state()
        if state == "rejected":
            self.status.configure(
                text="Comp Finder no longer accepts your saved login — sign in again "
                     "(More → Comp Finder sign-in…). eBay ↗ works without it.",
                foreground=ui.C["warn"])
        elif state == "none":
            self.status.configure(
                text="Not signed in to Comp Finder — searching needs it (More → Comp Finder "
                     "sign-in…). History, imported sales and eBay ↗ work without it.",
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

    def open_priced(self, folder=None):
        """A priced-<job>.json from Price & report: every card, every sale behind it."""
        path = filedialog.askopenfilename(title="priced-<job>.json from Price & report",
                                          initialdir=folder or None,
                                          filetypes=[("Priced batch", "priced-*.json"),
                                                     ("JSON", "*.json")])
        if not path:
            return
        self.open_priced_file(path)

    def open_priced_file(self, path, sku=None):
        """A priced batch; with `sku`, open on that card (the Terapeak queue does)."""
        from . import folders
        folders.remember("priced", path)
        self._want_sku = sku
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
                                             "account.\n\nSign in now? (Or press No and use "
                                             "eBay ↗ to search in your browser.)"):
                self.set_login()
            return
        game, cond, language = self._game(), self.cond_var.get(), self._lang()

        def run():
            ev = pricing.batch_comps([{"id": "lookup", "title": title}])
            r = ev.get("lookup") or {}
            p = pricing.price_card("lookup", title, r.get("sold"), game, cond,
                                   status=r.get("status", "ok"))
            shown = market.reprice(p, set(), language=language) if language == "all" else p
            market.save_search(title, r, shown, game, cond, language=language)
            return [p], {}, ev, f"Searched “{title}” ({game}, {cond}, " \
                                f"{'all languages' if language == 'all' else 'English only'}) — " \
                                f"{len(p.kept) + len(p.excluded)} sale(s) found, " \
                                f"{shown.n_sales} counted"
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

    def set_login(self, retry=False):
        """
        Sign in to Comp Finder with its email and password — the app then keeps
        the session fresh by itself (see compfinder_auth). Pasting a cookie
        from the browser is still there underneath, for when sign-in cannot
        be used.
        """
        from . import compfinder_auth, folders
        top = tk.Toplevel(self)
        top.title("Comp Finder sign-in")
        top.transient(self.winfo_toplevel())
        top.resizable(False, False)
        f = ttk.Frame(top, padding=16)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text="Sign in to Comp Finder", style="Heading.TLabel")\
            .grid(row=0, column=0, columnspan=2, sticky="w")
        who = compfinder_auth.signed_in_as()
        state = market.login_state()
        ttk.Label(f, style="Muted.TLabel", wraplength=440, justify="left", text=(
            (f"Signed in as {who}." + (" Comp Finder has stopped accepting this session — "
                                       "sign in again." if state == "rejected" else "")
             if who else "The same email and password as on the Comp Finder website.")
            + " Your password is sent to Comp Finder's sign-in service once and is never "
              "saved; the app keeps the session fresh from then on."))\
            .grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 10))
        email = tk.StringVar(value=who or folders.load_settings().get("comp_finder_email", ""))
        password = tk.StringVar()
        ttk.Label(f, text="Email").grid(row=2, column=0, sticky="w", pady=2)
        e1 = ttk.Entry(f, textvariable=email, width=36)
        e1.grid(row=2, column=1, sticky="ew", pady=2)
        ttk.Label(f, text="Password").grid(row=3, column=0, sticky="w", pady=2)
        e2 = ttk.Entry(f, textvariable=password, show="•", width=36)
        e2.grid(row=3, column=1, sticky="ew", pady=2)
        msg = ttk.Label(f, wraplength=440, justify="left")
        msg.grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))

        def done(ok, text):
            if not top.winfo_exists():
                return
            sign_btn.configure(state="normal", text="Sign in")
            if not ok:
                msg.configure(text=text, foreground=ui.C["bad"])
                return
            market.mark_login(rejected=False)
            folders.save_settings(comp_finder_email=email.get().strip())
            top.destroy()
            self.status.configure(text=f"Signed in to Comp Finder as {text}.",
                                  foreground=ui.C["ok"])
            if retry and self.title_var.get().strip():
                self.lookup()

        def sign_in():
            addr, pw = email.get(), password.get()
            password.set("")
            sign_btn.configure(state="disabled", text="Signing in…")
            msg.configure(text="")

            def run():
                try:
                    result = (True, compfinder_auth.sign_in(addr, pw))
                except compfinder_auth.SignInError as exc:
                    text = str(exc)
                    if "invalid" in text.lower() and "credential" in text.lower():
                        text = "That email and password were not accepted."
                    result = (False, f"Could not sign in: {text}")
                answer.append(result)
            answer = []
            threading.Thread(target=run, daemon=True).start()

            # Tk is only touched from the window's own thread: the sign-in
            # thread leaves its answer here, and this picks it up.
            def wait():
                if answer:
                    done(*answer[0])
                elif top.winfo_exists():
                    top.after(100, wait)
            top.after(100, wait)

        def sign_out():
            compfinder_auth.sign_out()
            market.mark_login(rejected=False)
            top.destroy()
            self.status.configure(text="Signed out of Comp Finder.", foreground=ui.C["muted"])

        row = ttk.Frame(f)
        row.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        sign_btn = ttk.Button(row, text="Sign in", style="Accent.TButton", command=sign_in)
        sign_btn.pack(side="right")
        ttk.Button(row, text="Cancel", style="Ghost.TButton", command=top.destroy)\
            .pack(side="right", padx=6)
        if who or pricing.comp_finder_cookie():
            out = ttk.Button(row, text="Sign out", command=sign_out)
            out.pack(side="left")
            ui.tip(out, "Forget the saved session and cookie on this computer.")

        # The old way, kept for when sign-in cannot be used: a cookie copied
        # out of the browser's developer tools.
        adv = ttk.Frame(f)
        shown = tk.BooleanVar(value=False)

        def toggle():
            if shown.get():
                adv.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(8, 0))
            else:
                adv.grid_remove()
        ttk.Checkbutton(f, text="Paste a cookie from the browser instead", variable=shown,
                        command=toggle).grid(row=6, column=0, columnspan=2, sticky="w",
                                             pady=(14, 0))
        ttk.Label(adv, style="Muted.TLabel", wraplength=440, justify="left", text=(
            "Signed in to Comp Finder in your browser: F12 → Network, click any request to "
            "comp-finder, copy the whole Cookie request header and paste it here. A pasted "
            "cookie is not refreshed — it stops working when that session expires.")).pack(
            anchor="w")
        box = tk.Text(adv, height=4, width=60, wrap="char")
        box.pack(fill="x", pady=(4, 4))
        if not who:
            box.insert("1.0", pricing.comp_finder_cookie())

        def save_cookie():
            text = " ".join(box.get("1.0", "end").split())
            compfinder_auth.forget_session()
            os.makedirs(os.path.dirname(pricing.cookie_path()), exist_ok=True)
            with open(pricing.cookie_path(), "w", encoding="utf-8") as fh:
                fh.write(text)
            market.mark_login(rejected=False)
            top.destroy()
            self.status.configure(text="Comp Finder cookie saved." if text else
                                  "Comp Finder cookie cleared.", foreground=ui.C["ok"])
            if retry and text and self.title_var.get().strip():
                self.lookup()
        ttk.Button(adv, text="Save cookie", command=save_cookie).pack(anchor="e")

        top.bind("<Return>", lambda _e: sign_in() if not shown.get() else None)
        top.bind("<Escape>", lambda _e: top.destroy())
        (e2 if email.get() else e1).focus_set()

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
        for label, key in market.LANGUAGES.items():
            if key == (row.get("language") or "english"):
                self.lang_var.set(label)
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
        self.counted = {}
        self.cards.delete(*self.cards.get_children())
        for i, p in enumerate(priced):
            exp, ok = expected.get(p.sku, (None, True))
            self.cards.insert("", "end", iid=str(i), tags=() if ok else ("fail",),
                              values=self._card_values(i, exp))
        ui.restripe(self.cards)
        if not keep_tab:
            self.lists.select(0)
        if priced:
            want = getattr(self, "_want_sku", None)
            self._want_sku = None
            first = next((i for i, p in enumerate(priced) if want and p.sku == want), 0)
            self.cards.selection_set(str(first))
            self.cards.see(str(first))
            self.show(first)

    def _picked_card(self):
        sel = self.cards.selection()
        if sel:
            self.show(int(sel[0]))

    def _lang(self):
        return market.LANGUAGES.get(self.lang_var.get(), "english")

    def _language_changed(self):
        from . import folders
        folders.save_settings(market_language=self.lang_var.get())
        for i in range(len(self.priced)):
            exp, _ = self.expected.get(self.priced[i].sku, (None, True))
            if self.cards.exists(str(i)):
                self.cards.item(str(i), values=self._card_values(i, exp))
        self.show()

    def _card_values(self, i, expected=None):
        p = self._view(i)
        price = _money(p.suggested) if p.suggested is not None else "no price"
        return (p.title, price, _money(expected), p.n_sales, p.decision)

    def _whatif(self, i=None):
        i = self.current if i is None else i
        return bool(self.left_out.get(i) or self.counted.get(i))

    def _view(self, i, by_hand=True):
        """
        Card `i` as it should be shown: in the chosen language setting, and —
        with `by_hand` — with the sales left out or counted by hand. The
        evidence is Comp Finder's either way; only what counts changes.
        """
        p = self.priced[i]
        language = self._lang()
        if language == "english" and not (by_hand and self._whatif(i)):
            return p
        return market.reprice(p, self.left_out.get(i, set()) if by_hand else set(),
                              self.variant_text.get(p.sku, ""),
                              counted=self.counted.get(i, set()) if by_hand else (),
                              language=language)

    def _shown(self):
        """The card on screen, worked again for language and anything done by hand."""
        return self._view(self.current)

    def show(self, index=None):
        if index is not None:
            self.current = index
        if self.current is None or self.current >= len(self.priced):
            return
        p = self._shown()
        base = self._view(self.current, by_hand=False)
        whatif = self._whatif()
        if self.cards.exists(str(self.current)):
            exp, _ = self.expected.get(self.priced[self.current].sku, (None, True))
            self.cards.item(str(self.current), values=self._card_values(self.current, exp))
        self.card_title.configure(text=p.title + (f"   ·   {p.condition}" if p.condition else ""))
        self.use_btn.configure(state="normal")
        self._show_chosen(p)
        try:
            points = market.trend(p.title, p.condition)
            watching = market.is_watched(p.title)
        except Exception:                                     # noqa: BLE001
            points, watching = [], False
        text = market.describe_trend(points)
        self.trend_lbl.configure(text=f"Trend: {text}" if text else "")
        self._want_ref(p.title)
        self.watch_btn.configure(state="normal", text="★ Watching" if watching else "☆ Watch")
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
            lines.insert(0, f"WHAT-IF: {len(self.left_out.get(self.current, ()))} sale(s) "
                            f"left out and {len(self.counted.get(self.current, ()))} counted "
                            "by hand. Nothing on a listing has changed.")
        self.working.configure(state="normal")
        self.working.delete("1.0", "end")
        self.working.insert("end", "\n".join(lines))
        self.working.configure(state="disabled")
        self.rows = market.all_sales(p)
        why = market.reasons(p)
        if why:
            note = f"Left out {len(p.excluded)}: " + ", ".join(
                f"{n} {r.replace('_', ' ')}" for r, n in why) + "."
            if not p.kept:
                note += (" None counted — check the reasons. Comp Finder matches by the "
                         "listing-title form (name, number/total, set, and Japanese if it "
                         "is): try searching that way, or select a sale and Count it anyway.")
            self.why_lbl.configure(text=note, foreground=ui.C["warn"] if not p.kept
                                   else ui.C["muted"])
        else:
            self.why_lbl.configure(text="")
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
            tag = ("yours" if s.get("counted_by_hand") else "kept") if state == KEPT \
                else ("mine" if mine else "out")
            iid = self.sales.insert("", "end", iid=str(i), tags=(tag,), values=(
                ("by you" if s.get("counted_by_hand") else
                 "counted" if state == KEPT else state),
                (f"{s['excluded_reason'].replace('_', ' ')}, counted"
                 if state == KEPT and s.get("put_back") and s.get("excluded_reason")
                 and not s.get("counted_by_hand") else why.replace("_", " ")),
                _money(pricing.sale_total(s)), (s.get("date") or "")[:10], s.get("title", "")))
            if s.get("url"):
                self.urls[iid] = s["url"]
        self._clear_sale()
        self._draw_gallery()

    # ------------------------------------------------------------ chart

    def _draw_chart(self, selected=None):
        c = self.chart
        c.delete("all")
        self._hovering = None
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
        everything = sorted(tot for _, _, tot, _, _ in pts)
        # With a few sales counted, scale to the bulk of all of them instead:
        # one counted sale would pin every other dot to the top.
        if len(kept) < 3:
            middle = everything[len(everything) // 2] or 1
            kept = [t for t in everything if t <= 3 * middle] or everything
        top = max(kept) * 1.25 or 1
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
            c.tag_bind(dot, "<Leave>", lambda _e: self._unhover())

    def _hover(self, i, cx, cy):
        """
        A sale's details beside its dot.

        The bubble must never come under the pointer. It used to overlap the
        dot by a few pixels: pointing at that corner put the pointer over the
        bubble, which counts as leaving the dot, which removed the bubble,
        which put the pointer back on the dot — drawn and removed for ever,
        and Windows called the app Not Responding (freeze report, 22 Nov).
        So it sits well clear of the dot, takes no pointer events (disabled),
        and a dot already showing its bubble is left alone.
        """
        if getattr(self, "_hovering", None) == i:
            return
        self._hovering = i
        state, why, s = self.rows[i]
        text = f"{_money(pricing.sale_total(s))}  {(s.get('date') or '')[:10]}\n" \
               f"{(s.get('title') or '')[:60]}" + (f"\n{why}" if why else "")
        c = self.chart
        c.delete("hover")
        anchor = "sw" if cx < c.winfo_width() * 0.6 else "se"
        tid = c.create_text(cx + (16 if anchor == "sw" else -16), cy - 16, text=text,
                            anchor=anchor, fill=ui.C["tip_fg"], font="CCSmall",
                            tags=("hover",), state="disabled")
        x0, y0, x1, y1 = c.bbox(tid)
        bg = c.create_rectangle(x0 - 6, y0 - 4, x1 + 6, y1 + 4, fill=ui.C["tip_bg"],
                                outline="", tags=("hover",), state="disabled")
        c.tag_lower(bg, tid)

    def _unhover(self):
        self._hovering = None
        self.chart.delete("hover")

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
                (f"Counted by you — Comp Finder had left it out "
                 f"({(s.get('excluded_reason') or 'no reason').replace('_', ' ')})"
                 if s.get("counted_by_hand") else
                 "Counted" + (f" — Comp Finder flagged it "
                              f"{(s.get('excluded_reason') or '').replace('_', ' ')}, which "
                              "counts anyway (postage is capped; language per the setting)"
                              if s.get("put_back") else "")) if state == KEPT
                else f"Left out — {why.replace('_', ' ')}"]
        skip = {"title", "price", "postage", "date", "url", "excluded_reason", "why",
                "put_back", "counted_by_hand"}
        for k, v in s.items():
            if k not in skip and v not in (None, "", [], {}) and not market.image_in({k: v}):
                info.append(f"{k.replace('_', ' ').capitalize()}: {v}")
        self.sale_info.configure(text="\n".join(info))
        self.open_btn.configure(state="normal" if s.get("url") else "disabled")
        mine = why == market.LEFT_OUT
        self.toggle_btn.configure(
            state="normal",
            text="Put back" if mine else "Uncount" if s.get("counted_by_hand") else
            "Leave out" if state == KEPT else "Count it anyway")
        self._draw_chart(selected=str(i))
        self._show_pic(None, waiting=True)
        self._want(s)
        self._mark_gallery(str(i))

    # ------------------------------------------------------------ pictures

    def _want(self, sale, ebay_page=True):
        """
        Get a sale's picture in the background: from the evidence when it
        carries one, else from the listing's eBay page (remembered in
        market.sqlite). Arrives as a ("pic", (key, image)) event.
        """
        key = market.sale_key(sale)
        if key in self.pictures:
            self.events.put(("pic", (key, self.pictures[key])))
            return
        if key in self._asked:
            return
        self._asked.add(key)

        if not ebay_page and not market.image_in(sale):
            # The Pictures grid asks eBay for nothing: a page per thumbnail was
            # dozens of requests at once. Selecting a sale still looks it up.
            self._asked.discard(key)
            return

        def load():
            img = None
            try:
                url = market.image_in(sale) or market.listing_image(sale.get("url", ""))
                if url:
                    from .variant_tab import fetch_image
                    img = _prepared(fetch_image(market.sized(url, 500)))
            except Exception:                                 # noqa: BLE001
                img = None
            self.events.put(("pic", (key, img)))
        self._pool.submit(load)

    def _got_pic(self, key, img):
        self.pictures[key] = img
        if key == self._sale_key_selected():
            self._show_pic(img)
        self._draw_thumb(key)

    def _show_pic(self, img, waiting=False):
        from PIL import Image, ImageTk
        self.pic.delete("all")
        w, h = max(self.pic.winfo_width(), 200), max(self.pic.winfo_height(), 150)
        if img is None:
            sel = self.sales.selection()
            url = self.rows[int(sel[0])][2].get("url") if sel else ""
            self.pic.create_text(w / 2, h / 2, fill=ui.C["faint"], justify="center", text=(
                "loading picture…" if waiting else
                "No picture could be found for this sale.\nClick to open it on eBay."
                if url else "No picture, and no link, came with this sale."))
            return
        k = min(w / img.size[0], h / img.size[1], 1.0)
        if k < 1:
            # Already no bigger than PICTURE_MAX (see _prepared), so this is
            # a small, quick resize.
            img = img.resize((max(1, int(img.size[0] * k)), max(1, int(img.size[1] * k))),
                             Image.BILINEAR)
        photo = ImageTk.PhotoImage(img)
        self.photos = [photo]
        self.pic.create_image(w / 2, h / 2, image=photo)

    def _gallery_rows(self):
        return [(i, st, why, s) for i, (st, why, s) in enumerate(self.rows)
                if st == KEPT or self.show_out.get()][:MAX_PICTURES]

    def _draw_gallery(self):
        """The Pictures view: a grid of the listings' photos, loaded as they arrive."""
        g = self.gallery
        if not g.winfo_ismapped():
            return
        g.delete("all")
        self.thumbs = []
        self._cells = {}
        rows = self._gallery_rows()
        if not rows:
            g.create_text(max(g.winfo_width(), 200) / 2, 60, fill=ui.C["faint"],
                          text="Search for a card to see its listings' pictures here")
            return
        pad, cap = 10, 34
        cols = max(1, (max(g.winfo_width(), 200) - pad) // (THUMB + pad))
        for n, (i, state, why, s) in enumerate(rows):
            x = pad + (n % cols) * (THUMB + pad)
            y = pad + (n // cols) * (THUMB + cap + pad)
            colour = ui.C["ok"] if state == KEPT else (
                ui.C["bad"] if why == market.LEFT_OUT else ui.C["border"])
            tag = f"cell{i}"
            g.create_rectangle(x - 2, y - 2, x + THUMB + 2, y + THUMB + 2, outline=colour,
                               width=2, tags=(tag, f"frame{i}"))
            g.create_rectangle(x, y, x + THUMB, y + THUMB, fill=ui.C["sunken"], outline="",
                               tags=(tag,))
            g.create_text(x + 4, y + THUMB + 4, anchor="nw", tags=(tag,), font="CCSmall",
                          fill=ui.C["text"] if state == KEPT else ui.C["faint"],
                          text=f"{_money(pricing.sale_total(s))}  "
                               f"{(s.get('date') or '')[:10]}\n"
                               + ("counted" if state == KEPT else why.replace("_", " "))[:24])
            g.tag_bind(tag, "<Button-1>", lambda _e, i=i: self._select_sale(i))
            g.tag_bind(tag, "<Double-1>", lambda _e: self.open_listing())
            self._cells[market.sale_key(s)] = (i, x, y)
            self._draw_thumb(market.sale_key(s))
            self._want(s, ebay_page=False)
        last = len(rows) - 1
        g.configure(scrollregion=(0, 0, cols * (THUMB + pad) + pad,
                                  pad + (last // cols + 1) * (THUMB + cap + pad)))
        sel = self.sales.selection()
        if sel:
            self._mark_gallery(sel[0])

    def _draw_thumb(self, key):
        from PIL import ImageTk
        cell = getattr(self, "_cells", {}).get(key)
        if cell is None or not self.gallery.winfo_ismapped():
            return
        i, x, y = cell
        img = self.pictures.get(key, "pending")
        g = self.gallery
        g.delete(f"img{i}")
        if img == "pending":
            g.create_text(x + THUMB / 2, y + THUMB / 2, text="click for\nthe photo",
                          fill=ui.C["faint"], font="CCSmall", justify="center",
                          tags=(f"img{i}", f"cell{i}"))
            return
        if img is None:
            g.create_text(x + THUMB / 2, y + THUMB / 2, text="no picture", fill=ui.C["faint"],
                          font="CCSmall", tags=(f"img{i}", f"cell{i}"))
            return
        photo = ImageTk.PhotoImage(img.thumb)
        self.thumbs.append(photo)
        g.create_image(x + THUMB / 2, y + THUMB / 2, image=photo, tags=(f"img{i}", f"cell{i}"))

    def _mark_gallery(self, iid):
        g = self.gallery
        g.delete("chosen")
        for key, (i, x, y) in getattr(self, "_cells", {}).items():
            if str(i) == iid:
                g.create_rectangle(x - 5, y - 5, x + THUMB + 5, y + THUMB + 5,
                                   outline=ui.C["accent"], width=3, tags=("chosen",))

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
        key = market.sale_key(s)
        out = self.left_out.setdefault(self.current, set())
        counted = self.counted.setdefault(self.current, set())
        if key in out:
            out.discard(key)                    # put back
        elif key in counted:
            counted.discard(key)                # uncount
        elif state == KEPT:
            out.add(key)                        # leave out
        else:
            counted.add(key)                    # count it anyway
        self.show()
        for i, (_, _, s2) in enumerate(self.rows):
            if market.sale_key(s2) == key and self.sales.exists(str(i)):
                self.sales.selection_set(str(i))
                self.sales.see(str(i))
                break

    # ------------------------------------------------------------ watch list

    def toggle_watch(self):
        if self.current is None:
            return
        p = self._shown()
        if market.is_watched(p.title):
            market.unwatch(p.title)
        else:
            market.watch(p.title, p.game, p.condition, self._lang(), p.suggested)
        self.show()
        self.refresh_watch()

    def unwatch_selected(self):
        for iid in self.watch_tv.selection():
            market.unwatch(self.watch_tv.set(iid, "title"))
        self.refresh_watch()

    def refresh_watch(self, moved=None):
        try:
            rows = market.watched()
        except Exception:                                     # noqa: BLE001
            return
        self.watch_tv.delete(*self.watch_tv.get_children())
        for r in rows:
            old, new = r["previous_price"], r["last_price"]
            change, tag = "", ()
            if old and new is not None and old != new:
                change = f"{(new - old) / old * 100:+.0f}%"
                if market.moved(old, new):
                    tag = ("up",) if new > old else ("down",)
            self.watch_tv.insert("", "end", tags=tag, values=(
                r["title"], r["condition"] or "NM", _money(new) or "—", change,
                market.ago(r["last_checked"]) if r["last_checked"] else "never"))
        ui.restripe(self.watch_tv)
        n = len(rows)
        self.lists.tab(2, text=f"Watch list ({n})" if n else "Watch list")

    def check_watch(self, quiet=False):
        if not market.watched():
            if not quiet:
                self.status.configure(text="Nothing on the watch list yet — ☆ Watch a card "
                                           "first.", foreground=ui.C["muted"])
            return
        if not pricing.comp_finder_cookie():
            if not quiet:
                self._login_hint()
            return
        if self.worker and self.worker.is_alive():
            return
        self.check_btn.configure(state="disabled", text="Checking…")

        def run():
            res = market.check_watch()
            priced = [p for _, p, _ in res]
            moved = [(r, p) for r, p, m in res if m]
            note = (f"Checked {len(res)} watched card(s): {len(moved)} moved — " +
                    ", ".join(f"{r['title'][:30]} {_money(r['last_price'])}→"
                              f"{_money(p.suggested) or 'none'}" for r, p in moved[:4])
                    if moved else f"Checked {len(res)} watched card(s): no price moved enough "
                                  "to act on.")
            return priced, {}, {}, note
        self._background("Checking the watch list…", run)

    def auto_check_watch(self):
        """Once a week, on opening the app: check the watch list quietly."""
        try:
            if market.watch_due() and pricing.comp_finder_cookie():
                self.check_watch(quiet=True)
        except Exception:                                     # noqa: BLE001
            pass

    def _open_watched(self):
        sel = self.watch_tv.selection()
        if not sel:
            return
        title = self.watch_tv.set(sel[0], "title")
        for iid, sid in self.history_ids.items():
            if self.hist.set(iid, "title") == title:
                self.lists.select(1)
                self.hist.selection_set(iid)
                self.hist.see(iid)
                return
        self.title_var.set(title)
        self.status.configure(text="No saved search for that card yet — Search sold to get "
                                   "one.", foreground=ui.C["muted"])

    # ------------------------------------------------------------ reference prices

    def _want_ref(self, title):
        """
        Reference prices for the card, fetched once per title in the background:
        Cardmarket's own price guide (downloaded daily — cardmarket.py), and
        TCGplayer from the catalogue.
        """
        if title in self.refs:
            self._show_ref(title)
            return
        self.refs[title] = None
        self.ref_lbl.configure(text="")

        def load():
            lines = []
            try:
                from . import cardmarket, catalog
                res = catalog.search(market.clean_query(title), limit=3)
                if res:
                    c = res[0]
                    try:
                        cm = cardmarket.describe(cardmarket.price_for(
                            c, printing=pricing.printing_of(title)))
                    except Exception:                         # noqa: BLE001
                        cm = ""
                    if cm:
                        lines.append(cm)
                    us = (c.prices or {}).get("TCGplayer market (USD)")
                    if isinstance(us, dict) and us:
                        lines.append("TCGplayer market: " + ", ".join(
                            f"{k} ${float(v):.2f}" for k, v in list(us.items())[:2] if v))
                    if lines:
                        lines[0] = f"{c.name} {c.number}, {c.set_name} — " + lines[0]
            except Exception:                                 # noqa: BLE001
                lines = []
            self.events.put(("ref", (title, "\n".join(lines))))
        self._pool.submit(load)

    def _show_ref(self, title):
        if self.current is not None and self.priced[self.current].title == title:
            self.ref_lbl.configure(text=self.refs.get(title) or "")

    def _show_chosen(self, p):
        try:
            d = market.decision_for(p.title, p.condition)
        except Exception:                                     # noqa: BLE001
            d = None
        self.chosen_lbl.configure(
            text=f"Your price: {_money(d['price'])}, chosen {d['when']} — Batch & price "
                 "uses it" if d else "")

    def use_price(self):
        """Keep a price for the card on screen — this one, or one typed in."""
        if self.current is None:
            return
        p = self._shown()
        from tkinter import simpledialog
        current = market.decision_for(p.title, p.condition)
        start = current["price"] if current else p.suggested
        text = simpledialog.askstring(
            "Use this price",
            f"{p.title}  ·  {p.condition}\n\nPrice to use (£). Batch & price will use it for "
            f"this card for {market.DECISION_DAYS} days.\nLeave empty to stop using a chosen "
            "price.", initialvalue=f"{start:.2f}" if start else "", parent=self)
        if text is None:
            return
        text = text.strip().lstrip("£")
        if not text:
            market.undecide(p.title, p.condition)
            self.status.configure(text="No chosen price for this card now.",
                                  foreground=ui.C["muted"])
        else:
            try:
                price = round(float(text), 2)
                if price <= 0:
                    raise ValueError
            except ValueError:
                self.status.configure(text=f"“{text}” is not a price.", foreground=ui.C["bad"])
                return
            note = "what-if" if self._whatif() else ""
            if self._lang() == "all":
                note = (note + ", " if note else "") + "all languages"
            market.decide(p.title, p.condition, price, p, note=note)
            self.status.configure(text=f"Using {_money(price)} for {p.title} ({p.condition}).",
                                  foreground=ui.C["ok"])
        self._show_chosen(p)

    def reset_whatif(self):
        if self.current is not None:
            self.left_out.pop(self.current, None)
            self.counted.pop(self.current, None)
            self.show()
