"""
What is waiting today, for the Home page — each item one line and the page it
is done on.

Worked out fresh from the same files and records the pages use, so it is
never a to-do list that has to be kept up to date by hand:

* batches whose next step is grading, variants, pricing or the change file
  (pipeline.scan under the Batches folder);
* Terapeak checks waiting (terapeak.queue);
* NM cards priced £15 or more still to be checked in hand;
* job files the extension left in Downloads;
* watch-list cards that moved at their last check;
* a blind check due (the golden set can supply one, and none in a week);
* Comp Finder login missing or refused; freeze reports not yet looked at.
"""

import os
import time

from . import folders


def _safe(fn, default):
    try:
        return fn()
    except Exception:                                       # noqa: BLE001
        return default


def items(root=None):
    """[(text, page key, tip)] — the things waiting, most pressing first."""
    root = root if root is not None else folders.load_settings().get("batches_root", "")
    out = []
    have_root = bool(root and os.path.isdir(root))

    if have_root:
        from . import pipeline
        batches = _safe(lambda: pipeline.scan(root), [])
        by_step = {}
        for b in batches:
            if b.next_page:
                by_step.setdefault(b.next_step, []).append(b.name)
        for step, names in by_step.items():
            out.append((f"{step}: {len(names)} batch(es) — {', '.join(names[:4])}"
                        + ("…" if len(names) > 4 else ""), "batches",
                        "From the Batches page: each batch's next step, with the page it is "
                        "done on."))

        from . import terapeak
        n = _safe(lambda: terapeak.pending(root), 0)
        if n:
            out.append((f"{n} Terapeak check(s) waiting", "terapeak",
                        "Cards a price report sent to a person. Check each on Terapeak and "
                        "save the price."))

        n = _safe(lambda: len(in_hand_due(batches)), 0)
        if n:
            out.append((f"{n} NM card(s) priced £15+ to check in hand", "batches",
                        "House rule: NM priced £15 or more is checked with the card in hand "
                        "before listing."))
    else:
        out.append(("Set the Batches folder, so this list can see your batches", "batches",
                    "Batches page → choose the folder your batches live under."))

    n = _safe(lambda: len(_downloads_waiting()), 0)
    if n:
        out.append((f"{n} file(s) from the extension in Downloads to file", "jobs",
                    "Job data and history the extension saved. Tools → CardUploader jobs → "
                    "Collect from Downloads files them beside their batches."))

    from . import market
    rows = _safe(market.watched, [])
    moved = [r for r in rows if r.get("previous_price") and r.get("last_price") is not None
             and market.moved(r["previous_price"], r["last_price"])]
    if moved:
        out.append((f"{len(moved)} watched card(s) moved in price — "
                    + ", ".join(r["title"][:24] for r in moved[:3]), "prices",
                    "Market → Watch list. Green rose, red fell."))

    due = _safe(blind_check_due, "")
    if due:
        out.append((due, "library", "Grade library → Blind checks → Start a blind check."))

    state = _safe(market.login_state, "saved")
    if state != "saved":
        out.append(("Comp Finder " + ("login expired — sign in again" if state == "rejected"
                                      else "not signed in"), "prices",
                    "Market → More → Comp Finder sign-in…"))

    from . import watchdog
    if _safe(watchdog.unseen, []):
        out.append(("The app stopped responding — a freeze report was saved", "freezes",
                    "Help → Freeze reports… Sending one in gets the freeze fixed."))
    return out


def _downloads_waiting():
    from . import cu_jobs
    d = cu_jobs.downloads_dir()
    try:
        names = os.listdir(d)
    except OSError:
        return []
    cutoff = time.time() - 14 * 86400
    return [n for n in names if n.endswith(".json") and (
        n.startswith("job-") or n.startswith("carduploader-history"))
        and os.path.getmtime(os.path.join(d, n)) >= cutoff]


def in_hand_due(batches):
    """SKUs priced NM £15+ whose in-hand check is not recorded in their grades file."""
    from . import grading, pricing, report
    due = []
    for b in batches:
        priced_path = b.files.get("priced")
        if not priced_path:
            continue
        from . import pipeline
        priced = [pricing.Priced(**d) for d in (pipeline._read_json(priced_path)  # noqa: SLF001
                                                 or [])]
        rows = grading.read_grades(b.files.get("grades", ""))
        done = {r["sku"] for r in rows if r.get("source") == "in-hand"}
        final = {s: g for s, (g, _, _) in grading.final_grades(rows).items()}
        due += [s for s in report.in_hand_list(priced, final) if s not in done]
    return due


def blind_check_due(days=7):
    """A line when a golden set can supply a blind check and none was run this week."""
    from . import audits, library
    st = library.stats()
    ready = [line for line, g in st["golden"].items()
             if g["cards"] >= audits.BLIND_N and g["borderline"] >= audits.BLIND_BORDERLINE]
    if not ready:
        return ""
    recent = [p for p, *_ in library.blind_checks()
              if time.time() - os.path.getmtime(p) < days * 86400]
    if recent:
        return ""
    return f"Blind check due ({', '.join(ready)}) — none this week"
