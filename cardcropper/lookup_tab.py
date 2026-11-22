"""
The "Card lookup" page: which card is this, which printings of it exist, and
what the reference prices say — before it is priced and listed.

Type a name, number and set, or paste a listing title, and press Search. The
public catalogues (the Pokémon TCG API, YGOPRODeck) answer with candidates;
pick one to see its catalogue image, printings, reference prices and a
check of the printing the title claims ("Reverse Holo", "1st Edition"…).
"Match a scan…" compares a scan with every candidate's image and puts the
closest first.

Its limits, said where a person will read them:

* The catalogue knows which printings were MADE, not which one is on the
  desk. Foil, Cosmos and stamps are checked on the card itself.
* Image matching tells you WHICH card a scan is, not which printing: the
  catalogue shows a normal and a reverse holo with the same picture.
* Reference prices are TCGplayer (US) and Cardmarket (EU) figures. They are
  a sense check, not a UK eBay price — "Price this on eBay" is for that.

Nothing here changes a batch.
"""

import queue
import threading
import traceback
import webbrowser

import tkinter as tk
from tkinter import filedialog, ttk

from PIL import Image, ImageTk

from . import catalog, ui
from .variant_tab import fetch_image

IMG_W, IMG_H = 190, 265
DETAIL_W = 470
GAMES = {"Auto": "auto", "Pokémon": "pokemon", "Pokémon (Japanese)": "pokemon-jp",
         "Yu-Gi-Oh": "yugioh"}
#: The words a claimed printing adds to an eBay search.
PRINT_WORDS = {"reverse": "Reverse Holo", "1st-edition": "1st Edition",
               "cosmos": "Cosmos Holo", "shadowless": "Shadowless"}
COLUMNS = (("name", "Name", 130, {"stretch": True}), ("number", "No.", 84),
           ("set", "Set", 130, {"stretch": True}), ("rarity", "Rarity", 90),
           ("printings", "Printings", 120, {"stretch": True}),
           ("price", "Ref. price", 70, {"anchor": "e"}), ("match", "Match", 54, {"anchor": "e"}))


def _number(card):
    if card.game == "pokemon" and card.set_total and card.number.isdigit():
        return f"{card.number}/{card.set_total}"
    return card.number


def _fit(im, w=IMG_W, h=IMG_H):
    im = im.copy()
    im.thumbnail((w, h), Image.LANCZOS)
    return im


class LookupTab(ttk.Frame):
    def __init__(self, master, on_price=None):
        super().__init__(master, padding=10)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.on_price = on_price
        self.events = queue.Queue()
        self.worker = None          # the search / match thread, for the app to see
        self.results = []           # [CatalogCard], in the order searched
        self.parsed = {}            # catalog.parse_title of what was searched
        self.scores = {}            # card id -> similarity to the matched scan
        self.scan = None            # the matched scan, cut out of its bed
        self.scan_path = ""
        self.current = None         # the CatalogCard shown on the right
        self.images = {}            # url -> PIL image, Exception, or None while loading
        self._photos = []

        self._build_bar()
        self.reading = ttk.Label(self, style="Small.TLabel")
        self.reading.grid(row=1, column=0, sticky="w", pady=(4, 6))

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.grid(row=2, column=0, sticky="nsew")
        left = ttk.Frame(panes)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        wrap, self.tv = ui.tree(left, COLUMNS, height=14)
        wrap.grid(row=0, column=0, sticky="nsew")
        self.tv.tag_configure("best", background="#e3f4e8")
        self.tv.tag_configure("weak", foreground=ui.C["faint"])
        self.tv.bind("<<TreeviewSelect>>", lambda _e: self._selected())
        self.tv.bind("<Double-1>", lambda _e: self.open_sold())
        ui.tip(self.tv, "Catalogue candidates. Pick one to see its printings, prices and the "
                        "variant check. Click a heading to sort; double-click to open eBay "
                        "sold listings for it. After “Match a scan…” the Match column says how "
                        "alike the scan and each catalogue image are.")
        panes.add(left, weight=3)

        panes.add(self._scroller(panes), weight=2)
        self._build_detail()
        panes.bind("<Configure>", self._first_layout, add="+")

        bar = ttk.Frame(self, style="Status.TFrame", padding=(8, 3))
        bar.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.status = ttk.Label(bar, style="Status.TLabel",
                                text="Search the catalogue for a card, or paste a listing title.")
        self.status.pack(side="left")
        self._poll()

    # ------------------------------------------------------------ building

    def _build_bar(self):
        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text="Card").grid(row=0, column=0, padx=(0, 6))
        self.query_var = tk.StringVar()
        self.entry = ttk.Entry(bar, textvariable=self.query_var)
        self.entry.grid(row=0, column=1, sticky="ew")
        self.entry.bind("<Return>", lambda _e: self.search())
        ui.tip(self.entry, "name, number, set — or paste a listing title.\n\ne.g. “Pikachu 58/102 "
                           "Base Set”, “Riolu BW33”, “Dark Magician LOB-EN005”. A printing in "
                           "the text (Reverse Holo, 1st Edition…) is checked against the "
                           "catalogue. Press Return to search.")
        self.game_var = tk.StringVar(value="Auto")
        game = ttk.Combobox(bar, textvariable=self.game_var, values=tuple(GAMES), width=17,
                            state="readonly")
        game.grid(row=0, column=2, padx=6)
        ui.tip(game, "Which catalogue to ask. Auto picks Yu-Gi-Oh for a set code like "
                     "LOB-EN005 or the word Yu-Gi-Oh, and Pokémon otherwise.")
        self.search_btn = ttk.Button(bar, text="Search", style="Accent.TButton",
                                     command=self.search)
        self.search_btn.grid(row=0, column=3)
        ui.tip(self.search_btn, lambda: "Busy — wait for the current search or match to finish."
               if self._busy() else "Look the card up in the public catalogue. Answers are "
                                    "kept for a week, so a repeat search is instant.")
        self.match_btn = ttk.Button(bar, text="Match a scan…", command=self.pick_scan)
        self.match_btn.grid(row=0, column=4, padx=(6, 0))
        ui.tip(self.match_btn, "Pick a scan of the card. It is compared with every result's "
                               "catalogue image and the closest goes first. It tells you WHICH "
                               "card it is, not which printing.")
        more = ui.more_menu(bar, [("Check a whole batch…", self.check_batch), None,
                                  ("Open eBay sold search ↗", self.open_sold),
                                  ("Copy eBay search title", self.copy_title), None,
                                  ("Clear cache", self.clear_cache)],
                            tip_text="Now-and-then actions: eBay sold listings for the selected "
                                     "card (or what you typed), and emptying the week-long "
                                     "cache of catalogue answers.")
        more.grid(row=0, column=5, padx=(6, 0))

    def _scroller(self, parent):
        """
        The detail panel scrolls: a card with a scan beside it, its prices and
        a few findings is taller than a laptop window.
        """
        outer = ttk.Frame(parent)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)
        canvas = tk.Canvas(outer, highlightthickness=0, background=ui.C["bg"],
                           width=DETAIL_W)
        canvas.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        sb.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=sb.set)
        self.detail = ttk.Frame(canvas, padding=(12, 0, 8, 8))
        self.detail.columnconfigure(0, weight=1)
        win = canvas.create_window(0, 0, anchor="nw", window=self.detail)

        def inner(_e):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def outer_size(e):
            canvas.itemconfigure(win, width=e.width)
            self._wrap(e.width - 30)

        def wheel(e):
            if self.detail.winfo_reqheight() > canvas.winfo_height():
                step = -1 if (e.num == 4 or e.delta > 0) else 1
                canvas.yview_scroll(step, "units")
        self.detail.bind("<Configure>", inner)
        canvas.bind("<Configure>", outer_size)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            outer.bind_all(seq, lambda e: wheel(e) if str(e.widget).startswith(str(outer))
                           else None, add="+")
        self._detail_canvas = canvas
        return outer

    def _wrap(self, width):
        width = max(200, width)
        for w in (self.empty, self.verdict, self.name_lbl, self.meta_lbl, self.prices_lbl):
            w.configure(wraplength=width)
        for w in self.findings.winfo_children():
            w.configure(wraplength=width)
        self._wrap_width = width

    def _first_layout(self, e):
        """Give the detail panel its width once, when the window first has a size."""
        if e.width > DETAIL_W * 2 and not getattr(self, "_laid_out", False):
            self._laid_out = True
            e.widget.sashpos(0, e.width - DETAIL_W)

    def _build_detail(self):
        d = self.detail
        self.empty = ttk.Label(d, style="Muted.TLabel", wraplength=360, justify="left",
                               text="Search for a card, then pick a row to see its printings, "
                                    "reference prices and whether the printing you typed "
                                    "exists.")
        self.empty.grid(row=0, column=0, sticky="nw")
        self.body = ttk.Frame(d)
        self.body.columnconfigure(0, weight=1)

        pics = ttk.Frame(self.body)
        pics.grid(row=0, column=0, sticky="w")
        self.cat_caption = ttk.Label(pics, text="Catalogue", style="Small.TLabel")
        self.cat_caption.grid(row=0, column=0, sticky="w")
        self.scan_caption = ttk.Label(pics, text="Your scan", style="Small.TLabel")
        self.cat_canvas = tk.Canvas(pics, width=IMG_W, height=IMG_H, highlightthickness=0,
                                    background=ui.C["sunken"])
        self.cat_canvas.grid(row=1, column=0, sticky="nw")
        ui.tip(self.cat_canvas, "The catalogue's picture of this card. It looks the same for "
                                "every printing, so it cannot show a reverse holo.")
        self.scan_canvas = tk.Canvas(pics, width=IMG_W, height=IMG_H, highlightthickness=0,
                                     background=ui.C["sunken"])
        ui.tip(self.scan_canvas, "The scan you matched, cut out of the scanner bed.")
        self.verdict = ttk.Label(self.body, wraplength=440, justify="left")
        ui.tip(self.verdict, "How alike the scan and this catalogue image are. PROVISIONAL "
                             f"thresholds: {catalog.SAME_CARD:.0%}+ likely the same card, "
                             f"{catalog.UNCERTAIN:.0%}–{catalog.SAME_CARD:.0%} uncertain, below "
                             "that a different card. Wear, glare and sleeves lower it.")

        self.name_lbl = ttk.Label(self.body, font="CCHeading", wraplength=440)
        self.name_lbl.grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.meta_lbl = ttk.Label(self.body, style="Muted.TLabel", wraplength=440,
                                  justify="left")
        self.meta_lbl.grid(row=3, column=0, sticky="w")

        ttk.Label(self.body, text="Printings in the catalogue", style="Small.TLabel")\
            .grid(row=4, column=0, sticky="w", pady=(8, 2))
        self.chips = ttk.Frame(self.body)
        self.chips.grid(row=5, column=0, sticky="w")

        ttk.Label(self.body, text="Reference prices", style="Small.TLabel")\
            .grid(row=6, column=0, sticky="w", pady=(8, 2))
        self.prices_lbl = ttk.Label(self.body, justify="left", wraplength=440)
        self.prices_lbl.grid(row=7, column=0, sticky="w")
        ui.tip(self.prices_lbl, "TCGplayer (US dollars) from the catalogue, and Cardmarket "
                                "(euros) from Cardmarket's own price guide, downloaded "
                                "daily. A sense check, not a UK eBay price.")

        ttk.Label(self.body, text="Variant check", style="Small.TLabel")\
            .grid(row=8, column=0, sticky="w", pady=(8, 2))
        self.findings = ttk.Frame(self.body)
        self.findings.grid(row=9, column=0, sticky="ew")

        # The main next step on a row of its own; the links to elsewhere under it.
        if self.on_price is not None:
            b = ttk.Button(self.body, text="Price this on eBay", command=self.price_this)
            b.grid(row=10, column=0, sticky="w", pady=(12, 0))
            ui.tip(b, "Send a search title for this card (with the printing you typed) to the "
                      "Market page, to be priced from UK sold listings.")
        btns = ttk.Frame(self.body)
        btns.grid(row=11, column=0, sticky="w", pady=(6, 0))
        b = ttk.Button(btns, text="Open eBay sold ↗", style="Ghost.TButton",
                       command=self.open_sold)
        b.pack(side="left")
        ui.tip(b, "Open eBay.co.uk sold and completed listings for this card in your browser.")
        b = ttk.Button(btns, text="Open catalogue page ↗", style="Ghost.TButton",
                       command=self.open_catalogue)
        b.pack(side="left", padx=(4, 0))
        ui.tip(b, "Open the card's page on TCGplayer / Cardmarket (Pokémon) or YGOPRODeck.")

    # ------------------------------------------------------------ background

    def _busy(self):
        return bool(self.worker and self.worker.is_alive())

    def _background(self, label, fn):
        if self._busy():
            self._say("Still working on the last request — a moment.")
            return False
        self._say(label)
        self.search_btn.state(["disabled"])
        self.match_btn.state(["disabled"])

        def go():
            try:
                fn()
            except Exception as exc:                          # noqa: BLE001
                traceback.print_exc()
                self.events.put(("error", f"{type(exc).__name__}: {exc}"))
            finally:
                self.events.put(("idle", None))
        self.worker = threading.Thread(target=go, daemon=True)
        self.worker.start()
        return True

    def _poll(self):
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                getattr(self, "_on_" + kind)(payload)
            except tk.TclError:
                return                                        # the page has gone
            except Exception:                                 # noqa: BLE001
                traceback.print_exc()                         # one bad event, not a dead page
        try:
            self.after(120, self._poll)
        except tk.TclError:
            pass

    def _say(self, text, level=None):
        self.status.configure(text=text, foreground=ui.C[level] if level else ui.C["muted"])

    def _on_error(self, msg):
        self._say(msg + " — check the connection and try again.", "bad")

    def _on_idle(self, _):
        self.search_btn.state(["!disabled"])
        self.match_btn.state(["!disabled"])
        pending = getattr(self, "_pending_scan", None)
        if pending and self.results:
            # Opened from a batch check: match the card's own scan straight away.
            self._pending_scan = None
            self.match_scan(pending)

    def _on_status(self, text):
        self._say(text)

    # ------------------------------------------------------------ search

    def search(self, text=None):
        """Search the catalogue for `text` (default: what is typed)."""
        if text is not None:
            self.query_var.set(text)
        text = self.query_var.get().strip()
        if not text:
            self._say("Type a card name, number and set — or paste a listing title.", "warn")
            self.entry.focus_set()
            return
        game = GAMES.get(self.game_var.get(), "auto")

        def run():
            res = catalog.search(text, game=game)
            self.events.put(("results", (text, res)))
        self._background(f"Searching the catalogue for “{text}”…", run)

    def _on_results(self, payload):
        text, res = payload
        self.results = list(res)
        self.parsed = res.parsed
        self.scores = {}
        self.scan, self.scan_path = None, ""
        self._show_reading(res)
        self._fill()
        if not self.results:
            self._say(f"Nothing in the catalogue for “{text}”. Try just the name, or check the "
                      "game.", "warn")
            self._show(None)
            return
        loose = " (a looser search: the set or number in the text did not match)" \
            if res.parsed.get("game") == "pokemon" and res.query and "set.name" not in res.query \
            and res.parsed.get("set_name") else ""
        self._say(f"{len(self.results)} candidate(s) for “{text}”{loose}.")

    def _show_reading(self, res):
        p = res.parsed
        bits = [("Yu-Gi-Oh" if p.get("game") == "yugioh" else "Pokémon")]
        if p.get("name"):
            bits.append(f"name “{p['name']}”")
        if p.get("number"):
            bits.append("number " + p["number"] + (f"/{p['total']}" if p.get("total") else ""))
        if p.get("set_name"):
            bits.append(f"set “{p['set_name']}”")
        if p.get("rarity"):
            bits.append(p["rarity"])
        claimed = [PRINT_WORDS.get(k, k) for k in sorted(p.get("printing") or ())]
        bits.append("printing: " + (", ".join(claimed) if claimed else "none named"))
        self.reading.configure(text="Read as: " + " · ".join(bits))

    def _fill(self):
        self.tv.delete(*self.tv.get_children())
        order = list(range(len(self.results)))
        if self.scores:
            order.sort(key=lambda i: -self.scores.get(self.results[i].id, -1))
        best = order[0] if self.scores and order else None
        for n, i in enumerate(order):
            c = self.results[i]
            s = self.scores.get(c.id)
            tags = ["odd"] if n % 2 else []
            if s is not None and i == best:
                tags.append("best")
            elif s is not None and s < catalog.UNCERTAIN:
                tags.append("weak")
            self.tv.insert("", "end", iid=str(i), tags=tags, values=(
                c.name, _number(c), c.set_name, c.rarity, ", ".join(c.printings),
                catalog.ref_price(c), "" if s is None else f"{s:.0%}"))
        if order:
            self.tv.selection_set(str(order[0]))
            self.tv.see(str(order[0]))

    def _selected(self):
        sel = self.tv.selection()
        if sel:
            self._show(self.results[int(sel[0])])

    # ------------------------------------------------------------ detail

    def _show(self, card):
        self.current = card
        if card is None:
            self.body.grid_remove()
            self.empty.grid()
            return
        self.empty.grid_remove()
        self.body.grid(row=0, column=0, sticky="nsew")
        self._detail_canvas.yview_moveto(0)
        self.name_lbl.configure(text=card.name)
        meta = [card.set_name or "set unknown"]
        if card.number:
            meta.append(_number(card))
        if card.rarity:
            meta.append(card.rarity)
        if card.release_date:
            meta.append("released " + card.release_date)
        self.meta_lbl.configure(text=" · ".join(meta))

        for w in self.chips.winfo_children():
            w.destroy()
        claimed = {PRINT_WORDS.get(k, "").lower() for k in self.parsed.get("printing") or ()}
        claimed.add((self.parsed.get("rarity") or "").lower())
        for p in card.printings or ["none recorded"]:
            on = p.lower() in claimed
            chip = tk.Label(self.chips, text=p, padx=7, pady=2, borderwidth=0,
                            background=ui.C["accent"] if on else ui.C["accent_soft"],
                            foreground="#ffffff" if on else ui.C["accent"])
            chip.pack(side="left", padx=(0, 4))
            ui.tip(chip, "The printing your text names." if on else
                   "A printing the catalogue records for this card.")

        lines = []
        for label, v in card.prices.items():
            # The catalogue's Cardmarket figures can be months old; today's
            # come from Cardmarket's own price guide instead (below).
            if label.startswith("Cardmarket"):
                continue
            sym = "$" if "USD" in label else "€"
            if isinstance(v, dict):
                lines.append(label + ": " + ", ".join(f"{k} {sym}{x:.2f}" for k, x in v.items()))
            else:
                lines.append(f"{label}: {sym}{v:.2f}")
        self._price_lines = (card.id, lines)
        self.prices_lbl.configure(text="\n".join(lines + ["Cardmarket: loading today's price "
                                                         "guide…"]))
        self._want_cardmarket(card)

        for w in self.findings.winfo_children():
            w.destroy()
        found = catalog.check_variant(self.parsed.get("printing") or set(), card,
                                      self.parsed.get("rarity", ""))
        if not found:
            found = [catalog.Finding("Nothing to flag from the catalogue.", "ok")]
        for f in found:
            colour = {"warn": ui.C["warn"], "ok": ui.C["ok"]}.get(f.level, ui.C["muted"])
            mark = {"warn": "⚠", "ok": "✓"}.get(f.level, "•")
            ttk.Label(self.findings, text=f"{mark} {f}", foreground=colour,
                      wraplength=getattr(self, "_wrap_width", 430),
                      justify="left").pack(anchor="w", pady=1)

        s = self.scores.get(card.id)
        if self.scan is not None and s is not None:
            level = "ok" if s >= catalog.SAME_CARD else "warn" if s >= catalog.UNCERTAIN \
                else "bad"
            self.verdict.configure(text=f"Scan vs this card: {s:.0%} — {catalog.verdict(s)} "
                                        "(provisional). Says which card, not which printing.",
                                   foreground=ui.C[level])
            self.verdict.grid(row=1, column=0, sticky="w", pady=(4, 0))
            self.scan_caption.grid(row=0, column=1, sticky="w", padx=(8, 0))
            self.scan_canvas.grid(row=1, column=1, sticky="nw", padx=(8, 0))
        else:
            self.verdict.grid_remove()
            self.scan_caption.grid_remove()
            self.scan_canvas.grid_remove()
        self._load(card.image_small)
        self._draw()

    def _load(self, url):
        if not url or url in self.images:
            return
        self.images[url] = None

        def go():
            try:
                im = fetch_image(url)
            except Exception as exc:                          # noqa: BLE001
                im = exc
            self.events.put(("image", (url, im)))
        threading.Thread(target=go, daemon=True).start()

    def _on_image(self, payload):
        url, im = payload
        self.images[url] = im
        if self.current is not None and self.current.image_small == url:
            self._draw()

    def _draw(self):
        self._photos = []
        card = self.current
        pairs = [(self.cat_canvas, self.images.get(card.image_small) if card else None,
                  card and card.image_small)]
        if self.scan is not None:
            pairs.append((self.scan_canvas, self.scan, True))
        for canvas, im, has in pairs:
            canvas.delete("all")
            if isinstance(im, Image.Image):
                photo = ImageTk.PhotoImage(_fit(im))
                self._photos.append(photo)
                canvas.create_image(IMG_W // 2, IMG_H // 2, image=photo)
            else:
                text = ("no image in the catalogue" if not has else
                        "loading…" if im is None else "could not load the image")
                canvas.create_text(IMG_W // 2, IMG_H // 2, text=text, fill=ui.C["muted"],
                                   width=IMG_W - 20)

    # ------------------------------------------------------------ matching

    def pick_scan(self):
        kinds = "*.jpg *.jpeg *.png *.tif *.tiff *.bmp *.webp"
        path = filedialog.askopenfilename(title="A scan of the card",
                                          filetypes=[("Images", kinds), ("All", "*.*")])
        if path:
            self.match_scan(path)

    def check_batch(self, folder=None):
        """Every card of a prepared batch against the catalogue (batch_check)."""
        from .batch_check import BatchCheck

        def open_one(title, front):
            self.search(title)
            self._pending_scan = front
        BatchCheck(self.winfo_toplevel(), folder, on_open=open_one)

    def _want_cardmarket(self, card):
        """Today's Cardmarket prices for the card, in the background (cardmarket.py)."""
        def go():
            try:
                from . import cardmarket
                found = cardmarket.price_for(card)
                text = cardmarket.describe(found) or \
                    "Cardmarket: no match for this card in today's price guide."
            except Exception as exc:                          # noqa: BLE001
                text = f"Cardmarket: price guide unavailable ({type(exc).__name__})."
            self.events.put(("cardmarket", (card.id, text)))
        threading.Thread(target=go, daemon=True).start()

    def _on_cardmarket(self, payload):
        card_id, text = payload
        cur, lines = getattr(self, "_price_lines", (None, []))
        if cur == card_id:
            self.prices_lbl.configure(text="\n".join(lines + [text]))

    def match_scan(self, path):
        """Compare the scan at `path` with every current result's catalogue image."""
        if not self.results:
            self._say("Search for the card first — the scan is compared with the results.",
                      "warn")
            return
        cards = list(self.results)

        def run():
            with Image.open(path) as im:
                im.load()
                cut = catalog.card_only(im)
            fp = catalog.fingerprint(cut)
            pairs = []
            for n, c in enumerate(cards, 1):
                note = f"Comparing the scan with {n} of {len(cards)}: {c.name} {_number(c)}…"
                self.events.put(("status", note))
                try:
                    pairs.append((c, fetch_image(c.image_small) if c.image_small else None))
                except Exception:                             # noqa: BLE001
                    pairs.append((c, None))
            ranked = catalog.rank_matches(fp, pairs)
            self.events.put(("matched", (path, cut, {c.id: s for s, c in ranked},
                                         len(cards) - len(ranked))))
        self._background("Reading the scan…", run)

    def _on_matched(self, payload):
        path, cut, scores, missing = payload
        self.scan_path, self.scan, self.scores = path, cut, scores
        self._fill()
        if not scores:
            self._say("None of the results had an image to compare with.", "warn")
            return
        best = max(self.results, key=lambda c: scores.get(c.id, -1))
        s = scores[best.id]
        level = "ok" if s >= catalog.SAME_CARD else "warn" if s >= catalog.UNCERTAIN else "bad"
        gone = f" ({missing} without an image skipped)" if missing else ""
        self._say(f"Best match: {best.name} {_number(best)}, {best.set_name} — {s:.0%}, "
                  f"{catalog.verdict(s)}{gone}. Check the printing on the card itself.", level)

    # ------------------------------------------------------------ actions

    def _title(self):
        """An eBay search title for the shown card, with the printing the text names."""
        c = self.current
        if c is None:
            return self.query_var.get().strip()
        if c.game == "yugioh":
            words = self.parsed.get("rarity") or (c.printings[0] if len(c.printings) == 1
                                                  else "")
        else:
            words = " ".join(PRINT_WORDS[k] for k in sorted(self.parsed.get("printing") or ())
                             if k in PRINT_WORDS)
        return catalog.title_for(c, words)

    def price_this(self):
        if self.on_price and self.current is not None:
            self.on_price(self._title())

    def open_sold(self):
        title = self._title()
        if title:
            webbrowser.open(catalog.ebay_sold_url(title))
        else:
            self._say("Search for a card first.", "warn")

    def copy_title(self):
        title = self._title()
        if title:
            ui.copy_text(self, title)
            self._say(f"Copied “{title}”.")

    def open_catalogue(self):
        if self.current is not None and self.current.url:
            webbrowser.open(self.current.url)
        else:
            self._say("The catalogue has no page link for this card.", "warn")

    def clear_cache(self):
        n = catalog.clear_cache()
        self._say(f"Cleared {n} saved catalogue answer(s); the next search asks afresh.")
