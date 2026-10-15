"""
The conditioning pipeline end to end, on generated scans and a fake
CardUploader export whose PicURLs are file:// links — no network, no real
card, no display.

What it pins down are the things that go wrong silently: a worn back ranking
as clean, a grade written with condition strings nobody has seen, a photo
prepended over the gallery thumbnail, a filled-in review sheet overwritten by
a re-run, and an Excel-mangled card number going back out as a date.
"""

import csv
import json
import os
import pathlib
import shutil
import sys
import tempfile

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cardcropper import carduploader as cu          # noqa: E402
from cardcropper import conditioning, scoring       # noqa: E402

#: A cut-down CardUploader header: the columns the pipeline touches, plus one
#: it does not, to prove untouched columns survive the round trip.
HEADER = ["*Action(SiteID=UK|Country=GB|Currency=GBP|Version=1193)", "CustomLabel",
          "*Title", "PicURL", "*ConditionID", "C:Card Condition",
          "CD:Card Condition - (ID: 40001)", "*Description", "*StartPrice",
          "*Quantity", "*C:Set", "*C:Card Number", "*C:Card Name"]


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def make_back(path, wear=0, angle=0.4):
    """
    A Pokémon-style back: navy border (value 47) round a lighter interior, on
    a black bed. `wear` is the side, in scan pixels, of a whitening blob put on
    every corner's border.
    """
    W, H = 1000, 1400
    card = Image.new("RGB", (W, H), (0, 0, 47))
    d = ImageDraw.Draw(card)
    d.rectangle([60, 60, W - 60, H - 60], fill=(40, 90, 170))
    if wear:
        for x, y in ((10, 10), (W - 10 - wear, 10), (10, H - 10 - wear),
                     (W - 10 - wear, H - 10 - wear)):
            d.rectangle([x, y, x + wear, y + wear], fill=(225, 225, 230))
    bed = Image.new("RGB", (1400, 1800), (0, 0, 0))
    bed.paste(card, (200, 200))
    bed.rotate(angle, resample=Image.BICUBIC, fillcolor=(0, 0, 0)).save(path, quality=95)


def make_front(path, angle=-0.3):
    W, H = 1000, 1400
    card = Image.new("RGB", (W, H), (230, 200, 40))
    ImageDraw.Draw(card).rectangle([60, 80, W - 60, H - 160], fill=(120, 150, 90))
    bed = Image.new("RGB", (1400, 1800), (0, 0, 0))
    bed.paste(card, (200, 200))
    bed.rotate(angle, resample=Image.BICUBIC, fillcolor=(0, 0, 0)).save(path, quality=95)


def write_export(path, rows):
    sheet = cu.Sheet(HEADER, [])
    for r in rows:
        sheet.rows.append([r.get(c, "") for c in HEADER])
    sheet.write(path)


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


class FakeUploader:
    """Hosts nothing; fails on request, so per-card failure can be checked."""

    def __init__(self, fail_skus=()):
        self.fail = set(fail_skus)
        self.n = 0

    def upload(self, local_path, sku):
        check(os.path.getsize(local_path) > 1024, "listing photo was not written")
        if sku in self.fail:
            raise OSError("bucket not found (is migration 012 applied?)")
        self.n += 1
        return f"https://example.supabase.co/storage/v1/object/public/listing-photos/" \
               f"{conditioning.PHOTO_PREFIX}{sku}/{self.n:04d}.jpg"


def main():
    work = tempfile.mkdtemp(prefix="cardcropper-condition-")
    try:
        _run(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("conditioning smoke: ok")
    return 0


def _run(work):
    scans = os.path.join(work, "scans")
    os.makedirs(scans)

    def url(name):
        return pathlib.Path(os.path.join(scans, name)).as_uri()

    make_front(os.path.join(scans, "front.jpg"))
    make_back(os.path.join(scans, "clean.jpg"))
    make_back(os.path.join(scans, "worn.jpg"), wear=24)
    make_back(os.path.join(scans, "nick.jpg"), wear=4)
    with open(os.path.join(scans, "broken.jpg"), "w") as fh:
        fh.write("not an image")

    desc = "<p>Card shown is the card you receive.</p>"
    base = {"*Action(SiteID=UK|Country=GB|Currency=GBP|Version=1193)": "Add",
            "*ConditionID": "4000", "C:Card Condition": "Near Mint or Better:",
            "CD:Card Condition - (ID: 40001)": "Near Mint or Better: -(ID: 400010)",
            "*Description": desc, "*StartPrice": "2.49", "*Quantity": "1"}
    rows = [
        dict(base, CustomLabel="26.10.15-001-001", **{
            "*Title": "Pikachu 065/202 Sword & Shield Pokemon NM",
            "PicURL": url("front.jpg") + "|" + url("worn.jpg"),
            "*C:Set": "Sword & Shield", "*C:Card Number": "065/202"}),
        dict(base, CustomLabel="26.10.15-001-002", **{
            "*Title": "Pokegear 3.0 174/202 Sword & Shield Pokemon NM",
            "PicURL": url("front.jpg") + "|" + url("clean.jpg"),
            "*C:Set": "Sword & Shield", "*C:Card Number": "174/202"}),
        dict(base, CustomLabel="26.10.15-001-003", **{
            "*Title": "Pokegear 3.0 174/202 Sword & Shield Pokemon NM",
            "PicURL": url("front.jpg") + "|" + url("nick.jpg"),
            "*C:Set": "Sword & Shield", "*C:Card Number": "174/202"}),
        dict(base, CustomLabel="26.10.15-001-004", **{
            "*Title": "Natu 4/99 Neo Discovery Pokemon NM",
            "PicURL": url("front.jpg") + "|" + url("clean.jpg"),
            "*C:Set": "Neo Discovery", "*C:Card Number": "Apr-99"}),   # Excel's doing
        dict(base, CustomLabel="26.10.15-001-005", **{
            "*Title": "Counter Catcher 91/111 Crimson Invasion Pokemon NM",
            "PicURL": url("front.jpg"),
            "*C:Set": "Crimson Invasion", "*C:Card Number": "91/111"}),
        dict(base, CustomLabel="26.10.15-001-006", **{
            "*Title": "Devoured Field 93/111 Burning Shadows Pokemon NM",
            "PicURL": url("front.jpg") + "|" + url("broken.jpg"),
            "*C:Set": "Burning Shadows", "*C:Card Number": "93/111"}),
        dict(base, CustomLabel="YGO-A1", **{
            "*Title": "Dark Magician LOB-005 Yugioh NM",
            "PicURL": url("front.jpg") + "|" + url("clean.jpg"),
            "*C:Set": "Legend of Blue Eyes", "*C:Card Number": "LOB-005"}),
    ]
    export = os.path.join(work, "export.csv")
    write_export(export, rows)
    with open(export, "rb") as fh:
        original = fh.read()

    # ---- the score, on its own
    from cardcropper import imaging
    clean = scoring.score_back(imaging.straighten(imaging.load(
        os.path.join(scans, "clean.jpg")))[0])
    worn = scoring.score_back(imaging.straighten(imaging.load(
        os.path.join(scans, "worn.jpg")))[0])
    check(clean["worst_corner_pct"] == 0 and clean["wear_total_pct"] == 0,
          f"a clean back scored {clean}")
    check(worn["worst_corner_pct"] > 3, f"a worn back scored only {worn}")
    check(45 <= clean["border_base"] <= 50, f"border_base {clean['border_base']} "
                                            "is not the navy it was drawn with")

    # ---- prepare
    out = os.path.join(work, "out")
    uploader = FakeUploader(fail_skus={"26.10.15-001-003"})
    res = conditioning.prepare(export, out, uploader=uploader)
    with open(export, "rb") as fh:
        check(fh.read() == original, "the original export was modified")

    tri = read_csv(res.triage_path)
    check([r["sku"] for r in tri][:1] == ["26.10.15-001-001"],
          "triage is not sorted worst corner first")
    check(list(tri[0].keys()) == conditioning.TRIAGE_FIELDS, "triage.csv columns changed")
    band = {r["sku"]: r["triage"] for r in tri}
    check(band["26.10.15-001-001"] == "likely-worse", f"worn back: {band}")
    check(band["26.10.15-001-002"] == "likely-nm", f"clean back: {band}")
    check(band["26.10.15-001-003"] == "review", f"one small nick per corner: {band}")
    check(band["26.10.15-001-005"] == "no-scan", f"one image in PicURL: {band}")
    check(band["26.10.15-001-006"] == "error", f"unreadable back: {band}")
    check(band["YGO-A1"] == "review", "a Yu-Gi-Oh back must never be called likely-nm "
                                      "off a score fitted to Pokémon backs")
    by_sku = {r["sku"]: r for r in tri}
    check(by_sku["26.10.15-001-004"]["check_back_era"] == "yes", "WotC set not flagged")
    check(by_sku["26.10.15-001-004"]["number"] == "4/99",
          f"Excel date not repaired: {by_sku['26.10.15-001-004']['number']}")
    check(by_sku["26.10.15-001-002"]["copies"] == "2"
          and by_sku["26.10.15-001-003"]["duplicate_of"],
          "duplicate copies not grouped")
    check(not res.warnings, f"unexpected warnings: {res.warnings}")

    card = os.path.join(out, "cards", "26.10.15-001-001")
    for name in ("front.jpg", "back.jpg", "corners-back.jpg", "crops-back.jpg",
                 "crops-front.jpg", "listing-front.jpg", "listing-back.jpg"):
        check(os.path.getsize(os.path.join(card, name)) > 1024, f"{name} missing")
    check(max(Image.open(os.path.join(card, "crops-back.jpg")).size) <= 1568,
          "grading sheet would be downscaled before a model reads it")

    review = read_csv(res.review_path)
    check(list(review[0].keys()) == conditioning.REVIEW_FIELDS, "review columns changed")
    rv = {r["sku"]: r for r in review}
    check(rv["26.10.15-001-005"]["grade"] == "HOLD", "no-scan should be pre-held")
    check(all(not rv[s]["grade"] for s in ("26.10.15-001-001", "26.10.15-001-002")),
          "the machine filled in a grade nobody saw")
    check(rv["26.10.15-001-001"]["look_at"].endswith("corners-back.jpg"),
          "likely-worse should point at the corner sheet")

    # ---- photos: appended, never prepended; a failed card is left alone
    photos = {r["CustomLabel"]: r["PicURL"].split("|") for r in read_csv(res.photos_csv)}
    first = photos["26.10.15-001-001"]
    check(len(first) == 4 and first[0] == url("front.jpg"),
          f"photos not appended after the scans: {first}")
    check(len(photos["26.10.15-001-003"]) == 2, "a failed upload changed its PicURL")
    check(any(s == "26.10.15-001-003" for s, _ in res.upload_failures),
          "the failed upload was not reported")

    # A re-run on the with-photos copy still finds exactly two scans, and does
    # not overwrite a review sheet someone has started filling in.
    review[0]["grade"] = "MP"
    with open(res.review_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=conditioning.REVIEW_FIELDS)
        w.writeheader()
        w.writerows(review)
    again = conditioning.prepare(res.photos_csv, out, crops=False)
    check(again.review_kept, "a filled-in review sheet was overwritten")
    check({r["sku"]: r["triage"] for r in again.records}["26.10.15-001-001"]
          == "likely-worse", "the hosted photos were mistaken for scans")

    # ---- apply: only NM strings are known, so Pokémon (NM lists as LP) holds
    grades = {"26.10.15-001-001": ("MP", "Whitening on all four back corners.", "yes"),
              "26.10.15-001-002": ("NM", "", "yes"),
              "26.10.15-001-003": ("NM", "", "yes"),
              "26.10.15-001-004": ("HOLD", "", "yes"),
              "26.10.15-001-005": ("", "", ""),
              "YGO-A1": ("NM", "", "yes")}
    for r in review:
        g = grades.get(r["sku"])
        if g:
            r["grade"], r["flaw_note"], r["looked"] = g
            r["evidence"] = "vintage front, modern back" if g[0] == "HOLD" else "seen"
    with open(res.review_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=conditioning.REVIEW_FIELDS)
        w.writeheader()
        w.writerows(review)

    cmap = os.path.join(work, "condition-map.json")
    cu.ensure_condition_map(cmap)
    applied = conditioning.apply_grades(export, res.review_path, out, cmap)
    check(applied.written == 1, f"only the Yu-Gi-Oh NM is writable, got {applied.written}")
    check(set(applied.missing_grades) == {"LP", "MP"}, applied.missing_grades)
    written = read_csv(applied.out_path)
    check([r["CustomLabel"] for r in written] == ["YGO-A1"], "wrong rows written")
    check(written[0]["*ConditionID"] == "4000", "Yu-Gi-Oh NM must stay NM")
    held = {r["CustomLabel"]: r["hold_reason"] for r in read_csv(applied.held_path)}
    check("vintage front" in held["26.10.15-001-004"], "hold reason lost")
    check("rescan" in held["26.10.15-001-006"], "an unreadable scan should hold for a rescan")
    check("not graded" in held["26.10.15-001-005"], "an ungraded card was not held")
    check("listed as LP" in held["26.10.15-001-002"], "NM->LP house rule not applied")

    # ...and once the strings have been harvested from a real export, they are.
    with open(cmap, encoding="utf-8") as fh:
        m = json.load(fh)
    m["LP"] = {"*ConditionID": "5000", "C:Card Condition": "Light Play:",
               "CD:Card Condition - (ID: 40001)": "Light Play: -(ID: 400011)"}
    m["MP"] = {"*ConditionID": "6000", "C:Card Condition": "Moderate Play:",
               "CD:Card Condition - (ID: 40001)": "Moderate Play: -(ID: 400012)"}
    with open(cmap, "w", encoding="utf-8") as fh:
        json.dump(m, fh)
    applied = conditioning.apply_grades(export, res.review_path, out, cmap)
    written = {r["CustomLabel"]: r for r in read_csv(applied.out_path)}
    check(len(written) == 4, f"expected 4 written, got {sorted(written)}")
    pika = written["26.10.15-001-001"]
    check(pika["*ConditionID"] == "6000" and pika["*Title"].endswith(" MP"),
          f"MP not written: {pika['*Title']}")
    check(pika["*Description"] == desc + "<br>Whitening on all four back corners.",
          "flaw note should be appended to the boilerplate, not replace it")
    check(written["26.10.15-001-002"]["*Title"].endswith(" LP"), "NM Pokémon not listed LP")
    check("26.10.15-001-004" not in written, "a HOLD was written")
    check(written["26.10.15-001-001"]["*Action(SiteID=UK|Country=GB|Currency=GBP|"
                                      "Version=1193)"] == "Add",
          "an untouched column did not survive")
    check(any(g == "LP" and len(s) == 2 for _, g, s in applied.price_groups),
          "same title + same grade should price once")

    # A map that contradicts the confirmed NM strings is refused outright.
    m["NM"] = dict(m["NM"], **{"*ConditionID": "1000"})
    with open(cmap, "w", encoding="utf-8") as fh:
        json.dump(m, fh)
    try:
        cu.load_condition_map(cmap)
        check(False, "a contradicting NM mapping should be refused")
    except ValueError:
        pass

    # ---- Excel repair needs the title to agree
    check(cu.repair_card_number("Apr-99", "Natu 4/99 Neo") == ("4/99", "repaired"), "Mon-YY")
    check(cu.repair_card_number("12-Apr", "Foo 4/12 Bar")[0] == "4/12", "D-Mon")
    check(cu.repair_card_number("Apr-99", "no number here")[1] == "unrepaired",
          "a date the title cannot vouch for should be left and reported")
    check(cu.repair_card_number("TG05/TG30", "x")[1] == "ok", "a real number was touched")

    # ---- calibration: seen grades only, and a fit that keeps the bands safe
    ledger = os.path.join(work, "calibration.csv")
    thresholds = os.path.join(work, "thresholds.json")
    lines = conditioning.calibrate(res.review_path, res.triage_path, ledger, thresholds)
    check(any("not enough graded cards" in ln for ln in lines),
          "a fit should be refused on a handful of cards")
    rows = [{"sku": f"c{i}", "grade": g, "worst_corner_pct": c, "wear_total_pct": t}
            for i, (g, c, t) in enumerate([("NM", 0.05, 0.0), ("NM", 0.1, 0.01),
                                           ("NM", 0.12, 0.0), ("LP", 0.41, 0.04),
                                           ("LP", 0.63, 0.05), ("LP", 2.49, 0.41),
                                           ("MP", 2.85, 0.52), ("MP", 9.76, 1.73),
                                           ("HP", 15.0, 3.1)])]
    added, skipped = scoring.record_grades(
        [{"sku": r["sku"], "grade": r["grade"], "looked": "yes"} for r in rows]
        + [{"sku": "c0", "grade": "LP", "looked": "no"}], rows, ledger)
    check(added == 9 and len(skipped) == 1, f"looked=no must not calibrate: {skipped}")
    proposed, _ = scoring.fit(scoring.read_ledger(ledger))
    check(proposed is not None, "nine graded cards should give a proposal")
    for r in rows:
        band = scoring.triage(r, proposed)
        check(not (band == "likely-nm" and r["grade"] != "NM"),
              f"fitted thresholds call {r['grade']} card likely-nm")
        check(not (band == "likely-worse" and r["grade"] in ("NM", "LP")),
              f"fitted thresholds call {r['grade']} card likely-worse")
    proposed.save(thresholds)
    check(scoring.Thresholds.load(thresholds) == proposed, "thresholds do not round-trip")

    # ---- the batch sanity check: a missed edge reads as low border_base
    bad = [{"sku": str(i), "border_base": 8.0, "triage": "likely-worse"} for i in range(12)]
    check(scoring.sanity_check(bad), "a batch of border_base 8 must be called out")


if __name__ == "__main__":
    sys.exit(main())
