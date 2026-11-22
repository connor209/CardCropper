"""
The Terapeak checks every priced batch leaves behind, as one queue.

Price & report sends some cards to a person — a rise on few sales, a wide
spread, no sales of the printing — and lists them in `terapeak-<job>.csv`
beside the job, with `checked` and `terapeak_price` for that person to fill
in. This finds every such file under the batches folder, so the checks can be
worked through in the app: open Terapeak already searched, settle the price,
save it into the same file the report reads. When a job has none left, its
report is run again (`--cli report`) and the change file can be written.

The file stays the record: what is saved here is exactly what typing into the
CSV would have saved.
"""

import contextlib
import csv
import io
import os
import urllib.parse

from . import market, report

FIELDS = report.TERAPEAK_FIELDS


def research_url(title):
    """eBay's Terapeak product research for a title: UK, sold, last 90 days."""
    q = urllib.parse.urlencode({"marketplace": "EBAY-GB", "keywords": market.clean_query(title),
                                "dayRange": "90", "tabName": "SOLD", "categoryId": "0"})
    return f"https://www.ebay.co.uk/sh/research?{q}"


def files(root):
    """Every terapeak-<job>.csv under `root`."""
    from . import pipeline
    out = []
    for dirpath, names in pipeline._walk(root):                  # noqa: SLF001
        for n in names:
            if n.startswith("terapeak-") and n.endswith(".csv"):
                out.append(os.path.join(dirpath, n))
    return sorted(out)


def tag_of(path):
    return os.path.basename(path)[len("terapeak-"):-len(".csv")]


def is_done(row):
    return (row.get("checked") or "").strip().lower() in ("yes", "y")


def queue(root):
    """[(file, row)] for every card under `root`, unchecked first, by job."""
    out = []
    for path in files(root):
        for r in report.read_csv(path):
            out.append((path, r))
    out.sort(key=lambda t: (is_done(t[1]), tag_of(t[0]), t[1].get("sku", "")))
    return out


def pending(root):
    return sum(1 for _, r in queue(root) if not is_done(r))


def save(path, sku, price=None, note=None, checked="yes"):
    """Record one check in the file, keeping every other row and column as it was."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fields = list(reader.fieldnames or FIELDS)
        rows = list(reader)
    for r in rows:
        if r.get("sku") == sku:
            r["checked"] = checked
            if price is not None:
                r["terapeak_price"] = f"{float(price):.2f}" if price != "" else ""
            if note is not None:
                r["note"] = note
            break
    else:
        raise KeyError(f"{sku} is not in {os.path.basename(path)}")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def job_for(path):
    """The job data the checks belong to: job-<tag>.json, or any job JSON named <tag>."""
    folder, tag = os.path.dirname(path), tag_of(path)
    direct = os.path.join(folder, f"job-{tag}.json")
    if os.path.exists(direct):
        return direct
    from . import pipeline
    for n in os.listdir(folder):
        if n.endswith(".json") and not n.startswith(("priced-", "changes-", "manifest-")):
            p = os.path.join(folder, n)
            try:
                if pipeline._job_name(p) == tag:                 # noqa: SLF001
                    return p
            except OSError:
                continue
    return ""


def report_again(path):
    """
    Run the job's report again now its checks are in (`--cli report`).
    Returns (exit code, the report's text). Exit 0 means READY.
    """
    from . import cli_ops
    job = job_for(path)
    if not job:
        return 1, (f"No job data found beside {os.path.basename(path)} — save the job's "
                   "data there (Tools → CardUploader jobs) and try again.")
    argv = [job, "--out", os.path.dirname(path)]
    grades = os.path.join(os.path.dirname(path), f"grades-{tag_of(path)}.csv")
    if os.path.exists(grades):
        argv += ["--grades", grades]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            code = cli_ops.main("report", argv)
        except SystemExit as e:
            code = e.code or 1
    return code or 0, buf.getvalue()
