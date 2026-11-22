"""
The grade library, the market evidence store, and — where there is a display —
the whole window built page by page.

What it pins down:

* every human grade lands in the library once, however often it is recorded,
  and a card's images survive the batch folder being deleted;
* a promoted card is written to the golden-set file the blind checks read,
  with the library's own copies of its images;
* machine "grades" (HOLD, a bare second opinion) never become library grades;
* leaving a sale out by hand reprices under the same rules, and putting it
  back restores the price exactly;
* sales imported from a Terapeak-style CSV price the same way as Comp Finder's;
* every tooltip in the guide still finds the control it was written for — a
  renamed button would otherwise lose its explanation without anyone noticing;
* Ctrl+1 switches page without grading the card on the Grade tab.
"""

import csv
import os
import shutil
import sys
import tempfile
import time

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def library_checks(work):
    from cardcropper import audits, conditioning, library

    scans = os.path.join(work, "batch")
    os.makedirs(scans)
    imgs = {}
    for role in library.ROLES:
        p = os.path.join(scans, f"{role}.jpg")
        Image.new("RGB", (2400, 3300), (30, 40, 120)).save(p)
        imgs[role] = p

    library.record("KA1", "NM", "grade-tab", images=imgs, keyed="NM", pass_no="1",
                   origin="g.csv", back_points=2, back_corners=0, line="pokemon-en",
                   batch="KA", card="Lugia 78/124")
    library.record("KA1", "NM", "grade-tab", images=imgs, keyed="NM", pass_no="1",
                   origin="g.csv", back_points=2, line="pokemon-en")
    check(len(library.grades_for("KA1")) == 1, "the same pass recorded twice was doubled")
    library.record("KA1", "LP", "grade-tab", pass_no="2", origin="g.csv", line="pokemon-en")
    check(library.record("KA2", "HOLD", "grade-tab") is None, "HOLD is not a grade")
    check(library.record("", "NM", "grade-tab") is None, "a grade with no SKU was kept")

    c = library.cards()[0]
    check(c["standing"] == "LP", f"the worse pass should stand, got {c['standing']}")
    check(any("disagreed" in r for r in c["suggest_borderline"]),
          f"disagreeing passes not suggested as borderline: {c['suggest_borderline']}")
    check(any("one short" in r for r in c["suggest_borderline"]),
          "2 wear points (one short of the NM limit) not suggested as borderline")
    with Image.open(c["front"]) as im:
        check(max(im.size) <= library.SNAPSHOT_MAX, "whole-card snapshot was not shrunk")

    shutil.rmtree(scans)                     # the batch folder goes; the record stays
    gpath = library.promote("KA1", "LP", borderline=True, notes="checked in hand")
    rows = audits.read_golden(gpath)
    check(len(rows) == 1 and rows[0]["true_grade"] == "LP" and rows[0]["borderline"] == "yes",
          f"golden row wrong: {rows}")
    check(os.path.exists(rows[0]["front"]) and rows[0]["front"].startswith(
        library.settings_dir()), "golden row should point at the library's own images")
    library.promote("KA1", "MP")             # promoting again replaces, never duplicates
    check(len(audits.read_golden(gpath)) == 1, "a re-promoted card was duplicated")
    library.demote("KA1")
    check(not audits.read_golden(gpath), "demote left the card in the golden file")
    check(library.cards()[0]["grades"], "demote must keep the card's grades")

    # A review sheet: grades on the scale only, and recording twice adds nothing.
    out = os.path.join(work, "cond")
    os.makedirs(os.path.join(out, "cards", "KB1"))
    Image.new("RGB", (700, 980), (9, 9, 9)).save(os.path.join(out, "cards", "KB1", "front.jpg"))
    review = os.path.join(out, "conditioning-review.csv")
    with open(review, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=conditioning.REVIEW_FIELDS)
        w.writeheader()
        w.writerow({"sku": "KB1", "title": "Pikachu 58/102", "grade": "MP",
                    "flaw_note": "edge wear", "looked": "yes", "confidence": "low"})
        w.writerow({"sku": "KB2", "title": "x", "grade": "HOLD"})
        w.writerow({"sku": "KB3", "title": "y", "grade": ""})
    with open(os.path.join(out, "triage.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["sku", "worst_corner_pct", "wear_total_pct"])
        w.writeheader()
        w.writerow({"sku": "KB1", "worst_corner_pct": 4.2, "wear_total_pct": 1.1})
    check(library.import_review(review) == 1, "only the one real grade should be recorded")
    library.import_review(review)
    check(len(library.grades_for("KB1")) == 1, "a review sheet recorded twice was doubled")
    kb1 = next(c for c in library.cards() if c["sku"] == "KB1")
    check(kb1["grades"][0]["worst_corner_pct"] == 4.2, "triage score not carried in")
    check(any("confidence" in r for r in kb1["suggest_borderline"]),
          "a low-confidence grade should be suggested as borderline")
    counts, near = library.similar("pokemon-en", back_points=2, back_corners=0)
    check(near and near[0]["sku"] == "KA1", f"nearest card wrong: {[c['sku'] for c in near]}")

    library.record_later("KC1", "HP", "grade-tab", line="yugioh")
    library.flush()
    check(any(c["sku"] == "KC1" for c in library.cards(line="yugioh")),
          "a background record never landed")
    check(library.export_csv(os.path.join(work, "all.csv")) >= 4, "export lost grades")


def market_checks(work):
    from cardcropper import market, pricing

    sold = [{"title": "Lugia 78/124 Fates Collide", "price": p, "postage": 0.5,
             "date": f"2026-11-{d:02d}", "url": f"https://ebay.example/{i}",
             "excluded_reason": None}
            for i, (p, d) in enumerate(((3.0, 1), (3.5, 2), (4.0, 3), (4.5, 4), (5.0, 5),
                                        (19.0, 6)))]
    sold.append({"title": "Lugia 78/124 PSA 10", "price": 80, "date": "2026-11-07",
                 "excluded_reason": "graded"})
    p = pricing.price_card("x", "Lugia 78/124 Fates Collide Pokemon NM", sold)
    f = market.figures(p)
    check(f["n"] == 6 and f["excluded"] == 1 and f["max"] == 19.5, f"figures wrong: {f}")
    expensive = market.sale_key(sold[5])
    q = market.reprice(p, {expensive})
    check(q.n_sales == 5 and q.suggested <= p.suggested,
          f"leaving the dear sale out should not raise the price: {q.suggested} vs "
          f"{p.suggested}")
    check(any(s.get("why") == market.LEFT_OUT for s in q.excluded), "not marked as left out")
    back = market.reprice(q, set())
    check(back.suggested == p.suggested and back.n_sales == p.n_sales,
          "putting the sale back should restore the price exactly")
    check(any(s.get("why") == "graded" for s in back.excluded),
          "Comp Finder's own exclusion must survive a reprice")

    sid = market.save_search("Lugia 78/124", {"sold": sold, "status": "ok"}, p)
    again, ev, row = market.load_search(sid)
    check(again.suggested == p.suggested, "a saved search priced differently on reopening")
    check(market.history()[0]["title"] == "Lugia 78/124", "history lost the search")
    market.forget(sid)
    check(not market.history(), "forget left the search")

    path = os.path.join(work, "terapeak.csv")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("Listing title,Sold price,Shipping,Date sold\n")
        fh.write('"Lugia 78/124 Fates Collide","£4.20","£0.85","05/11/2026"\n')
        fh.write('"Lugia 78/124 Fates Collide","£1,004.00","0","2026-11-02"\n')
        fh.write('"","£3.00","0","2026-11-02"\n')
    got, note = market.read_sales_csv(path)
    check(len(got) == 2 and got[0]["price"] == 4.2 and got[0]["postage"] == 0.85
          and got[0]["date"] == "2026-11-05" and got[1]["price"] == 1004.0,
          f"CSV sales read wrong: {got} ({note})")
    # Comp Finder's bare "Not signed in." is a login that has expired: it is
    # recognised, remembered against that cookie, and forgotten for a new one.
    check(market.is_login_error(pricing.CompFinderError("Not signed in.")),
          "'Not signed in.' not recognised as a login problem")
    check(market.is_login_error(pricing.CompFinderError("Comp Finder: HTTP 401")),
          "HTTP 401 not recognised as a login problem")
    check(not market.is_login_error(pricing.CompFinderError("rate limited, try later")),
          "an unrelated Comp Finder error taken for a login problem")
    check(market.login_state() == "none", "no cookie should read as no login")
    os.makedirs(os.path.dirname(pricing.cookie_path()), exist_ok=True)
    with open(pricing.cookie_path(), "w", encoding="utf-8") as fh:
        fh.write("session=old")
    check(market.login_state() == "saved", "a saved cookie should read as saved")
    market.mark_login(rejected=True)
    check(market.login_state() == "rejected", "a refused login was not remembered")
    with open(pricing.cookie_path(), "w", encoding="utf-8") as fh:
        fh.write("session=fresh")
    check(market.login_state() == "saved", "a fresh cookie should clear the refusal")
    os.remove(pricing.cookie_path())

    compfinder_checks()

    url = market.ebay_sold_url("Lugia 78/124 Fates Collide Pokemon NM")
    check("LH_Sold=1" in url and "Pokemon" not in url and "NM" not in url,
          f"sold search URL wrong: {url}")


def review_panel_checks(work):
    """Grading on the Condition tab writes the review sheet and the library."""
    import tkinter as tk
    from tkinter import filedialog
    from cardcropper import conditioning, library, ui
    from cardcropper.condition_tab import ConditionTab
    from cardcropper.review_panel import read_sheet

    out = os.path.join(work, "prepared")
    for sku in ("KD1", "KD2"):
        d = os.path.join(out, "cards", sku)
        os.makedirs(d)
        for n in ("corners-back.jpg", "crops-back.jpg", "front.jpg", "back.jpg"):
            Image.new("RGB", (400, 560), (20, 30, 90)).save(os.path.join(d, n))
    with open(os.path.join(out, "triage.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=conditioning.TRIAGE_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerow({"sku": "KD1", "title": "Card one", "triage": "review",
                    "worst_corner_pct": 1.5, "wear_total_pct": 0.3})
        w.writerow({"sku": "KD2", "title": "Card two", "triage": "likely-nm",
                    "worst_corner_pct": 0.1, "wear_total_pct": 0.0})
    with open(os.path.join(out, "conditioning-review.csv"), "w", encoding="utf-8",
              newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=conditioning.REVIEW_FIELDS + ["extra"])
        w.writeheader()
        for sku in ("KD1", "KD2"):
            w.writerow({"sku": sku, "title": "t", "extra": "keep me"})

    root = tk.Tk()
    try:
        ui.apply_theme(root)
        page = tk.Frame(root)
        page.pack(fill="both", expand=True)
        tab = ConditionTab(page)
        real = filedialog.askdirectory
        filedialog.askdirectory = lambda **k: out
        try:
            tab.open_prepared()
        finally:
            filedialog.askdirectory = real
        root.update()
        check(tab.panel.rec and tab.panel.rec["sku"] == "KD1", "first ungraded card not shown")
        tab.panel.pick("LP")
        tab.panel.note_var.set("light crease top left")
        tab.panel.save()
        _, rows = read_sheet(tab._review_path())
        check(not rows[0]["grade"], "an LP with a crease in its note was saved")
        tab.panel.note_var.set("whitening on back corners")
        tab.panel.save()
        root.update()
        _, rows = read_sheet(tab._review_path())
        check(rows[0]["grade"] == "LP" and rows[0]["looked"] == "yes"
              and rows[0]["extra"] == "keep me", f"review row wrong: {rows[0]}")
        check(tab.panel.rec["sku"] == "KD2", "did not move on to the next card")
        check(tab.tree.set(tab.tree.get_children()[0], "grade") == "LP", "row not marked")
        library.flush()
        check(any(c["sku"] == "KD1" and c["standing"] == "LP" for c in library.cards()),
              "a grade saved on the Condition tab did not reach the library")
    finally:
        root.destroy()


def compfinder_checks():
    """Signing in to Comp Finder from the app, and staying signed in."""
    import base64
    import json as _json
    from cardcropper import compfinder_auth as cfa, market, pricing

    calls = []

    def post(path, body):
        calls.append((path, dict(body)))
        if "password" in path:
            if body["password"] != "right":
                raise cfa.SignInError("Invalid login credentials")
            n = 1
        else:
            n = int(body["refresh_token"][1:]) + 1
        return {"access_token": f"a{n}", "refresh_token": f"r{n}", "expires_in": 3600,
                "token_type": "bearer", "user": {"email": "me@example.com", "id": "u1"}}

    try:
        cfa.sign_in("me@example.com", "wrong", post=post)
        raise AssertionError("a wrong password signed in")
    except cfa.SignInError:
        pass
    check(cfa.sign_in(" me@example.com ", "right", post=post) == "me@example.com",
          "sign-in should say who it signed in as")
    check(calls[-1][1]["email"] == "me@example.com", "email not trimmed")
    cookie = pricing.comp_finder_cookie()
    name, value = cookie.split("=", 1)
    check(name == cfa.COOKIE_NAME and value.startswith("base64-"), f"cookie wrong: {cookie}")
    raw = value[len("base64-"):]
    session = _json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    check(session["access_token"] == "a1" and session["expires_at"] > 0,
          f"cookie does not hold the session: {session}")
    for path in (cfa.session_path(), pricing.cookie_path()):
        with open(path, encoding="utf-8") as fh:
            check("right" not in fh.read(), f"the password was written to {path}")
    check(market.login_state() == "saved" and cfa.signed_in_as() == "me@example.com",
          "not shown as signed in")

    # A long session is split into chunks the way @supabase/ssr splits it.
    long_session = {"access_token": "x" * 5000}
    whole = "base64-" + base64.urlsafe_b64encode(_json.dumps(
        long_session, separators=(",", ":")).encode()).decode().rstrip("=")
    parts = cfa.cookie_for(long_session).split("; ")
    names = [p.split("=", 1)[0] for p in parts]
    values = [p.split("=", 1)[1] for p in parts]
    check(names == [f"{cfa.COOKIE_NAME}.{i}" for i in range(len(parts))] and len(parts) > 1
          and all(len(v) <= cfa.CHUNK for v in values) and "".join(values) == whole,
          f"chunking wrong: {names}, {[len(v) for v in values]}")

    n = len(calls)
    check(not cfa.ensure_fresh(post=post) and len(calls) == n,
          "a fresh session should not be refreshed")
    check(cfa.ensure_fresh(now=time.time() + 3500, post=post), "an expiring session was kept")
    check(calls[-1][1] == {"refresh_token": "r1"}, "refreshed with the wrong token")
    check("a2" in base64.urlsafe_b64decode(
        pricing.comp_finder_cookie().split("base64-")[1] + "==").decode(),
        "the cookie was not rewritten after a refresh")

    def refused(path, body):
        raise cfa.SignInError("refresh token revoked")
    check(not cfa.ensure_fresh(now=time.time() + 9999, post=refused),
          "a failed refresh must not raise — the call goes ahead and says what is wrong")

    cfa.sign_out()
    check(not pricing.comp_finder_cookie() and not cfa.signed_in_as(),
          "sign-out left the session behind")


def window_checks():
    """Build the real window, every page, and check its catalogue of tips."""
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception as exc:                                # noqa: BLE001
        print(f"  (no display — window not built: {exc})")
        return
    root.destroy()
    review_panel_checks(tempfile.mkdtemp(prefix="cc-panel-"))
    from cardcropper import gui, guide, ui, updater
    import cardcropper.shell as shell_mod

    updater.offer = lambda *a, **k: None
    built = {}
    real_mainloop = tk.Tk.mainloop
    real_init = shell_mod.Shell.__init__

    def init(self, *a, **k):
        real_init(self, *a, **k)
        built["shell"] = self

    def loop(self):
        sh = built["shell"]
        try:
            check(set(sh.pages) == set(guide.PAGES),
                  f"pages built {sorted(sh.pages)} vs guide {sorted(guide.PAGES)}")
            for key in sh.pages:
                sh.show(key)
                self.update()
            # Every tip must still find its control.
            for key, tips in guide.TIPS.items():
                texts = set()
                for w in ui.walk(sh.bodies[key]):
                    t = ui._text_of(w)                     # noqa: SLF001
                    if t:
                        texts.add(t)
                for k in tips:
                    hit = any(t.startswith(k[1:]) for t in texts) if k.startswith("^") \
                        else k in texts
                    check(hit, f"tip on '{key}' for {k!r} matches no control — renamed?")
            # Ctrl+1 changes page and must not grade a card on the Grade tab.
            grade = sh.objects["grade"]
            graded = []
            grade.grade = lambda g: graded.append(g)
            grade.cards = [("KA1", "", "", "", "")]
            sh.show("grade")
            self.update()
            check(self.bind_all("<Control-Key-1>"), "no Ctrl+1 page shortcut bound")
            self.focus_force()
            self.update()
            self.event_generate("<Control-Key-1>")
            self.update()
            check(not graded, f"Ctrl+1 graded the card: {graded}")
            # A headless Windows runner may not deliver a synthetic key press
            # at all; then the page stays put. Anywhere else is a wrong binding.
            check(sh.current in ("folders", "grade"), f"Ctrl+1 went to {sh.current}")
            sh.toggle_guide("crop")
            sh.toggle_guide("crop")
        finally:
            self.destroy()

    shell_mod.Shell.__init__ = init
    tk.Tk.mainloop = loop
    try:
        gui.main()
    finally:
        tk.Tk.mainloop = real_mainloop
        shell_mod.Shell.__init__ = real_init


def main():
    work = tempfile.mkdtemp(prefix="cc-window-")
    old = {k: os.environ.get(k) for k in ("XDG_CONFIG_HOME", "APPDATA")}
    # Settings, library and market into the scratch folder — never the
    # machine's real ones.
    os.environ["XDG_CONFIG_HOME"] = os.path.join(work, "config")
    os.environ["APPDATA"] = os.path.join(work, "config")
    try:
        library_checks(work)
        market_checks(work)
        window_checks()
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(work, ignore_errors=True)
    print("window smoke: ok")


if __name__ == "__main__":
    main()
