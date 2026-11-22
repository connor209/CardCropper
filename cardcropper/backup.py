"""
Backing up the settings folder — everything the app has learned.

The grade library, the golden sets, the market history and chosen prices,
the price register, the calibration ledger and thresholds, the condition
map, the Comp Finder session: all of it lives in one folder, and months of
grading would go with a failed disk. So once a week, when the app opens, it
zips that folder into `backups/` inside it (keeping the newest KEEP), and
File → Back up settings… writes one wherever you choose — a USB stick, a
synced folder.

Databases are copied with SQLite's own backup, not as files: with
write-ahead logging the newest changes may still be in a side file, and a
plain copy taken mid-write can be inconsistent. Caches that rebuild
themselves (catalogue answers) and the backups themselves are left out.

To restore: close the app, unzip a backup over the settings folder.
"""

import datetime as _dt
import os
import sqlite3
import tempfile
import threading
import time
import zipfile

KEEP = 8
EVERY_DAYS = 7
SKIP_DIRS = {"backups", "catalog-cache", "__pycache__"}
SKIP_SUFFIXES = ("-wal", "-shm", ".tmp", ".part")


def settings_dir():
    from .conditioning import settings_dir as sd
    return sd()


def backup_dir():
    return os.path.join(settings_dir(), "backups")


def make(dest=None, source=None):
    """Zip the settings folder to `dest` (default: backups/…). Returns the path."""
    source = source or settings_dir()
    if dest is None:
        os.makedirs(backup_dir(), exist_ok=True)
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = os.path.join(backup_dir(), f"cardcropper-settings-{stamp}.zip")
    tmp_dest = dest + ".part"
    with tempfile.TemporaryDirectory() as tmp, \
            zipfile.ZipFile(tmp_dest, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirs, files in os.walk(source):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                if name.endswith(SKIP_SUFFIXES):
                    continue
                path = os.path.join(dirpath, name)
                arc = os.path.relpath(path, source)
                if name.endswith((".sqlite", ".db")):
                    copy = os.path.join(tmp, arc.replace(os.sep, "_"))
                    try:
                        src = sqlite3.connect(path, timeout=10)
                        out = sqlite3.connect(copy)
                        with out:
                            src.backup(out)
                        src.close()
                        out.close()
                        z.write(copy, arc)
                    except sqlite3.DatabaseError:
                        z.write(path, arc)
                else:
                    try:
                        z.write(path, arc)
                    except OSError:
                        pass
    os.replace(tmp_dest, dest)
    return dest


def backups():
    try:
        return sorted((os.path.join(backup_dir(), n) for n in os.listdir(backup_dir())
                       if n.startswith("cardcropper-settings-") and n.endswith(".zip")),
                      reverse=True)
    except OSError:
        return []


def due(now=None):
    have = backups()
    if not have:
        return True
    return (now or time.time()) - os.path.getmtime(have[0]) >= EVERY_DAYS * 86400


def prune(keep=KEEP):
    for old in backups()[keep:]:
        try:
            os.remove(old)
        except OSError:
            pass


def auto(done=None):
    """The weekly backup, in the background. `done(path or None)` when finished."""
    if not os.path.isdir(settings_dir()) or not due():
        return

    def run():
        path = None
        try:
            path = make()
            prune()
        except Exception:                                   # noqa: BLE001
            path = None
        if done:
            done(path)
    threading.Thread(target=run, name="cardcropper-backup", daemon=True).start()
