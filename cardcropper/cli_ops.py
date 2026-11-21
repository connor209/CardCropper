"""
Command-line entry points for the batch workflow after cropping:

    batch       cut cropped folders into 50-card batches, assign codes, write
                manifests, run the completeness/crop checks and triage scores
    reference   build the English Pokémon reference back from ~50 LP backs
    variants    the variant review queue from a CardUploader job's data
    price       price a job from Comp Finder's sold comps, with the run report
    report      the run report again, after Terapeak checks are filled in
    changes     assemble the change file for the extension (price, condition,
                variant)
    golden      blind-check picks from a golden set, and scoring them
    audit       post-batch audit picks, recording results, NM overgrade status
    price-golden  the golden pricing tests, through Comp Finder
"""

import argparse
import csv
import json
import os
import random
import sys
from dataclasses import asdict

from . import (audits, batches, carduploader_api, checks, conditioning, grading,
               imaging, pricing, report, variants, wear)

COMMANDS = ("batch", "reference", "variants", "price", "report", "changes", "golden",
            "audit", "price-golden")


def settings_dir():
    return conditioning.settings_dir()


def paths():
    d = settings_dir()
    return {"reference": os.path.join(d, "reference-back-pokemon-en.npy"),
            "calibration": os.path.join(d, "triage-calibration.json"),
            "register": os.path.join(d, "price-register.sqlite"),
            "audits": os.path.join(d, "audits.csv"),
            "errors": os.path.join(d, "error-log.csv")}


def main(cmd, argv):
    return {"batch": batch_cmd, "reference": reference_cmd, "variants": variants_cmd,
            "price": price_cmd, "report": report_cmd, "changes": changes_cmd,
            "golden": golden_cmd, "audit": audit_cmd,
            "price-golden": price_golden_cmd}[cmd](argv)


def _read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _write_csv(path, fields, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _load_job(path):
    job = variants.load_job(path)
    return job, job.get("jobId") or job.get("id") or job.get("_id") or "", \
        job.get("jobName") or job.get("name") or ""


# ---------------------------------------------------------------- batch


def batch_cmd(argv):
    p = paths()
    ap = argparse.ArgumentParser(prog="cardcropper --cli batch",
                                 description="Cut cropped folders into batches of 50, give each "
                                             "its CardUploader code, write manifests and checks.")
    ap.add_argument("folders", nargs="+", help="cropped folders, in run order")
    ap.add_argument("--game", choices=batches.GAMES, required=True)
    ap.add_argument("--language", choices=batches.LANGUAGES, default="en")
    ap.add_argument("--history", help="saved /jobs/history JSON (or a list of job names) — "
                                      "the codes already used in CardUploader")
    ap.add_argument("--fetch-history", action="store_true",
                    help="read /jobs/history live with $CARDUPLOADER_TOKEN")
    ap.add_argument("--codes", help="comma-separated codes to use instead of the next ones")
    ap.add_argument("--per", type=int, default=batches.PER_BATCH)
    ap.add_argument("--order", choices=sorted(batches.batch.ORDERS), default="crops-last")
    ap.add_argument("--reference", default=p["reference"],
                    help="reference back (.npy) for English Pokémon back marks")
    ap.add_argument("--background", choices=imaging.BACKGROUNDS, default="dark")
    ap.add_argument("--no-checks", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    a = ap.parse_args(argv)

    if a.fetch_history:
        names = batches.fetch_history(os.environ.get("CARDUPLOADER_TOKEN", ""))
    elif a.history:
        names = batches.load_history(a.history)
    elif a.codes:
        names = []
    else:
        sys.exit("refusing to pick codes blind: pass --history (the extension saves it), "
                 "--fetch-history, or --codes. A reused code puts two cards under one SKU.")
    if (a.history or a.fetch_history) and not names:
        sys.exit("the history holds no job names — refusing to start codes from scratch. "
                 "Check the file, or pass --codes.")
    codes = [c.strip().upper() for c in a.codes.split(",")] if a.codes else None
    plan = batches.plan_batches(a.folders, a.game, a.language, names, a.per, a.order, codes)
    print(f"latest code in CardUploader: {batches.latest_code(names, a.game) or '(none)'}")
    for b in plan:
        missing = sum(1 for c in b.cards if c.missing)
        print(f"{b.code}: {len(b.cards)} card(s) from {b.source}"
              + (f", {missing} missing images" if missing else ""))
    if a.dry_run:
        return 0
    reference = wear.load_reference(a.reference)
    calibration = wear.load_calibration(p["calibration"])
    worst = 0
    for b in plan:
        j, c = batches.write_manifest(b)
        print(f"  wrote {j}")
        if a.no_checks:
            continue
        rows = checks.check_batch(
            b, reference, calibration, a.background,
            progress=lambda i, n, r: print(f"\r  checking {i}/{n}", end="", flush=True))
        print()
        path = checks.write_checks(rows, checks.checks_path(b))
        print(f"  {checks.summary(rows)} -> {path}")
        for r in rows:
            if r["status"] != "OK":
                worst = 1
                print(f"    {r['sku']}: {r['status']} {r['hold_reasons'] or r['notes']}")
    if reference is None and a.game == "pokemon" and a.language == "en" and not a.no_checks:
        print("no reference back yet — back marks not scored (cardcropper --cli reference)")
    return worst


def reference_cmd(argv):
    ap = argparse.ArgumentParser(prog="cardcropper --cli reference",
                                 description="Build the reference back from ~50 LP English "
                                             "Pokémon back scans.")
    ap.add_argument("backs", nargs="+", help="back scans, or folders of them")
    ap.add_argument("--out", default=paths()["reference"])
    ap.add_argument("--background", choices=imaging.BACKGROUNDS, default="dark")
    a = ap.parse_args(argv)
    files = []
    for item in a.backs:
        files += batches.batch.list_images(item) if os.path.isdir(item) else [item]
    ref = checks.reference_from_manifest_backs(files, a.background)
    wear.save_reference(ref, a.out)
    print(f"reference back from {len(files)} scans -> {a.out}")
    return 0


# ---------------------------------------------------------------- variants


def variants_cmd(argv):
    ap = argparse.ArgumentParser(prog="cardcropper --cli variants",
                                 description="List every card whose catalog entry also has a "
                                             "Reverse / Cosmos / stamped / 1st Edition printing.")
    ap.add_argument("job", help="saved GET /jobs/{id}/data JSON")
    ap.add_argument("--out", help="queue CSV (default: variant-queue-<job>.csv beside it)")
    a = ap.parse_args(argv)
    job, job_id, name = _load_job(a.job)
    rows = variants.build_queue(job)
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.job)),
                                f"variant-queue-{name or job_id or 'job'}.csv")
    variants.write_queue(rows, out)
    print(f"{len(rows)} card(s) to review -> {out}")
    for r in rows:
        if r["hint"]:
            print(f"  {r['sku']}: {r['hint']}")
    return 0


# ---------------------------------------------------------------- price


def _grades_for(job, game, grades_path, capped):
    if not grades_path:
        return {}
    final = {s: g for s, (g, _, _) in grading.final_grades(grading.read_grades(grades_path)).items()}
    out = {}
    for c in variants.job_cards(job):
        g = final.get(c.get("sku"))
        if g and g != grading.HOLD:
            vintage = grading.is_vintage(game, c.get("year"), c.get("setName", ""))
            out[c["sku"]] = audits.apply_cap(grading.listing_grade(g, game, vintage), capped)
    return out


def _dump_priced(priced, path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump([asdict(p) for p in priced], fh, indent=1, default=str)


def _load_priced(path):
    with open(path, encoding="utf-8") as fh:
        return [pricing.Priced(**d) for d in json.load(fh)]


def price_cmd(argv):
    p = paths()
    ap = argparse.ArgumentParser(prog="cardcropper --cli price",
                                 description="Price a CardUploader job from Comp Finder's sold "
                                             "comps under the house rules.")
    ap.add_argument("job", help="saved GET /jobs/{id}/data JSON")
    ap.add_argument("--out", help="folder for pricing-*.csv, terapeak-*.csv (default: beside job)")
    ap.add_argument("--grades", help="grades-<code>.csv from the grading screen")
    ap.add_argument("--evidence", help="saved Comp Finder response(s) instead of calling it")
    ap.add_argument("--register", default=p["register"])
    ap.add_argument("--no-register", action="store_true", help="do not reuse register rows")
    ap.add_argument("--fresh", action="store_true", help="Comp Finder skips its 24h cache")
    ap.add_argument("--base", default=pricing.COMP_FINDER)
    a = ap.parse_args(argv)

    job, job_id, name = _load_job(a.job)
    game = carduploader_api.game_of_tcg(variants.job_tcg(job))
    _, capped, why = audits.nm_status(audits.read_audits(p["audits"]), game)
    if capped:
        print(why)
    grades = _grades_for(job, game, a.grades, capped)
    out = a.out or os.path.dirname(os.path.abspath(a.job))
    os.makedirs(out, exist_ok=True)
    reg = pricing.Register(a.register)
    evidence = None
    if a.evidence:
        with open(a.evidence, encoding="utf-8") as fh:
            data = json.load(fh)
        evidence = {r.get("id"): r for r in (data.get("results", data) if isinstance(data, dict)
                                             else data)}

    def fetch(cards):
        return pricing.batch_comps(cards, a.base, fresh=a.fresh,
                                   progress=lambda d, n: print(f"\r  comps {d}/{n}", end="",
                                                               flush=True))

    priced = report.price_job(job, game, evidence, reg, grades, batch=name, fetch=fetch,
                              reuse_register=not a.no_register)
    print()
    reg.close()
    tag = name or job_id or "job"
    report.write_pricing(priced, os.path.join(out, f"pricing-{tag}.csv"))
    report.write_terapeak(priced, os.path.join(out, f"terapeak-{tag}.csv"))
    _dump_priced(priced, os.path.join(out, f"priced-{tag}.json"))
    print(f"wrote pricing-{tag}.csv, terapeak-{tag}.csv in {out}")
    return _report(job, priced, out, tag, a.grades, game)


def _report(job, priced, out, tag, grades_path, game):
    tp = report.read_csv(os.path.join(out, f"terapeak-{tag}.csv"))
    changes, not_applied = report.price_changes(priced, tp, job)
    extra, in_hand = [], []
    if grades_path:
        rows = grading.read_grades(grades_path)
        done = {r["sku"] for r in rows if r.get("source") == "in-hand"}
        final = {s: g for s, (g, _, _) in grading.final_grades(rows).items()}
        in_hand = [s for s in report.in_hand_list(priced, final) if s not in done]
        extra += [(s, "graded HOLD") for s, g in final.items() if g == grading.HOLD]
    lines, ready = report.run_report(priced, tp, not_applied, extra,
                                     total=len(variants.job_cards(job)), in_hand=in_hand)
    text = "\n".join(lines)
    print(text)
    with open(os.path.join(out, f"run-report-{tag}.txt"), "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    return 0 if ready else 2


def report_cmd(argv):
    ap = argparse.ArgumentParser(prog="cardcropper --cli report")
    ap.add_argument("job")
    ap.add_argument("--out")
    ap.add_argument("--grades")
    a = ap.parse_args(argv)
    job, job_id, name = _load_job(a.job)
    tag = name or job_id or "job"
    out = a.out or os.path.dirname(os.path.abspath(a.job))
    priced = _load_priced(os.path.join(out, f"priced-{tag}.json"))
    return _report(job, priced, out, tag, a.grades,
                   carduploader_api.game_of_tcg(variants.job_tcg(job)))


# ---------------------------------------------------------------- changes


def changes_cmd(argv):
    p = paths()
    ap = argparse.ArgumentParser(prog="cardcropper --cli changes",
                                 description="Assemble the change file the extension applies.")
    ap.add_argument("job", help="saved GET /jobs/{id}/data JSON")
    ap.add_argument("--out", help="default: changes-<job>.json beside the job")
    ap.add_argument("--grades", help="grades-<code>.csv: condition changes")
    ap.add_argument("--pricing", help="folder holding priced-<job>.json / terapeak-<job>.csv")
    ap.add_argument("--variants", help="decided variant-queue CSV: variant switches")
    a = ap.parse_args(argv)
    job, job_id, name = _load_job(a.job)
    tag = name or job_id or "job"
    game = carduploader_api.game_of_tcg(variants.job_tcg(job))
    changes = []
    if a.grades:
        _, capped, _ = audits.nm_status(audits.read_audits(p["audits"]), game)
        final = {s: g for s, (g, _, _) in grading.final_grades(grading.read_grades(a.grades)).items()}
        cc, holds = report.condition_changes(job, final, game, capped)
        changes += cc
        print(f"{len(cc)} condition change(s); {len(holds)} card(s) not graded or held")
    if a.pricing and not os.path.exists(os.path.join(a.pricing, f"priced-{tag}.json")):
        print(f"no priced-{tag}.json in {a.pricing} — price the job first; no price changes")
    elif a.pricing:
        priced = _load_priced(os.path.join(a.pricing, f"priced-{tag}.json"))
        tp = report.read_csv(os.path.join(a.pricing, f"terapeak-{tag}.csv"))
        pc, not_applied = report.price_changes(priced, tp, job)
        changes += pc
        print(f"{len(pc)} price change(s); {len(not_applied)} recommendation(s) not applied")
    if a.variants:
        vc, holds, undecided = variants.queue_changes(variants.read_queue(a.variants))
        changes += vc
        print(f"{len(vc)} variant switch(es); {len(holds)} held; {len(undecided)} undecided")
        if undecided:
            print("  undecided: " + ", ".join(undecided))
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.job)), f"changes-{tag}.json")
    variants.write_change_file(out, job_id, name, game, changes, variants.job_tcg(job))
    listed = sum(1 for c in changes if c["listed"])
    print(f"{len(changes)} change(s) -> {out}" + (f" ({listed} on LISTED cards — these "
                                                   "push to eBay)" if listed else ""))
    return 0


# ---------------------------------------------------------------- golden + audit


def golden_cmd(argv):
    ap = argparse.ArgumentParser(prog="cardcropper --cli golden",
                                 description="Before a batch: pick 10 golden cards (3+ borderline) "
                                             "to blind-grade, or score a blind check.")
    ap.add_argument("line", choices=wear.GAME_LINES)
    ap.add_argument("--pick", metavar="OUT", help="write the blind picks (ids only) to OUT")
    ap.add_argument("--score", metavar="GRADES",
                    help="CSV with id,grade from the blind check, compared with the set")
    ap.add_argument("--seed", type=int)
    a = ap.parse_args(argv)
    path = audits.golden_path(settings_dir(), a.line)
    audits.ensure_golden(path)
    golden = audits.read_golden(path)
    if a.pick:
        picks = audits.pick_blind(golden, rng=random.Random(a.seed))
        _write_csv(a.pick, ["id", "front", "back", "front_crops", "back_crops", "grade"],
                   [{k: g.get(k, "") for k in ("id", "front", "back", "front_crops",
                                              "back_crops")} for g in picks])
        print(f"{len(picks)} golden cards to grade blind -> {a.pick}")
        return 0
    if a.score:
        ours = {r["id"]: r.get("grade", "") for r in _read_csv(a.score)}
        picks = [g for g in golden if g["id"] in ours]
        stop, rows = audits.score_blind(picks, ours)
        for r in rows:
            print(f"  {r['id']} {r['card']}: ours {r['our_grade'] or '-'} true "
                  f"{r['true_grade']} {r['result']}")
        print("STOP — overgrade (or ungraded pick) in the blind check; do not grade the batch"
              if stop else "blind check passed")
        return 2 if stop else 0
    print(f"golden set {a.line}: {len(golden)} card(s) in {path}")
    return 0


def audit_cmd(argv):
    p = paths()
    ap = argparse.ArgumentParser(prog="cardcropper --cli audit",
                                 description="After a batch: pick 8 NM + 3 LP for an in-hand "
                                             "audit, record the results, or show NM status.")
    ap.add_argument("--grades", help="grades-<code>.csv (for --pick)")
    ap.add_argument("--pick", metavar="OUT")
    ap.add_argument("--record", metavar="RESULTS",
                    help="CSV: sku,card,our_grade,true_grade[,root_cause,fix,refund]")
    ap.add_argument("--batch", default="")
    ap.add_argument("--game", choices=batches.GAMES)
    ap.add_argument("--seed", type=int)
    a = ap.parse_args(argv)
    if a.pick:
        final = {s: g for s, (g, _, _) in grading.final_grades(grading.read_grades(a.grades)).items()}
        skus = audits.pick_audit(final, rng=random.Random(a.seed))
        _write_csv(a.pick, ["sku", "card", "our_grade", "true_grade", "root_cause", "fix",
                            "refund"], [{"sku": s, "our_grade": final[s]} for s in skus])
        print(f"{len(skus)} card(s) for the in-hand audit -> {a.pick}")
        return 0
    if a.record:
        if not a.game:
            sys.exit("--record needs --game")
        rows = audits.record_audit(p["audits"], p["errors"], a.batch, a.game,
                                   _read_csv(a.record))
        bad = [r for r in rows if r["result"] != "match"]
        print(f"recorded {len(rows)} audit result(s), {len(bad)} disagreement(s) logged "
              f"to {p['errors']}")
    for g in ([a.game] if a.game else batches.GAMES):
        rate, capped, why = audits.nm_status(audits.read_audits(p["audits"]), g)
        print(f"{g}: NM overgrade rate {rate:.1%}" + (f" — {why}" if capped else ""))
    return 0


def price_golden_cmd(argv):
    ap = argparse.ArgumentParser(prog="cardcropper --cli price-golden",
                                 description="Run the golden pricing tests through Comp Finder.")
    ap.add_argument("--base", default=pricing.COMP_FINDER)
    ap.add_argument("--evidence", help="saved Comp Finder response instead of calling it")
    ap.add_argument("--show", action="store_true",
                    help="print the sales behind every price, not just the failures")
    a = ap.parse_args(argv)
    if a.evidence:
        with open(a.evidence, encoding="utf-8") as fh:
            data = json.load(fh)
        ev = {r.get("id"): r for r in data.get("results", [])}
    else:
        ev = pricing.batch_comps(pricing.golden_cards(), a.base)
        # Kept so a failure can be looked into again without another search.
        saved = os.path.join(settings_dir(), "price-golden-evidence.json")
        with open(saved, "w", encoding="utf-8") as fh:
            json.dump({"results": list(ev.values())}, fh, indent=1)
    fails = 0
    for i, (title, expected, p, ok) in enumerate(pricing.golden_priced(ev)):
        fails += not ok
        got = p.suggested
        print(f"{'ok  ' if ok else 'FAIL'} {title}: expected ~£{expected:.2f}, got "
              + (f"£{got:.2f}" if got is not None else
                 "no sales — floor after a Terapeak check" if ok else "no price"))
        if not ok or a.show:
            print("\n".join(pricing.explain(p, ev.get(f"golden-{i}"))))
    return 1 if fails else 0
