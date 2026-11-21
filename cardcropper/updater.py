"""
Keeping CardCropper up to date from the builds of `main`.

Every push to `main` that builds and passes the smoke test is published as a
GitHub release — see `.github/workflows/build-windows.yml` — with a
`version.txt` beside the downloads naming the commit it was built from. On
start the app reads that file, and where it names a different commit from the
one this copy was built from, it offers to update.

Only the Python bundle (`CardCropper.bat`) updates itself. Its code is plain
files next to the launcher, which can be swapped for the new ones and the app
restarted. The `.exe` cannot rewrite itself while it runs, and Smart App
Control would block the replacement anyway, so it offers the download page.

A copy that was not built by CI — a git checkout, someone running from source
— has no `_version.py` and never updates: overwriting a working tree with a
release would throw away whatever is being worked on in it.

The check on start fails quietly. No network, a private repository, GitHub
having a bad day: the app opens as it would have, and asks again next time.
The Check for updates button (`check_now`) is the opposite: it always says
what it found, so "up to date" and "could not check" never look the same.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

REPO = "connor209/CardCropper"
RELEASES = f"https://github.com/{REPO}/releases/latest"
VERSION_URL = f"{RELEASES}/download/version.txt"
BUNDLE_URL = f"{RELEASES}/download/CardCropper-python.zip"

#: Long enough for a slow connection, short enough that an unreachable GitHub
#: is not noticed. The check runs off the window's thread either way.
TIMEOUT = 6

#: Files at the bundle's top level that an update replaces alongside the code.
BUNDLE_FILES = ("CardCropper.bat", "requirements.txt", "README.md")

PACKAGE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(PACKAGE)


def current():
    """The commit this copy was built from, or None if CI did not build it."""
    try:
        from . import _version
    except ImportError:
        return None
    return getattr(_version, "COMMIT", None)


def can_self_update(root=ROOT):
    """Whether this copy is the Python bundle, which can swap its own files."""
    return (not getattr(sys, "frozen", False)
            and os.path.isfile(os.path.join(root, "CardCropper.bat"))
            and not os.path.exists(os.path.join(root, ".git")))


def parse_version(text):
    """`version.txt` is the commit on its first line and its summary on the next."""
    lines = text.strip().splitlines()
    if not lines or not lines[0].strip():
        return None, ""
    return lines[0].strip(), (lines[1].strip() if len(lines) > 1 else "")


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "CardCropper-updater"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def latest():
    """(commit, summary) of the newest published build, or (None, "")."""
    try:
        return parse_version(_get(VERSION_URL).decode("utf-8", "replace"))
    except Exception:                                   # noqa: BLE001
        return None, ""


def check():
    """
    (commit, summary) when there is a build newer than this one to offer, else
    None. "Newer" is simply "different": releases only ever move forward, and
    comparing commits properly would need the history, which a bundle lacks.
    """
    mine = current()
    if not mine:
        return None
    commit, summary = latest()
    if not commit or commit == mine:
        return None
    return commit, summary


def status(root=ROOT):
    """
    What a check finds, said out loud — for the Check for updates button, where
    "nothing to report" must not look the same as "could not check".

    Returns a dict: `kind` is one of
      "newer"     a different build is published (`commit`, `summary`)
      "current"   this copy is the published build
      "no-stamp"  not built by CI (a git checkout, a GitHub "Download ZIP",
                  a copy built by hand), so it cannot tell which build it is
      "offline"   the published version could not be read (`error`)
    plus `mine` (this build's commit or None), `can_self_update` and `git`.
    """
    mine = current()
    out = {"mine": mine, "can_self_update": can_self_update(root),
           "git": os.path.exists(os.path.join(root, ".git")),
           "commit": None, "summary": "", "error": ""}
    try:
        commit, summary = parse_version(_get(VERSION_URL).decode("utf-8", "replace"))
    except Exception as exc:                            # noqa: BLE001
        out.update(kind="offline", error=f"{type(exc).__name__}: {exc}")
        return out
    out.update(commit=commit, summary=summary)
    if not commit:
        out.update(kind="offline", error="the published version file is empty")
    elif not mine:
        out["kind"] = "no-stamp"
    elif commit == mine:
        out["kind"] = "current"
    else:
        out["kind"] = "newer"
    return out


def describe(st):
    """A plain-English report of `status()` for a message box."""
    short = lambda c: (c or "")[:7]                     # noqa: E731
    if st["kind"] == "current":
        return f"CardCropper is up to date (build {short(st['mine'])})."
    if st["kind"] == "newer":
        return (f"A new version is available: build {short(st['commit'])}"
                + (f" — “{st['summary']}”" if st["summary"] else "")
                + f". You have build {short(st['mine'])}.")
    if st["kind"] == "offline":
        return ("Could not reach GitHub to check for updates, so it is not known "
                f"whether this copy is current.\n\n{st['error']}\n\nCheck the internet "
                "connection, or whether antivirus or a firewall blocks Python "
                f"from reaching github.com. The latest build is always at\n{RELEASES}")
    if st["git"]:
        return ("This copy is a git checkout, so it never updates itself — that "
                f"would overwrite the working tree. The latest build is "
                f"{short(st['commit'])}; pull main to get it.")
    why = (
           "it has no build stamp (cardcropper\\_version.py) — it was not "
           "downloaded from the Releases page, e.g. it came from GitHub's "
           "“Download ZIP” button or was built by hand")
    return (f"This copy cannot update itself: {why}.\n\nThe latest build is "
            f"{short(st['commit'])}"
            + (f" — “{st['summary']}”" if st["summary"] else "")
            + f".\n\nDownload CardCropper-python.zip from\n{RELEASES}\n"
            "and unzip it over this folder. That copy will offer updates by itself.")


def install(zip_path, root=ROOT, expected=None):
    """
    Put the bundle at `zip_path` in place of the one at `root`.

    The new package is unpacked beside the old one and swapped in by renaming,
    so an update that fails part-way leaves the old copy working rather than a
    mixture of the two. Returns whether requirements.txt changed, in which case
    the new dependencies need installing before the app will start.

    `expected` is the commit the bundle should have been built from; a
    download that turns out to be some other build is refused rather than
    installed under the wrong name.
    """
    staging = tempfile.mkdtemp(prefix="cardcropper-update-", dir=root)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(staging)
        new_pkg = os.path.join(staging, "cardcropper")
        if not os.path.isfile(os.path.join(new_pkg, "__init__.py")):
            raise ValueError("the download is not a CardCropper bundle")
        if expected:
            found = None
            with open(os.path.join(new_pkg, "_version.py"), encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("COMMIT"):
                        found = line.split("=", 1)[1].strip().strip("\"'")
            if found != expected:
                raise ValueError(f"the download is build {found}, not {expected}")

        old_req = _read(os.path.join(root, "requirements.txt"))
        pkg, old = os.path.join(root, "cardcropper"), os.path.join(root, "cardcropper.old")
        shutil.rmtree(old, ignore_errors=True)      # left by a previous update
        _replace(pkg, old)
        try:
            _replace(new_pkg, pkg)
        except OSError:
            _replace(old, pkg)                      # put the working copy back
            raise
        shutil.rmtree(old, ignore_errors=True)
        for name in BUNDLE_FILES:
            src = os.path.join(staging, name)
            if os.path.isfile(src):
                shutil.copyfile(src, os.path.join(root, name))
        _swap_folder(staging, root, EXTENSION)
        return _read(os.path.join(root, "requirements.txt")) != old_req
    finally:
        shutil.rmtree(staging, ignore_errors=True)


#: A folder inside Google Drive for Desktop or OneDrive can refuse a rename for
#: a moment while the sync client has a file in it open — on Windows as
#: PermissionError or [Errno 22]. A second try a moment later usually works.
RENAME_TRIES, RENAME_WAIT = 5, 1.0


def _replace(src, dst):
    import time
    for attempt in range(RENAME_TRIES):
        try:
            return os.replace(src, dst)
        except OSError:
            if attempt == RENAME_TRIES - 1:
                raise
            time.sleep(RENAME_WAIT)


#: The Chrome extension ships in the bundle too. It is swapped like the code
#: so it stays in step with the change files the app writes; Chrome then needs
#: its reload button pressed on chrome://extensions to pick the new copy up.
EXTENSION = "extension"


def _swap_folder(staging, root, name):
    """Replace root/name with staging/name, if the bundle has one."""
    new = os.path.join(staging, name)
    if not os.path.isdir(new):
        return
    dest, old = os.path.join(root, name), os.path.join(root, name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if os.path.exists(dest):
        _replace(dest, old)
    try:
        _replace(new, dest)
    except OSError:
        if os.path.exists(old):
            _replace(old, dest)
        raise
    shutil.rmtree(old, ignore_errors=True)


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def download_and_install(commit, root=ROOT):
    """Fetch the latest bundle and install it. Raises on any failure."""
    fd, zip_path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        with open(zip_path, "wb") as fh:
            fh.write(_get(BUNDLE_URL))
        changed = install(zip_path, root, expected=commit)
    finally:
        os.remove(zip_path)
    if changed:
        # The launcher only checks for Pillow and numpy, so anything new has to
        # be in place before the restart or the app fails on import with no
        # window to say so.
        subprocess.run([_console_python(), "-m", "pip", "install", "--user",
                        "--disable-pip-version-check", "-r",
                        os.path.join(root, "requirements.txt")],
                       check=True, capture_output=True, **_no_window())


def restart(root=ROOT):
    """Start the updated app. The caller closes this one."""
    subprocess.Popen([sys.executable, "-m", "cardcropper"], cwd=root, **_no_window())


def _console_python():
    """python.exe beside pythonw.exe — pip under pythonw has nowhere to write."""
    exe = sys.executable
    alt = os.path.join(os.path.dirname(exe), "python.exe")
    return alt if exe.lower().endswith("pythonw.exe") and os.path.exists(alt) else exe


def _no_window():
    if sys.platform.startswith("win"):
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


# ---------------------------------------------------------------- window


def offer(root_window, is_busy=lambda: False):
    """
    Check in the background and, if there is a newer build, ask.

    `is_busy()` says whether a job is running; an update restarts the app, so
    it is not offered in the middle of one — it is asked about next launch.
    """
    import threading
    from tkinter import messagebox

    found = {}
    worker = threading.Thread(target=lambda: found.update(result=check()), daemon=True)
    worker.start()

    def wait():
        if worker.is_alive():
            root_window.after(300, wait)
            return
        result = found.get("result")
        if not result or is_busy():
            return
        _ask_and_install(root_window, *result)

    root_window.after(300, wait)


def check_now(root_window, is_busy=lambda: False, done=None):
    """
    The Check for updates button: check, and ALWAYS say what was found — up
    to date, a new build, no build stamp, or GitHub unreachable.
    """
    import threading
    from tkinter import messagebox

    found = {}
    worker = threading.Thread(target=lambda: found.update(st=status()), daemon=True)
    worker.start()

    def wait():
        if worker.is_alive():
            root_window.after(200, wait)
            return
        if done:
            done()
        st = found["st"]
        if st["kind"] != "newer":
            show = messagebox.showinfo if st["kind"] == "current" else messagebox.showwarning
            show("CardCropper", describe(st))
            if st["kind"] == "no-stamp" and messagebox.askyesno(
                    "CardCropper", "Open the download page?"):
                import webbrowser
                webbrowser.open(RELEASES)
            return
        if is_busy():
            messagebox.showinfo("CardCropper", describe(st) + "\n\nSomething is running — "
                                "it will be offered again when the app next starts.")
            return
        _ask_and_install(root_window, st["commit"], st["summary"])

    root_window.after(200, wait)


def _ask_and_install(root_window, commit, summary):
    """Ask, and on yes install and restart (or open the download page)."""
    from tkinter import messagebox

    what = f"\n\n“{summary}”" if summary else ""
    if not can_self_update():
        if messagebox.askyesno(
                "CardCropper",
                f"A new version of CardCropper is available.{what}\n\n"
                "Open the download page?"):
            import webbrowser
            webbrowser.open(RELEASES)
        return
    if not messagebox.askyesno(
            "CardCropper",
            f"A new version of CardCropper is available.{what}\n\n"
            "Update now? CardCropper will restart."):
        return
    root_window.configure(cursor="watch")
    root_window.update()
    try:
        download_and_install(commit)
    except Exception as exc:                        # noqa: BLE001
        root_window.configure(cursor="")
        messagebox.showerror("CardCropper",
                             f"The update did not install, so nothing has "
                             f"changed.\n\n{exc}")
        return
    restart()
    root_window.destroy()
