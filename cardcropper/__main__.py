"""Entry point: `python -m cardcropper` opens the window, `--cli` runs headless."""

import sys


def main():
    if "--cli" in sys.argv:
        sys.argv.remove("--cli")
        from .cli import main as run
    else:
        from .gui import main as run
    run()


if __name__ == "__main__":
    main()
