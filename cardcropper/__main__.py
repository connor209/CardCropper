"""
Entry point: `python -m cardcropper` opens the window, `--cli` crops headless,
`--split` files a long scanning run into stack folders headless.
"""

import sys


def main():
    if "--cli" in sys.argv:
        sys.argv.remove("--cli")
        from .cli import main as run
    elif "--split" in sys.argv:
        sys.argv.remove("--split")
        from .cli import split_main as run
    else:
        from .gui import main as run
    run()


if __name__ == "__main__":
    main()
