"""
The grade library: every card a person has graded, kept in one place, so a
golden set is chosen from evidence instead of assembled by hand.

**Why it exists.** A golden set (`audits`, `golden/<line>.csv`) is 30-50 cards
per game line whose grade is known IN HAND, used to blind-check graders before
a batch. Building one by hand means remembering which cards were graded, which
were borderline, and where their scans went — and the scans go where the
batch went, which is often deleted a week later. So every human grade is
recorded here as it is made, the card's four images are copied into the
library, and promoting a card into a golden set is one click from a list that
already says which cards are worth it.

What lands here, automatically:

* every pass keyed on the **Grade** tab (`source = grade-tab`);
* every grade in a **conditioning-review.csv**, when its grades are recorded
  or written (`source = review-sheet`);
* blind checks of golden cards (`source = golden-blind`), kept apart: they
  test the grader and never feed a suggestion.

Machine grades — a triage band, a Claude second opinion that nobody accepted
— are never recorded as grades. They would only teach the library what the
machine already thinks.

**Borderline** is suggested, not decided: two passes that disagreed, a keyed
grade the rules capped, wear counts sitting one short of a rule's limit, a
second opinion that disagreed. A blind check needs three borderline cards in
ten, so those are the cards worth promoting.

One SQLite file in the settings folder; the images beside it under
`grade-library/`. Safe to open from several threads: every call opens its own
connection.
"""

import csv
import datetime as _dt
import os
import queue
import re
import sqlite3
import threading

from . import audits, grading

DB_NAME = "grade-library.sqlite"
IMAGE_DIR = "grade-library"
ROLES = ("front", "back", "front_crops", "back_crops")
#: Longest side a snapshot of a whole card is kept at. Crop sheets are kept
#: as they are: they are what a grade is read from.
SNAPSHOT_MAX = 1600

#: How big a golden set should be (audits: 30-50), and what a blind check
#: draws from it.
GOLDEN_TARGET = (30, 50)

SOURCES = {"grade-tab": "Grade tab", "review-sheet": "Review sheet",
           "golden-blind": "Blind check", "manual": "Entered here"}

_GRADE_COLS = ["sku", "card", "game", "line", "batch", "grade", "keyed", "reasons",
               "pass_no", "grader", "source", "origin", "looked", "front_points",
               "back_points", "front_corners", "back_corners", "crease", "notes",
               "confidence", "worst_corner_pct", "wear_total_pct", "border_base",
               "graded_at"]


def settings_dir():
    from . import conditioning
    return conditioning.settings_dir()


def default_path():
    return os.path.join(settings_dir(), DB_NAME)


def now():
    return _dt.datetime.now().isoformat(timespec="seconds")


def safe(name):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name or "").strip(" .") or "card"


# ---------------------------------------------------------------- storage


def connect(path=None):
    path = path or default_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    # Write-ahead logging: background writers (pictures, grades) never make
    # the window wait to read — a locked read on the window's thread is a
    # frozen window.
    try:
        db.execute("PRAGMA journal_mode=WAL")
    except sqlite3.DatabaseError:
        pass
    db.execute("""CREATE TABLE IF NOT EXISTS grades (
        id INTEGER PRIMARY KEY,
        sku TEXT NOT NULL, card TEXT, game TEXT, line TEXT, batch TEXT,
        grade TEXT NOT NULL, keyed TEXT, reasons TEXT, pass_no TEXT, grader TEXT,
        source TEXT NOT NULL, origin TEXT NOT NULL DEFAULT '',
        looked TEXT, front_points TEXT, back_points TEXT, front_corners TEXT,
        back_corners TEXT, crease TEXT, notes TEXT, confidence TEXT,
        worst_corner_pct REAL, wear_total_pct REAL, border_base REAL,
        graded_at TEXT NOT NULL,
        UNIQUE (sku, source, origin, pass_no))""")
    db.execute("""CREATE TABLE IF NOT EXISTS cards (
        sku TEXT PRIMARY KEY, card TEXT, game TEXT, line TEXT, batch TEXT,
        front TEXT, back TEXT, front_crops TEXT, back_crops TEXT,
        src_front TEXT, src_back TEXT, src_front_crops TEXT, src_back_crops TEXT,
        golden INTEGER NOT NULL DEFAULT 0, true_grade TEXT, borderline INTEGER,
        golden_notes TEXT, promoted_at TEXT, updated_at TEXT)""")
    db.execute("CREATE INDEX IF NOT EXISTS grades_sku ON grades(sku)")
    db.commit()
    return db


def snapshot(sku, images, root=None):
    """
    Copy a card's images into the library, so the record outlives the batch
    folder. Returns {role: library path} for the ones that could be read.
    Whole-card scans are shrunk to SNAPSHOT_MAX; crop sheets are copied as
    they are. An image already snapshotted from the same source is kept.
    """
    from PIL import Image
    root = root or os.path.join(settings_dir(), IMAGE_DIR)
    dest = os.path.join(root, safe(sku))
    out = {}
    for role in ROLES:
        src = (images or {}).get(role) or ""
        if not src or not os.path.exists(src):
            continue
        target = os.path.join(dest, f"{role.replace('_', '-')}.jpg")
        try:
            if os.path.exists(target) and os.path.getmtime(target) >= os.path.getmtime(src):
                out[role] = target
                continue
            os.makedirs(dest, exist_ok=True)
            with Image.open(src) as im:
                im = im.convert("RGB")
                if role in ("front", "back") and max(im.size) > SNAPSHOT_MAX:
                    k = SNAPSHOT_MAX / max(im.size)
                    im = im.resize((round(im.size[0] * k), round(im.size[1] * k)),
                                   Image.LANCZOS)
                im.save(target, quality=92)
            out[role] = target
        except Exception:                                   # noqa: BLE001
            continue
    return out


def record(sku, grade, source, *, path=None, images=None, take_snapshot=True, **fields):
    """
    Record one human grade, and the card it belongs to.

    `fields` are any of the grade columns (card, game, line, batch, keyed,
    reasons, pass_no, grader, origin, looked, …, graded_at). The same pass of
    the same card from the same file is updated rather than added again, so
    recording a review sheet twice does not double it. Returns the row id, or
    None when there is nothing to record (no SKU, or not a grade).
    """
    sku = (sku or "").strip()
    grade = (grade or "").strip().upper()
    if not sku or grade not in grading.GRADES:
        return None
    row = {c: fields.get(c) for c in _GRADE_COLS}
    row.update(sku=sku, grade=grade, source=source,
               origin=fields.get("origin") or "", pass_no=str(fields.get("pass_no") or ""),
               graded_at=fields.get("graded_at") or now())
    for k in ("looked",):
        if isinstance(row[k], (set, list, tuple)):
            row[k] = " ".join(sorted(row[k]))
    snaps = snapshot(sku, images) if (take_snapshot and images) else {}
    db = connect(path)
    try:
        cols = ", ".join(_GRADE_COLS)
        marks = ", ".join("?" for _ in _GRADE_COLS)
        update = ", ".join(f"{c}=excluded.{c}" for c in _GRADE_COLS
                           if c not in ("sku", "source", "origin", "pass_no"))
        cur = db.execute(f"INSERT INTO grades ({cols}) VALUES ({marks}) "
                         f"ON CONFLICT(sku, source, origin, pass_no) DO UPDATE SET {update}",
                         [row[c] for c in _GRADE_COLS])
        _upsert_card(db, sku, row, images or {}, snaps)
        db.commit()
        return cur.lastrowid
    finally:
        db.close()


def _upsert_card(db, sku, row, images, snaps):
    have = db.execute("SELECT * FROM cards WHERE sku=?", (sku,)).fetchone()
    vals = {"card": row.get("card"), "game": row.get("game"), "line": row.get("line"),
            "batch": row.get("batch")}
    for role in ROLES:
        if snaps.get(role):
            vals[role] = snaps[role]
        if images.get(role):
            vals["src_" + role] = images[role]
    if have is None:
        vals["sku"] = sku
        vals["updated_at"] = now()
        cols = ", ".join(vals)
        db.execute(f"INSERT INTO cards ({cols}) VALUES ({', '.join('?' for _ in vals)})",
                   list(vals.values()))
    else:
        # Never blank out what is known with what this source did not know.
        vals = {k: v for k, v in vals.items() if v}
        vals["updated_at"] = now()
        sets = ", ".join(f"{k}=?" for k in vals)
        db.execute(f"UPDATE cards SET {sets} WHERE sku=?", list(vals.values()) + [sku])


# ---------------------------------------------------------------- background writer

_jobs = queue.Queue()
_worker = None
_lock = threading.Lock()


def record_later(*args, on_error=None, **kwargs):
    """
    `record`, on a background thread — the Grade tab records on every key
    press, and copying four images there would make the next card wait.
    """
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_drain, daemon=True)
            _worker.start()
    _jobs.put((args, kwargs, on_error))


def _drain():
    while True:
        args, kwargs, on_error = _jobs.get()
        try:
            record(*args, **kwargs)
        except Exception as exc:                            # noqa: BLE001
            if on_error:
                on_error(exc)
        finally:
            _jobs.task_done()


def flush(timeout=30):
    """Wait for background records to land (tests; and before closing)."""
    end = _dt.datetime.now().timestamp() + timeout
    while _jobs.unfinished_tasks and _dt.datetime.now().timestamp() < end:
        threading.Event().wait(0.05)


# ---------------------------------------------------------------- reading


def borderline_reasons(grades, card=None):
    """
    Why a card might be borderline, from its grade rows — the cards a blind
    check needs three of. Suggestions only; promoting decides.
    """
    reasons = []
    human = [g for g in grades if g["source"] != "golden-blind"]
    distinct = {g["grade"] for g in human}
    if len(distinct) > 1:
        reasons.append("passes disagreed: " + " / ".join(
            sorted(distinct, key=grading.GRADES.index)))
    for g in human:
        keyed = (g["keyed"] or "").upper()
        if keyed and keyed != g["grade"] and keyed in grading.KEYS:
            reasons.append(f"keyed {keyed}, the rules made it {g['grade']}")
            break
    for g in human:
        for face in ("front", "back"):
            pts = _int(g[f"{face}_points"])
            corners = _int(g[f"{face}_corners"])
            if pts is not None and pts == grading.NM_MAX_POINTS - 1:
                reasons.append(f"{pts} wear points on the {face} — one short of the NM limit")
            if corners is not None and corners == grading.CORNERS_FOR_LP - 1:
                reasons.append(f"{corners} whitened corners on the {face} — one short of "
                               "the LP cap")
        if (g["confidence"] or "").lower() in ("low", "medium"):
            reasons.append(f"graded with {g['confidence'].lower()} confidence")
        notes = (g["notes"] or "") + " " + (g["reasons"] or "")
        if re.search(r"\bborder(line)?\b|\bunsure\b|\bclose\b|\b50/50\b", notes, re.I):
            reasons.append("the notes call it close")
    seen, out = set(), []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def grades_for(sku, path=None):
    db = connect(path)
    try:
        return [dict(r) for r in db.execute(
            "SELECT * FROM grades WHERE sku=? ORDER BY graded_at", (sku,))]
    finally:
        db.close()


def cards(path=None, line=None, grade=None, golden=None, search="", borderline=None):
    """
    One row per card: its images, its golden status, every grade it has had,
    the grade that stands (the worst human pass, as the grading rules say),
    and the borderline suggestions.
    """
    db = connect(path)
    try:
        rows = [dict(r) for r in db.execute("SELECT * FROM cards ORDER BY updated_at DESC")]
        all_grades = {}
        for g in db.execute("SELECT * FROM grades ORDER BY graded_at"):
            all_grades.setdefault(g["sku"], []).append(dict(g))
    finally:
        db.close()
    out = []
    needle = (search or "").strip().lower()
    for c in rows:
        gs = all_grades.get(c["sku"], [])
        human = [g for g in gs if g["source"] != "golden-blind"]
        c["grades"] = gs
        c["standing"] = grading.worst(g["grade"] for g in human) if human else ""
        c["passes"] = len(human)
        c["last_graded"] = gs[-1]["graded_at"] if gs else ""
        c["graders"] = sorted({g["grader"] for g in human if g["grader"]})
        c["suggest_borderline"] = borderline_reasons(gs, c)
        if line and c.get("line") != line:
            continue
        if grade and (c["true_grade"] or c["standing"]) != grade:
            continue
        if golden is not None and bool(c["golden"]) != golden:
            continue
        if borderline and not (c["borderline"] or c["suggest_borderline"]):
            continue
        if needle and needle not in " ".join(str(c.get(k) or "") for k in
                                             ("sku", "card", "batch", "line")).lower():
            continue
        out.append(c)
    return out


def stats(path=None):
    """Counts the Library page and the Home page show."""
    all_cards = cards(path)
    by_grade, by_line, golden = {}, {}, {}
    for c in all_cards:
        g = c["true_grade"] or c["standing"]
        if g:
            by_grade[g] = by_grade.get(g, 0) + 1
        line = c.get("line") or "unknown"
        by_line[line] = by_line.get(line, 0) + 1
        if c["golden"]:
            gl = golden.setdefault(line, {"cards": 0, "borderline": 0})
            gl["cards"] += 1
            gl["borderline"] += 1 if c["borderline"] else 0
    return {"cards": len(all_cards), "by_grade": by_grade, "by_line": by_line,
            "golden": golden,
            "suggested": sum(1 for c in all_cards if not c["golden"]
                             and c["suggest_borderline"])}


def similar(line=None, back_points=None, back_corners=None, worst_corner_pct=None,
            exclude="", k=12, path=None):
    """
    How cards that measured like this one were graded: the `k` nearest graded
    cards in the same line, by whichever measurements both have. Returns
    ({grade: count}, [cards]). Advice for a grader — never a grade.
    """
    def measured(x):
        return x["source"] != "golden-blind" and any(
            x[k] not in (None, "") for k in ("back_points", "back_corners", "worst_corner_pct"))

    def dist(c):
        # The latest grade that MEASURED something: a second pass keyed with
        # no counts says nothing about where the card sits.
        g = next((x for x in reversed(c["grades"]) if measured(x)), None)
        if g is None:
            return None
        d, n = 0.0, 0
        bp = _int(g["back_points"])
        if back_points is not None and bp is not None:
            d += (bp - back_points) ** 2
            n += 1
        bc = _int(g["back_corners"])
        if back_corners is not None and bc is not None:
            d += 4 * (bc - back_corners) ** 2
            n += 1
        wc = g["worst_corner_pct"]
        if worst_corner_pct is not None and wc is not None:
            d += (float(wc) - worst_corner_pct) ** 2
            n += 1
        return (d / n) if n else None

    scored = []
    for c in cards(path, line=line):
        if c["sku"] == exclude or not (c["true_grade"] or c["standing"]):
            continue
        d = dist(c)
        if d is not None:
            scored.append((d, c))
    scored.sort(key=lambda t: t[0])
    near = [c for _, c in scored[:k]]
    counts = {}
    for c in near:
        g = c["true_grade"] or c["standing"]
        counts[g] = counts.get(g, 0) + 1
    return counts, near


# ---------------------------------------------------------------- golden sets


def golden_file(line):
    return audits.golden_path(settings_dir(), line or "pokemon-en")


def promote(sku, true_grade, borderline=False, notes="", path=None):
    """
    Put a card in its line's golden set — `golden/<line>.csv`, the file the
    blind checks read — with the grade it has IN HAND, which may differ from
    the screen's. Uses the library's own copies of the images, so the set
    does not break when the batch folder is cleared out.
    """
    true_grade = true_grade.upper()
    if true_grade not in grading.GRADES:
        raise ValueError(f"{true_grade} is not one of {', '.join(grading.GRADES)}")
    db = connect(path)
    try:
        c = db.execute("SELECT * FROM cards WHERE sku=?", (sku,)).fetchone()
        if c is None:
            raise KeyError(f"{sku} is not in the grade library")
        c = dict(c)
        db.execute("UPDATE cards SET golden=1, true_grade=?, borderline=?, golden_notes=?, "
                   "promoted_at=?, updated_at=? WHERE sku=?",
                   (true_grade, 1 if borderline else 0, notes, now(), now(), sku))
        db.commit()
    finally:
        db.close()
    line = c.get("line") or "pokemon-en"
    gpath = golden_file(line)
    audits.ensure_golden(gpath)
    rows = [r for r in audits.read_golden(gpath) if r.get("id") != sku]
    rows.append({"id": sku, "card": c.get("card") or "", "game_line": line,
                 "true_grade": true_grade, "borderline": "yes" if borderline else "no",
                 "front": c.get("front") or c.get("src_front") or "",
                 "back": c.get("back") or c.get("src_back") or "",
                 "front_crops": c.get("front_crops") or c.get("src_front_crops") or "",
                 "back_crops": c.get("back_crops") or c.get("src_back_crops") or "",
                 "notes": notes})
    _write_golden(gpath, rows)
    return gpath


def demote(sku, path=None):
    """Take a card out of its golden set. Its grades stay in the library."""
    db = connect(path)
    try:
        c = db.execute("SELECT line FROM cards WHERE sku=?", (sku,)).fetchone()
        db.execute("UPDATE cards SET golden=0, updated_at=? WHERE sku=?", (now(), sku))
        db.commit()
    finally:
        db.close()
    if c is None:
        return
    gpath = golden_file(c["line"])
    rows = audits.read_golden(gpath)
    kept = [r for r in rows if r.get("id") != sku]
    if len(kept) != len(rows):
        _write_golden(gpath, kept)


def _write_golden(gpath, rows):
    os.makedirs(os.path.dirname(os.path.abspath(gpath)), exist_ok=True)
    tmp = gpath + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=audits.GOLDEN_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, gpath)


def sync_golden(path=None):
    """
    Mark the cards that are in a golden-set file, including ones added to it
    by hand. Cards in a golden file the library has never seen are added from
    the file, so the library shows the whole of every set.
    """
    gdir = os.path.join(settings_dir(), "golden")
    if not os.path.isdir(gdir):
        return 0
    db = connect(path)
    n = 0
    try:
        for name in os.listdir(gdir):
            if not name.endswith(".csv") or name.endswith("-grades.csv"):
                continue
            line = name[:-4]
            for r in audits.read_golden(os.path.join(gdir, name)):
                sku = (r.get("id") or "").strip()
                if not sku:
                    continue
                true = (r.get("true_grade") or "").upper() or None
                border = 1 if (r.get("borderline") or "").lower() in ("yes", "y", "1",
                                                                      "true") else 0
                have = db.execute("SELECT sku FROM cards WHERE sku=?", (sku,)).fetchone()
                if have is None:
                    db.execute("INSERT INTO cards (sku, card, line, front, back, front_crops, "
                               "back_crops, golden, true_grade, borderline, golden_notes, "
                               "updated_at) VALUES (?,?,?,?,?,?,?,1,?,?,?,?)",
                               (sku, r.get("card"), line, r.get("front"), r.get("back"),
                                r.get("front_crops"), r.get("back_crops"), true, border,
                                r.get("notes"), now()))
                else:
                    db.execute("UPDATE cards SET golden=1, true_grade=?, borderline=? "
                               "WHERE sku=?", (true, border, sku))
                n += 1
        db.commit()
    finally:
        db.close()
    return n


def set_card(sku, path=None, **fields):
    """Edit a card's name or line by hand."""
    allowed = {k: v for k, v in fields.items() if k in ("card", "line", "game")}
    if not allowed:
        return
    db = connect(path)
    try:
        sets = ", ".join(f"{k}=?" for k in allowed)
        db.execute(f"UPDATE cards SET {sets}, updated_at=? WHERE sku=?",
                   list(allowed.values()) + [now(), sku])
        db.commit()
    finally:
        db.close()


def delete(sku, path=None):
    """Forget a card and its grades (not its golden-set row — demote first)."""
    db = connect(path)
    try:
        db.execute("DELETE FROM grades WHERE sku=?", (sku,))
        db.execute("DELETE FROM cards WHERE sku=?", (sku,))
        db.commit()
    finally:
        db.close()


def export_csv(out_path, path=None):
    """Every grade, one row each, for a spreadsheet."""
    db = connect(path)
    try:
        rows = [dict(r) for r in db.execute(
            "SELECT g.*, c.golden, c.true_grade, c.borderline FROM grades g "
            "LEFT JOIN cards c ON c.sku = g.sku ORDER BY g.graded_at")]
    finally:
        db.close()
    fields = _GRADE_COLS + ["golden", "true_grade", "borderline"]
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


# ---------------------------------------------------------------- importing


def import_review(review_path, triage_path=None, out_dir=None, path=None):
    """
    The grades in a conditioning-review.csv. Only grades on the scale count —
    HOLD is not a grade. Scores and images come from the triage.csv and the
    cards/<sku>/ folder beside it. Returns how many grades were recorded.
    """
    from . import conditioning
    out_dir = out_dir or os.path.dirname(os.path.abspath(review_path))
    triage_path = triage_path or os.path.join(out_dir, "triage.csv")
    scores = {}
    if os.path.exists(triage_path):
        with open(triage_path, encoding="utf-8-sig", newline="") as fh:
            scores = {r.get("sku", ""): r for r in csv.DictReader(fh)}
    stamp = _dt.datetime.fromtimestamp(os.path.getmtime(review_path)).isoformat(
        timespec="seconds")
    n = 0
    for r in conditioning.read_review(review_path):
        sku = (r.get("sku") or "").strip()
        grade = (r.get("grade") or "").strip().upper()
        if not sku or grade not in grading.GRADES:
            continue
        s = scores.get(sku, {})
        folder = os.path.join(out_dir, "cards", conditioning.safe_name(sku))
        images = {"front": os.path.join(folder, "front.jpg"),
                  "back": os.path.join(folder, "back.jpg"),
                  "front_crops": os.path.join(folder, "crops-front.jpg"),
                  "back_crops": os.path.join(folder, "crops-back.jpg")}
        title = r.get("title") or s.get("title") or ""
        game = "yugioh" if sku.upper().startswith("YGO-") or "yu-gi-oh" in title.lower() \
            else "pokemon"
        if record(sku, grade, "review-sheet", path=path, images=images,
                  card=title, game=game, line="yugioh" if game == "yugioh" else "pokemon-en",
                  batch=os.path.basename(out_dir), origin=os.path.abspath(review_path),
                  looked=r.get("looked") or "", notes=" — ".join(
                      x for x in (r.get("flaw_note"), r.get("evidence")) if x),
                  confidence=r.get("confidence") or "",
                  worst_corner_pct=_float(s.get("worst_corner_pct")),
                  wear_total_pct=_float(s.get("wear_total_pct")),
                  border_base=_float(s.get("border_base")), graded_at=stamp):
            n += 1
    return n


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def import_grades_file(grades_path, manifest_path=None, path=None):
    """
    A Grade tab's grades-<code>.csv, with the manifest beside it for the
    images. Human passes only; a bare Claude opinion is advice, not a grade.
    """
    from . import batches, checks
    folder = os.path.dirname(os.path.abspath(grades_path))
    code = os.path.basename(grades_path)[len("grades-"):-len(".csv")]
    manifest_path = manifest_path or os.path.join(folder, f"manifest-{code}.json")
    cards_by, game, line = {}, "pokemon", "pokemon-en"
    if os.path.exists(manifest_path):
        from . import wear
        b = batches.read_manifest(manifest_path)
        game, line = b.game, wear.game_line(b.game, b.language)
        cards_by = {c.sku: c for c in b.cards}
    chk = checks.read_checks(os.path.join(folder, f"checks-{code}.csv"))
    n = 0
    for r in grading.read_grades(grades_path):
        if r.get("source") == "ai":
            continue
        c = cards_by.get(r["sku"])
        images = {role: getattr(c, role, "") for role in ROLES} if c else {}
        if record(r["sku"], r.get("grade"), "grade-tab", path=path, images=images,
                  game=game, line=line, batch=code, origin=os.path.abspath(grades_path),
                  keyed=r.get("keyed"), reasons=r.get("reasons"), pass_no=r.get("pass"),
                  grader=r.get("grader"), looked=r.get("looked"),
                  front_points=r.get("front_points"), back_points=r.get("back_points"),
                  front_corners=r.get("front_corners"), back_corners=r.get("back_corners"),
                  crease=r.get("crease"), notes=r.get("notes") or
                  chk.get(r["sku"], {}).get("whitening", ""),
                  graded_at=r.get("time") or now()):
            n += 1
    return n


def import_folder(folder, path=None, progress=None):
    """
    Every grades-*.csv and conditioning-review.csv under `folder` — to bring
    grades made before the library existed into it. Returns (files, grades).
    """
    found = []
    for dirpath, _dirs, files in os.walk(folder):
        for name in files:
            if name.startswith("grades-") and name.endswith(".csv") \
                    and not name.endswith("-score.csv"):
                found.append(("grades", os.path.join(dirpath, name)))
            elif name == "conditioning-review.csv":
                found.append(("review", os.path.join(dirpath, name)))
    total = 0
    for i, (kind, p) in enumerate(found, 1):
        try:
            total += import_grades_file(p, path=path) if kind == "grades" \
                else import_review(p, path=path)
        except Exception:                                   # noqa: BLE001
            pass
        if progress:
            progress(i, len(found), p)
    return len(found), total


# ---------------------------------------------------------------- blind checks

PICK_FIELDS = ["id", "front", "back", "front_crops", "back_crops"]


def blind_dir():
    return os.path.join(settings_dir(), "golden", "blind")


def start_blind(line, rng=None):
    """
    Pick a blind check from a line's golden set (audits.pick_blind: 10 cards,
    3+ borderline) and write the pick list the Grade tab opens. The list
    holds only what the grader may see — the scans, never the true grade.
    Returns its path. Raises ValueError when the set cannot supply a check.
    """
    golden = audits.read_golden(golden_file(line))
    picks = audits.pick_blind(golden, rng=rng)
    os.makedirs(blind_dir(), exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(blind_dir(), f"blind-{line}-{stamp}.csv")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=PICK_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(picks)
    return path


def blind_checks():
    """Every blind check started, newest first: [(path, line, graded, total)]."""
    try:
        names = sorted((n for n in os.listdir(blind_dir())
                        if n.startswith("blind-") and n.endswith(".csv")
                        and "-grades" not in n), reverse=True)
    except OSError:
        return []
    out = []
    for n in names:
        path = os.path.join(blind_dir(), n)
        line = n[len("blind-"):].rsplit("-", 2)[0]
        total = len(audits._read(path))                     # noqa: SLF001
        graded = len(_blind_grades(path))
        out.append((path, line, graded, total))
    return out


def _blind_grades(picks_path):
    """{id: grade} the Grade tab wrote for a blind check (<picks>-grades-score.csv)."""
    score = os.path.splitext(picks_path)[0] + "-grades-score.csv"
    return {r["id"]: r["grade"] for r in audits._read(score) if r.get("grade")}  # noqa: SLF001


def score_blind(picks_path, record=True):
    """
    Score a blind check against the golden truth. Returns (stop, rows):
    stop is True on any overgrade or ungraded card — the batch does not
    start. With `record`, the result goes in the audit log (kind 'blind'),
    and every disagreement in the error log, as `--cli golden --score` does.
    """
    name = os.path.basename(picks_path)
    line = name[len("blind-"):].rsplit("-", 2)[0]
    truth = {g["id"]: g for g in audits.read_golden(golden_file(line))}
    picks = [truth[r["id"]] for r in audits._read(picks_path)          # noqa: SLF001
             if r.get("id") in truth]
    stop, rows = audits.score_blind(picks, _blind_grades(picks_path))
    if record:
        from . import cli_ops
        p = cli_ops.paths()
        game = "pokemon" if line.startswith("pokemon") else line
        audits.record_audit(p["audits"], p["errors"], os.path.splitext(name)[0], game,
                            [{"sku": r["id"], "card": r.get("card", ""),
                              "our_grade": r["our_grade"] or "", "true_grade": r["true_grade"],
                              "source": "blind check"}
                             for r in rows if r["our_grade"]], kind="blind")
    return stop, rows


# ---------------------------------------------------------------- graders


def grader_stats(path=None):
    """
    How each grader is doing, from the library:

    * against golden truth — their blind-check grades beside each card's
      in-hand grade: matches, overgrades (the expensive mistake), undergrades;
    * against each other — cards with passes from two or more graders: how
      often their grade agreed with the grade that stood.

    Returns {grader: {blind, match, over, under, passes, agreed}}.
    """
    out = {}

    def entry(name):
        return out.setdefault(name or "(no name)", {"blind": 0, "match": 0, "over": 0,
                                                    "under": 0, "passes": 0, "agreed": 0})
    for c in cards(path):
        human = [g for g in c["grades"] if g["source"] != "golden-blind"]
        for g in c["grades"]:
            if g["source"] == "golden-blind" and c.get("true_grade"):
                e = entry(g["grader"])
                e["blind"] += 1
                if g["grade"] == c["true_grade"]:
                    e["match"] += 1
                elif audits.is_overgrade(g["grade"], c["true_grade"]):
                    e["over"] += 1
                else:
                    e["under"] += 1
        if len({g["grader"] for g in human}) >= 2:
            standing = grading.worst(g["grade"] for g in human)
            for g in human:
                e = entry(g["grader"])
                e["passes"] += 1
                e["agreed"] += 1 if g["grade"] == standing else 0
    return out
