"""
The same run without the window.

Worth having for two reasons: it is how the pipeline is tested without a
display, and a batch of several hundred is easier to leave running from a
command line than from a window that has to stay open.
"""

import argparse
import os
import sys

from . import batch, folders, imaging


def main():
    ap = argparse.ArgumentParser(
        prog="cardcropper --cli",
        description="Pair card scans and write corner & edge crops for each face.")
    ap.add_argument("inputs", nargs="+",
                    help="scan files, or folders of scans, in front/back order")
    ap.add_argument("--out", required=True, help="output folder")
    ap.add_argument("--style", choices=sorted(imaging.STYLES), default="grading",
                    help="grading = the labelled corner & edge sheet, listing = "
                         "the same crops with no captions (default: grading)")
    ap.add_argument("--naming", choices=batch.NAMING, default="grouped",
                    help="grouped = 0001_1_front.jpg, sequence = one continuous "
                         "run of numbers (default: grouped)")
    ap.add_argument("--order", choices=sorted(batch.ORDERS), default="crops-last",
                    help="where each card's crops sit relative to its scans "
                         "(default: crops-last)")
    ap.add_argument("--no-originals", action="store_true",
                    help="write only the crops, leaving the scans where they are")
    args = ap.parse_args()

    paths = []
    for item in args.inputs:
        if os.path.isdir(item):
            paths.extend(batch.list_images(item))
        else:
            paths.append(item)
    if not paths:
        sys.exit("no images found")

    plan = batch.pair_sequential(paths)
    for w in plan.warnings:
        print(f"warning: {w}", file=sys.stderr)
    if not plan.cards:
        sys.exit("nothing to do — a card needs two scans")

    # The output folder must not be a source folder: crops written there would
    # be read back as scans on the next run, and pairing is positional.
    sources = {os.path.dirname(os.path.abspath(p)) for p in paths}
    if os.path.abspath(args.out) in sources:
        sys.exit("--out is one of the folders the scans came from; the crops would "
                 "be paired in as scans next run. Choose a separate folder.")

    def report(i, total, card, names, notes, err):
        if err:
            print(f"[{i}/{total}] FAILED {os.path.basename(card.front)}: {err}")
            return
        print(f"[{i}/{total}] {names[0]} … {names[-1]}"
              + ("   " + "; ".join(notes) if notes else ""))

    done, failed, failures = batch.run(
        plan, args.out, naming=args.naming, order=args.order, style=args.style,
        copy_originals=not args.no_originals, progress=report)
    print(f"\n{done} card(s) written to {args.out}"
          + (f", {failed} failed" if failed else ""))
    # The failures again at the end. In a batch of a hundred the one that
    # failed has long scrolled away, and it is the only line that needs acting
    # on — those two scans are the ones to feed back in.
    for i, card, exc in failures:
        print(f"  card {i}: {os.path.basename(card.front)} + "
              f"{os.path.basename(card.back)} — {exc}", file=sys.stderr)
    return 1 if failed else 0


def folders_main():
    ap = argparse.ArgumentParser(
        prog="cardcropper --folders",
        description="Make the day's scan folders: YY.MM.DD - 001, YY.MM.DD - 002, …")
    ap.add_argument("count", type=int, help="how many folders to make")
    ap.add_argument("location", nargs="?",
                    help="where to make them (default: the last location used)")
    ap.add_argument("--date", help="YY.MM.DD (default: today)")
    ap.add_argument("--start", type=int,
                    help="first number (default: one past the highest already there)")
    args = ap.parse_args()

    location = args.location or folders.load_settings().get("folders_location")
    if not location:
        sys.exit("no location given, and none saved from an earlier run")
    try:
        made = folders.create(location, args.count, args.date, args.start)
    except (ValueError, OSError) as exc:
        sys.exit(str(exc))
    folders.save_settings(folders_location=location, folders_count=args.count)
    for path in made:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
