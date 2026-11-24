"""
CardUploader jobs, brought into the app without downloading and moving files
by hand — read-only.

Two ways in, both through the user's own logged-in session:

* **Fetch with a token.** The extension's "Copy token for CardCropper"
  button copies the tab's session token. With it the app lists the jobs
  (`GET /jobs/history`) and fetches one's data (`GET /jobs/{id}/data`). The
  token lasts until the user signs out of CardUploader and is held in
  memory only, never written down.
* **Collect from Downloads.** The extension's "Save job data" and "Save batch
  history" buttons write `job-<name>.json` and `carduploader-history.json` to
  Downloads; the app files each where it belongs.

Where a job belongs: beside its batch's `manifest-<code>.json` (the job is
named after the batch code), which is where Variants, Batch & price and the
Batches page look for it. A job with no manifest found goes in
`<batches folder>/<name>/`.

Nothing is ever written to CardUploader from here; changes still go through
the extension's diff-and-apply.
"""

import json
import os
import shutil
import time

from . import carduploader_api, folders


def batches_root():
    return folders.load_settings().get("batches_root", "")


def jobs_in(history):
    """[{name, id, created, cards}] from a /jobs/history answer, newest first."""
    found, seen = [], set()

    def walk(node):
        if isinstance(node, dict):
            name = node.get("jobName") or node.get("job_name") or (
                node.get("name") if ("id" in node or "jobId" in node) else None)
            jid = node.get("jobId") or node.get("id") or node.get("_id")
            if isinstance(name, str) and jid and (name, str(jid)) not in seen:
                seen.add((name, str(jid)))
                cards = node.get("cardCount") or node.get("cards")
                found.append({"name": name, "id": str(jid),
                              "created": str(node.get("createdAt") or node.get("created")
                                             or node.get("date") or "")[:19],
                              "cards": len(cards) if isinstance(cards, list) else cards or ""})
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(history)
    found.sort(key=lambda j: j["created"], reverse=True)
    return found


def destination(name, root=None):
    """The folder a job belongs in: beside manifest-<name>.json, else <root>/<name>."""
    root = root or batches_root()
    if root and os.path.isdir(root):
        from . import pipeline
        for dirpath, files in pipeline._walk(root):             # noqa: SLF001
            if f"manifest-{name}.json" in files:
                return dirpath
        return os.path.join(root, name)
    return os.path.join(os.path.expanduser("~"), "CardCropper jobs", name)


def save_job(data, name=None, root=None):
    """Write a job's data as job-<name>.json where it belongs. Returns the path."""
    name = name or data.get("jobName") or data.get("name") or data.get("jobId") or "job"
    dest = destination(name, root)
    os.makedirs(dest, exist_ok=True)
    path = os.path.join(dest, f"job-{name}.json")
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)
    folders.remember("variants", path)
    return path


def fetch_history(token, get=None):
    return (get or carduploader_api.history)(token=token)


def fetch_job(job, token, root=None, get=None):
    """Fetch a job's data (read-only) and file it. Returns the saved path."""
    data = (get or carduploader_api.job_data)(job["id"], token=token)
    data.setdefault("jobId", job["id"])
    data.setdefault("jobName", job["name"])
    return save_job(data, job["name"], root)


def downloads_dir():
    return os.path.join(os.path.expanduser("~"), "Downloads")


def collect_downloads(root=None, downloads=None, since_days=14):
    """
    File the extension's saves from Downloads: each job-<name>.json beside its
    batch, and the newest carduploader-history.json into the settings folder
    (where Batch & price can find it). Files are copied, then the copy in
    Downloads is removed. Returns [(from, to)].
    """
    from .conditioning import settings_dir
    downloads = downloads or downloads_dir()
    moved = []
    try:
        names = os.listdir(downloads)
    except OSError:
        return moved
    cutoff = time.time() - since_days * 86400
    for n in sorted(names, key=lambda n: os.path.getmtime(os.path.join(downloads, n))):
        src = os.path.join(downloads, n)
        if not n.endswith(".json") or os.path.getmtime(src) < cutoff:
            continue
        if n.startswith("job-"):
            try:
                with open(src, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict) or not (data.get("results") or data.get("cards")
                                                   or data.get("jobId")):
                continue        # a file called job-… that is not CardUploader job data
            name = data.get("jobName") or n[len("job-"):-len(".json")]
            path = save_job(data, name, root)
        elif n.startswith("carduploader-history"):
            os.makedirs(settings_dir(), exist_ok=True)
            path = os.path.join(settings_dir(), "carduploader-history.json")
            shutil.copyfile(src, path)
        else:
            continue
        try:
            os.remove(src)
        except OSError:
            pass
        moved.append((src, path))
    return moved


def local_jobs(root=None):
    """[(name, path, modified)] for every saved job under the batches folder, newest first."""
    from . import pipeline
    root = root or batches_root()
    if not root or not os.path.isdir(root):
        return []
    out = []
    for dirpath, names in pipeline._walk(root):                  # noqa: SLF001
        for n in names:
            if n.endswith(".json") and not n.startswith(("manifest-", "priced-", "changes-")):
                p = os.path.join(dirpath, n)
                name = pipeline._job_name(p)                     # noqa: SLF001
                if name:
                    out.append((name, p, os.path.getmtime(p)))
    out.sort(key=lambda t: -t[2])
    return out
