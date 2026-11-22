"""
Signing in to Comp Finder from the app, and staying signed in.

Comp Finder's own sign-in page is a Supabase email-and-password form. Its
server then reads the session from one cookie, `sb-<project>-auth-token`,
holding `base64-` + the session JSON in base64url, split into `.0`, `.1`, …
chunks when it is longer than 3180 characters (Supabase's `@supabase/ssr`,
read off Comp Finder's own login page, 22 Nov 2026). So the app can do
exactly what the page does: send the email and password to Supabase, get a
session back, and build that cookie — no copying a cookie out of the browser.

**Staying signed in.** A session's access token lasts about an hour; that is
why a pasted cookie stops working ("Not signed in."). The session also holds
a refresh token, so before every Comp Finder call the app swaps an expiring
session for a fresh one (`ensure_fresh`) and rewrites the cookie. Signing in
once is enough until the refresh token itself is revoked — signing out on
the website, a password change.

**What is kept.** The session (tokens and the account's email) in
`comp-finder-session.json` in the settings folder, and the cookie built from
it in `comp-finder-cookie.txt` — the file the rest of the app already reads.
The password is never written anywhere: it goes to Supabase once and is
dropped.

The URL and key below are Comp Finder's public, browser-side ones — the same
two values its login page sends to every visitor. They identify the project;
they are not a secret and grant nothing on their own.
"""

import base64
import json
import os
import time
import urllib.error
import urllib.request

SUPABASE_URL = "https://xytozasxmxpoxnlbkowf.supabase.co"
PUBLISHABLE_KEY = "sb_publishable_tXVcCnjxn973E7Yqm56R-Q_0bV8IAGD"
COOKIE_NAME = "sb-" + SUPABASE_URL.split("//")[1].split(".")[0] + "-auth-token"
#: @supabase/ssr's chunk size: a cookie value longer than this is split.
CHUNK = 3180
#: Refresh this long before the access token runs out, so a long batch
#: never starts with a token about to lapse mid-run.
REFRESH_MARGIN = 10 * 60
SESSION_FILE = "comp-finder-session.json"


class SignInError(RuntimeError):
    pass


def session_path():
    from . import conditioning
    return os.path.join(conditioning.settings_dir(), SESSION_FILE)


def _post(path, body, post=None):
    """POST to Supabase Auth; returns the JSON answer or raises SignInError."""
    if post is not None:
        return post(path, body)
    req = urllib.request.Request(
        SUPABASE_URL + path, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"apikey": PUBLISHABLE_KEY, "Content-Type": "application/json",
                 "User-Agent": "CardCropper"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read().decode("utf-8"))
        except ValueError:
            data = {}
        msg = data.get("msg") or data.get("error_description") or data.get("message") \
            or f"HTTP {e.code}"
        raise SignInError(msg) from None
    except urllib.error.URLError as e:
        raise SignInError(f"could not reach Comp Finder's sign-in service: {e.reason}") \
            from None


def cookie_for(session):
    """
    The Cookie header Comp Finder's server reads a session from — exactly
    what its own page sets: `base64-` + base64url(JSON), chunked like
    @supabase/ssr does.
    """
    raw = json.dumps(session, separators=(",", ":")).encode("utf-8")
    value = "base64-" + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    if len(value) <= CHUNK:
        return f"{COOKIE_NAME}={value}"
    parts = [value[i:i + CHUNK] for i in range(0, len(value), CHUNK)]
    return "; ".join(f"{COOKIE_NAME}.{n}={p}" for n, p in enumerate(parts))


def _stamp(session):
    if not session.get("access_token") or not session.get("refresh_token"):
        raise SignInError("Comp Finder's sign-in service answered without a session")
    if not session.get("expires_at"):
        session["expires_at"] = int(time.time()) + int(session.get("expires_in") or 3600)
    return session


def _save(session):
    from . import pricing
    path = session_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(session, fh)
    os.replace(tmp, path)
    with open(pricing.cookie_path(), "w", encoding="utf-8") as fh:
        fh.write(cookie_for(session))


def load():
    try:
        with open(session_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def sign_in(email, password, post=None):
    """Sign in with the Comp Finder account's email and password. Returns the email."""
    email = (email or "").strip()
    if not email or not password:
        raise SignInError("enter the email and password of your Comp Finder account")
    session = _stamp(_post("/auth/v1/token?grant_type=password",
                           {"email": email, "password": password}, post))
    _save(session)
    return (session.get("user") or {}).get("email") or email


def refresh(session=None, post=None):
    """Swap the session for a fresh one with its refresh token. Returns it."""
    session = session or load()
    if not session or not session.get("refresh_token"):
        raise SignInError("not signed in")
    fresh = _stamp(_post("/auth/v1/token?grant_type=refresh_token",
                         {"refresh_token": session["refresh_token"]}, post))
    _save(fresh)
    return fresh


def ensure_fresh(now=None, post=None):
    """
    Before a Comp Finder call: if the app signed in (rather than a cookie being
    pasted) and the session is close to running out, refresh it. Quietly does
    nothing when there is no session or the refresh fails — the call then
    goes ahead and Comp Finder's own answer says what is wrong.
    """
    session = load()
    if not session:
        return False
    if float(session.get("expires_at") or 0) - (now or time.time()) > REFRESH_MARGIN:
        return False
    try:
        refresh(session, post)
        return True
    except SignInError:
        return False


def signed_in_as():
    """The email of the account the app signed in as, or ''."""
    s = load()
    return ((s or {}).get("user") or {}).get("email", "") if s else ""


def sign_out():
    """Forget the session and the cookie built from it."""
    from . import pricing
    for path in (session_path(), pricing.cookie_path()):
        try:
            os.remove(path)
        except OSError:
            pass


def forget_session():
    """A cookie was pasted by hand: stop refreshing a session it replaced."""
    try:
        os.remove(session_path())
    except OSError:
        pass
