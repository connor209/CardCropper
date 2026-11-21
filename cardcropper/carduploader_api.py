"""
Reading from CardUploader's web backend — READ ONLY, and kept in this one
module because it is unofficial.

There is no public API. The web app talks to a JSON backend that accepts the
logged-in user's Firebase token (found 21 Nov 2026 by reading the app's code).
It can change without notice; check CardUploader's terms before relying on it.

Writes (price, condition, variant edits) are NOT made from here. They go
through the browser extension in `extension/`, which runs inside the user's
own logged-in tab, shows a diff, and applies only on a click.

The token: the page's `firebaseLocalStorageDb` IndexedDB store,
`stsTokenManager.accessToken`. It expires after about an hour, so take it
fresh each run (the extension's "Copy token" button), or have the extension
save the job data / history JSON and load the files instead.
"""

import json
import os
import urllib.request

BASE = "https://carduploader.com/backend"


def _get(path, token, timeout=60):
    token = token or os.environ.get("CARDUPLOADER_TOKEN", "")
    if not token:
        raise RuntimeError("no CardUploader token — copy one from the extension, or "
                           "save the JSON from the extension and load the file")
    req = urllib.request.Request(BASE + path, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def job_data(job_id, token=None):
    """`GET /jobs/{jobId}/data`: results.cards[] with skus[], status, prices."""
    return _get(f"/jobs/{job_id}/data", token)


def history(token=None):
    """`GET /jobs/history`."""
    return _get("/jobs/history", token)


def tcg_of(job):
    """The job's `settings.tcg` — mtg, pokemon japanese, pokemon english, yugioh…"""
    return ((job or {}).get("settings") or {}).get("tcg", "")


def game_of_tcg(tcg):
    t = (tcg or "").lower()
    if "yugioh" in t or "yu-gi-oh" in t:
        return "yugioh"
    if "mtg" in t or "magic" in t:
        return "mtg"
    return "pokemon"
