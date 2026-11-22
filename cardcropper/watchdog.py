"""
A record of where the app was when it stopped answering.

A frozen window says nothing about why, and by the time anyone looks it has
been closed. So a small background thread watches for the window's event
loop going quiet: the loop touches a heartbeat every half second, and if
that stops for FROZEN_AFTER seconds the watchdog writes every thread's stack
— the window's included, at the very line it is stuck on — to
`freezes/freeze-<time>.txt` in the settings folder. One file per freeze, not
per second of it; the newest twenty are kept.

It reads nothing, sends nothing and changes nothing. Python's own
faulthandler does the stack dump, which works even while the window's
thread is stuck inside Tk.
"""

import datetime as _dt
import faulthandler
import os
import threading
import time

BEAT = 0.5
FROZEN_AFTER = 8.0
KEEP = 20


def folder():
    from .conditioning import settings_dir
    return os.path.join(settings_dir(), "freezes")


def start(root):
    """Begin watching `root`'s event loop. Safe to call once per window."""
    state = {"beat": time.monotonic(), "reported": False, "closed": False}

    def beat():
        state["beat"] = time.monotonic()
        state["reported"] = False
        try:
            root.after(int(BEAT * 1000), beat)
        except Exception:                                   # noqa: BLE001
            state["closed"] = True

    def closed(e):
        if e.widget is root:
            state["closed"] = True
    root.bind("<Destroy>", closed, add="+")

    def watch():
        while not state["closed"]:
            time.sleep(BEAT)
            stalled = time.monotonic() - state["beat"]
            if stalled > FROZEN_AFTER and not state["reported"] and not state["closed"]:
                state["reported"] = True
                try:
                    write(stalled)
                except Exception:                           # noqa: BLE001
                    pass

    beat()
    threading.Thread(target=watch, name="cardcropper-watchdog", daemon=True).start()


def write(stalled):
    d = folder()
    os.makedirs(d, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(d, f"freeze-{stamp}.txt")
    with open(path, "w", encoding="utf-8") as fh:
        try:
            from . import updater
            build = (updater.current() or "from source")[:12]
        except Exception:                                   # noqa: BLE001
            build = "?"
        fh.write(f"CardCropper build {build}: the window stopped answering for "
                 f"{stalled:.0f}s at {stamp}.\nEvery thread's stack follows; the "
                 "'MainThread' one is the window.\n\n")
        fh.flush()
        faulthandler.dump_traceback(file=fh, all_threads=True)
    old = sorted(n for n in os.listdir(d) if n.startswith("freeze-"))
    for n in old[:-KEEP]:
        try:
            os.remove(os.path.join(d, n))
        except OSError:
            pass
    return path


def unseen():
    """Freeze reports written since the user was last told about them."""
    from . import folders
    seen = folders.load_settings().get("freeze_seen", "")
    try:
        names = sorted(n for n in os.listdir(folder()) if n.startswith("freeze-"))
    except OSError:
        return []
    return [os.path.join(folder(), n) for n in names if n > seen]


def mark_seen():
    from . import folders
    try:
        names = sorted(n for n in os.listdir(folder()) if n.startswith("freeze-"))
    except OSError:
        return
    if names:
        folders.save_settings(freeze_seen=names[-1])
