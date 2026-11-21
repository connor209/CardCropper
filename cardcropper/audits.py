"""
Golden sets, blind checks, post-batch audits and the error log (feature 2.5).

* A golden set per game line (Pokémon EN, Pokémon JP vintage, YGO, MTG) holds
  30-50 cards graded IN HAND by Connor: `golden/<line>.csv` in the settings
  folder, with the true grade, whether the card is borderline, and its scans.
* Before a batch: blind-grade 10 golden cards, 3 or more of them borderline.
  Any overgrade (our grade better than the true grade) stops the batch.
* After a batch: 8 random NM and 3 random LP go to an in-hand audit, and every
  disagreement is logged.
* Error log: date, SKU, card, game, source, our grade, true grade, root cause,
  fix, refund.
* Target: NM overgrade rate under 2% per game. Above that the game's NM is
  capped at LP until two clean audits in a row.
"""

import csv
import datetime as _dt
import os
import random

from . import grading

GOLDEN_FIELDS = ["id", "card", "game_line", "true_grade", "borderline", "front",
                 "back", "front_crops", "back_crops", "notes"]
ERROR_FIELDS = ["date", "sku", "card", "game", "source", "our_grade", "true_grade",
                "root_cause", "fix", "refund"]
AUDIT_FIELDS = ["date", "batch", "game", "kind", "sku", "card", "our_grade",
                "true_grade", "result"]

BLIND_N, BLIND_BORDERLINE = 10, 3
AUDIT_NM, AUDIT_LP = 8, 3
OVERGRADE_TARGET = 0.02
#: How many recent NM audit results the rate is taken over.
RATE_WINDOW = 100
CLEAN_AUDITS_TO_LIFT = 2


def _read(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _append(path, fields, rows):
    new = not os.path.exists(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


def today():
    return _dt.date.today().isoformat()


def is_overgrade(ours, true):
    """Our grade is BETTER than the true one (the expensive mistake)."""
    if ours not in grading.GRADES or true not in grading.GRADES:
        return False
    return grading.GRADES.index(ours) < grading.GRADES.index(true)


# ---------------------------------------------------------------- golden set


def golden_path(settings_dir, line):
    return os.path.join(settings_dir, "golden", f"{line}.csv")


def read_golden(path):
    return _read(path)


def ensure_golden(path):
    """Write an empty golden-set file with its header, if there is none."""
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            csv.DictWriter(fh, fieldnames=GOLDEN_FIELDS).writeheader()


def pick_blind(golden, n=BLIND_N, borderline=BLIND_BORDERLINE, rng=None):
    """
    `n` golden cards for a blind check, at least `borderline` of them marked
    borderline. Raises if the set cannot supply that.
    """
    rng = rng or random.Random()
    graded = [g for g in golden if (g.get("true_grade") or "").upper() in grading.GRADES]
    border = [g for g in graded if (g.get("borderline") or "").lower() in ("yes", "y", "1", "true")]
    if len(graded) < n or len(border) < borderline:
        raise ValueError(f"golden set has {len(graded)} graded cards ({len(border)} "
                         f"borderline); a blind check needs {n} with {borderline}+ borderline")
    picks = rng.sample(border, borderline)
    rest = [g for g in graded if g not in picks]
    picks += rng.sample(rest, n - borderline)
    rng.shuffle(picks)
    return picks


def score_blind(picks, our_grades):
    """
    Compare blind grades with the golden truth. `our_grades` is {id: grade}.
    Returns (stop, rows): stop is True on any overgrade or ungraded pick.
    """
    rows, stop = [], False
    for g in picks:
        ours = (our_grades.get(g["id"]) or "").upper()
        true = g["true_grade"].upper()
        if not ours:
            result = "not graded"
            stop = True
        elif is_overgrade(ours, true):
            result = "OVERGRADE"
            stop = True
        elif ours == true:
            result = "match"
        else:
            result = "undergrade"
        rows.append({"id": g["id"], "card": g.get("card", ""), "our_grade": ours,
                     "true_grade": true, "result": result})
    return stop, rows


# ---------------------------------------------------------------- post-batch audit


def pick_audit(final, n_nm=AUDIT_NM, n_lp=AUDIT_LP, rng=None):
    """
    SKUs for the in-hand audit from {sku: grade}: `n_nm` random NM and `n_lp`
    random LP (fewer if the batch does not have that many).
    """
    rng = rng or random.Random()
    nm = sorted(s for s, g in final.items() if g == "NM")
    lp = sorted(s for s, g in final.items() if g == "LP")
    return rng.sample(nm, min(n_nm, len(nm))) + rng.sample(lp, min(n_lp, len(lp)))


def record_audit(audit_log, error_log, batch, game, results, kind="post"):
    """
    Log an audit. `results` rows: sku, card, our_grade, true_grade, and
    optionally source, root_cause, fix, refund. Every disagreement is also
    appended to the error log. Returns the audit rows written.
    """
    rows, errors = [], []
    for r in results:
        ours, true = r["our_grade"].upper(), r["true_grade"].upper()
        result = ("match" if ours == true else
                  "overgrade" if is_overgrade(ours, true) else "undergrade")
        rows.append({"date": today(), "batch": batch, "game": game, "kind": kind,
                     "sku": r["sku"], "card": r.get("card", ""), "our_grade": ours,
                     "true_grade": true, "result": result})
        if result != "match":
            errors.append({"date": today(), "sku": r["sku"], "card": r.get("card", ""),
                           "game": game, "source": r.get("source", f"{kind} audit"),
                           "our_grade": ours, "true_grade": true,
                           "root_cause": r.get("root_cause", ""), "fix": r.get("fix", ""),
                           "refund": r.get("refund", "")})
    _append(audit_log, AUDIT_FIELDS, rows)
    if errors:
        _append(error_log, ERROR_FIELDS, errors)
    return rows


def log_error(error_log, **fields):
    """One error-log row (for a return or complaint found outside an audit)."""
    row = {"date": today()}
    row.update(fields)
    _append(error_log, ERROR_FIELDS, [row])


def read_audits(path):
    return _read(path)


def read_errors(path):
    return _read(path)


def nm_status(audit_rows, game):
    """
    (overgrade_rate, capped, reason) for `game`'s NM.

    Walks the audit batches in order. When the rate over the last RATE_WINDOW
    NM results goes above OVERGRADE_TARGET, NM is capped at LP; two clean
    audits in a row (no NM overgrade) lift it.
    """
    rows = [r for r in audit_rows if r.get("game") == game]
    batches, seen = [], {}
    for r in rows:
        key = (r.get("date", ""), r.get("batch", ""))
        if key not in seen:
            seen[key] = []
            batches.append(key)
        seen[key].append(r)
    window, capped, clean, reason = [], False, 0, ""
    for key in batches:
        nm = [r for r in seen[key] if r.get("our_grade") == "NM"]
        over = [r for r in nm if r.get("result") == "overgrade"]
        window = (window + [bool(r.get("result") == "overgrade") for r in nm])[-RATE_WINDOW:]
        rate = sum(window) / len(window) if window else 0.0
        if over and rate > OVERGRADE_TARGET:
            capped, clean = True, 0
            reason = (f"NM overgrade rate {rate:.1%} after batch {key[1]} — NM capped "
                      f"at LP until {CLEAN_AUDITS_TO_LIFT} clean audits")
        elif capped:
            if over:
                clean = 0
            else:
                clean += 1
                if clean >= CLEAN_AUDITS_TO_LIFT:
                    capped, reason = False, ""
    rate = sum(window) / len(window) if window else 0.0
    return rate, capped, reason


def apply_cap(grade, capped):
    """A game's NM listed as LP while its NM is capped."""
    return "LP" if capped and grade == "NM" else grade
