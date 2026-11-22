"""
The "Grade library" page: every card a person has graded, and the golden sets
chosen from them.

Grades arrive here on their own — from the Grade tab as each card is keyed,
and from conditioning review sheets when their grades are recorded or
written. This page is for looking back over them and for the one decision the
library cannot make: which cards go into a golden set, at what grade IN HAND.

The coverage strip says how far each game line's golden set is from what a
blind check needs (10 cards, 3 of them borderline; 30-50 in the set). Cards
the library thinks are borderline are marked, with the reason, because those
are the ones a blind check is short of.
"""

import os
import queue
import threading

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from . import audits, grading, library, ui

ALL = "All"
SHOW = (("all", "All cards"), ("golden", "Golden"), ("suggested", "Borderline?"),
        ("not", "Not golden"))
THUMB = (170, 238)


def _open(path):
    from .gui import _reveal
    _reveal(path)


class LibraryTab(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=10)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self.worker = None
        #: Set by the window: open a pick list on the Grade tab.
        self.open_in_grade = None
        self.events = queue.Queue()
        self.rows = {}
        self.photos = []
        self.selected = None
        self.face = tk.StringVar(value="whole")

        self._build_filters()
        self._build_coverage()
        self._build_body()
        self.bind("<Map>", lambda _e: self.refresh(), add="+")
        self._poll()

    # ------------------------------------------------------------ layout

    def _build_filters(self):
        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text="Find").grid(row=0, column=0, padx=(0, 6))
        self.search_var = tk.StringVar()
        e = ttk.Entry(bar, textvariable=self.search_var)
        e.grid(row=0, column=1, sticky="ew")
        ui.tip(e, "Filter by SKU, card name, batch or game line.")
        self.search_var.trace_add("write", lambda *_: self.refresh())
        self.line_var = tk.StringVar(value=ALL)
        self.line_box = ttk.Combobox(bar, textvariable=self.line_var, width=18,
                                     state="readonly", values=(ALL,))
        self.line_box.grid(row=0, column=2, padx=(8, 0))
        self.line_box.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        ui.tip(self.line_box, "Show one game line — each line has its own golden set.")
        self.grade_var = tk.StringVar(value=ALL)
        g = ttk.Combobox(bar, textvariable=self.grade_var, width=6, state="readonly",
                         values=(ALL,) + grading.GRADES)
        g.grid(row=0, column=3, padx=(6, 0))
        g.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        ui.tip(g, "Show one grade (the in-hand grade for golden cards, the standing grade "
                  "for the rest).")
        self.show_var = tk.StringVar(value="all")
        seg = ui.segmented(bar, self.show_var, SHOW, command=self.refresh)
        seg.grid(row=0, column=4, padx=(10, 0))
        ui.tip(seg, "Borderline? lists the cards the library thinks are close calls — "
                    "a blind check needs three of them in every ten.")
        blind = ui.more_menu(bar, [
            ("Start a blind check…", self.start_blind),
            ("Score a blind check…", self.score_blind),
            None,
            ("How graders are doing…", self.show_graders),
            ("Does the front score work yet?…", self.show_front),
        ], text="Blind checks", tip_text="Before a batch: grade 10 golden cards blind on the "
                                         "Grade tab, then score them against the grades they "
                                         "have in hand. Any overgrade stops the batch.")
        blind.grid(row=0, column=5, padx=(10, 0))
        imp = ttk.Button(bar, text="Import grades…", command=self.import_folder)
        imp.grid(row=0, column=6, padx=(6, 0))
        ui.tip(imp, "Bring in grades made before the library existed: picks up every "
                    "grades-*.csv and conditioning-review.csv under a folder you choose. "
                    "Safe to run twice.")
        more = ui.more_menu(bar, [
            ("Export every grade as CSV…", self.export),
            ("Re-read golden-set files", self.sync),
            None,
            ("Open golden-set folder", lambda: self._open_dir("golden")),
            ("Open library images folder", lambda: self._open_dir(library.IMAGE_DIR)),
        ], tip_text="Export, re-read golden files edited by hand, open folders.")
        more.grid(row=0, column=7, padx=(6, 0))

    def _build_coverage(self):
        self.coverage = ttk.Frame(self, style="Card.TFrame", padding=(12, 8))
        self.coverage.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ui.tip(self.coverage, f"How each game line's golden set is coming along. A blind check "
                              f"picks {audits.BLIND_N} cards, at least "
                              f"{audits.BLIND_BORDERLINE} of them borderline; the set should "
                              f"hold {library.GOLDEN_TARGET[0]}-{library.GOLDEN_TARGET[1]}.")
        self.counts = ttk.Label(self, style="Muted.TLabel")
        self.counts.grid(row=2, column=0, sticky="w", pady=(8, 4))

    def _build_body(self):
        pane = ttk.PanedWindow(self, orient="horizontal")
        pane.grid(row=3, column=0, sticky="nsew")
        left = ttk.Frame(pane)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        wrap, self.tree = ui.tree(left, (
            ("sku", "SKU", 80), ("card", "Card", 200, {"stretch": True}),
            ("line", "Line", 100), ("grade", "Grade", 56, {"anchor": "center"}),
            ("passes", "Passes", 52, {"anchor": "e"}),
            ("golden", "Golden", 70, {"anchor": "center"}),
            ("border", "Borderline", 76, {"anchor": "center"}),
            ("when", "Last graded", 130)), height=14)
        wrap.grid(row=0, column=0, sticky="nsew")
        for g, colour in ui.GRADE_COLOURS.items():
            self.tree.tag_configure(g, foreground=colour)
        self.tree.tag_configure("golden", font=("TkDefaultFont", 10, "bold"))
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._picked())
        self.tree.bind("<Double-1>", lambda _e: self.promote())
        ui.tip(self.tree, "Every card graded by hand. Select one for its images and grade "
                          "history; double-click to add it to its golden set.")
        pane.add(left, weight=3)

        right = ttk.Frame(pane, padding=(12, 0, 0, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(5, weight=1)
        self.head = ttk.Label(right, style="Heading.TLabel", text="Select a card",
                              wraplength=480, justify="left")
        self.head.grid(row=0, column=0, sticky="w")
        self.sub = ttk.Label(right, style="Muted.TLabel", wraplength=480, justify="left")
        self.sub.grid(row=1, column=0, sticky="w", pady=(2, 6))
        faces = ui.segmented(right, self.face, (("whole", "Front & back"),
                                                ("crops", "Crop sheets")),
                             command=self._draw_images)
        faces.grid(row=2, column=0, sticky="w")
        ui.tip(faces, "The library's own copies of the card's images — kept even after the "
                      "batch folder is cleared. Click a picture to open it full size.")
        pics = ttk.Frame(right)
        pics.grid(row=3, column=0, sticky="w", pady=(4, 8))
        self.pics = []
        for i in range(2):
            c = tk.Canvas(pics, width=THUMB[0], height=THUMB[1], background="#121212",
                          highlightthickness=0, cursor="hand2")
            c.grid(row=0, column=i, padx=(0, 8))
            self.pics.append(c)

        actions = ttk.Frame(right)
        actions.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        self.promote_btn = ttk.Button(actions, text="Add to golden set…",
                                      style="Accent.TButton", command=self.promote,
                                      state="disabled")
        self.promote_btn.pack(side="left")
        ui.tip(self.promote_btn, lambda: self._promote_tip())
        self.demote_btn = ttk.Button(actions, text="Remove from golden set",
                                     command=self.demote, state="disabled")
        self.demote_btn.pack(side="left", padx=(6, 0))
        ui.tip(self.demote_btn, "Take the card out of its golden set. Its grades stay here.")
        self.card_more = ui.more_menu(actions, [
            ("Name the card…", self.rename),
            ("Move to another game line…", self.reline),
            None,
            ("Delete from the library", self.delete),
        ], tip_text="Correct the card's name or line, or forget it.")
        self.card_more.pack(side="left", padx=(6, 0))

        hist = ttk.LabelFrame(right, text="Grades", padding=6)
        hist.grid(row=5, column=0, sticky="nsew")
        hist.columnconfigure(0, weight=1)
        hist.rowconfigure(0, weight=1)
        wrap, self.hist = ui.tree(hist, (
            ("when", "When", 128), ("source", "From", 90), ("who", "Grader", 70),
            ("pass", "Pass", 44, {"anchor": "e"}), ("grade", "Grade", 110),
            ("notes", "Notes", 220, {"stretch": True})), height=5, stripes=False)
        wrap.grid(row=0, column=0, sticky="nsew")
        ui.tip(self.hist, "Every grade this card has had. When passes disagree, the worse "
                          "one stands.")
        self.advice = ttk.Label(right, wraplength=480, justify="left")
        self.advice.grid(row=6, column=0, sticky="w", pady=(8, 0))
        pane.add(right, weight=2)

    # ------------------------------------------------------------ data

    def refresh(self):
        show = self.show_var.get()
        line = self.line_var.get()
        grade = self.grade_var.get()
        try:
            rows = library.cards(line=None if line == ALL else line,
                                 grade=None if grade == ALL else grade,
                                 golden={"golden": True, "not": False}.get(show),
                                 borderline=show == "suggested",
                                 search=self.search_var.get())
            st = library.stats()
        except Exception as exc:                              # noqa: BLE001
            self.counts.configure(text=f"Could not read the grade library: {exc}",
                                  foreground=ui.C["bad"])
            return
        if show == "suggested":
            rows = [r for r in rows if not r["golden"]]
        keep = self.selected
        self.tree.delete(*self.tree.get_children())
        self.rows = {}
        for r in rows:
            grade = r["true_grade"] or r["standing"]
            golden = f"★ {r['true_grade'] or ''}" if r["golden"] else ""
            border = "yes" if r["borderline"] else ("?" if r["suggest_borderline"] else "")
            tags = [grade] + (["golden"] if r["golden"] else [])
            self.tree.insert("", "end", iid=r["sku"], tags=tags, values=(
                r["sku"], r.get("card") or "", r.get("line") or "", grade, r["passes"],
                golden, border, (r["last_graded"] or "").replace("T", " ")))
            self.rows[r["sku"]] = r
        ui.restripe(self.tree)
        lines = sorted(set(st["by_line"]) | {"pokemon-en"})
        self.line_box.configure(values=(ALL,) + tuple(lines))
        by = "  ·  ".join(f"{g} {st['by_grade'][g]}" for g in grading.GRADES
                          if st["by_grade"].get(g))
        self.counts.configure(
            foreground=ui.C["muted"],
            text=f"{st['cards']} card(s) graded by hand" + (f"   ({by})" if by else "")
                 + f"   ·   showing {len(rows)}"
                 + (f"   ·   {st['suggested']} look borderline and are not golden yet"
                    if st["suggested"] else ""))
        self._draw_coverage(st)
        if keep and self.tree.exists(keep):
            self.tree.selection_set(keep)
            self.tree.see(keep)
        elif not rows:
            self._empty()

    def _draw_coverage(self, st):
        for w in self.coverage.winfo_children():
            w.destroy()
        ttk.Label(self.coverage, text="Golden sets", style="CardHead.TLabel")\
            .grid(row=0, column=0, sticky="w", padx=(0, 16))
        lines = sorted(set(st["golden"]) | {"pokemon-en"} | set(st["by_line"]) - {"unknown"})
        for i, line in enumerate(lines, 1):
            g = st["golden"].get(line, {"cards": 0, "borderline": 0})
            ready = g["cards"] >= audits.BLIND_N and g["borderline"] >= audits.BLIND_BORDERLINE
            col = ttk.Frame(self.coverage, style="Card.TFrame")
            col.grid(row=0, column=i, sticky="w", padx=(0, 18))
            ttk.Label(col, text=line, style="CardHead.TLabel").grid(row=0, column=0, sticky="w")
            bar = ttk.Progressbar(col, length=160, maximum=library.GOLDEN_TARGET[0],
                                  value=min(g["cards"], library.GOLDEN_TARGET[0]))
            bar.grid(row=1, column=0, sticky="w", pady=2)
            note = (f"{g['cards']}/{library.GOLDEN_TARGET[0]} cards · {g['borderline']} "
                    f"borderline · " + ("blind check ready ✓" if ready else
                                        "not enough for a blind check"))
            ttk.Label(col, text=note, style="CardMuted.TLabel",
                      foreground=ui.C["ok"] if ready else ui.C["muted"])\
                .grid(row=2, column=0, sticky="w")

    def _empty(self):
        self.selected = None
        self.head.configure(text="No graded cards here yet")
        self.sub.configure(text="Grades arrive on their own as you grade on the Grade tab, or "
                                "when a review sheet's grades are recorded on Condition a CSV. "
                                "Grades made before the library existed: Import grades….")
        self.promote_btn.configure(state="disabled")
        self.demote_btn.configure(state="disabled")
        self.hist.delete(*self.hist.get_children())
        self.advice.configure(text="")
        for c in self.pics:
            c.delete("all")

    def _picked(self):
        sel = self.tree.selection()
        if not sel:
            return
        self.selected = sel[0]
        r = self.rows[self.selected]
        self.head.configure(text=f"{r['sku']}  ·  {r.get('card') or 'card name not known'}")
        bits = [r.get("line") or "line unknown", f"batch {r['batch']}" if r.get("batch") else ""]
        if r["golden"]:
            bits.append(f"GOLDEN — {r['true_grade']} in hand"
                        + (", borderline" if r["borderline"] else ""))
        else:
            bits.append(f"standing grade {r['standing'] or '—'}")
        self.sub.configure(text="  ·  ".join(b for b in bits if b))
        self.promote_btn.configure(state="normal",
                                   text="Change golden grade…" if r["golden"]
                                   else "Add to golden set…")
        self.demote_btn.configure(state="normal" if r["golden"] else "disabled")
        self.hist.delete(*self.hist.get_children())
        for g in reversed(r["grades"]):
            keyed = (g["keyed"] or "").upper()
            grade = g["grade"] + (f" (keyed {keyed})" if keyed and keyed != g["grade"] else "")
            notes = "; ".join(x for x in (g["reasons"], g["notes"]) if x)
            self.hist.insert("", "end", values=(
                (g["graded_at"] or "").replace("T", " "),
                library.SOURCES.get(g["source"], g["source"]), g["grader"] or "",
                g["pass_no"] or "", grade, notes))
        advice = []
        if r["suggest_borderline"]:
            advice.append("Looks borderline: " + "; ".join(r["suggest_borderline"]) + ".")
        last = next((g for g in reversed(r["grades"]) if g["source"] != "golden-blind"
                     and (g["back_points"] or g["back_corners"] or g["worst_corner_pct"]
                          is not None)), None)
        if last:
            counts, near = library.similar(
                r.get("line"), back_points=library._int(last["back_points"]),
                back_corners=library._int(last["back_corners"]),
                worst_corner_pct=last["worst_corner_pct"], exclude=r["sku"])
            if near:
                advice.append("Cards that measured like this one were graded: " + ", ".join(
                    f"{g} {counts[g]}" for g in grading.GRADES if counts.get(g))
                    + f"  (nearest {len(near)}).")
        self.advice.configure(text="\n".join(advice),
                              foreground=ui.C["warn"] if r["suggest_borderline"] else
                              ui.C["muted"])
        self._draw_images()

    def _draw_images(self):
        if not self.selected or self.selected not in self.rows:
            return
        r = self.rows[self.selected]
        roles = ("front", "back") if self.face.get() == "whole" else ("front_crops",
                                                                       "back_crops")
        self.photos = []
        for canvas, role in zip(self.pics, roles):
            canvas.delete("all")
            path = r.get(role) or r.get("src_" + role) or ""
            canvas.unbind("<Button-1>")
            if not path or not os.path.exists(path):
                canvas.create_text(THUMB[0] / 2, THUMB[1] / 2, fill="#888", justify="center",
                                   text=f"no {role.replace('_', ' ')} image")
                continue
            try:
                with Image.open(path) as im:
                    im = im.convert("RGB")
                    im.thumbnail(THUMB, Image.LANCZOS)
                    photo = ImageTk.PhotoImage(im)
            except Exception:                                 # noqa: BLE001
                canvas.create_text(THUMB[0] / 2, THUMB[1] / 2, fill="#888", text="unreadable")
                continue
            self.photos.append(photo)
            canvas.create_image(THUMB[0] / 2, THUMB[1] / 2, image=photo)
            canvas.bind("<Button-1>", lambda _e, p=path: _open(p))

    # ------------------------------------------------------------ actions

    def _promote_tip(self):
        if not self.selected:
            return "Select a card first."
        return ("Put this card in its line's golden set, at the grade it has IN HAND — "
                "check the physical card first. The blind checks read the set from "
                "golden/<line>.csv in the settings folder.")

    def promote(self):
        if not self.selected:
            return
        r = self.rows[self.selected]
        dlg = tk.Toplevel(self)
        dlg.title("Golden set")
        dlg.transient(self.winfo_toplevel())
        dlg.grab_set()
        f = ttk.Frame(dlg, padding=16)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"{r['sku']} → golden set {r.get('line') or 'pokemon-en'}",
                  style="Heading.TLabel").grid(row=0, column=0, columnspan=6, sticky="w")
        ttk.Label(f, style="Muted.TLabel", wraplength=420, justify="left", text=(
            "A golden card's grade is its grade IN HAND, checked with the card in front of "
            "you — not the screen's. Blind checks score graders against it, so it has to be "
            "right.")).grid(row=1, column=0, columnspan=6, sticky="w", pady=(4, 10))
        grade = tk.StringVar(value=r["true_grade"] or r["standing"] or "NM")
        ttk.Label(f, text="Grade in hand").grid(row=2, column=0, sticky="w")
        for i, g in enumerate(grading.GRADES, 1):
            ttk.Radiobutton(f, text=g, value=g, variable=grade).grid(row=2, column=i,
                                                                    sticky="w")
        border = tk.BooleanVar(value=bool(r["borderline"] or r["suggest_borderline"]))
        cb = ttk.Checkbutton(f, text="Borderline — a close call between two grades",
                             variable=border)
        cb.grid(row=3, column=0, columnspan=6, sticky="w", pady=(8, 0))
        if r["suggest_borderline"]:
            ttk.Label(f, style="Warn.TLabel", wraplength=420, justify="left",
                      text="Suggested because: " + "; ".join(r["suggest_borderline"]))\
                .grid(row=4, column=0, columnspan=6, sticky="w")
        ttk.Label(f, text="Notes").grid(row=5, column=0, sticky="w", pady=(8, 0))
        notes = tk.StringVar(value=r.get("golden_notes") or "")
        ttk.Entry(f, textvariable=notes, width=48).grid(row=5, column=1, columnspan=5,
                                                        sticky="ew", pady=(8, 0))

        def ok():
            try:
                path = library.promote(r["sku"], grade.get(), border.get(), notes.get())
            except Exception as exc:                          # noqa: BLE001
                messagebox.showerror("Golden set", str(exc), parent=dlg)
                return
            dlg.destroy()
            self.counts.configure(text=f"{r['sku']} is in the golden set ({path}).",
                                  foreground=ui.C["ok"])
            self.refresh()
        row = ttk.Frame(f)
        row.grid(row=6, column=0, columnspan=6, sticky="e", pady=(14, 0))
        ttk.Button(row, text="Cancel", style="Ghost.TButton", command=dlg.destroy)\
            .pack(side="right")
        ttk.Button(row, text="Add to golden set", style="Accent.TButton", command=ok)\
            .pack(side="right", padx=(0, 6))
        dlg.bind("<Return>", lambda _e: ok())
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

    def demote(self):
        if self.selected and messagebox.askyesno(
                "Golden set", f"Take {self.selected} out of its golden set?"):
            library.demote(self.selected)
            self.refresh()

    # ------------------------------------------------------------ blind checks

    def _line_choice(self):
        line = self.line_var.get()
        return line if line != ALL else "pokemon-en"

    def start_blind(self):
        line = self._line_choice()
        try:
            path = library.start_blind(line)
        except ValueError as exc:
            messagebox.showinfo("Blind check", f"{line}: {exc}.\n\nAdd more cards to the "
                                               "golden set (Borderline? lists good "
                                               "candidates), or pick another line in the "
                                               "filter above.")
            return
        if self.open_in_grade:
            self.open_in_grade(path)
        messagebox.showinfo("Blind check", f"{audits.BLIND_N} golden cards from {line} are open "
                                           "on the Grade tab, true grades hidden. Grade them "
                                           "as usual, then come back to Blind checks → Score "
                                           "a blind check.")

    def score_blind(self):
        checks = library.blind_checks()
        if not checks:
            messagebox.showinfo("Blind check", "No blind check started yet — Blind checks → "
                                               "Start a blind check.")
            return
        path, line, graded, total = checks[0]
        if graded < total and not messagebox.askyesno(
                "Blind check", f"Only {graded} of {total} cards in the latest blind check "
                               f"({line}) are graded — an ungraded card counts as a stop.\n\n"
                               "Score it anyway?"):
            return
        stop, rows = library.score_blind(path)
        lines = [f"{'STOP — do not start the batch' if stop else 'PASS — the batch can start'}"
                 f"   ({line}, {os.path.basename(path)})", ""]
        for r in rows:
            lines.append(f"{r['result']:<11} ours {r['our_grade'] or '—':<4} in hand "
                         f"{r['true_grade']:<4} {r['id']}  {r.get('card', '')}")
        over = sum(1 for r in rows if r["result"] == "OVERGRADE")
        lines += ["", f"{over} overgrade(s). Recorded in the audit log; disagreements in the "
                      "error log."]
        ui.info_dialog(self, "Blind check result", "\n".join(lines))
        self.refresh()

    def show_front(self):
        line = self.line_var.get()
        ui.info_dialog(self, "Front-border score", "\n".join(
            library.front_calibration(None if line == ALL else line)))

    def show_graders(self):
        st = library.grader_stats()
        if not st:
            messagebox.showinfo("Graders", "No grades with a grader's name yet.")
            return
        lines = ["Against golden truth (blind checks) and against each other (cards with "
                 "passes from two graders).", ""]
        for name, e in sorted(st.items()):
            parts = []
            if e["blind"]:
                parts.append(f"blind {e['match']}/{e['blind']} matched, "
                             f"{e['over']} OVERGRADED, {e['under']} undergraded")
            if e["passes"]:
                parts.append(f"agreed with the standing grade on {e['agreed']}/{e['passes']} "
                             f"shared cards ({e['agreed'] / e['passes']:.0%})")
            lines.append(f"{name}: " + ("; ".join(parts) or "no blind checks or shared "
                                                            "cards yet"))
        lines += ["", "An overgrade (our grade better than the card's in-hand grade) is the "
                      "expensive mistake: it is what gets returned."]
        ui.info_dialog(self, "How graders are doing", "\n".join(lines))

    def _ask(self, title, prompt, initial=""):
        from tkinter import simpledialog
        return simpledialog.askstring(title, prompt, initialvalue=initial, parent=self)

    def rename(self):
        if not self.selected:
            return
        name = self._ask("Card name", "What is this card? (as a listing title)",
                         self.rows[self.selected].get("card") or "")
        if name is not None:
            library.set_card(self.selected, card=name.strip())
            self.refresh()

    def reline(self):
        if not self.selected:
            return
        line = self._ask("Game line", "pokemon-en, pokemon-jp, pokemon-jp-vintage, yugioh "
                                      "or mtg", self.rows[self.selected].get("line") or "")
        if line:
            library.set_card(self.selected, line=line.strip())
            self.refresh()

    def delete(self):
        if not self.selected:
            return
        r = self.rows[self.selected]
        if r["golden"]:
            messagebox.showinfo("Grade library", "Remove it from its golden set first.")
            return
        if messagebox.askyesno("Grade library", f"Forget {r['sku']} and its "
                                                f"{len(r['grades'])} grade(s)?"):
            library.delete(r["sku"])
            self.selected = None
            self.refresh()

    def import_folder(self):
        folder = filedialog.askdirectory(title="Folder to search for grades-*.csv and "
                                               "conditioning-review.csv")
        if not folder or (self.worker and self.worker.is_alive()):
            return
        self.counts.configure(text="Looking for grades…", foreground=ui.C["muted"])

        def run():
            try:
                files, n = library.import_folder(
                    folder, progress=lambda i, t, p: self.events.put(
                        ("progress", f"Importing {i} of {t}: {os.path.basename(p)}")))
                self.events.put(("done", f"Imported {n} grade(s) from {files} file(s)."))
            except Exception as exc:                          # noqa: BLE001
                self.events.put(("done", f"Import failed: {exc}"))
        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def export(self):
        path = filedialog.asksaveasfilename(title="Export grades", defaultextension=".csv",
                                            initialfile="grade-library.csv",
                                            filetypes=[("CSV", "*.csv")])
        if path:
            n = library.export_csv(path)
            self.counts.configure(text=f"{n} grade(s) written to {path}",
                                  foreground=ui.C["ok"])

    def sync(self):
        n = library.sync_golden()
        self.refresh()
        self.counts.configure(text=f"Read {n} golden card(s) from the golden-set files.",
                              foreground=ui.C["ok"])

    def _open_dir(self, name):
        path = os.path.join(library.settings_dir(), name)
        os.makedirs(path, exist_ok=True)
        _open(path)

    def _poll(self):
        try:
            while True:
                kind, text = self.events.get_nowait()
                self.counts.configure(text=text, foreground=ui.C["muted"])
                if kind == "done":
                    self.refresh()
                    self.counts.configure(text=text, foreground=ui.C["ok"])
        except queue.Empty:
            pass
        self.after(150, self._poll)
