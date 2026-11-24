"""
Reading from CardUploader's web backend — READ ONLY, and kept in this one
module because it is unofficial.

There is no public API. The web app talks to a JSON backend that accepts the
logged-in user's session (found 21 Nov 2026 by reading the app's code; the
sign-in changed days later, see below).
It can change without notice; check CardUploader's terms before relying on it.

Writes (price, condition, variant edits) are NOT made from here. They go
through the browser extension in `extension/`, which runs inside the user's
own logged-in tab, shows a diff, and applies only on a click.

Signing in: since late Nov 2026 the backend no longer takes the Firebase
bearer token. The page swaps it (`POST /backend/session`) for an httpOnly
session cookie, and every backend call carries `x-cu-request: 1`. The
extension's "Copy token" button copies that cookie as a Cookie header value
("name=value; …"); it lasts until the user signs out. An old-style bearer
token (no "=") is still sent as Authorization, in case it ever works again.
Or have the extension save the job data / history JSON and load the files.
"""

import json
import os
import urllib.error
import urllib.request

BASE = "https://carduploader.com/backend"


class NotSignedIn(RuntimeError):
    """CardUploader answered 401: the copied session is not (or no longer) valid."""


def auth_headers(token):
    """Headers for the backend: the session cookie (or a legacy bearer token)."""
    token = " ".join((token or "").split())
    h = {"Accept": "application/json", "x-cu-request": "1",
         "Origin": "https://carduploader.com", "Referer": "https://carduploader.com/dashboard",
         "User-Agent": "Mozilla/5.0 (CardCropper)"}
    if "=" in token:
        h["Cookie"] = token
    else:
        h["Authorization"] = f"Bearer {token}"
    return h


def _get(path, token, timeout=60):
    token = token or os.environ.get("CARDUPLOADER_TOKEN", "")
    if not token:
        raise RuntimeError("no CardUploader token — copy one from the extension, or "
                           "save the JSON from the extension and load the file")
    req = urllib.request.Request(BASE + path, headers=auth_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8", "replace") or "{}")
        except ValueError:
            body = {}
        code = body.get("code", "") if isinstance(body, dict) else ""
        if exc.code == 401:
            raise NotSignedIn(code or "HTTP 401") from None
        msg = (body.get("error") if isinstance(body, dict) else "") or exc.reason
        raise RuntimeError(f"CardUploader answered HTTP {exc.code}"
                           + (f" ({code})" if code else "") + f": {msg}") from None


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
