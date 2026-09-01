"""
Frozen-app entry point.

PyInstaller wants a plain script rather than a package, and `python -m
cardcropper` is not something a .exe can do to itself.
"""

import multiprocessing

from cardcropper.__main__ import main

if __name__ == "__main__":
    # Without this a frozen Windows build re-launches the whole GUI in any
    # child process it starts, which looks like the app opening itself twice.
    multiprocessing.freeze_support()
    main()
