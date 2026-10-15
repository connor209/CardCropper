"""
Reading and writing a CardUploader export: eBay File Exchange bulk-listing
format, 67 columns, one row per physical card.

A fresh export is a blank slate. Every row is Add / 4000 / "Near Mint or
Better:" / £2.49 / quantity 1, and those are placeholders, not decisions.

The file is read and written as rows of cells rather than through a dict, so
every column (including any whose header repeats) goes back out exactly as it
came in, in the same order. Only cells this module deliberately changes differ.
"""

import csv
import io
import json
import os
import re

SKU = "CustomLabel"
TITLE = "*Title"
PICS = "PicURL"
CONDITION_ID = "*ConditionID"
CONDITION_LABEL = "C:Card Condition"
CONDITION_DETAIL = "CD:Card Condition - (ID: 40001)"
DESCRIPTION = "*Description"
SET = "*C:Set"
NUMBER = "*C:Card Number"
NAME = "*C:Card Name"

CONDITION_COLUMNS = (CONDITION_ID, CONDITION_LABEL, CONDITION_DETAIL)

#: WotC-era sets (1999-2003). A vintage front scanned against a modern swirl
#: back means the scanner paired the wrong two images: not a wear check, a
#: "look at this pair before listing" flag.
VINTAGE_SETS = {
    "base set", "base set 2", "jungle", "fossil", "team rocket",
    "gym heroes", "gym challenge", "neo genesis", "neo discovery",
    "neo revelation", "neo destiny", "legendary collection",
    "expedition", "aquapolis", "skyridge",
}


class Sheet:
    """A CardUploader CSV held as header + rows of raw cells."""

    def __init__(self, header, rows, bom=False):
        self.header = header
        self.rows = rows
        self.bom = bom
        self._index = {}
        for i, name in enumerate(header):
            self._index.setdefault(name, i)

    @classmethod
    def read(cls, path):
        with open(path, "rb") as fh:
            raw = fh.read()
        bom = raw.startswith(b"\xef\xbb\xbf")
        text = raw.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text, newline=""))
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError(f"{os.path.basename(path)} is empty") from None
        rows = [r for r in reader if any(c.strip() for c in r)]
        sheet = cls(header, rows, bom)
        missing = [c for c in (SKU, TITLE, PICS) if c not in sheet._index]
        if missing:
            raise ValueError(f"{os.path.basename(path)} is not a CardUploader export — "
                             f"no {', '.join(missing)} column")
        return sheet

    def has(self, col):
        return col in self._index

    def get(self, row, col):
        i = self._index.get(col)
        if i is None or i >= len(row):
            return ""
        return row[i]

    def set(self, row, col, value):
        i = self._index[col]
        while len(row) <= i:
            row.append("")
        row[i] = value

    def copy(self):
        return Sheet(list(self.header), [list(r) for r in self.rows], self.bom)

    def write(self, path, rows=None):
        """
        Write header + rows, quoting only where needed and with CRLF endings —
        eBay accepts both quoting styles and a diff against the input reads
        better this way. The BOM is kept if the input had one.
        """
        rows = self.rows if rows is None else rows
        with open(path, "w", encoding="utf-8-sig" if self.bom else "utf-8",
                  newline="") as fh:
            fh.write(",".join(_field(c) for c in self.header) + "\r\n")
            for r in rows:
                r = list(r) + [""] * (len(self.header) - len(r))
                fh.write(",".join(_field(c) for c in r) + "\r\n")


def _field(v):
    t = "" if v is None else str(v)
    return f'"{t.replace(chr(34), chr(34) * 2)}"' if any(c in t for c in ',"\r\n') else t


def pic_urls(value):
    return [u.strip() for u in (value or "").split("|") if u.strip()]


def game(sheet, row):
    """'yugioh' or 'pokemon'. The back-border score is only meaningful for Pokémon."""
    sku = sheet.get(row, SKU).strip().upper()
    title = sheet.get(row, TITLE).lower()
    if sku.startswith("YGO-") or "yugioh" in title or "yu-gi-oh" in title:
        return "yugioh"
    return "pokemon"


def is_japanese(sheet, row):
    """Japanese cards have a different back, which the score is not fitted to."""
    if "japanese" in sheet.get(row, TITLE).lower() or "japanese" in sheet.get(row, SET).lower():
        return True
    return any("language" in col.lower() and "japanese" in (sheet.get(row, col) or "").lower()
               for col in sheet.header)


def is_vintage(sheet, row):
    return sheet.get(row, SET).strip().lower() in VINTAGE_SETS


# ---------------------------------------------------------------- Excel damage

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_NUMBER_IN_TITLE = re.compile(r"(?<![\w/])(\w*?\d+)\s*/\s*(\w*?\d+)(?![\w/])")


def _date_candidates(value):
    """
    Number pairs a mangled card-number cell could have started as.

    Excel turns `4/99` into `Apr-99` (99 cannot be a day, so it reads month and
    year) and something like `12/4` into `12-Apr` or `4-Dec` depending on the
    locale. A cell saved with a date format can also come out as a full date.
    """
    v = value.strip().lower()
    m = re.fullmatch(r"([a-z]{3})[a-z]*[-\s/](\d{1,4})", v)
    if m and m.group(1) in _MONTHS:
        month, n = _MONTHS[m.group(1)], int(m.group(2))
        return {(month, n % 100), (n, month)}
    m = re.fullmatch(r"(\d{1,2})[-\s/]([a-z]{3})[a-z]*(?:[-\s/](\d{2,4}))?", v)
    if m and m.group(2) in _MONTHS:
        d, month = int(m.group(1)), _MONTHS[m.group(2)]
        out = {(d, month), (month, d)}
        if m.group(3):
            out.add((month, int(m.group(3)) % 100))
        return out
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", v)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3)) % 100
        return {(a, b), (b, a), (a, y), (b, y)}
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ t].*)?", v)
    if m:
        y, month, d = int(m.group(1)) % 100, int(m.group(2)), int(m.group(3))
        return {(month, y), (d, month), (month, d)}
    return set()


def repair_card_number(number, title):
    """
    Undo Excel's date rewrite of a `*C:Card Number` cell, if it has one.

    Returns (value, status): status is "ok" for a number that was never
    mangled, "repaired" when the title confirms what it used to be, and
    "unrepaired" when the cell is plainly a date but the title does not say
    which number it was. The title is the arbiter because Excel's rewrite
    throws away which part was which — `12-Apr` could have been 12/4 or 4/12 —
    and the listing title carries the number untouched.
    """
    candidates = _date_candidates(number or "")
    if not candidates:
        return number, "ok"
    for m in _NUMBER_IN_TITLE.finditer(title or ""):
        a, b = re.sub(r"\D", "", m.group(1)), re.sub(r"\D", "", m.group(2))
        if a and b and (int(a), int(b)) in candidates:
            return f"{m.group(1)}/{m.group(2)}", "repaired"
    return number, "unrepaired"


def repair_sheet(sheet):
    """Repair every card number in place. Returns (repaired, unrepaired) SKU lists."""
    repaired, unrepaired = [], []
    if not sheet.has(NUMBER):
        return repaired, unrepaired
    for row in sheet.rows:
        value, status = repair_card_number(sheet.get(row, NUMBER), sheet.get(row, TITLE))
        if status == "repaired":
            sheet.set(row, NUMBER, value)
            repaired.append(sheet.get(row, SKU))
        elif status == "unrepaired":
            unrepaired.append(sheet.get(row, SKU))
    return repaired, unrepaired


# ---------------------------------------------------------------- conditions
#
# Three columns carry a card's condition and they have to agree. Only the NM
# strings have been seen in a real export. eBay's numeric IDs for ungraded TCG
# singles are probably 5000/6000/7000/8000 for LP/MP/HP/DMG, but the label
# strings and the -(ID: ...) sub-IDs cannot be derived, and a guessed value
# does not fail loudly: it rejects the batch or lists every card at the wrong
# condition. So nothing but NM is built in.
#
# To unlock the rest: condition a few cards by hand in CardUploader at each
# grade, export, and copy the three cells off those rows into the
# condition-map.json this module writes a template of.

KNOWN_CONDITIONS = {
    "NM": {
        CONDITION_ID: "4000",
        CONDITION_LABEL: "Near Mint or Better:",
        CONDITION_DETAIL: "Near Mint or Better: -(ID: 400010)",
    },
}

_MAP_HELP = (
    "Fill in each grade from a real CardUploader export: condition a card by hand "
    "at that grade in CardUploader, export, and copy the three cells off its row "
    "exactly (the trailing colon in the label is real). Leave a grade blank until "
    "it has been seen — a blank grade is refused rather than guessed.")


def condition_map_template():
    t = {"_how": _MAP_HELP}
    for g in ("NM", "LP", "MP", "HP", "DMG"):
        t[g] = dict(KNOWN_CONDITIONS.get(g) or {c: "" for c in CONDITION_COLUMNS})
    return t


def load_condition_map(path):
    """
    The grades whose three condition cells are known: NM always, plus any grade
    fully filled in at `path`. A grade with any blank cell is left out.
    """
    out = {g: dict(v) for g, v in KNOWN_CONDITIONS.items()}
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for g, cells in data.items():
            if g.startswith("_") or not isinstance(cells, dict):
                continue
            vals = {c: str(cells.get(c) or "").strip() for c in CONDITION_COLUMNS}
            if all(vals.values()):
                if g.upper() in KNOWN_CONDITIONS and vals != KNOWN_CONDITIONS[g.upper()]:
                    raise ValueError(f"{path}: {g} disagrees with the NM strings "
                                     "confirmed from a real export")
                out[g.upper()] = vals
    return out


def ensure_condition_map(path):
    """Write the template if there is no map yet, so there is a file to fill in."""
    if path and not os.path.exists(path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(condition_map_template(), fh, indent=2, ensure_ascii=False)
            fh.write("\n")
