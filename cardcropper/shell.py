"""
The window around the pages: a sidebar in the order the work is done, a
header on every page saying what it is for, a guide that can be opened or
put away, a menu bar, keyboard shortcuts and a status bar.

Ten pages in one row of tabs read as ten equal choices. They are not: the
work runs Scan → Grade → List & price, and most days use three or four
pages. So the sidebar groups them by stage, Home shows the whole run and
where things stand, and each page says in one line what it is for before a
person has to read any of it.

The pages themselves are unchanged — each is still a frame built into the
`master` it is given — so a page added later needs a line in
`guide.PAGES` / `guide.GROUPS` and a builder, and nothing else.
"""

import os
import sys
import tkinter as tk
from tkinter import ttk

from . import folders, guide, ui


class Shell:
    def __init__(self, root, settings, app_name="CardCropper"):
        self.root = root
        self.settings = settings
        self.app_name = app_name
        self.pages = {}          # key -> page container (header + body)
        self.bodies = {}         # key -> body frame the page builds into
        self.objects = {}        # key -> the page object
        self.nav = {}            # key -> sidebar button
        self.guides = {}         # key -> (guide frame, toggle button)
        self.current = None
        #: Whether each page's guide is open, as last left. Pages that are one
        #: big workspace start with theirs shut; the rest start open.
        self.guide_state = dict(settings.get("guides") or {})
        self.sidebar_on = not settings.get("sidebar_hidden", False)
        self.status_parts = {}

        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)
        self._build_sidebar()
        self.content = ttk.Frame(root)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)
        self._build_status()
        self._build_menu()
        self._bind_keys()

    # ------------------------------------------------------------ structure

    def _order(self):
        return [k for _, keys in guide.GROUPS for k in keys]

    def _build_sidebar(self):
        self.sidebar = ttk.Frame(self.root, style="Nav.TFrame", width=196)
        self.sidebar.grid(row=0, column=0, sticky="nsw")
        self.sidebar.grid_propagate(False)
        self.sidebar.columnconfigure(0, weight=1)
        ttk.Label(self.sidebar, text=self.app_name, style="Brand.TLabel")\
            .grid(row=0, column=0, sticky="ew")
        r = 1
        for group, keys in guide.GROUPS:
            if group:
                ttk.Label(self.sidebar, text=group.upper(), style="NavGroup.TLabel")\
                    .grid(row=r, column=0, sticky="ew")
                r += 1
            for key in keys:
                info = guide.PAGES[key]
                b = ttk.Button(self.sidebar, text=f"{info['icon']}   {info['label']}",
                               style="Nav.TButton", command=lambda k=key: self.show(k),
                               takefocus=False)
                b.grid(row=r, column=0, sticky="ew")
                ui.tip(b, info["lead"] + self._shortcut_hint(key))
                self.nav[key] = b
                r += 1
        self.sidebar.rowconfigure(r, weight=1)
        if not self.sidebar_on:
            self.sidebar.grid_remove()

    def _shortcut_hint(self, key):
        order = [k for k in self._order() if k != "home"]
        if key == "home":
            return "   (Ctrl+0)"
        if key in order and order.index(key) < 9:
            return f"   (Ctrl+{order.index(key) + 1})"
        return ""

    def add_page(self, key, build):
        """
        Make the page `key`: a header from `guide.PAGES`, and a body that
        `build(body)` fills. Returns what `build` returned.
        """
        info = guide.PAGES[key]
        page = ttk.Frame(self.content)
        page.grid(row=0, column=0, sticky="nsew")
        page.columnconfigure(0, weight=1)
        page.rowconfigure(2, weight=1)

        if key != "home":
            head = ttk.Frame(page, style="Header.TFrame", padding=(18, 12, 18, 10))
            head.grid(row=0, column=0, sticky="ew")
            head.columnconfigure(0, weight=1)
            ttk.Label(head, text=info["title"], style="Title.TLabel")\
                .grid(row=0, column=0, sticky="w")
            ttk.Label(head, text=info["lead"], style="Lead.TLabel")\
                .grid(row=1, column=0, sticky="w", pady=(2, 0))
            if info.get("guide"):
                btn = ttk.Button(head, style="Ghost.TButton",
                                 command=lambda k=key: self.toggle_guide(k))
                btn.grid(row=0, column=1, rowspan=2, sticky="e")
                ui.tip(btn, "How to use this page, step by step (F1).")
                g = ttk.Frame(page, style="Help.TFrame", padding=(18, 10, 18, 10))
                g.columnconfigure(0, weight=1)
                lbl = ttk.Label(g, text=info["guide"], style="Help.TLabel", justify="left")
                lbl.grid(row=0, column=0, sticky="w")
                self.guides[key] = (g, btn)
                self._set_guide(key, self.guide_state.get(
                    key, key not in guide.GUIDE_CLOSED))
            ttk.Separator(page, orient="horizontal").grid(row=1, column=0, sticky="sew")

        body = ttk.Frame(page)
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.pages[key] = page
        self.bodies[key] = body
        page.grid_remove()
        obj = build(body)
        self.objects[key] = obj
        tips = guide.TIPS.get(key)
        if tips:
            ui.attach_tips(body, tips, primary=guide.PRIMARY.get(key, ()),
                           subtle=guide.SUBTLE.get(key, ()), styles=guide.STYLES.get(key))
        return obj

    # ------------------------------------------------------------ navigation

    def show(self, key):
        if key not in self.pages:
            return
        if self.current and self.current in self.pages:
            self.pages[self.current].grid_remove()
            self.nav[self.current].configure(style="Nav.TButton")
        self.pages[key].grid()
        self.nav[key].configure(style="NavOn.TButton")
        self.current = key
        self.root.title(f"{guide.PAGES[key]['label']} — {self.app_name}"
                        if key != "home" else self.app_name)
        folders.save_settings(tab=key)
        refresh = getattr(self.objects.get(key), "on_show", None)
        if refresh:
            refresh()
        self.refresh_status()

    def _set_guide(self, key, on):
        g, btn = self.guides[key]
        if on:
            g.grid(row=1, column=0, sticky="ew")
            btn.configure(text="Hide guide ▴")
        else:
            g.grid_remove()
            btn.configure(text="Guide ▾")

    def toggle_guide(self, key=None):
        key = key or self.current
        if key not in self.guides:
            return
        on = not self.guides[key][0].winfo_ismapped()
        self._set_guide(key, on)
        self.guide_state[key] = on
        folders.save_settings(guides=self.guide_state)

    def toggle_sidebar(self):
        self.sidebar_on = not self.sidebar_on
        if self.sidebar_on:
            self.sidebar.grid()
        else:
            self.sidebar.grid_remove()
        folders.save_settings(sidebar_hidden=not self.sidebar_on)

    # ------------------------------------------------------------ menu & keys

    def _build_menu(self):
        bar = tk.Menu(self.root)
        file = tk.Menu(bar, tearoff=False)
        file.add_command(label="Open settings folder", accelerator="Ctrl+,",
                         command=self.open_settings)
        file.add_separator()
        file.add_command(label="Exit", command=self.root.destroy)
        bar.add_cascade(label="File", menu=file)

        go = tk.Menu(bar, tearoff=False)
        for group, keys in guide.GROUPS:
            if group:
                go.add_separator()
            for key in keys:
                hint = self._shortcut_hint(key).strip(" ()")
                go.add_command(label=guide.PAGES[key]["label"], accelerator=hint,
                               command=lambda k=key: self.show(k))
        bar.add_cascade(label="Go", menu=go)

        view = tk.Menu(bar, tearoff=False)
        view.add_command(label="Show / hide this page's guide", accelerator="F1",
                         command=self.toggle_guide)
        view.add_command(label="Show / hide the sidebar", accelerator="Ctrl+B",
                         command=self.toggle_sidebar)
        bar.add_cascade(label="View", menu=view)

        self.tools_menu = tk.Menu(bar, tearoff=False)
        bar.add_cascade(label="Tools", menu=self.tools_menu)

        help_ = tk.Menu(bar, tearoff=False)
        help_.add_command(label="Guide for this page", accelerator="F1",
                          command=self.toggle_guide)
        help_.add_command(label="Keyboard shortcuts",
                          command=lambda: ui.info_dialog(self.root, "Keyboard shortcuts",
                                                         guide.SHORTCUTS))
        help_.add_separator()
        help_.add_command(label="About CardCropper", command=self.about)
        bar.add_cascade(label="Help", menu=help_)
        self.root.configure(menu=bar)

    def add_tool(self, label, command, separator=False):
        if separator:
            self.tools_menu.add_separator()
        self.tools_menu.add_command(label=label, command=command)

    def _bind_keys(self):
        order = [k for k in self._order() if k != "home"]
        for i, key in enumerate(order[:9], 1):
            self.root.bind_all(f"<Control-Key-{i}>", lambda _e, k=key: self.show(k))
        self.root.bind_all("<Control-Key-0>", lambda _e: self.show("home"))
        self.root.bind_all("<F1>", lambda _e: self.toggle_guide())
        self.root.bind_all("<Control-b>", lambda _e: self.toggle_sidebar())
        self.root.bind_all("<Control-comma>", lambda _e: self.open_settings())

    def open_settings(self):
        from .conditioning import settings_dir
        from .gui import _reveal
        os.makedirs(settings_dir(), exist_ok=True)
        _reveal(settings_dir())

    def about(self):
        from . import updater
        mine = updater.current()
        ui.info_dialog(self.root, "About CardCropper", f"""\
CardCropper — scans in, graded and priced listings out.

Build: {mine[:12] if mine else "not stamped (a copy run from source)"}
Python {sys.version.split()[0]}, Tk {self.root.tk.call('info', 'patchlevel')}

Settings, the grade library, golden sets and the market history live in the
settings folder (File → Open settings folder).

Nothing here grades a card on its own or changes a listing without you: it
straightens, crops, ranks, finds evidence and files. The judgement stays
with you.""")

    # ------------------------------------------------------------ status bar

    def _build_status(self):
        bar = ttk.Frame(self.root, style="Status.TFrame", padding=(12, 3, 8, 3))
        bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.status_bar = bar
        self.status_right = ttk.Frame(bar, style="Status.TFrame")
        self.status_right.pack(side="right")

    def status(self, key, text, tip_text=None, command=None):
        """A small figure in the status bar, kept up to date by `refresh_status`."""
        lbl = self.status_parts.get(key)
        if lbl is None:
            lbl = ttk.Label(self.status_bar, style="Status.TLabel",
                            cursor="hand2" if command else "")
            lbl.pack(side="left", padx=(0, 18))
            if command:
                lbl.bind("<Button-1>", lambda _e: command())
            self.status_parts[key] = lbl
        lbl.configure(text=text)
        if tip_text:
            ui.tip(lbl, tip_text)

    def refresh_status(self):
        hook = getattr(self, "on_status", None)
        if hook:
            try:
                hook()
            except Exception:                               # noqa: BLE001
                pass


# ---------------------------------------------------------------- home


class HomePage(ttk.Frame):
    """
    Where things stand, and the way in to each stage. Three columns in the
    order the work runs; each step is a button straight to its page.
    """

    STEPS = {
        "1 · Scan": (("folders", "Make today's scan folders"),
                     ("split", "File a long run into stacks"),
                     ("crop", "Pair and crop the scans")),
        "2 · Grade": (("condition", "Rank an export by wear"),
                      ("grade", "Grade card by card"),
                      ("library", "Review grades, build golden sets")),
        "3 · List & price": (("variants", "Check special printings"),
                             ("lookup", "Look a card up"),
                             ("prices", "See eBay sold prices"),
                             ("batch", "Batch, price & change file")),
    }

    def __init__(self, master, shell):
        super().__init__(master, padding=(24, 20))
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.shell = shell
        for c in range(3):
            self.columnconfigure(c, weight=1, uniform="col")

        ttk.Label(self, text="CardCropper", font="CCBig").grid(row=0, column=0, columnspan=3,
                                                               sticky="w")
        ttk.Label(self, style="Muted.TLabel", text=guide.PAGES["home"]["lead"])\
            .grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 16))

        # Quick search: straight to a card's sold prices or catalogue entry.
        q = ttk.Frame(self, style="Card.TFrame", padding=(14, 10))
        q.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(0, 16))
        q.columnconfigure(1, weight=1)
        ttk.Label(q, text="Quick search", style="CardHead.TLabel").grid(row=0, column=0,
                                                                        padx=(0, 12))
        self.query = tk.StringVar()
        e = ttk.Entry(q, textvariable=self.query)
        e.grid(row=0, column=1, sticky="ew")
        e.bind("<Return>", lambda _e: self.search_prices())
        ui.tip(e, "A card or listing title — e.g. \"Lugia 78/124 Fates Collide\".")
        b = ttk.Button(q, text="Sold prices", style="Accent.TButton",
                       command=self.search_prices)
        b.grid(row=0, column=2, padx=(8, 0))
        ui.tip(b, "Search recent UK sold listings and see the price they make (Enter).")
        b = ttk.Button(q, text="Catalogue", command=self.search_catalogue)
        b.grid(row=0, column=3, padx=(6, 0))
        ui.tip(b, "Look the card up: printings, reference prices, picture.")

        for c, (stage, steps) in enumerate(self.STEPS.items()):
            col = ttk.Frame(self, style="Card.TFrame", padding=(14, 12))
            col.grid(row=3, column=c, sticky="nsew", padx=(0 if c == 0 else 12, 0))
            col.columnconfigure(0, weight=1)
            ttk.Label(col, text=stage, style="CardHead.TLabel").grid(row=0, column=0,
                                                                     sticky="w", pady=(0, 6))
            for r, (key, text) in enumerate(steps, 1):
                if key not in shell.pages and key not in guide.PAGES:
                    continue
                info = guide.PAGES[key]
                btn = ttk.Button(col, text=f"{info['icon']}   {text}", style="Step.TButton",
                                 command=lambda k=key: shell.show(k))
                btn.grid(row=r, column=0, sticky="ew", pady=2)
                ui.tip(btn, f"{info['label']}: {info['lead']}")

        self.cards = ttk.Frame(self)
        self.cards.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(16, 0))
        self.figures = {}
        for i, (key, label, tiptext, go) in enumerate((
                ("graded", "Cards graded by hand", "Every card in the grade library.",
                 "library"),
                ("golden", "Golden set (pokemon-en)", "Cards in the English Pokémon golden "
                                                      "set, of the 30 it should hold.",
                 "library"),
                ("borderline", "Borderline, not golden", "Close calls the library suggests "
                                                         "adding to a golden set.", "library"),
                ("searches", "Market searches kept", "Sold-listing searches saved with their "
                                                     "listings.", "prices"),
                ("login", "Comp Finder", "Searching sold listings needs your Comp Finder "
                                         "login.", "prices"))):
            card = ui.stat_card(self.cards, label, "—", "", width=150)
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 10, 0))
            self.cards.columnconfigure(i, weight=1)
            ui.tip(card, tiptext + " Click to go there.")
            for w in [card] + list(card.winfo_children()):
                w.bind("<Button-1>", lambda _e, k=go: shell.show(k))
            self.figures[key] = card
        ttk.Label(self, style="Small.TLabel", text=(
            "Hover over anything for what it does. Each page has a guide (F1). "
            "Ctrl+1…9 jumps between pages.")).grid(row=5, column=0, columnspan=3, sticky="w",
                                                   pady=(18, 0))

    def on_show(self):
        from . import library, market, pricing
        try:
            st = library.stats()
            self.figures["graded"].set(str(st["cards"]), "  ·  ".join(
                f"{g} {n}" for g, n in st["by_grade"].items()) or "none yet")
            g = st["golden"].get("pokemon-en", {"cards": 0, "borderline": 0})
            self.figures["golden"].set(f"{g['cards']}/{library.GOLDEN_TARGET[0]}",
                                       f"{g['borderline']} borderline")
            self.figures["borderline"].set(str(st["suggested"]), "worth a look"
                                           if st["suggested"] else "")
        except Exception:                                   # noqa: BLE001
            pass
        try:
            self.figures["searches"].set(str(len(market.history(limit=10000))), "")
        except Exception:                                   # noqa: BLE001
            pass
        on = bool(pricing.comp_finder_cookie())
        self.figures["login"].set("Logged in" if on else "Not set",
                                  "" if on else "Market → More → Comp Finder login…",
                                  colour=ui.C["ok"] if on else ui.C["warn"])

    def search_prices(self):
        q = self.query.get().strip()
        prices = self.shell.objects.get("prices")
        self.shell.show("prices")
        if q and prices is not None:
            prices.lookup(q)

    def search_catalogue(self):
        q = self.query.get().strip()
        lookup = self.shell.objects.get("lookup")
        if lookup is None:
            return
        self.shell.show("lookup")
        if q and hasattr(lookup, "search"):
            lookup.search(q)
