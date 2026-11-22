"""
Where every batch has got to, read off the files each step already writes.

A batch passes through several pages — batched, graded, variants checked,
priced, change file written — and each leaves a file behind:

    manifest-<code>.json      Batch & price → Make batches
    grades-<code>.csv         Grade tab
    <job data>.json           saved by the extension (jobName = <code>)
    variant-queue-<code>.csv  Variants tab
    priced-<code>.json        Batch & price → Price & report
    run-report-<code>.txt     the same
    changes-<code>.json       Write change file

and a batch conditioned from a CardUploader export leaves triage.csv,
conditioning-review.csv and <export>-conditioned.csv in its output folder.

Nothing new is recorded: the status is worked out from those files every
time, so it can never disagree with them — delete a file and the batch is
simply back a step. `scan(root)` finds every batch under a folder.
"""

import csv
import json
import os
from dataclasses import dataclass, field

#: How deep under the chosen folder to look. Batch folders sit a level or
#: two down; walking a whole drive would be slow and pointless.
MAX_DEPTH = 4
SKIP_DIRS = {"raw", "cards", "__pycache__", ".git", "node_modules", "grade-library"}

STAGES = ("batched", "graded", "variants", "priced", "changes")
STAGE_NAMES = {"batched": "Batched", "graded": "Graded", "variants": "Variants",
               "priced": "Priced", "changes": "Change file"}


@dataclass
class Batch:
    kind: str                     # "batch" (manifest) or "export" (conditioning)
    name: str
    folder: str
    cards: int = 0
    files: dict = field(default_factory=dict)
    stages: dict = field(default_factory=dict)    # stage -> (done: bool|None, text)
    next_step: str = ""
    next_page: str = ""


def _read_csv(path):
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _walk(root):
    root = os.path.abspath(root)
    base = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirs, files in os.walk(root):
        depth = dirpath.count(os.sep) - base
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")
                   and depth < MAX_DEPTH]
        yield dirpath, files


def _job_name(path):
    """A saved CardUploader job's name, or '' when the JSON is not one."""
    if os.path.getsize(path) > 50_000_000:
        return ""
    data = _read_json(path)
    if not isinstance(data, dict):
        return ""
    if not (data.get("results") or data.get("cards") or data.get("jobId")):
        return ""
    return str(data.get("jobName") or data.get("name") or "")


def scan(root):
    """Every batch under `root`, as Batch records, most advanced last."""
    found = {}            # code -> {role: path}
    exports = []
    for dirpath, files in _walk(root):
        names = set(files)
        for n in files:
            p = os.path.join(dirpath, n)
            for prefix, role, ext in (("manifest-", "manifest", ".json"),
                                      ("grades-", "grades", ".csv"),
                                      ("checks-", "checks", ".csv"),
                                      ("variant-queue-", "variants", ".csv"),
                                      ("priced-", "priced", ".json"),
                                      ("run-report-", "report", ".txt"),
                                      ("changes-", "changes", ".json")):
                if n.startswith(prefix) and n.endswith(ext):
                    code = n[len(prefix):-len(ext)]
                    if role == "grades" and code.endswith(("-grades", "-score")):
                        continue
                    if role == "changes" and code.endswith("-variants"):
                        continue
                    found.setdefault(code, {})[role] = p
                    break
            else:
                if n.endswith(".json") and not n.startswith(("manifest-", "priced-",
                                                             "changes-")):
                    try:
                        name = _job_name(p)
                    except OSError:
                        name = ""
                    if name:
                        found.setdefault(name, {})["job"] = p
        if "triage.csv" in names and "conditioning-review.csv" in names:
            exports.append(dirpath)
    batches = [_batch(code, roles) for code, roles in found.items() if "manifest" in roles]
    batches += [_export(d) for d in exports]
    order = {s: i for i, s in enumerate(STAGES)}
    batches.sort(key=lambda b: (order.get(_stage_of(b), len(STAGES)), b.name))
    return batches


def _stage_of(b):
    for s in STAGES:
        done = b.stages.get(s, (None, ""))[0]
        if done is False:
            return s
    return "done"


def _batch(code, roles):
    from . import batches, grading, variants
    b = Batch("batch", code, os.path.dirname(roles["manifest"]), files=dict(roles))
    try:
        m = batches.read_manifest(roles["manifest"])
        b.cards = len(m.cards)
    except Exception:                                       # noqa: BLE001
        m = None
    skus = {c.sku for c in m.cards} if m else set()
    checks = _read_csv(roles.get("checks", ""))
    holds = sum(1 for r in checks if (r.get("status") or "").upper().startswith("HOLD"))
    b.stages["batched"] = (True, f"{b.cards} cards" + (f", {holds} HOLD from the crop checks"
                                                       if holds else ""))
    final = grading.final_grades(grading.read_grades(roles.get("grades", "")))
    graded = sum(1 for s in skus if s in final) if skus else len(final)
    split = sum(1 for s, (_, _, d) in final.items() if d)
    b.stages["graded"] = (graded >= b.cards > 0, f"{graded}/{b.cards}"
                          + (f", {split} disagreed" if split else ""))
    if "variants" in roles:
        q = variants.read_queue(roles["variants"])
        decided = sum(1 for r in q if (r.get("decision") or "").strip())
        b.stages["variants"] = (decided >= len(q), f"{decided}/{len(q)} decided")
    elif "job" in roles:
        b.stages["variants"] = (False, "job saved — not opened on Variants yet")
    else:
        b.stages["variants"] = (False, "no job data saved yet")
    priced = _read_json(roles.get("priced", "")) if "priced" in roles else None
    if priced:
        with_price = sum(1 for p in priced if p.get("suggested") is not None)
        ready = ""
        if "report" in roles:
            try:
                with open(roles["report"], encoding="utf-8") as fh:
                    text = fh.read()
                ready = " · READY" if "READY" in text and "NOT READY" not in text else \
                    " · not ready"
            except OSError:
                pass
        b.stages["priced"] = (True, f"{with_price}/{len(priced)} priced{ready}")
    else:
        b.stages["priced"] = (False, "not priced")
    ch = _read_json(roles.get("changes", "")) if "changes" in roles else None
    if ch is not None:
        n = len(ch.get("changes", ch) if isinstance(ch, dict) else ch)
        b.stages["changes"] = (True, f"{n} change(s) written")
    else:
        b.stages["changes"] = (False, "not written")
    _next(b)
    return b


def _export(folder):
    from . import conditioning
    b = Batch("export", os.path.basename(folder), folder,
              files={"triage": os.path.join(folder, "triage.csv"),
                     "review": os.path.join(folder, "conditioning-review.csv")})
    rows = _read_csv(b.files["triage"])
    b.cards = len(rows)
    review = conditioning.read_review(b.files["review"]) if os.path.exists(
        b.files["review"]) else []
    graded = sum(1 for r in review if (r.get("grade") or "").strip())
    b.stages["batched"] = (True, f"{b.cards} cards prepared")
    b.stages["graded"] = (graded >= b.cards > 0, f"{graded}/{b.cards} in the review sheet")
    written = [n for n in os.listdir(folder) if n.endswith("-conditioned.csv")]
    b.stages["priced"] = (None, "")
    b.stages["variants"] = (None, "")
    b.stages["changes"] = (bool(written), "conditioned CSV written" if written
                           else "grades not written to a CSV yet")
    if written:
        b.files["conditioned"] = os.path.join(folder, written[0])
    _next(b)
    return b


NEXT = {"batched": ("Make batches & check", "batch"),
        "graded": ("Grade the cards", "grade"),
        "variants": ("Check the special printings", "variants"),
        "priced": ("Price & report", "batch"),
        "changes": ("Write the change file", "batch")}
NEXT_EXPORT = {"graded": ("Grade the cards", "condition"),
               "changes": ("Write grades to a copy of the CSV", "condition")}


def _next(b):
    for s in STAGES:
        done, _ = b.stages.get(s, (None, ""))
        if done is False:
            b.next_step, b.next_page = (NEXT_EXPORT if b.kind == "export" else NEXT).get(
                s, ("", ""))
            return
    b.next_step, b.next_page = "Done", ""
