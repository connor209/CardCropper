"""
The same run without the window.

Worth having for two reasons: it is how the pipeline is tested without a
display, and a batch of several hundred is easier to leave running from a
command line than from a window that has to stay open.
"""

import argparse
import os
import sys

from . import batch, imaging, stacks


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


def split_main():
    """
    File one long run into a folder per stack, cutting every --per cards.

    There is no walk through the breaks here — that needs the pictures. Use
    --dry-run to see where each break falls first; the window is the place to
    check them against the pile.
    """
    ap = argparse.ArgumentParser(
        prog="cardcropper --split",
        description="Split a long scanning run into a folder per stack of cards.")
    ap.add_argument("folder", help="the folder the scanner wrote the whole run to")
    ap.add_argument("--per", type=int, default=stacks.PER_STACK,
                    help=f"cards per stack (default: {stacks.PER_STACK})")
    ap.add_argument("--name", default=stacks.TEMPLATE,
                    help="folder name; {n} stack number, {first} {last} card "
                         f"numbers, {{count}} cards in it (default: {stacks.TEMPLATE!r})")
    ap.add_argument("--into", help="where to create the folders (default: FOLDER)")
    ap.add_argument("--dry-run", action="store_true",
                    help="show where the breaks fall and move nothing")
    args = ap.parse_args()

    plan = stacks.plan_stacks(batch.list_images(args.folder), args.per)
    if not plan.cards:
        sys.exit("no cards found — a card needs two scans")
    for w in batch.Plan(leftover=plan.leftover).warnings:
        print(f"warning: {w} — left where they are", file=sys.stderr)
    try:
        names = stacks.folder_names(plan, args.name)
    except ValueError as exc:
        sys.exit(str(exc))

    for k, name in enumerate(names):
        cards = plan.stack(k)
        print(f"{name}: cards {plan.span(k)[0] + 1}–{plan.span(k)[1]} ({len(cards)})   "
              f"{os.path.basename(cards[0].front)} … {os.path.basename(cards[-1].back)}")
    if args.dry_run:
        return 0

    dest = args.into or args.folder
    try:
        moves = stacks.apply(plan, dest, args.name)
    except stacks.SplitFailed as exc:
        print(f"\n{exc}. {len(exc.moves) // 2} card(s) were filed first; run the "
              "same command again to carry on.", file=sys.stderr)
        return 1
    except ValueError as exc:
        sys.exit(f"nothing moved: {exc}")
    print(f"\n{len(moves) // 2} card(s) filed into {len(names)} folder(s) in {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
