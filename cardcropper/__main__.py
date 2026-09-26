"""
Entry point: `python -m cardcropper` opens the window, `--cli` crops headless,
`--folders` makes the day's scan folders, `--stacks` files a long scanning run
into stack folders.
"""

import sys


def main():
    if "--cli" in sys.argv:
        sys.argv.remove("--cli")
        from .cli import main as run
    elif "--folders" in sys.argv:
        sys.argv.remove("--folders")
        from .cli import folders_main as run
    elif "--stacks" in sys.argv:
        sys.argv.remove("--stacks")
        from .cli import split_main as run
    else:
        from .gui import main as run
    run()


if __name__ == "__main__":
    main()
