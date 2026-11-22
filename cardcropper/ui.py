"""
The look and the small pieces every page is built from.

One theme for the whole window, so a primary action looks the same on every
page and a person learns once where to look:

* **Primary** (`Accent.TButton`) — the one thing a page is for. At most one
  per page: Crop cards, Prepare batch, Create folders…
* **Secondary** (plain `TButton`) — the steps around it.
* **Subtle** (`Ghost.TButton`) — housekeeping: Clear, Open folder, Browse…
* **More ▾** (`more_menu`) — actions used now and then, layered behind a menu
  rather than added to an ever-longer row of buttons.

Tooltips are part of the controls rather than the page text: hover anything to
be told what it does, and why it might be greyed out. `attach_tips` puts them
on pages that were built without them, matched by the text a control shows,
so a page's own code does not have to change to be explained.

Tk only — it ships with Python, so the frozen .exe stays one file.
"""

import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# ---------------------------------------------------------------- palette

#: Light, quiet surfaces; one accent; three status colours that are the same
#: hues the pages already use for done / warning / failed.
C = {
    "bg": "#f4f5f7",          # window
    "surface": "#ffffff",     # tables, entries, cards
    "sunken": "#eceef2",      # sidebar, header strips
    "border": "#d5d9e0",
    "text": "#1d2330",
    "muted": "#5f6b7a",
    "faint": "#8a94a3",
    "accent": "#3451c7",
    "accent_hover": "#2a43a8",
    "accent_soft": "#e6ebfb",
    "ok": "#1a7f37",
    "warn": "#8a5a00",
    "bad": "#b3261e",
    "select": "#cfdaf7",
    "tip_bg": "#1f2430",
    "tip_fg": "#f2f4f8",
    "nav_active": "#dfe5f6",
}

#: Grade colours, best to worst — used wherever a grade is shown as a chip.
GRADE_COLOURS = {"NM": "#1a7f37", "LP": "#3b7d1f", "MP": "#8a5a00", "HP": "#b45309",
                 "DMG": "#b3261e", "HOLD": "#5f6b7a"}

PAD = 10


def _family():
    if sys.platform.startswith("win"):
        return "Segoe UI"
    if sys.platform == "darwin":
        return "Helvetica Neue"
    return None


def apply_theme(root):
    """
    Style the whole window. Called once, before any page is built, so the
    option-database defaults (fonts and colours of the plain Tk widgets — the
    logs are `tk.Text`) reach every widget made afterwards.
    """
    style = ttk.Style(root)
    try:
        # clam, not vista: vista draws native buttons that ignore colour, and
        # a primary button that looks like every other button is not one.
        style.theme_use("clam")
    except tk.TclError:
        pass

    fam = _family()
    for name, size, weight in (("TkDefaultFont", 10, "normal"), ("TkTextFont", 10, "normal"),
                               ("TkMenuFont", 10, "normal"), ("TkHeadingFont", 10, "bold"),
                               ("TkTooltipFont", 9, "normal")):
        try:
            f = tkfont.nametofont(name)
            f.configure(size=size, weight=weight, **({"family": fam} if fam else {}))
        except tk.TclError:
            pass
    base = tkfont.nametofont("TkDefaultFont").actual()["family"]
    fonts = {
        "CCTitle": (base, 15, "bold"),
        "CCHeading": (base, 11, "bold"),
        "CCSmall": (base, 9, "normal"),
        "CCBig": (base, 20, "bold"),
        "CCValue": (base, 15, "bold"),
        "CCMono": ("Consolas" if sys.platform.startswith("win") else "DejaVu Sans Mono", 9,
                   "normal"),
    }
    # Kept on the root: a tkinter Font deletes its Tk font when the Python
    # object is collected, and every label using it falls back to the default.
    root._cc_fonts = []                                     # noqa: SLF001
    for name, (family, size, weight) in fonts.items():
        try:
            root._cc_fonts.append(tkfont.Font(root, name=name, family=family, size=size,
                                              weight=weight))
        except tk.TclError:
            tkfont.nametofont(name).configure(family=family, size=size, weight=weight)

    root.configure(background=C["bg"])
    root.option_add("*Text.background", C["surface"])
    root.option_add("*Text.foreground", C["text"])
    root.option_add("*Text.relief", "flat")
    root.option_add("*Text.borderWidth", 0)
    root.option_add("*Text.highlightThickness", 1)
    root.option_add("*Text.highlightBackground", C["border"])
    root.option_add("*Text.highlightColor", C["accent"])
    root.option_add("*Text.font", "CCMono")
    root.option_add("*Text.padX", 6)
    root.option_add("*Text.padY", 4)
    root.option_add("*Menu.background", C["surface"])
    root.option_add("*Menu.activeBackground", C["accent_soft"])
    root.option_add("*Menu.activeForeground", C["text"])
    root.option_add("*Menu.relief", "flat")
    root.option_add("*TCombobox*Listbox.background", C["surface"])
    root.option_add("*TCombobox*Listbox.selectBackground", C["select"])
    root.option_add("*TCombobox*Listbox.selectForeground", C["text"])

    style.configure(".", background=C["bg"], foreground=C["text"], bordercolor=C["border"],
                    lightcolor=C["bg"], darkcolor=C["border"], troughcolor=C["sunken"],
                    focuscolor=C["accent"], selectbackground=C["select"],
                    selectforeground=C["text"], fieldbackground=C["surface"],
                    insertcolor=C["text"])
    style.map(".", foreground=[("disabled", C["faint"])])

    # Buttons — three layers.
    style.configure("TButton", padding=(10, 4), background=C["surface"],
                    bordercolor=C["border"], lightcolor=C["surface"], darkcolor=C["surface"],
                    relief="flat", focusthickness=1)
    style.map("TButton",
              background=[("disabled", C["bg"]), ("pressed", C["sunken"]),
                          ("active", C["accent_soft"])],
              bordercolor=[("focus", C["accent"]), ("active", C["accent"])],
              lightcolor=[("active", C["accent_soft"])], darkcolor=[("active", C["accent_soft"])])
    style.configure("Accent.TButton", background=C["accent"], foreground="#ffffff",
                    bordercolor=C["accent"], lightcolor=C["accent"], darkcolor=C["accent"],
                    padding=(14, 5), font=("TkDefaultFont", 10, "bold"))
    style.map("Accent.TButton",
              background=[("disabled", "#aab6e3"), ("pressed", C["accent_hover"]),
                          ("active", C["accent_hover"])],
              foreground=[("disabled", "#eef1fb")],
              bordercolor=[("disabled", "#aab6e3"), ("active", C["accent_hover"])],
              lightcolor=[("active", C["accent_hover"])], darkcolor=[("active", C["accent_hover"])])
    style.configure("Danger.TButton", foreground=C["bad"])
    style.configure("Ghost.TButton", background=C["bg"], bordercolor=C["bg"],
                    lightcolor=C["bg"], darkcolor=C["bg"], foreground=C["accent"],
                    padding=(6, 3))
    style.map("Ghost.TButton", background=[("active", C["accent_soft"])],
              bordercolor=[("active", C["accent_soft"])],
              foreground=[("disabled", C["faint"])])
    style.configure("Step.TButton", anchor="w", padding=(12, 6))
    style.configure("Grade.TButton", font=("TkDefaultFont", 10, "bold"), padding=(6, 6))
    for g, colour in GRADE_COLOURS.items():
        style.configure(f"{g}.Grade.TButton", foreground=colour)
    style.configure("TMenubutton", background=C["surface"], bordercolor=C["border"],
                    padding=(10, 4), relief="flat", arrowcolor=C["muted"])
    style.map("TMenubutton", background=[("active", C["accent_soft"])])

    # Fields.
    for w in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(w, padding=(6, 3), fieldbackground=C["surface"],
                        bordercolor=C["border"], lightcolor=C["surface"],
                        darkcolor=C["surface"], arrowcolor=C["muted"])
        style.map(w, bordercolor=[("focus", C["accent"])],
                  lightcolor=[("focus", C["accent"])],
                  fieldbackground=[("readonly", C["surface"]), ("disabled", C["bg"])])
    style.configure("TCheckbutton", background=C["bg"], indicatorbackground=C["surface"],
                    indicatorforeground=C["accent"])
    style.map("TCheckbutton", indicatorbackground=[("selected", C["surface"])],
              background=[("active", C["bg"])])
    style.configure("TRadiobutton", background=C["bg"], indicatorbackground=C["surface"],
                    indicatorforeground=C["accent"])
    style.map("TRadiobutton", background=[("active", C["bg"])])
    style.configure("Seg.TRadiobutton", padding=(8, 3))

    # Containers.
    style.configure("TLabelframe", background=C["bg"], bordercolor=C["border"],
                    lightcolor=C["bg"], darkcolor=C["bg"], relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=C["bg"], foreground=C["muted"],
                    font=("TkDefaultFont", 10, "bold"))
    style.configure("Card.TFrame", background=C["surface"], relief="solid",
                    bordercolor=C["border"], borderwidth=1)
    style.configure("Card.TLabel", background=C["surface"])
    style.configure("CardMuted.TLabel", background=C["surface"], foreground=C["muted"],
                    font="CCSmall")
    style.configure("CardBig.TLabel", background=C["surface"], font="CCBig")
    style.configure("CardValue.TLabel", background=C["surface"], font="CCValue")
    style.configure("CardHead.TLabel", background=C["surface"], font="CCHeading")
    style.configure("Sunken.TFrame", background=C["sunken"])
    style.configure("Header.TFrame", background=C["surface"])
    style.configure("Title.TLabel", background=C["surface"], font="CCTitle")
    style.configure("Lead.TLabel", background=C["surface"], foreground=C["muted"])
    style.configure("Heading.TLabel", font="CCHeading")
    style.configure("Muted.TLabel", foreground=C["muted"])
    style.configure("Small.TLabel", foreground=C["muted"], font="CCSmall")
    style.configure("Ok.TLabel", foreground=C["ok"])
    style.configure("Warn.TLabel", foreground=C["warn"])
    style.configure("Bad.TLabel", foreground=C["bad"])
    style.configure("Status.TFrame", background=C["sunken"])
    style.configure("Status.TLabel", background=C["sunken"], foreground=C["muted"],
                    font="CCSmall")
    style.configure("Help.TLabel", background=C["accent_soft"], foreground=C["text"],
                    padding=(10, 8))
    style.configure("Help.TFrame", background=C["accent_soft"])
    style.configure("TSeparator", background=C["border"])
    style.configure("TNotebook", background=C["bg"], bordercolor=C["border"])
    style.configure("TNotebook.Tab", padding=(12, 5), background=C["sunken"])
    style.map("TNotebook.Tab", background=[("selected", C["surface"])])
    style.configure("TProgressbar", background=C["accent"], troughcolor=C["sunken"],
                    bordercolor=C["sunken"], lightcolor=C["accent"], darkcolor=C["accent"])

    # Sidebar.
    style.configure("Nav.TFrame", background=C["sunken"])
    style.configure("NavGroup.TLabel", background=C["sunken"], foreground=C["faint"],
                    font=("TkDefaultFont", 8, "bold"), padding=(14, 12, 8, 3))
    style.configure("Nav.TButton", background=C["sunken"], bordercolor=C["sunken"],
                    lightcolor=C["sunken"], darkcolor=C["sunken"], anchor="w",
                    padding=(16, 6), foreground=C["text"])
    style.map("Nav.TButton", background=[("active", C["nav_active"])],
              bordercolor=[("active", C["nav_active"])],
              lightcolor=[("active", C["nav_active"])], darkcolor=[("active", C["nav_active"])])
    style.configure("NavOn.TButton", background=C["surface"], bordercolor=C["surface"],
                    lightcolor=C["surface"], darkcolor=C["surface"], anchor="w",
                    padding=(16, 6), foreground=C["accent"],
                    font=("TkDefaultFont", 10, "bold"))
    style.map("NavOn.TButton", background=[("active", C["surface"])])
    style.configure("Brand.TLabel", background=C["sunken"], font="CCHeading",
                    padding=(14, 12, 8, 2))

    # Tables.
    rowh = max(22, tkfont.nametofont("TkDefaultFont").metrics("linespace") + 8)
    style.configure("Treeview", background=C["surface"], fieldbackground=C["surface"],
                    bordercolor=C["border"], rowheight=rowh, lightcolor=C["surface"],
                    darkcolor=C["surface"])
    style.map("Treeview", background=[("selected", C["select"])],
              foreground=[("selected", C["text"])])
    style.configure("Treeview.Heading", background=C["sunken"], foreground=C["muted"],
                    font=("TkDefaultFont", 9, "bold"), relief="flat", padding=(6, 4),
                    bordercolor=C["border"], lightcolor=C["sunken"], darkcolor=C["sunken"])
    style.map("Treeview.Heading", background=[("active", C["nav_active"])])
    style.configure("Vertical.TScrollbar", background=C["sunken"], troughcolor=C["bg"],
                    bordercolor=C["bg"], arrowcolor=C["muted"], lightcolor=C["sunken"],
                    darkcolor=C["sunken"])
    style.configure("Horizontal.TScrollbar", background=C["sunken"], troughcolor=C["bg"],
                    bordercolor=C["bg"], arrowcolor=C["muted"], lightcolor=C["sunken"],
                    darkcolor=C["sunken"])
    return style


# ---------------------------------------------------------------- tooltips


class Tooltip:
    """
    Hover help for one widget. Shown after a short pause, hidden on leave or
    click. `text` may be a callable, read each time it is shown — for tips that
    say why a control is greyed out right now.

    Disabled widgets still get <Enter>, so a greyed-out button can say what it
    is waiting for — the moment a person most needs telling.
    """

    DELAY = 450
    WRAP = 360

    def __init__(self, widget, text, delay=None):
        self.widget = widget
        self.text = text
        self.delay = self.DELAY if delay is None else delay
        self._job = None
        self._win = None
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self._leave, add="+")
        widget.bind("<ButtonPress>", self._leave, add="+")
        widget.bind("<Destroy>", self._leave, add="+")
        widget._cc_tooltip = self                           # noqa: SLF001

    def _enter(self, _e=None):
        self._cancel()
        self._job = self.widget.after(self.delay, self.show)

    def _leave(self, _e=None):
        self._cancel()
        self.hide()

    def _cancel(self):
        if self._job:
            try:
                self.widget.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def show(self):
        text = self.text() if callable(self.text) else self.text
        if not text:
            return
        self.hide()
        try:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        except tk.TclError:
            return
        win = tk.Toplevel(self.widget)
        win.wm_overrideredirect(True)
        try:
            win.wm_attributes("-topmost", True)
        except tk.TclError:
            pass
        lbl = tk.Label(win, text=text, justify="left", wraplength=self.WRAP,
                       background=C["tip_bg"], foreground=C["tip_fg"], padx=10, pady=7,
                       font="TkTooltipFont", borderwidth=0)
        lbl.pack()
        win.update_idletasks()
        # Keep it on screen: flip above the widget near the bottom edge, pull
        # left near the right edge.
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        if x + w > sw - 8:
            x = max(8, sw - w - 8)
        if y + h > sh - 8:
            y = self.widget.winfo_rooty() - h - 6
        win.wm_geometry(f"+{x}+{y}")
        self._win = win

    def hide(self):
        if self._win is not None:
            try:
                self._win.destroy()
            except tk.TclError:
                pass
            self._win = None


def tip(widget, text, **kw):
    """Attach a tooltip and return the widget, so it can wrap a constructor."""
    existing = getattr(widget, "_cc_tooltip", None)
    if existing is not None:
        existing.text = text
        return widget
    Tooltip(widget, text, **kw)
    return widget


def _text_of(w):
    try:
        return str(w.cget("text"))
    except (tk.TclError, AttributeError):
        return ""


FIELD_CLASSES = ("TEntry", "TCombobox", "TSpinbox", "Entry", "Spinbox")


def walk(widget):
    """Every widget under `widget`, in creation order, depth first."""
    for child in widget.winfo_children():
        yield child
        yield from walk(child)


def attach_tips(container, tips, primary=(), subtle=(), styles=None):
    """
    Put tooltips on a page built without them, and lift its buttons into the
    three layers.

    `tips` maps what a control SHOWS to what it does:

    * a button, checkbox, radio or label's text, exactly — ``"Add folder…"``;
    * ``"^prefix"`` — any text starting with it, for labels with a number or
      path in them;
    * a label's text also covers the entry, list or spinbox that follows it in
      the same row, so hovering either explains the field.

    `primary` and `subtle` are button texts to restyle as Accent / Ghost;
    `styles` maps any other button text to a style name.
    Returns how many widgets got a tip, so a test can see a page still matches
    its catalogue.
    """
    exact = {k: v for k, v in tips.items() if not k.startswith("^")}
    prefix = [(k[1:], v) for k, v in tips.items() if k.startswith("^")]

    def lookup(text):
        if not text:
            return None
        if text in exact:
            return exact[text]
        for p, v in prefix:
            if text.startswith(p):
                return v
        return None

    n = 0
    for parent in [container] + list(walk(container)):
        pending = None
        for w in parent.winfo_children():
            cls = w.winfo_class()
            text = _text_of(w)
            found = lookup(text)
            if found:
                tip(w, found)
                n += 1
            elif cls in FIELD_CLASSES and pending:
                tip(w, pending)
                n += 1
            if cls in ("TButton", "Button") and text:
                if styles and text in styles:
                    w.configure(style=styles[text])
                elif text in primary:
                    w.configure(style="Accent.TButton")
                elif text in subtle:
                    w.configure(style="Ghost.TButton")
            # A label's tip carries to the one field after it, and no further.
            pending = found if cls in ("TLabel", "Label") else None
    return n


# ---------------------------------------------------------------- building blocks


def more_menu(parent, items, text="More", tip_text=None):
    """
    A menu button holding the actions used now and then.

    `items` is a list of (label, command) — or None for a separator, or
    (label, [sub items]) for a cascade. Keeps a page's row of buttons to the
    few that are used every time.
    """
    mb = ttk.Menubutton(parent, text=text)
    menu = tk.Menu(mb, tearoff=False)

    def fill(m, entries):
        for item in entries:
            if item is None:
                m.add_separator()
            elif isinstance(item[1], (list, tuple)):
                sub = tk.Menu(m, tearoff=False)
                fill(sub, item[1])
                m.add_cascade(label=item[0], menu=sub)
            else:
                m.add_command(label=item[0], command=item[1])
    fill(menu, items)
    mb.configure(menu=menu)
    if tip_text:
        tip(mb, tip_text)
    return mb


def help_icon(parent, text, title="About this"):
    """
    A small ⓘ that explains on hover and opens the full text on click — for
    a field whose tooltip would be too long.
    """
    lbl = ttk.Label(parent, text="ⓘ", foreground=C["accent"], cursor="hand2")
    first = text.strip().split("\n\n")[0]
    tip(lbl, first + ("\n\nClick for more." if first != text.strip() else ""))
    lbl.bind("<Button-1>", lambda _e: info_dialog(parent, title, text))
    return lbl


def info_dialog(parent, title, text, width=560):
    """A plain, scrollable, closable read-only text window."""
    top = tk.Toplevel(parent)
    top.title(title)
    top.transient(parent.winfo_toplevel())
    top.configure(background=C["bg"])
    frame = ttk.Frame(top, padding=14)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=title, font="CCHeading").pack(anchor="w", pady=(0, 8))
    box = tk.Text(frame, wrap="word", width=width // 8, height=min(28, text.count("\n") + 6),
                  font="TkDefaultFont", padx=10, pady=8)
    box.insert("1.0", text.strip())
    box.configure(state="disabled")
    box.pack(fill="both", expand=True)
    ttk.Button(frame, text="Close", command=top.destroy, style="Accent.TButton")\
        .pack(anchor="e", pady=(10, 0))
    top.bind("<Escape>", lambda _e: top.destroy())
    top.focus_set()
    return top


def stat_card(parent, label, value="—", sub="", width=150, compact=False):
    """
    A boxed figure: small label, big number, smaller note. Returns the frame;
    its `.set(value, sub=None, colour=None)` updates it.
    """
    f = ttk.Frame(parent, style="Card.TFrame", padding=(12, 8))
    ttk.Label(f, text=label, style="CardMuted.TLabel").pack(anchor="w")
    v = ttk.Label(f, text=value, style="CardValue.TLabel" if compact else "CardBig.TLabel")
    v.pack(anchor="w")
    s = ttk.Label(f, text=sub, style="CardMuted.TLabel", wraplength=width)
    s.pack(anchor="w")

    def set_(value, sub=None, colour=None):
        v.configure(text=value, foreground=colour or C["text"])
        if sub is not None:
            s.configure(text=sub)
    f.set = set_
    return f


def log_pane(parent, height=8):
    """
    A read-only log with a scrollbar and a small toolbar (Copy, Clear).
    Returns (frame, say) — `say(text, level=None)` appends a line; level
    'ok' / 'warn' / 'bad' colours it.
    """
    wrap = ttk.Frame(parent)
    wrap.columnconfigure(0, weight=1)
    wrap.rowconfigure(0, weight=1)
    text = tk.Text(wrap, height=height, wrap="word", state="disabled")
    text.grid(row=0, column=0, sticky="nsew")
    sb = ttk.Scrollbar(wrap, orient="vertical", command=text.yview)
    sb.grid(row=0, column=1, sticky="ns")
    text.configure(yscrollcommand=sb.set)
    for level in ("ok", "warn", "bad"):
        text.tag_configure(level, foreground=C[level])

    def say(line, level=None):
        text.configure(state="normal")
        text.insert("end", line + "\n", (level,) if level else ())
        text.see("end")
        text.configure(state="disabled")
    wrap.text = text
    return wrap, say


def copy_text(widget, text):
    widget.clipboard_clear()
    widget.clipboard_append(text)


def tree(parent, columns, height=10, selectmode="browse", stripes=True):
    """
    A Treeview with a scrollbar, from [(key, heading, width, anchor/stretch)].
    Each column spec is (key, heading, width) plus optional dict of options.
    Headings sort the rows when clicked. Returns (frame, treeview).
    """
    wrap = ttk.Frame(parent)
    wrap.columnconfigure(0, weight=1)
    wrap.rowconfigure(0, weight=1)
    keys = [c[0] for c in columns]
    tv = ttk.Treeview(wrap, columns=keys, show="headings", height=height,
                      selectmode=selectmode)
    for spec in columns:
        key, heading, width = spec[:3]
        opts = spec[3] if len(spec) > 3 else {}
        tv.heading(key, text=heading, command=lambda k=key: sort_by(tv, k))
        tv.column(key, width=width, anchor=opts.get("anchor", "w"),
                  stretch=opts.get("stretch", False), minwidth=30)
    tv.grid(row=0, column=0, sticky="nsew")
    sb = ttk.Scrollbar(wrap, orient="vertical", command=tv.yview)
    sb.grid(row=0, column=1, sticky="ns")
    tv.configure(yscrollcommand=sb.set)
    if stripes:
        tv.tag_configure("odd", background="#f8f9fb")
    return wrap, tv


def _sort_key(v):
    s = str(v).replace("£", "").replace("$", "").replace("€", "").replace(",", "").strip()
    try:
        return (0, float(s.rstrip("%")), "")
    except ValueError:
        return (1, 0.0, s.lower())


def sort_by(tv, key):
    """Sort a Treeview by a column; clicking again reverses."""
    rev = getattr(tv, "_cc_sort", None) == (key, False)
    rows = [(tv.set(i, key), i) for i in tv.get_children("")]
    rows.sort(key=lambda r: _sort_key(r[0]), reverse=rev)
    for n, (_, iid) in enumerate(rows):
        tv.move(iid, "", n)
    tv._cc_sort = (key, rev)                                # noqa: SLF001
    for col in tv["columns"]:
        text = tv.heading(col, "text").rstrip(" ▲▼")
        if col == key:
            text += " ▼" if rev else " ▲"
        tv.heading(col, text=text)


def restripe(tv):
    for n, iid in enumerate(tv.get_children("")):
        tags = [t for t in tv.item(iid, "tags") if t != "odd"]
        if n % 2:
            tags.append("odd")
        tv.item(iid, tags=tags)


def segmented(parent, variable, options, command=None):
    """A row of radio buttons that reads as one control: [(value, label)]."""
    f = ttk.Frame(parent)
    for value, label in options:
        ttk.Radiobutton(f, text=label, value=value, variable=variable,
                        command=command, style="Seg.TRadiobutton").pack(side="left")
    return f


def money(v, symbol="£"):
    try:
        return f"{symbol}{float(v):,.2f}"
    except (TypeError, ValueError):
        return ""
