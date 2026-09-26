"""
The same run without the window.

Worth having for two reasons: it is how the pipeline is tested without a
display, and a batch of several hundred is easier to leave running from a
command line than from a window that has to stay open.
"""

import argparse
import os
import sys

from . import batch, folders, imaging, stacks


def main():
    ap = argparse.ArgumentParser(
        prog="cardcropper --cli",
        description="Pair card scans — or divide the ones holding both faces — "
                    "and write corner & edge crops for each face.")
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
                    help="write only the crops — no copy of the scan, and no "
                         "front/back cut out of a combined one")
    ap.add_argument("--split", choices=batch.SPLIT_MODES, default="auto",
                    help="auto = look at each scan and divide the ones holding "
                         "both faces, single = every scan is one face, combined "
                         "= every scan holds both (default: auto)")
    ap.add_argument("--front", choices=batch.FRONTS, default="auto",
                    help="which half of a combined scan, or which file of a "
                         "pair, is the front. auto reads it off the batch: every "
                         "card has a different front and the same back, so the "
                         "side that looks the same on every card is the back "
                         "(default: auto)")
    ap.add_argument("--background", choices=imaging.BACKGROUNDS, default="dark",
                    help="what the cards were laid on. light is for a white "
                         "backing sheet, which is the only way a black-bordered "
                         "back shows an outline at all. Not detected: a white bed "
                         "and a white-bordered card cropped flush are the same "
                         "pixels (default: dark)")
    ap.add_argument("--restart", action="store_true",
                    help="number this batch from 0001, overwriting any cards "
                         "already in the output folder. The default carries on "
                         "from the highest card number already there")
    args = ap.parse_args()

    paths = []
    for item in args.inputs:
        if os.path.isdir(item):
            paths.extend(batch.list_images(item))
        else:
            paths.append(item)
    if not paths:
        sys.exit("no images found")

    # Only worth reporting because on a cloud-synced folder
    # this is where the wait is: every scan has to be streamed down before
    # anything can be planned. Rewritten in place on a terminal, and left out
    # entirely when the output is a log file, where a thousand half-lines of
    # progress bury the one line that matters.
    live = sys.stderr.isatty()

    def examining(i, total, path):
        if not live:
            return
        print(f"\rexamining scan {i}/{total}…", end="", file=sys.stderr, flush=True)
        if i == total:
            print("\r" + " " * 32 + "\r", end="", file=sys.stderr, flush=True)

    def comparing(i, total):
        if live:
            print(f"\rcomparing faces {i}/{total}…", end="", file=sys.stderr, flush=True)
            if i == total:
                print("\r" + " " * 32 + "\r", end="", file=sys.stderr, flush=True)

    plan = batch.plan_scans(paths, split=args.split, front=args.front,
                            progress=examining, faces=comparing,
                            background=args.background)
    for note in plan.notes:
        print(note)
    combined = sum(1 for c in plan.cards if c.combined)
    if combined:
        print(f"{combined} of {len(plan.cards)} card(s) have both faces on one scan")
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

    start = 1 if args.restart else batch.next_index(args.out)
    if start > 1:
        print(f"{args.out} already holds {start - 1} card(s) — this batch is "
              f"numbered from {start:04d}")

    def report(i, total, card, names, notes, err):
        if err:
            print(f"[{i}/{total}] FAILED {card.front.name}: {err}")
            return
        print(f"[{i}/{total}] {names[0]} … {names[-1]}"
              + ("   " + "; ".join(notes) if notes else ""))

    done, failed, failures = batch.run(
        plan, args.out, naming=args.naming, order=args.order, style=args.style,
        copy_originals=not args.no_originals, progress=report, start=start,
        background=args.background)
    print(f"\n{done} card(s) written to {args.out}"
          + (f", {failed} failed" if failed else ""))
    # The failures again at the end. In a batch of a hundred the one that
    # failed has long scrolled away, and it is the only line that needs acting
    # on — those two scans are the ones to feed back in.
    for i, card, exc in failures:
        print(f"  card {i}: {card.front.name} + {card.back.name} — {exc}",
              file=sys.stderr)
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


def split_main():
    """
    File one long run into a folder per stack, cutting every --per cards.

    There is no walk through the breaks here — that needs the pictures. Use
    --dry-run to see where each break falls first; the window is the place to
    check them against the pile.
    """
    ap = argparse.ArgumentParser(
        prog="cardcropper --stacks",
        description="Split a long scanning run into a folder per stack of cards.")
    ap.add_argument("folder", help="the folder the scanner wrote the whole run to")
    ap.add_argument("--per", type=int, default=stacks.PER_STACK,
                    help=f"cards per stack (default: {stacks.PER_STACK})")
    ap.add_argument("--name", default=stacks.TEMPLATE,
                    help="folder name; {date} YY.MM.DD, {n} stack number, {first} "
                         "{last} card numbers, {count} cards in it (default: "
                         f"{stacks.TEMPLATE!r}, numbered on from that day's folders)")
    ap.add_argument("--start", type=int,
                    help="the first stack's {n} (default: one past the day's "
                         "highest; give the same one again to resume a stopped run)")
    ap.add_argument("--into", help="where to create the folders (default: the "
                                   "folder FOLDER is in, beside it)")
    ap.add_argument("--split", choices=batch.SPLIT_MODES, default="auto",
                    help="as for --cli: auto = work out per scan, single = one face "
                         "per file, combined = both faces on every scan (default: auto)")
    ap.add_argument("--background", choices=imaging.BACKGROUNDS, default="dark",
                    help="as for --cli: what the cards were laid on (default: dark)")
    ap.add_argument("--dry-run", action="store_true",
                    help="show where the breaks fall and move nothing")
    crop = ap.add_argument_group(
        "cropping", f"--crop crops each stack into a '{stacks.CROP_FOLDER}' folder "
                    "inside it once filed; the rest are as for --cli")
    crop.add_argument("--crop", action="store_true")
    crop.add_argument("--style", choices=sorted(imaging.STYLES), default="grading")
    crop.add_argument("--naming", choices=batch.NAMING, default="grouped")
    crop.add_argument("--order", choices=sorted(batch.ORDERS), default="crops-last")
    crop.add_argument("--front", choices=batch.FRONTS, default="auto")
    crop.add_argument("--no-originals", action="store_true")
    args = ap.parse_args()

    folder = os.path.abspath(args.folder)
    dest = args.into or os.path.dirname(folder)
    plan = stacks.plan_stacks(batch.list_images(folder), args.per, split=args.split,
                              background=args.background)
    if args.start is not None:
        plan.first_number = args.start
    else:
        plan.number_from(dest, args.name)
    if not plan.cards:
        sys.exit("no cards found — a card needs two scans")
    for note in plan.notes:
        print(f"note: {note}", file=sys.stderr)
    for w in batch.Plan(leftover=plan.leftover).warnings:
        print(f"warning: {w} — left where they are", file=sys.stderr)
    try:
        names = stacks.folder_names(plan, args.name)
    except ValueError as exc:
        sys.exit(str(exc))

    for k, name in enumerate(names):
        cards = plan.stack(k)
        print(f"{name}: cards {plan.span(k)[0] + 1}–{plan.span(k)[1]} ({len(cards)})   "
              f"{cards[0].front.name} … {cards[-1].back.name}")
    if args.dry_run:
        return 0

    try:
        stacks.apply(plan, dest, args.name)
    except stacks.SplitFailed as exc:
        print(f"\n{exc}. {exc.card_index} card(s) were filed first; run the same "
              f"command again with --start {plan.first_number} to carry on.",
              file=sys.stderr)
        return 1
    except ValueError as exc:
        sys.exit(f"nothing moved: {exc}")
    print(f"\n{len(plan.cards)} card(s) filed into {len(names)} folder(s) in {dest}")
    if not args.crop:
        return 0

    def report(s, stacks_total, i, total, card, names_, notes, err):
        where = f"[{names[s - 1]} {i}/{total}]"
        if err:
            print(f"{where} FAILED {card.front.name}: {err}")
        elif notes:
            print(f"{where} " + "; ".join(notes))

    print("cropping…")
    _, failures = stacks.crop_stacks(
        [os.path.join(dest, n) for n in names], split=args.split, front=args.front,
        background=args.background, progress=report, style=args.style,
        naming=args.naming, order=args.order, copy_originals=not args.no_originals)
    print(f"{len(plan.cards) - len(failures)} card(s) cropped"
          + (f", {len(failures)} failed" if failures else ""))
    for stack_folder, i, card, exc in failures:
        print(f"  {os.path.basename(stack_folder)}, card {i}: {card.front.name} + "
              f"{card.back.name} — {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main() or 0)
