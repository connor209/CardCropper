"""
Making the day's empty scan folders before a scanning session.

Each batch of scans goes in its own folder, named for the day and numbered
within it: `26.09.26 - 001`, `26.09.26 - 002`, … This makes the next N of
them in one go.

Numbering carries on from whatever is already there for that day. A second
session on the same afternoon gets `004`–`006`, not a second `001` — and an
existing folder is never touched, because it may already be full of scans.
"""

import datetime
import json
import os
import re
import sys

#: `YY.MM.DD` — two-digit year first, so the folders sort by date as text.
DATE_FORMAT = "%y.%m.%d"
SEPARATOR = " - "
DIGITS = 3


def date_label(day=None):
    """`26.09.26` for 26 September 2026. Today when no day is given."""
    return (day or datetime.date.today()).strftime(DATE_FORMAT)


def parse_date_label(text):
    """The inverse of `date_label`; raises ValueError on anything else."""
    try:
        return datetime.datetime.strptime(text.strip(), DATE_FORMAT).date()
    except ValueError:
        raise ValueError(f"'{text.strip()}' is not a date in YY.MM.DD form") from None


def folder_name(label, number):
    return f"{label}{SEPARATOR}{number:0{DIGITS}d}"


def existing_numbers(location, label):
    """The numbers already used for `label` in `location`, e.g. {1, 2, 3}."""
    pattern = re.compile(re.escape(label + SEPARATOR) + r"(\d+)$")
    try:
        entries = os.listdir(location)
    except FileNotFoundError:
        return set()
    return {int(m.group(1)) for name in entries
            if (m := pattern.match(name)) and os.path.isdir(os.path.join(location, name))}


def next_number(location, label):
    """One past the highest folder already made for that day; 1 if none."""
    used = existing_numbers(location, label)
    return max(used) + 1 if used else 1


def plan(location, count, label=None, start=None):
    """The folder names that `create` would make, without making them."""
    if count < 1:
        raise ValueError("the number of folders must be at least 1")
    label = label or date_label()
    parse_date_label(label)
    if start is None:
        start = next_number(location, label)
    if start < 1:
        raise ValueError("folder numbers start at 1")
    return [folder_name(label, n) for n in range(start, start + count)]


def create(location, count, label=None, start=None):
    """
    Make `count` folders for the day in `location` and return their paths.

    An explicit `start` that runs into a folder that already exists is refused
    before anything is made, rather than leaving half the run created.
    """
    names = plan(location, count, label, start)
    clash = [n for n in names if os.path.exists(os.path.join(location, n))]
    if clash:
        raise FileExistsError(
            f"{clash[0]} already exists in {location}"
            + (f" (and {len(clash) - 1} more)" if len(clash) > 1 else ""))
    os.makedirs(location, exist_ok=True)
    made = []
    for name in names:
        path = os.path.join(location, name)
        os.mkdir(path)
        made.append(path)
    return made


# ------------------------------------------------------------ settings

def _settings_path():
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "CardCropper", "settings.json")


def load_settings():
    """What was chosen last time — where the day folders go, which tab was open."""
    try:
        with open(_settings_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(**changes):
    """Merge `changes` into the saved settings. Failing to save is not an error."""
    data = load_settings()
    data.update(changes)
    path = _settings_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError:
        pass
