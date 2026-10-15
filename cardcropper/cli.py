"""
The same runs without the window.

Worth having for two reasons: it is how the pipeline is tested without a
display, and a batch of several hundred is easier to leave running from a
command line than from a window that has to stay open.

    cardcropper --cli <scans or folders> --out DIR       pair scans and crop
    cardcropper --cli condition <export.csv> --out DIR   triage a CardUploader batch
    cardcropper --cli calibrate --review R --triage T    learn from graded cards
    cardcropper --cli apply <export.csv> --review R      write grades to a copy
"""

import argparse
import os
import sys

from . import batch, carduploader, conditioning, imaging, scoring

COMMANDS = ("condition", "calibrate", "apply")


def main():
    argv = sys.argv[1:]
    # A folder that happens to be called "condition" is still a folder of scans.
    if argv and argv[0] in COMMANDS and not os.path.exists(argv[0]):
        return {"condition": condition, "calibrate": calibrate,
                "apply": apply}[argv[0]](argv[1:])
    return crop()


def crop():
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


if __name__ == "__main__":
    sys.exit(main() or 0)


# ---------------------------------------------------------------- conditioning


def _settings_args(ap):
    paths = conditioning.default_paths()
    ap.add_argument("--thresholds", default=paths["thresholds"],
                    help=f"triage thresholds file (default: {paths['thresholds']}; "
                         "the provisional built-in values if it does not exist)")
    return paths


def condition(argv):
    ap = argparse.ArgumentParser(
        prog="cardcropper --cli condition",
        description="Download, straighten, score and crop every card in a CardUploader "
                    "export, and rank them so as few as possible need looking at. Writes "
                    "triage.csv, conditioning-review.csv and cards/<sku>/ crops.")
    ap.add_argument("csv", help="CardUploader export")
    ap.add_argument("--out", required=True, help="output folder")
    ap.add_argument("--limit", type=int, help="only the first N rows")
    ap.add_argument("--refresh", action="store_true", help="re-download cached scans")
    ap.add_argument("--no-crops", action="store_true", help="score only, write no images")
    ap.add_argument("--upload", action="store_true",
                    help="build a listing photo per face, host it in Supabase Storage and "
                         "write <stem>-with-photos.csv with the URLs appended to PicURL. "
                         "Needs NEXT_PUBLIC_SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY, and "
                         "migration 012 applied.")
    ap.add_argument("--bucket", default="listing-photos",
                    help="storage bucket for listing photos (default: listing-photos)")
    _settings_args(ap)
    args = ap.parse_args(argv)

    uploader = None
    if args.upload:
        uploader = conditioning.SupabaseUploader.from_env(args.bucket)
        if uploader is None:
            sys.exit("--upload needs NEXT_PUBLIC_SUPABASE_URL and "
                     "SUPABASE_SERVICE_ROLE_KEY in the environment")
    thresholds = scoring.Thresholds.load(args.thresholds)
    print(f"thresholds: {thresholds.source}")

    def report(i, total, rec):
        line = f"[{i}/{total}] {rec['sku']}  {rec['triage']:<12}"
        if "worst_corner_pct" in rec:
            line += (f" corner={rec['worst_corner_pct']:>5}%  "
                     f"wear={rec['wear_total_pct']:>5}%")
        print(line + "  " + (rec.get("note") or rec["title"][:44]))

    try:
        result = conditioning.prepare(
            args.csv, args.out, thresholds=thresholds, limit=args.limit,
            refresh=args.refresh, crops=not args.no_crops, uploader=uploader,
            progress=report)
    except ValueError as exc:
        sys.exit(str(exc))
    print()
    for line in result.summary():
        print(line)
    return 1 if result.warnings else 0


def calibrate(argv):
    ap = argparse.ArgumentParser(
        prog="cardcropper --cli calibrate",
        description="Add a reviewed batch's grades to the calibration ledger and propose "
                    "triage thresholds from everything graded so far. Only grades with "
                    "looked=yes are used.")
    ap.add_argument("--review", required=True, help="a filled-in conditioning-review.csv")
    ap.add_argument("--triage", help="the triage.csv from the same run "
                                     "(default: beside the review sheet)")
    paths = _settings_args(ap)
    ap.add_argument("--ledger", default=paths["ledger"],
                    help=f"calibration ledger (default: {paths['ledger']})")
    ap.add_argument("--adopt", action="store_true",
                    help="write the proposed thresholds so the next run uses them")
    args = ap.parse_args(argv)
    triage = args.triage or os.path.join(os.path.dirname(os.path.abspath(args.review)),
                                         "triage.csv")
    if not os.path.exists(triage):
        sys.exit(f"no triage.csv at {triage} — pass --triage")
    for line in conditioning.calibrate(args.review, triage, args.ledger,
                                       args.thresholds, args.adopt):
        print(line)
    return 0


def apply(argv):
    ap = argparse.ArgumentParser(
        prog="cardcropper --cli apply",
        description="Write reviewed grades into a COPY of the export (<stem>-conditioned.csv). "
                    "Cards that are held, ungraded, or graded at a condition whose eBay "
                    "strings are not known yet go to <stem>-held.csv instead.")
    ap.add_argument("csv", help="CardUploader export (or its -with-photos copy)")
    ap.add_argument("--review", required=True, help="the filled-in conditioning-review.csv")
    ap.add_argument("--out", help="output folder (default: beside the review sheet)")
    paths = conditioning.default_paths()
    ap.add_argument("--conditions", default=paths["conditions"],
                    help=f"condition strings per grade (default: {paths['conditions']}; "
                         "a template is written there if it does not exist)")
    args = ap.parse_args(argv)
    carduploader.ensure_condition_map(args.conditions)
    out = args.out or os.path.dirname(os.path.abspath(args.review))
    try:
        result = conditioning.apply_grades(args.csv, args.review, out, args.conditions)
    except ValueError as exc:
        sys.exit(str(exc))
    for line in result.summary():
        print(line)
    return 0 if result.written else 1
