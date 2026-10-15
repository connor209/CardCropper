"""
Conditioning a CardUploader batch: the part between the CSV export and pricing.

Two halves, with a person in between:

1. `prepare()` does the cheap work. It downloads every card's two scans from
   `PicURL`, straightens them, scores the back border, cuts crops, and writes a
   `triage.csv` sorted worst corner first, plus a `conditioning-review.csv` for
   the grades to go in. Looking at a card is the expensive part, so the output
   is arranged so that as few cards as possible need looking at.

2. Someone works down the ranking and fills in the review sheet: grade, flaw
   note, confidence, the evidence they saw, and whether they actually looked.

3. `apply_grades()` writes those grades into a COPY of the export — but only
   for grades whose eBay condition strings are known. Everything it cannot
   write honestly is held back and listed, never guessed.

The original export is never modified. Every file is written alongside it.
"""

import csv
import html
import io
import os
import re
import time
import urllib.parse
import urllib.request
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from . import carduploader as cu
from . import imaging, scoring

TRIAGE_FIELDS = ["sku", "title", "set", "number", "triage", "worst_corner_pct",
                 "wear_total_pct", "corner_tl", "corner_tr", "corner_bl", "corner_br",
                 "edge_top", "edge_bottom", "edge_left", "edge_right", "border_base",
                 "deskew_deg", "duplicate_of", "copies", "check_back_era", "note"]

#: The seven columns are the review sheet proper. `triage` and `look_at` trail
#: behind as working aids — which band a card fell in, and which file to open
#: for it — so the sheet can be worked down without the triage beside it.
REVIEW_FIELDS = ["sku", "title", "grade", "flaw_note", "confidence", "evidence",
                 "looked", "triage", "look_at"]

BANDS = ("likely-worse", "review", "likely-nm", "no-scan", "error")
GRADES = scoring.GRADES
HOLD = "HOLD"

#: Pokémon cards graded NM are LISTED as LP. That is a house rule applied at
#: listing time; the grade itself stays honest in the review sheet. Yu-Gi-Oh
#: NM stays NM.
LIST_AS = {"pokemon": {"NM": "LP"}, "yugioh": {}}

#: Any crease makes a card HP at best (house decision, 15 Oct 2026, adopting
#: the 9 Oct crease definition over the guide's "a single light crease: MP").
#: A grade above HP whose notes mention a crease is a contradiction, and it is
#: held rather than resolved either way: the crease may be real and the grade
#: wrong, or the word may be loose. "No crease" and the like do not count.
CREASE_FLOOR = "HP"
_CREASE = re.compile(r"\bcreas(e|ed|es|ing)\b", re.I)
_NO_CREASE = re.compile(r"\b(no|not a|without|free of|zero)\s+(visible\s+)?creas", re.I)


def mentions_crease(*texts):
    text = " ".join(t or "" for t in texts)
    return bool(_CREASE.search(_NO_CREASE.sub("", text)))

#: Where hosted listing photos go inside the bucket. Also how a re-run
#: recognises its own photos already appended to a PicURL.
PHOTO_PREFIX = "conditioning/"


def safe_name(sku):
    """A SKU as a folder name — Windows refuses a handful of characters."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", sku).strip(" .")
    return name or "unnamed"


# ---------------------------------------------------------------- fetching

FETCH_RETRIES = 3


def fetch(url, path, refresh=False):
    """
    Cached download: a scan already on disk is never fetched again, so a run
    that stops part-way picks up where it left off for the price of the
    scoring alone. Written to a temp name first, so a download cut off half
    way is not cached as a broken image.
    """
    if os.path.exists(path) and os.path.getsize(path) and not refresh:
        with open(path, "rb") as fh:
            return fh.read()
    last = None
    for attempt in range(1, FETCH_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "CardCropper"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            break
        except Exception as exc:                        # noqa: BLE001
            last = exc
            if attempt < FETCH_RETRIES:
                time.sleep(attempt)
    else:
        raise OSError(f"download failed: {last}")
    tmp = path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    return data


# ---------------------------------------------------------------- hosting


class SupabaseUploader:
    """
    Puts listing photos in a PUBLIC Supabase Storage bucket.

    Plain REST rather than an SDK: it is one POST. Needs migration 012, which
    creates the `listing-photos` bucket — without it every upload fails, and
    each failure is reported against its card.
    """

    def __init__(self, base_url, key, bucket="listing-photos"):
        self.base = base_url.rstrip("/")
        self.key = key
        self.bucket = bucket

    @classmethod
    def from_env(cls, bucket="listing-photos"):
        url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL", "").strip()
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not (url and key):
            return None
        return cls(url, key, bucket)

    def upload(self, local_path, sku):
        """
        Upload one photo and return its public URL, or raise.

        **A fresh path every time, never reused.** eBay caches pictures by
        URL, so new bytes at an old path change nothing a buyer sees and look
        exactly like the revise failing.

        **Uploaded is not the same as listable.** eBay fails silently on a
        picture it cannot fetch — the listing reports success with fewer
        photos than were sent — so the URL is fetched back before it is used.
        """
        key = f"{PHOTO_PREFIX}{urllib.parse.quote(sku, safe='')}/{uuid.uuid4().hex}.jpg"
        with open(local_path, "rb") as fh:
            data = fh.read()
        req = urllib.request.Request(
            f"{self.base}/storage/v1/object/{self.bucket}/{key}", data=data,
            method="POST", headers={"Authorization": f"Bearer {self.key}",
                                    "apikey": self.key, "Content-Type": "image/jpeg",
                                    "x-upsert": "false"})
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
        url = f"{self.base}/storage/v1/object/public/{self.bucket}/{key}"
        if not self._fetchable(url):
            raise OSError(f"uploaded but not fetchable: {url}")
        return url

    @staticmethod
    def _fetchable(url):
        try:
            req = urllib.request.Request(url, headers={"Range": "bytes=0-64"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status in (200, 206) and \
                    (r.headers.get("Content-Type") or "").startswith("image/")
        except Exception:                               # noqa: BLE001
            return False


def append_photo_urls(sheet, urls_by_sku):
    """
    A copy of `sheet` with each card's listing photos appended to its PicURL.

    **Append, never prepend.** The first URL is the gallery thumbnail a buyer
    sees in search results, and it has to stay the front of the card.

    **Running twice adds nothing the second time.** A URL already there is
    skipped, so a partial run can simply be redone.
    """
    out = sheet.copy()
    added = 0
    for row in out.rows:
        extra = urls_by_sku.get(out.get(row, cu.SKU), [])
        have = cu.pic_urls(out.get(row, cu.PICS))
        new = [u for u in extra if u not in have]
        if new:
            out.set(row, cu.PICS, "|".join(have + new))
            added += len(new)
    return out, added


# ---------------------------------------------------------------- prepare


@dataclass
class PrepareResult:
    out_dir: str
    records: list = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)
    warnings: list = field(default_factory=list)
    duplicates: dict = field(default_factory=dict)
    triage_path: str = ""
    review_path: str = ""
    review_kept: bool = False
    photos_csv: str = ""
    photos_added: int = 0
    upload_failures: list = field(default_factory=list)
    stopped: bool = False

    def summary(self):
        lines = [f"{len(self.records)} card(s) -> {self.triage_path}"]
        for band in BANDS:
            if self.counts[band]:
                lines.append(f"  {band:<13}{self.counts[band]}")
        if self.stopped:
            lines.append("  (stopped early — the rest of the batch was not processed)")
        vintage = [r for r in self.records if r.get("check_back_era")]
        if vintage:
            lines.append(f"{len(vintage)} WotC-era card(s): open front and back together — "
                         "a modern swirl back on a vintage front is a HOLD, not a grade")
        if self.duplicates:
            surplus = sum(len(v) - 1 for v in self.duplicates.values())
            lines.append(f"{len(self.duplicates)} duplicate group(s), {surplus} extra "
                         "copies — each copy keeps its own listing; copies that grade "
                         "the same are priced once")
        if self.review_kept:
            lines.append(f"kept the existing review sheet with grades in it: {self.review_path}")
        else:
            lines.append(f"review sheet to fill in: {self.review_path}")
        if self.photos_csv:
            if self.photos_added:
                lines.append(f"{self.photos_added} photo URL(s) appended -> {self.photos_csv} "
                             "(upload this one; the original is untouched)")
            else:
                lines.append(f"nothing was hosted, so {os.path.basename(self.photos_csv)} "
                             "is a copy of the input — fix the upload and run again")
            for sku, why in self.upload_failures:
                lines.append(f"  upload failed {sku}: {why}")
        for w in self.warnings:
            lines.append("WARNING: " + w)
        return lines


def duplicate_groups(sheet):
    """
    Same title => several physical copies of one card. Each copy still gets
    its own listing and photos; the grouping is for PRICING, once the copies
    have been graded.
    """
    groups = defaultdict(list)
    for row in sheet.rows:
        groups[sheet.get(row, cu.TITLE).strip()].append(sheet.get(row, cu.SKU))
    return {t: skus for t, skus in groups.items() if t and len(skus) > 1}


def _own_photo(url):
    return f"/{PHOTO_PREFIX}" in url


def look_at(rec):
    """The file worth opening for a card, relative to the output folder."""
    base = f"cards/{safe_name(rec['sku'])}/"
    if rec["triage"] in ("no-scan", "error"):
        return ""
    if rec.get("check_back_era"):
        return base + "front.jpg + " + base + "back.jpg"
    return {"likely-worse": base + "corners-back.jpg",
            "review": base + "crops-back.jpg"}.get(rec["triage"], "")


def _review_rows(records):
    """
    The review sheet, prefilled only where the machine actually knows: a card
    with no usable scan is a HOLD, and the evidence says why. Every other grade
    is left blank for a person.
    """
    rows = []
    for rec in records:
        row = {"sku": rec["sku"], "title": rec["title"], "grade": "", "flaw_note": "",
               "confidence": "", "evidence": "", "looked": "",
               "triage": rec["triage"], "look_at": look_at(rec)}
        if rec["triage"] in ("no-scan", "error"):
            row.update(grade=HOLD, confidence="high", looked="no",
                       evidence=f"{rec['triage']}: {rec.get('note', '')} — rescan "
                                "and re-export")
        rows.append(row)
    return rows


def _has_grades(path):
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            return any((r.get("grade") or "").strip() and
                       (r.get("grade") or "").strip().upper() != HOLD
                       for r in csv.DictReader(fh))
    except OSError:
        return False


def _write_csv(path, fields, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _process(sheet, row, out_dir, thresholds, refresh, crops, fetcher, uploader):
    """Score and crop one card. Returns (record, uploaded_urls, upload_errors)."""
    sku = sheet.get(row, cu.SKU).strip()
    title = sheet.get(row, cu.TITLE)
    number, _ = cu.repair_card_number(sheet.get(row, cu.NUMBER), title)
    rec = {"sku": sku, "title": title, "set": sheet.get(row, cu.SET),
           "number": number, "note": "",
           "check_back_era": "yes" if cu.is_vintage(sheet, row) else ""}
    notes = []

    urls = cu.pic_urls(sheet.get(row, cu.PICS))
    scans = [u for u in urls if not _own_photo(u)]
    if len(scans) != 2:
        rec.update(triage="no-scan",
                   note=f"{len(scans)} scan(s) in {cu.PICS}, expected front|back")
        return rec, [], []

    folder = safe_name(sku)
    dest = os.path.join(out_dir, "cards", folder)
    raw = os.path.join(out_dir, "raw")
    os.makedirs(raw, exist_ok=True)
    faces = {}
    try:
        for face, url in (("front", scans[0]), ("back", scans[1])):
            blob = fetcher(url, os.path.join(raw, f"{folder}_{face}.jpg"), refresh)
            source = imaging.load(io.BytesIO(blob))
            # CardUploader scans are on the feeder's black bed.
            exact, padded, angle, _ = imaging.straighten(source)
            ok, why = imaging.detection_ok(exact, source)
            if not ok and face == "back":
                # The score is read off the back border. With the border not
                # found, any number here is a measurement of the scanner bed.
                raise ValueError(f"back: {why}")
            if not ok:
                notes.append(f"front: {why} — check it is a usable listing photo")
            faces[face] = (exact, padded, angle)
    except Exception as exc:                            # noqa: BLE001
        rec.update(triage="error", note=str(exc)[:160])
        return rec, [], []

    exact, padded, angle = faces["back"]
    rec.update(scoring.score_back(exact))
    rec["deskew_deg"] = round(angle, 2)
    rec["triage"] = scoring.triage(rec, thresholds)

    # The score assumes the Pokémon English navy back. On anything else it is
    # a number with no thresholds behind it, so it can rank but never settle a
    # card either way.
    kind = cu.game(sheet, row)
    if kind != "pokemon" or cu.is_japanese(sheet, row):
        what = "Yu-Gi-Oh" if kind != "pokemon" else "Japanese"
        rec["triage"] = "review"
        notes.append(f"score not calibrated for {what} backs — look at it")

    if crops or uploader:
        os.makedirs(dest, exist_ok=True)
    if crops:
        for face in ("front", "back"):
            faces[face][0].save(os.path.join(dest, f"{face}.jpg"), quality=95, subsampling=0)
        imaging.corner_sheet(padded, os.path.join(dest, "corners-back.jpg"), "BACK")
        imaging.grading_sheet(padded, os.path.join(dest, "crops-back.jpg"), "BACK")
        imaging.grading_sheet(faces["front"][1], os.path.join(dest, "crops-front.jpg"), "FRONT")

    hosted, failures = [], []
    if uploader:
        # Front first, so the photos sit in the same order as the scans.
        for face in ("front", "back"):
            local = os.path.join(dest, f"listing-{face}.jpg")
            imaging.listing_sheet(faces[face][1], local, face.upper())
            try:
                hosted.append(uploader.upload(local, sku))
            except Exception as exc:                    # noqa: BLE001
                failures.append(f"{face}: {str(exc)[:120]}")
        if len(hosted) == 1:
            # Half a pair would put one face's detail on the listing and not
            # the other's. Better none than a set that looks complete and isn't.
            failures.append("only one face uploaded — neither was added")
            hosted = []

    rec["note"] = "; ".join(notes)
    return rec, hosted, failures


def prepare(csv_path, out_dir, thresholds=None, limit=None, refresh=False,
            crops=True, uploader=None, fetcher=fetch, progress=None, should_stop=None):
    """
    Download, straighten, score, crop and rank a CardUploader batch.

    `progress(index, total, record)` is called once per card. A card that fails
    is recorded as `error` and the batch carries on.
    """
    thresholds = thresholds or scoring.Thresholds()
    sheet = cu.Sheet.read(csv_path)
    rows = sheet.rows[:limit] if limit else sheet.rows
    os.makedirs(out_dir, exist_ok=True)

    dupes = duplicate_groups(sheet)
    dup_of = {sku: t for t, skus in dupes.items() for sku in skus}
    result = PrepareResult(out_dir=out_dir, duplicates=dupes)

    photos = {}
    for i, row in enumerate(rows, 1):
        if should_stop and should_stop():
            result.stopped = True
            break
        rec, hosted, failures = _process(sheet, row, out_dir, thresholds, refresh,
                                         crops, fetcher, uploader)
        sku = rec["sku"]
        rec["duplicate_of"] = dup_of.get(sku, "")
        rec["copies"] = len(dupes.get(rec["duplicate_of"], [])) or 1
        if hosted:
            photos[sku] = hosted
        for f in failures:
            result.upload_failures.append((sku, f))
        result.records.append(rec)
        result.counts[rec["triage"]] += 1
        if progress:
            progress(i, len(rows), rec)

    result.records.sort(key=lambda r: -(r.get("worst_corner_pct") or 0))
    result.warnings = scoring.sanity_check(result.records)
    blank_skus = Counter(r["sku"] for r in result.records)
    if blank_skus[""]:
        result.warnings.append(f"{blank_skus['']} row(s) have no {cu.SKU} — they cannot "
                               "be matched back to the export")
    repeated = [s for s, n in blank_skus.items() if s and n > 1]
    if repeated:
        result.warnings.append("SKU appears more than once: " + ", ".join(repeated[:10]))

    result.triage_path = os.path.join(out_dir, "triage.csv")
    _write_csv(result.triage_path, TRIAGE_FIELDS, result.records)

    # A review sheet with grades in it is somebody's work; a re-run (to add
    # photos, say) must not wipe it.
    result.review_path = os.path.join(out_dir, "conditioning-review.csv")
    if _has_grades(result.review_path):
        result.review_kept = True
    else:
        _write_csv(result.review_path, REVIEW_FIELDS, _review_rows(result.records))

    if uploader:
        stem = os.path.splitext(os.path.basename(csv_path))[0]
        result.photos_csv = os.path.join(out_dir, f"{stem}-with-photos.csv")
        with_photos, result.photos_added = append_photo_urls(sheet, photos)
        with_photos.write(result.photos_csv)
    return result


# ---------------------------------------------------------------- applying


def read_review(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


_GRADE_SUFFIX = re.compile(r"(\s)(NM|LP|MP|HP|DMG)\s*$")


@dataclass
class ApplyResult:
    out_path: str = ""
    held_path: str = ""
    written: int = 0
    held: list = field(default_factory=list)          # (sku, title, reason)
    price_groups: list = field(default_factory=list)  # (title, grade, [skus])
    repaired: list = field(default_factory=list)
    unrepaired: list = field(default_factory=list)
    unknown_skus: list = field(default_factory=list)
    missing_grades: list = field(default_factory=list)

    def summary(self):
        lines = []
        if self.written:
            lines.append(f"{self.written} card(s) conditioned -> {self.out_path}")
        else:
            lines.append("no card could be written, so no conditioned CSV was produced")
        if self.missing_grades:
            lines.append(
                f"no condition strings yet for {', '.join(self.missing_grades)} — "
                "condition a card at that grade by hand in CardUploader, export, and copy "
                "its three condition cells into condition-map.json")
        if self.held:
            lines.append(f"{len(self.held)} card(s) held back -> {self.held_path}")
            for sku, title, reason in self.held:
                lines.append(f"  {sku}  {title[:44]}  — {reason}")
        if self.price_groups:
            lines.append("price once, apply to every copy (same title, same grade):")
            for title, grade, skus in self.price_groups:
                lines.append(f"  {grade:<3} x{len(skus)}  {title[:60]}   "
                             f"({', '.join(skus)})")
        if self.repaired:
            lines.append(f"repaired {len(self.repaired)} card number(s) Excel had "
                         "turned into dates")
        if self.unrepaired:
            lines.append("card number looks like an Excel date and the title does not "
                         "say what it was — fix by hand: " + ", ".join(self.unrepaired))
        if self.unknown_skus:
            lines.append("in the review sheet but not in this export: "
                         + ", ".join(self.unknown_skus[:10]))
        return lines


def apply_grades(csv_path, review_path, out_dir=None, condition_map_path=None):
    """
    Write the reviewed grades into a copy of the export.

    A card is written only when it has a grade on the scale AND the eBay
    condition strings for the grade it will be LISTED at are known. Everything
    else — holds, ungraded cards, grades whose strings have not been seen —
    goes to a separate held file with the reason, untouched. Nothing is
    guessed: a wrong condition enum fails silently, as a rejected batch or a
    batch listed at the wrong condition.
    """
    sheet = cu.Sheet.read(csv_path)
    conditions = cu.load_condition_map(condition_map_path)
    review = {(r.get("sku") or "").strip(): r for r in read_review(review_path)}
    out_dir = out_dir or os.path.dirname(os.path.abspath(csv_path))
    stem = os.path.splitext(os.path.basename(csv_path))[0]
    res = ApplyResult(out_path=os.path.join(out_dir, f"{stem}-conditioned.csv"),
                      held_path=os.path.join(out_dir, f"{stem}-held.csv"))

    out = sheet.copy()
    res.repaired, res.unrepaired = cu.repair_sheet(out)
    graded, written_rows, held_rows = [], [], []
    missing = set()
    seen = set()
    for row in out.rows:
        sku = out.get(row, cu.SKU).strip()
        title = out.get(row, cu.TITLE)
        seen.add(sku)
        r = review.get(sku)
        grade = ((r or {}).get("grade") or "").strip().upper()

        def hold(reason, row=row, sku=sku, title=title):
            res.held.append((sku, title, reason))
            held_rows.append(list(row) + [""] * (len(out.header) - len(row)) + [reason])

        if r is None or not grade:
            hold("not graded in the review sheet")
            continue
        if grade == HOLD:
            hold("HOLD — " + ((r.get("evidence") or r.get("flaw_note") or "").strip()
                              or "no reason given"))
            continue
        if grade not in GRADES:
            hold(f"'{grade}' is not one of {'/'.join(GRADES)}")
            continue
        if GRADES.index(grade) < GRADES.index(CREASE_FLOOR) and \
                mentions_crease(r.get("flaw_note"), r.get("evidence")):
            hold(f"graded {grade} but the notes mention a crease — any crease is "
                 f"{CREASE_FLOOR} or worse; regrade, or reword if there is no crease")
            continue
        listed = LIST_AS[cu.game(out, row)].get(grade, grade)
        if listed not in conditions:
            missing.add(listed)
            why = f"graded {grade}, listed as {listed}" if listed != grade else f"graded {grade}"
            hold(f"{why} — the eBay condition strings for {listed} are not known yet")
            continue

        for col, value in conditions[listed].items():
            if out.has(col):
                out.set(row, col, value)
        new_title = _GRADE_SUFFIX.sub(lambda m: m.group(1) + listed, title)
        out.set(row, cu.TITLE, new_title)
        note = (r.get("flaw_note") or "").strip()
        if note and out.has(cu.DESCRIPTION):
            # Appended to the shared boilerplate, never replacing it.
            desc = out.get(row, cu.DESCRIPTION)
            out.set(row, cu.DESCRIPTION, f"{desc}<br>{html.escape(note)}" if desc
                    else html.escape(note))
        graded.append((sku, new_title, listed))
        written_rows.append(row)
        res.written += 1

    res.missing_grades = [g for g in GRADES if g in missing]
    res.unknown_skus = [s for s in review if s and s not in seen]

    groups = defaultdict(list)
    for sku, title, listed in graded:
        groups[(title.strip(), listed)].append(sku)
    res.price_groups = [(t, g, skus) for (t, g), skus in groups.items() if len(skus) > 1]

    os.makedirs(out_dir, exist_ok=True)
    # A file left by an earlier apply, when this one produces none, would sit
    # in the folder looking current — and could be uploaded as though it were.
    if res.written:
        out.write(res.out_path, written_rows)
    else:
        _remove(res.out_path)
        res.out_path = ""
    if held_rows:
        held = cu.Sheet(out.header + ["hold_reason"], held_rows, out.bom)
        held.write(res.held_path)
    else:
        _remove(res.held_path)
        res.held_path = ""
    return res


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------- settings


def settings_dir():
    """
    Where the app keeps what outlives a batch: thresholds, ledger, condition
    map. The same folder as the app's own settings.json.
    """
    from . import folders
    return os.path.dirname(folders._settings_path())


def default_paths():
    d = settings_dir()
    return {"thresholds": os.path.join(d, "thresholds.json"),
            "ledger": os.path.join(d, "calibration.csv"),
            "conditions": os.path.join(d, "condition-map.json")}


def calibrate(review_path, triage_path, ledger_path, thresholds_path, adopt=False):
    """
    Record a reviewed batch's seen grades in the ledger, re-fit, and report.

    With `adopt`, a proposal is written to `thresholds_path` and becomes what
    the next run triages with. Without it the proposal is only shown — moving
    the bands is a decision, and it should be one somebody made.
    """
    with open(triage_path, encoding="utf-8-sig", newline="") as fh:
        triage_rows = list(csv.DictReader(fh))
    added, skipped = scoring.record_grades(read_review(review_path), triage_rows,
                                           ledger_path, os.path.basename(review_path))
    lines = [f"recorded {added} looked-at grade(s) in {ledger_path}"]
    reasons = Counter(why for _, why in skipped)
    for why, n in reasons.most_common():
        lines.append(f"  skipped {n}: {why}")
    ledger = scoring.read_ledger(ledger_path)
    current = scoring.Thresholds.load(thresholds_path)
    lines.append(f"ledger holds {len(ledger)} graded card(s); current thresholds: "
                 f"{current.source}")
    proposed, report = scoring.fit(ledger, current)
    lines.extend(report)
    if proposed and adopt:
        proposed.save(thresholds_path)
        lines.append(f"adopted -> {thresholds_path}")
    elif proposed:
        lines.append("not adopted — run again with --adopt to triage with these")
    return lines
