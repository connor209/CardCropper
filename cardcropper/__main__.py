"""
Entry point: `python -m cardcropper` opens the window, `--cli` runs headless,
`--folders` makes the day's scan folders.
"""

import sys


def main():
    if "--cli" in sys.argv:
        sys.argv.remove("--cli")
        from .cli import main as run
    elif "--folders" in sys.argv:
        sys.argv.remove("--folders")
        from .cli import folders_main as run
    else:
        from .gui import main as run
    run()


if __name__ == "__main__":
    main()
