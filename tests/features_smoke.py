"""
The batch workflow after cropping: batch codes and manifests, crop checks,
triage scores, grading rules, golden sets and audits, the variant queue, the
house pricing rules, the change file and the run report.

No network and no display. Comp Finder's answers are made up here in the
shape its title form returns (Comp Finder docs/BATCH_COMPS.md).
"""

import json
import os
import random
import shutil
import sys
import tempfile
import time

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cardcropper import (audits, batch, batches, checks, grading, imaging,  # noqa: E402
                         pricing, report, variants, wear)


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def raises(fn, exc=Exception):
    try:
        fn()
    except exc:
        return True
    return False


# ---------------------------------------------------------------- 2.1


def codes_checks():
    nc = batches.next_codes
    check(nc(["FZ"], "pokemon") == ["GA"], "GA follows FZ")
    check(nc(["FZ", "IP", "IQ - binder"], "pokemon", 2) == ["IR", "IS"],
          "next after the highest code, an empty batch included")
    check(nc(["MTG-I", "YGO-Z"], "pokemon") == ["AA"], "other games' codes are not Pokémon's")
    check(nc(["YGO-Y"], "yugioh", 3) == ["YGO-Z", "YGO-AA", "YGO-AB"], "YGO-Z then YGO-AA")
    check(nc([], "yugioh") == ["YGO-A"], "first YGO code")
    check(nc(["MTG-I"], "mtg") == ["MTG-J"], "MTG sequence")
    check(batches.sku("KA", 1) == "KA1" and batches.sku("YGO-A", 50) == "YGO-A50", "SKUs")
    check(raises(lambda: nc(["ZZ"], "pokemon")), "codes past ZZ must not wrap round")
    hist = {"jobs": [{"jobName": "FZ", "id": 1}, {"jobName": "IP", "id": 2,
                                                   "cards": []}, {"name": "IQ", "jobId": "x"}]}
    check(batches.latest_code(batches.job_names(hist), "pokemon") == "IQ",
          "every job name in /jobs/history is read")


def make_card(path, colour=(230, 200, 40), wear_px=0, scratch=False):
    W, H = 1000, 1400
    card = Image.new("RGB", (W, H), colour)
    d = ImageDraw.Draw(card)
    d.rectangle([60, 60, W - 60, H - 60], fill=(40, 90, 170))
    if wear_px:
        for x, y in ((4, 4), (W - 4 - wear_px, 4), (4, H - 4 - wear_px),
                     (W - 4 - wear_px, H - 4 - wear_px)):
            d.rectangle([x, y, x + wear_px, y + wear_px], fill=(250, 250, 250))
    if scratch:
        d.line([(300, 400), (420, 520)], fill=(255, 255, 255), width=6)
    bed = Image.new("RGB", (1400, 1800), (0, 0, 0))
    bed.paste(card, (200, 200))
    bed.save(path, quality=95)


def batch_and_check(work):
    scans = os.path.join(work, "scans")
    os.makedirs(scans)
    for i in range(3):
        make_card(os.path.join(scans, f"{i:02d}a.jpg"), colour=(230, 200, 40))
        make_card(os.path.join(scans, f"{i:02d}b.jpg"), colour=(0, 0, 60),
                  wear_px=(0, 0, 14)[i], scratch=i == 1)
    out = os.path.join(scans, "cropped")
    done, failed, _ = batch.run(batch.pair_sequential(batch.list_images(scans)), out)
    check(done == 3 and not failed, "crop run failed")
    os.remove(os.path.join(out, "0002_4_back-crops.jpg"))

    plan = batches.plan_batches([out], "pokemon", history=["FZ", "IP"], per_batch=2)
    check([b.code for b in plan] == ["IQ", "IR"], f"codes {[b.code for b in plan]}")
    check([c.sku for c in plan[0].cards] == ["IQ1", "IQ2"] and plan[1].cards[0].sku == "IR1",
          "SKUs are code + position within the batch")
    check(plan[0].cards[1].missing == ["back-crops"], "missing image not recorded")
    check(raises(lambda: batches.plan_batches([out], "pokemon", history=["IQ"],
                                              codes=["IQ"])), "a used code must be refused")
    j, c = batches.write_manifest(plan[0])
    back = batches.read_manifest(j)
    check(back.code == "IQ" and back.cards[1].missing == ["back-crops"], "manifest round trip")
    check(open(c, encoding="utf-8").read().startswith("sku,game,language"), "manifest CSV")
    check(batch.next_index(out) == 4, "a manifest in the folder must not count as a card")

    # Reference back from the clean backs; the scratched one is flagged.
    whole = batches.plan_batches([out], "pokemon", history=["FZ"], per_batch=50)[0]
    exacts = [imaging.straighten(imaging.load(c.back))[0] for c in whole.cards]
    ref = wear.build_reference([exacts[0], exacts[0], exacts[2]])
    clean = wear.compare_back(exacts[0], ref)
    scratched = wear.compare_back(exacts[1], ref)
    check(clean["flag"] == "", f"a clean back was flagged: {clean}")
    check(scratched["flag"] in ("review", "dmg-candidate") and scratched["longest"] >= 30,
          f"a 170px scratch was not flagged: {scratched}")

    rows = checks.check_batch(whole, reference=ref)
    by = {r["sku"]: r for r in rows}
    check(by["GA2"]["status"] == "INCOMPLETE", "missing crops not flagged")
    check(by["GA1"]["status"] == "OK" and by["GA1"]["back_points"] == 0,
          f"clean card: {by['GA1']}")
    check(by["GA3"]["back_corners_whitened"] == 4, f"worn corners not counted: {by['GA3']}")
    check(by["GA1"]["may_skip_review"] == "no", "no game is calibrated: never skip a look")
    check(by["GA3"]["whitening"].startswith("front: corners 0, top 0, R 0, bottom 0, L 0; "
                                            "back: corners 4"), by["GA3"]["whitening"])
    checks.write_checks(rows, checks.checks_path(whole))

    # HOLDs: a second card in frame, bed inside the outline; a black strip.
    two = Image.new("RGB", (2600, 1800))
    two.paste(Image.new("RGB", (1000, 1400), (230, 200, 40)), (200, 200))
    two.paste(Image.new("RGB", (1000, 1400), (230, 200, 40)), (1400, 200))
    two.save(os.path.join(work, "two.jpg"))
    res = checks.check_face(os.path.join(work, "two.jpg"))[0]
    check(any("second card" in h for h in res["holds"]), f"second card: {res}")
    cut = Image.new("RGB", (1400, 1800))
    c = Image.new("RGB", (1000, 1400), (230, 200, 40))
    ImageDraw.Draw(c).polygon([(0, 0), (250, 0), (0, 250)], fill=(0, 0, 0))
    cut.paste(c, (200, 200))
    cut.save(os.path.join(work, "cut.jpg"))
    res = checks.check_face(os.path.join(work, "cut.jpg"))[0]
    check(any("bed showing" in h for h in res["holds"]), f"bed showing: {res}")
    padded = Image.new("RGB", (800, 1100))
    padded.paste(Image.new("RGB", (650, 1060), (230, 200, 40)), (130, 20))
    check(checks.black_strips(padded) == ["left"], "solid black edge strip not flagged")


def whitening_checks():
    card = Image.new("RGB", (700, 980), (20, 30, 90))
    d = ImageDraw.Draw(card)
    d.line([(100, 2), (600, 2)], fill=(200, 200, 200), width=1)   # scanner hairline? no:
    counts = wear.whitening(card)
    check(counts["top"] == 0, f"a line along most of the edge is a hairline, not wear: {counts}")
    d.rectangle([300, 970, 304, 975], fill=(255, 255, 255))
    d.rectangle([3, 3, 7, 7], fill=(255, 255, 255))
    counts = wear.whitening(card)
    check(counts["bottom"] == 1 and counts["tl"] == 1, f"points not counted: {counts}")
    mtg = Image.new("RGB", (700, 980), (60, 40, 20))
    ImageDraw.Draw(mtg).rectangle([12, 12, 14, 14], fill=(255, 255, 255))
    check(wear.whitening(mtg, game="mtg", face="back")["tl"] == 0,
          "the printed dot on an MTG back is not wear")
    check(not wear.may_skip_review("yugioh", {"yugioh": {"calibrated": True,
                                                         "lp_or_worse_below_clean": 0,
                                                         "clean_threshold": 1}}),
          "Yu-Gi-Oh: every card is opened")
    check(not wear.may_skip_review("pokemon-en", {}), "uncalibrated lines never skip")
    ok, _ = wear.calibrate_line([{"grade": "LP", "points": 0}] + [{"grade": "NM", "points": 0}] * 40, 1)
    check(not ok, "an LP card below the clean threshold fails calibration")


# ---------------------------------------------------------------- 2.4 / 2.5


def grading_checks():
    every = set(grading.ALL_PANELS)
    A = grading.Assessment
    check(raises(lambda: grading.enforce(A("NM", looked=every - {"back-tl"})),
                 grading.RuleError), "NM with an unopened panel must be refused")
    check(raises(lambda: grading.enforce(A("NM", looked=every, unchecked={"front-left"})),
                 grading.RuleError), "NM over an unchecked panel must be refused")
    check(grading.enforce(A("NM", looked=every, points={"front": 1, "back": 1}))[0] == "NM",
          "2 points is NM")
    check(grading.enforce(A("NM", looked=every, points={"front": 1, "back": 2}))[0] == "LP",
          "3 points across both faces is LP")
    check(grading.enforce(A("NM", looked=every, corners_whitened={"back": 3}))[0] == "LP",
          "3 whitened corners: LP at best")
    check(grading.enforce(A("MP", crease=grading.CREASE_SHORT))[0] == "HP",
          "a short edge crease, ink intact: HP")
    check(grading.enforce(A("LP", crease=grading.CREASE_OTHER))[0] == "DMG",
          "any other crease: DMG")
    check(grading.enforce(A("DMG", crease=grading.CREASE_SHORT))[0] == "DMG", "never better")
    check(grading.worst(["NM", "LP", "NM"]) == "LP" and grading.worse("MP", "HOLD") == "HOLD",
          "the worse pass wins")
    check(grading.listing_grade("NM", "pokemon", grading.is_vintage("pokemon", 2001)) == "LP",
          "WotC NM lists as LP")
    check(grading.listing_grade("NM", "pokemon", grading.is_vintage("pokemon", None,
                                                                    "Holon Phantoms")) == "LP",
          "EX-era NM lists as LP")
    check(grading.listing_grade("NM", "pokemon", grading.is_vintage("pokemon", 2022)) == "NM",
          "modern NM lists as NM")
    check(grading.listing_grade("NM", "yugioh", True) == "NM", "YGO NM stays NM")
    check(grading.needs_in_hand("NM", 15) and not grading.needs_in_hand("NM", 14.99)
          and not grading.needs_in_hand("LP", 40), "NM £15+ goes in hand")


def audit_checks(work):
    golden = [{"id": f"g{i}", "card": f"c{i}", "true_grade": "LP" if i % 3 else "NM",
               "borderline": "yes" if i < 4 else ""} for i in range(30)]
    picks = audits.pick_blind(golden, rng=random.Random(1))
    check(len(picks) == 10 and sum(p["borderline"] == "yes" for p in picks) >= 3,
          "10 blind picks, 3+ borderline")
    stop, _ = audits.score_blind(picks, {p["id"]: p["true_grade"] for p in picks})
    check(not stop, "matching grades pass")
    lp = next(p for p in picks if p["true_grade"] == "LP")
    stop, rows = audits.score_blind(picks, dict({p["id"]: p["true_grade"] for p in picks},
                                                **{lp["id"]: "NM"}))
    check(stop and any(r["result"] == "OVERGRADE" for r in rows), "an overgrade stops the batch")

    final = {f"K{i}": ("NM" if i < 20 else "LP") for i in range(30)}
    pick = audits.pick_audit(final, rng=random.Random(2))
    check(sum(final[s] == "NM" for s in pick) == 8 and sum(final[s] == "LP" for s in pick) == 3,
          "8 NM + 3 LP")
    log, err = os.path.join(work, "audits.csv"), os.path.join(work, "errors.csv")
    audits.record_audit(log, err, "KA", "pokemon",
                        [{"sku": f"KA{i}", "our_grade": "NM", "true_grade": "NM"} for i in range(8)])
    check(not audits.nm_status(audits.read_audits(log), "pokemon")[1], "clean: not capped")
    audits.record_audit(log, err, "KB", "pokemon",
                        [{"sku": "KB1", "our_grade": "NM", "true_grade": "LP",
                          "root_cause": "missed back corner"}]
                        + [{"sku": f"KB{i}", "our_grade": "NM", "true_grade": "NM"}
                           for i in range(2, 9)])
    rate, capped, _ = audits.nm_status(audits.read_audits(log), "pokemon")
    check(capped and rate > 0.02, f"an overgrade above 2% caps NM ({rate})")
    check(audits.apply_cap("NM", capped) == "LP", "capped NM lists as LP")
    errs = audits.read_errors(err)
    check(len(errs) == 1 and errs[0]["root_cause"] == "missed back corner", "error log")
    for b in ("KC", "KD"):
        audits.record_audit(log, err, b, "pokemon",
                            [{"sku": f"{b}{i}", "our_grade": "NM", "true_grade": "NM"}
                             for i in range(8)])
    check(not audits.nm_status(audits.read_audits(log), "pokemon")[1],
          "two clean audits lift the cap")
    check(not audits.nm_status(audits.read_audits(log), "yugioh")[1], "per game")


# ---------------------------------------------------------------- 2.6 - 2.8


def sale(price, postage=0.0, title="Lugia 78/124 Fates Collide", why=None, date="2026-11-01"):
    return {"price": price, "postage": postage, "title": title, "date": date,
            "excluded_reason": why}


def pricing_checks(work):
    check(pricing.round_up(12.50, 2.49) == 12.99 and pricing.round_up(12.49, 2.49) == 12.49,
          ".49/.99 ladder, strictly up")
    check(pricing.round_up(1.20, 2.49) == 2.49 and pricing.round_up(1.20, 1.99) == 1.99,
          "floors")
    check(pricing.sale_total(sale(3.0, 6.0)) == 7.0, "postage capped at £4.00")
    check(pricing.percentile([1, 2, 3, 4, 5], 40) == 2.6, "PERCENTILE.INC")

    sold = [sale(p) for p in (3.0, 3.5, 4.0, 4.2, 5.0, 6.0)] + [
        sale(30.0, title="Lugia 78/124 PSA 10", why="graded"),
        sale(9.0, title="Lugia 78/124 Fates Collide Reverse Holo"),
        sale(3.9, postage=9.0, why="high_postage")]
    p = pricing.price_card("KA1", "Lugia 78/124 Fates Collide Pokemon NM", sold, current=4.49)
    check(p.n_sales == 7, f"graded and reverse sales out, high postage put back: {p.n_sales}")
    check(p.method == "p40" and p.suggested == 4.49, f"Lugia priced {p.suggested} by {p.method}")
    check(p.decision == "apply", p.reason)

    cosmos = [sale(48.0, 2.0, "Riolu Cosmos Holo BW33 promo"),
              sale(14.5, 0, "Riolu BW33 promo"), sale(52.0, 0, "Riolu BW33 Cosmos")]
    p = pricing.price_card("KA2", "Riolu (Cosmos Holo) BW33 Black and White Promos", cosmos,
                           current=14.99)
    check(p.n_sales == 2 and p.method == "lowest" and p.suggested == 50.49,
          f"Cosmos: {p.n_sales} sales, {p.suggested}")
    check(p.decision == "terapeak", "a rise on fewer than 4 sales goes to Terapeak")
    p = pricing.price_card("KA3", "Riolu BW33 Black and White Promos", cosmos, current=20.0)
    check(p.n_sales == 1 and p.suggested == 14.99 and p.decision == "apply",
          f"plain Riolu: {p.suggested} {p.decision}")
    p = pricing.price_card("YGO-A1", "Kuriboh LOB Yugioh NM", [sale(0.5, 0, "Kuriboh LOB")],
                           game="yugioh")
    check(p.suggested == 1.99, "Yu-Gi-Oh floor £1.99")
    p = pricing.price_card("KA4", "Lugia 78/124", [sale(20.0)] * 5, condition="MP")
    check(p.lp_price == 20.49 and p.suggested == 17.49, f"MP scale: {p.suggested}")
    p = pricing.price_card("KA5", "Lugia 78/124", [sale(2.9)] * 5, condition="MP")
    check(p.suggested == 2.99, "MP under £5 LP sells as LP")
    p = pricing.price_card("KA6", "Lugia 78/124", [], current=3.49)
    check(p.decision == "keep", "no comps: old price kept")
    p = pricing.price_card("KA7", "Lugia 78/124", [], status="timeout")
    check(p.decision == "hold", "a failed lookup is a hold")

    # Lots: Comp Finder kept "Fearow, Cacturn, Cradilly" (£22.41) as a Fearow
    # 36/100 comp on 21 Nov 2026 and the price came out £22.49.
    fearow = "Fearow 36/100 Majestic Dawn Pokemon"
    lot = [sale(22.41, title="Fearow, Cacturn, Cradilly")]
    p = pricing.price_card("G7", fearow, lot)
    check(p.n_sales == 0 and p.excluded[0]["why"] == "bundle (cardcropper)",
          f"a three-card lot was kept: {p.kept}")
    for t in ("Fearow 36/100 Majestic Dawn Pokemon Card", "Pokemon Fearow 036/100 MD",
              "Fearow 36/100, Majestic Dawn, Rare, NM", "FEAROW #36 MAJESTIC DAWN"):
        check(not pricing.looks_like_lot(fearow, t), f"a single card read as a lot: {t}")
    for t in ("Fearow, Pidgey & Spearow", "Fearow 36/100 x3", "Pokemon card bundle Fearow",
              "3x Fearow Majestic Dawn", "Fearow and Pidgeot"):
        check(pricing.looks_like_lot(fearow, t), f"a lot was kept: {t}")
    check(not pricing.looks_like_lot("Riolu BW33 Black and White Promos", "Riolu BW33 promo"),
          "a promo code is a number")
    check(pricing.looks_like_lot("Riolu BW33 Black and White Promos", "Riolu, Lucario, Zorua"),
          "a promo list without the code is a lot")
    check(not pricing.looks_like_lot("Dark Magician LOB-005", "Dark Magician LOB-EN005 1st"),
          "a Yu-Gi-Oh code is a number")
    res = {t: (got, ok) for t, _, got, ok in
           pricing.golden_check({"golden-6": {"status": "ok", "sold": lot}})}
    check(res[fearow] == (None, True), f"no sales of a floor card is the floor: {res[fearow]}")

    # Comp Finder client: 35 a call, one after another.
    calls = []

    def post(url, body):
        calls.append(len(body["cards"]))
        return {"ok": True, "results": [{"id": c["id"], "status": "ok", "sold": [sale(4.0)] * 4}
                                        for c in body["cards"]]}
    ev = pricing.batch_comps([{"id": f"K{i}", "title": "Lugia 78/124"} for i in range(80)],
                             post=post)
    check(calls == [35, 35, 10] and len(ev) == 80, f"calls {calls}")

    # The register: reuse only eBay sold comps, and only recent.
    reg = pricing.Register(os.path.join(work, "reg.sqlite"))
    p = pricing.price_card("KA1", "Lugia 78/124 Fates Collide Pokemon NM", [sale(4.0)] * 5)
    reg.record(p, now=time.time() - 30 * 86400)
    check(reg.reusable("Lugia 78/124 Fates Collide Pokemon LP", "NM") is None, "stale row reused")
    reg.record(p, method="manual")
    check(reg.reusable("Lugia 78/124 Fates Collide Pokemon", "NM") is None,
          "a row that is not eBay sold comps was reused")
    reg.record(p)
    hit = reg.reusable("lugia 78/124  fates collide pokemon MP", "NM")
    check(hit and hit["price"] == 4.49, f"recent comps row not reused: {hit}")
    reg.close()

    # Golden pricing tests run on whatever evidence is given.
    golden_ev = {"golden-3": {"status": "ok", "sold": [sale(4.2)] * 6}}
    res = {t: (exp, got, ok) for t, exp, got, ok in pricing.golden_check(golden_ev)}
    check(res["Lugia 78/124 Fates Collide Pokemon"][2], "golden Lugia")


def job_fixture():
    def skus(variants_):
        return [{"sku": 1000 + i * 10 + j, "variant": v, "language": "English",
                 "condition": c, "printing_id": i, "tcgplayer_name": v}
                for i, v in enumerate(variants_)
                for j, c in enumerate(("Near Mint", "Lightly Played", "Moderately Played",
                                       "Heavily Played", "Damaged"))]
    return {"jobId": "job-1", "jobName": "KA", "settings": {"tcg": "pokemon english"},
            "results": {"cards": [
                {"inventoryId": "i1", "sku": "KA1", "title": "Lugia 78/124 Fates Collide Pokemon NM",
                 "variant": "Normal", "condition": "NM", "price": "2.49", "status": "processed",
                 "year": 2016, "setName": "Fates Collide", "skus": skus(["Normal", "Reverse Holofoil"]),
                 "imageUrls": {"front": "f1"}, "databaseImageUrl": "d1"},
                {"inventoryId": "i2", "sku": "KA2", "title": "Natu 59/75 Neo Discovery Pokemon NM",
                 "variant": "Unlimited", "condition": "NM", "price": "2.49", "status": "listed",
                 "year": 2001, "setName": "Neo Discovery",
                 "skus": skus(["Unlimited", "1st Edition"])},
                {"inventoryId": "i3", "sku": "KA3",
                 "title": "Charizard World Championship 2004 Base Pokemon NM",
                 "variant": "Holofoil", "condition": "NM", "price": "2.49", "status": "processed",
                 "year": 2004, "setName": "Base Set", "skus": skus(["Holofoil", "1st Edition"])},
                {"inventoryId": "i4", "sku": "KA4", "title": "Aron 44/100 Crystal Guardians NM",
                 "variant": "Normal", "condition": "NM", "price": "2.49", "status": "processed",
                 "year": 2006, "setName": "Crystal Guardians", "skus": skus(["Normal"])},
            ]}}


def variant_and_report_checks(work):
    job = job_fixture()
    q = variants.build_queue(job)
    check([r["sku"] for r in q] == ["KA1", "KA2", "KA3"], f"queue {[r['sku'] for r in q]}")
    check("world champ" in q[2]["hint"].lower(), "WCD title not hinted")
    check(all(not r["decision"] for r in q), "the queue must never decide by itself")
    q[0].update(decision="switch", target_variant="Reverse Holofoil")
    q[1].update(decision="correct")
    q[2].update(decision="HOLD", note="World Championship deck")
    vc, holds, undecided = variants.queue_changes(q)
    check(len(vc) == 1 and vc[0]["value"] == "Reverse Holofoil" and vc[0]["inventoryId"] == "i1",
          f"variant change {vc}")
    check(holds[0][0] == "KA3" and not undecided, "holds")

    final = {"KA1": "NM", "KA2": "NM", "KA3": "HOLD"}
    cc, holds = report.condition_changes(job, final, "pokemon")
    check([(c["sku"], c["value"]) for c in cc] == [("KA2", "LP")],
          f"vintage NM lists LP, modern NM unchanged: {cc}")
    check(cc[0]["listed"], "a listed card is marked so the extension shows it apart")
    cc, _ = report.condition_changes(job, final, "pokemon", capped=True)
    check({c["sku"] for c in cc} == {"KA1", "KA2"}, "capped NM lists as LP")

    check(variants.validate_changes([variants.change("YGO-A1", "price", 0.99)], "yugioh"),
          "Yu-Gi-Oh under £1.99 must be refused")
    check(raises(lambda: variants.write_change_file(
        os.path.join(work, "bad.json"), "j", "YGO-A", "yugioh",
        [variants.change("YGO-A1", "price", 0.99)])), "refused change file written")

    ev = {"KA1": {"status": "ok", "sold": [sale(4.0)] * 12},
          "KA2": {"status": "ok", "sold": [sale(30.0, title="Natu 59/75 Neo Discovery")]},
          "KA3": {"status": "ok", "sold": []},
          "KA4": {"status": "timeout", "sold": []}}
    reg = pricing.Register(os.path.join(work, "reg2.sqlite"))
    priced = report.price_job(job, "pokemon", ev, reg, grades={"KA2": "LP"}, batch="KA")
    reg.close()
    by = {p.sku: p for p in priced}
    check(by["KA1"].suggested == 4.49 and by["KA1"].decision == "apply", "KA1")
    check(by["KA2"].condition == "LP" and by["KA2"].decision == "terapeak", "KA2")
    check(by["KA3"].decision == "keep" and by["KA4"].decision == "hold", "KA3/KA4")
    tp_path = report.write_terapeak(priced, os.path.join(work, "tp.csv"))
    changes, not_applied = report.price_changes(priced, report.read_csv(tp_path), job)
    check([c["sku"] for c in changes] == ["KA1"], f"only applied prices change: {changes}")
    lines, ready = report.run_report(priced, report.read_csv(tp_path), not_applied, total=4)
    check(not ready and lines[-1].startswith("NOT READY"), "skipped cards: not ready")
    check("SKUs priced: 2 / 4" in lines[0] and "10+ 1" in lines[1], lines[:2])
    check(any("kept at an old price" in ln and ln.endswith("1") for ln in lines), lines)

    rows = report.read_csv(tp_path)
    for r in rows:
        r.update(checked="yes", terapeak_price="24.99" if r["sku"] == "KA2" else "2.49")
    import csv as _csv
    with open(tp_path, "w", encoding="utf-8", newline="") as fh:
        w = _csv.DictWriter(fh, fieldnames=report.TERAPEAK_FIELDS)
        w.writeheader()
        w.writerows(rows)
    changes, not_applied = report.price_changes(priced, report.read_csv(tp_path), job)
    check({c["sku"] for c in changes} == {"KA1", "KA2"}, "a Terapeak price is applied")
    ok_priced = [p for p in priced if p.sku != "KA4"]
    _, not_applied = report.price_changes(ok_priced, report.read_csv(tp_path), job)
    lines, ready = report.run_report(ok_priced, report.read_csv(tp_path), not_applied, total=3)
    check(ready, "\n".join(lines))
    path = variants.write_change_file(os.path.join(work, "changes.json"), "job-1", "KA",
                                      "pokemon", changes + vc, "pokemon english")
    data = json.load(open(path, encoding="utf-8"))
    check(data["kind"] == "cardcropper-changes" and len(data["changes"]) == 3, "change file")


def main():
    work = tempfile.mkdtemp(prefix="cc-features-")
    try:
        codes_checks()
        batch_and_check(work)
        whitening_checks()
        grading_checks()
        audit_checks(work)
        pricing_checks(work)
        variant_and_report_checks(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("features smoke: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
