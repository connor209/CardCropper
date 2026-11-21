"""
Cutting a cropped run into 50-card batches, giving each batch its code, and
writing the manifest every later step reads.

A batch code is a promise to CardUploader: it becomes the prefix of every SKU
in the batch, and a code used twice puts two different cards under one SKU. So
codes are never worked out from a counter kept here. They are read from what
CardUploader already holds (`GET /jobs/history`), and the next code is the one
after the HIGHEST seen — not the first gap. A gap is usually a batch that was
created and left empty (batch "IP" is one), and filling it would still collide
with whatever CardUploader remembers about it.

Sequences:

* Pokémon, English and Japanese alike: two letters, AA .. AZ, BA .. ZZ. GA
  follows FZ. SKUs are code + position: KA1 .. KA50.
* Yu-Gi-Oh: YGO-A .. YGO-Z, then YGO-AA, YGO-AB (bijective base 26, the same
  way spreadsheet columns run). SKUs YGO-A1 .. YGO-A50.
* MTG: the same with an MTG- prefix (MTG-I exists).
"""

import csv
import datetime as _dt
import json
import os
import re
import urllib.request
from dataclasses import asdict, dataclass, field

from . import batch, imaging

PER_BATCH = 50

GAMES = ("pokemon", "yugioh", "mtg")
LANGUAGES = ("en", "jp")

#: Prefix per game for the lettered sequences. Pokémon has none: its codes are
#: bare two-letter pairs.
PREFIX = {"yugioh": "YGO-", "mtg": "MTG-"}

#: Every file a card must have, in the manifest's own column names.
ROLES = ("front", "back", "front-crops", "back-crops")

CARDUPLOADER = "https://carduploader.com/backend"

_POKEMON = re.compile(r"^([A-Z]{2})(?![A-Za-z0-9-])")
_PREFIXED = re.compile(r"^(YGO|MTG)-([A-Z]{1,3})(?![A-Za-z])")


# ---------------------------------------------------------------- codes


def _letters_to_int(s):
    """Bijective base 26: A=1 .. Z=26, AA=27."""
    n = 0
    for ch in s:
        n = n * 26 + (ord(ch) - 64)
    return n


def _int_to_letters(n):
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def _pokemon_index(code):
    return (ord(code[0]) - 65) * 26 + (ord(code[1]) - 65)


def _pokemon_code(i):
    if not 0 <= i < 26 * 26:
        raise ValueError("two-letter Pokémon codes have run out after ZZ")
    return chr(65 + i // 26) + chr(65 + i % 26)


def code_of(name, game):
    """
    The batch code a CardUploader job name carries for `game`, or None.

    Names are read from the front, so "KA" and "KA - Base Set binder" are both
    KA, while "MTG-I" is never mistaken for the Pokémon code "MT".
    """
    name = (name or "").strip().upper()
    if game == "pokemon":
        m = _POKEMON.match(name)
        return m.group(1) if m else None
    m = _PREFIXED.match(name)
    if m and PREFIX.get(game) == m.group(1) + "-":
        return m.group(0)
    return None


def _rank(code, game):
    if game == "pokemon":
        return _pokemon_index(code)
    return _letters_to_int(code[len(PREFIX[game]):])


def code_after(code, game):
    """The code that follows `code`. None gives the first code of the game."""
    if game == "pokemon":
        return _pokemon_code(0 if code is None else _pokemon_index(code) + 1)
    n = 0 if code is None else _letters_to_int(code[len(PREFIX[game]):])
    return PREFIX[game] + _int_to_letters(n + 1)


def latest_code(names, game):
    """The highest code among `names` for `game`, or None if there is none."""
    codes = [c for c in (code_of(n, game) for n in names) if c]
    return max(codes, key=lambda c: _rank(c, game)) if codes else None


def next_codes(names, game, count=1):
    """
    `count` fresh codes after the highest one in `names`.

    `names` is every job name CardUploader holds — empty batches included,
    since a code is spent the moment a job is created under it.
    """
    if game not in GAMES:
        raise ValueError(f"unknown game {game!r}")
    out, code = [], latest_code(names, game)
    for _ in range(count):
        code = code_after(code, game)
        out.append(code)
    return out


def sku(code, position):
    return f"{code}{position}"


# ---------------------------------------------------------------- history


def job_names(history):
    """
    Every job name in a `/jobs/history` response, whatever shape it is in.

    The response has not been pinned down, so it is walked rather than
    indexed: any `jobName` (or a `name` beside an `id`/`jobId`) anywhere in it
    counts. Missing one would hand out a used code, so this errs wide.
    """
    found = []

    def walk(node):
        if isinstance(node, dict):
            for key in ("jobName", "job_name"):
                if isinstance(node.get(key), str):
                    found.append(node[key])
            if isinstance(node.get("name"), str) and ("id" in node or "jobId" in node):
                found.append(node["name"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(history)
    return found


def load_history(path):
    """Job names from a saved `/jobs/history` JSON, or a plain list of names."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    try:
        data = json.loads(text)
    except ValueError:
        return [ln.strip() for ln in text.splitlines() if ln.strip()]
    if isinstance(data, list) and all(isinstance(x, str) for x in data):
        return [x.strip() for x in data if x.strip()]
    return job_names(data)


def fetch_history(token, base=CARDUPLOADER, timeout=30):
    """
    `GET /jobs/history` with the user's own Firebase token.

    The token lives in the logged-in CardUploader tab and expires after about
    an hour, so it is taken fresh each run (the extension can copy it, or save
    the history itself as a file for `load_history`). Unofficial endpoint.
    """
    req = urllib.request.Request(f"{base}/jobs/history",
                                 headers={"Authorization": f"Bearer {token}",
                                          "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return job_names(json.loads(resp.read().decode("utf-8")))


# ---------------------------------------------------------------- cropped folders


def cards_in(folder, order="crops-last"):
    """
    The cards in a cropped folder, as {number: {role: path}} in number order.

    Both naming schemes are read. A `sequence` folder carries no roles in its
    names, so they come from `order` and the file's place in its group of four.
    """
    cards = {}
    if not os.path.isdir(folder):
        return cards
    roles = batch.ORDERS[order]
    for name in sorted(os.listdir(folder), key=batch.natural_key):
        stem, ext = os.path.splitext(name)
        if ext.lower() not in imaging.IMAGE_EXTS:
            continue
        path = os.path.join(folder, name)
        m = batch.GROUPED_NAME.match(stem)
        if m:
            role = stem.split("_", 2)[2]
            if role in ROLES:
                cards.setdefault(int(m.group(1)), {})[role] = path
            continue
        m = batch.SEQUENCE_NAME.match(stem)
        if m:
            n = int(m.group(1))
            cards.setdefault((n - 1) // batch.PER_CARD + 1, {})[
                roles[(n - 1) % batch.PER_CARD]] = path
    return dict(sorted(cards.items()))


# ---------------------------------------------------------------- batches


@dataclass
class ManifestCard:
    sku: str
    position: int
    card_number: int
    front: str = ""
    back: str = ""
    front_crops: str = ""
    back_crops: str = ""
    missing: list = field(default_factory=list)


@dataclass
class Batch:
    code: str
    game: str
    language: str
    source: str
    cards: list = field(default_factory=list)


def plan_batches(folders, game, language="en", history=(), per_batch=PER_BATCH,
                 order="crops-last", codes=None):
    """
    Cut the cards in `folders` (cropped folders, in run order) into batches.

    Each folder is cut on its own — a stack folder is a physical stack, and a
    batch must not straddle two piles — so a folder of 50 is one batch and a
    folder of 120 is three. `codes` overrides the codes read from `history`.
    """
    if game not in GAMES:
        raise ValueError(f"unknown game {game!r}")
    if language not in LANGUAGES:
        raise ValueError(f"unknown language {language!r}")
    chunks = []
    for folder in folders:
        items = list(cards_in(folder, order).items())
        for i in range(0, len(items), per_batch):
            chunks.append((folder, items[i:i + per_batch]))
    codes = list(codes) if codes else next_codes(history, game, len(chunks))
    if len(codes) < len(chunks):
        raise ValueError(f"{len(chunks)} batches need codes and only {len(codes)} given")
    used = {code_of(n, game) for n in history}
    clash = [c for c in codes if c in used]
    if clash:
        raise ValueError(f"code(s) already used in CardUploader: {', '.join(clash)}")
    out = []
    for code, (folder, items) in zip(codes, chunks):
        b = Batch(code, game, language, folder)
        for pos, (number, files) in enumerate(items, 1):
            b.cards.append(ManifestCard(
                sku=sku(code, pos), position=pos, card_number=number,
                front=files.get("front", ""), back=files.get("back", ""),
                front_crops=files.get("front-crops", ""),
                back_crops=files.get("back-crops", ""),
                missing=[r for r in ROLES if r not in files]))
        out.append(b)
    return out


MANIFEST_FIELDS = ["sku", "game", "language", "position", "card_number", "front",
                   "back", "front_crops", "back_crops", "missing"]


def manifest_name(code):
    return f"manifest-{code}"


def write_manifest(b, out_dir=None):
    """Write `manifest-<code>.json` and `.csv`. Returns the two paths."""
    out_dir = out_dir or b.source
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.join(out_dir, manifest_name(b.code))
    data = asdict(b)
    data["created"] = _dt.datetime.now().isoformat(timespec="seconds")
    with open(stem + ".json", "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    with open(stem + ".csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS)
        w.writeheader()
        for c in b.cards:
            row = asdict(c)
            row.update(game=b.game, language=b.language, missing=" ".join(c.missing))
            w.writerow(row)
    return stem + ".json", stem + ".csv"


def read_manifest(path):
    """A manifest JSON back as a Batch."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    cards = [ManifestCard(**{k: v for k, v in c.items()
                             if k in ManifestCard.__dataclass_fields__})
             for c in data.get("cards", [])]
    return Batch(data["code"], data["game"], data["language"], data.get("source", ""),
                 cards)
