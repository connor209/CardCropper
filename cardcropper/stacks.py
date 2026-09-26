"""
Splitting one long scanning run into stacks, and filing each stack's scans
into its own folder.

Scanning 500 cards in one go is faster than scanning ten runs of 50, but the
cards still have to leave as 50-card stacks, and the scans have to match them
exactly: stack 3's folder has to hold the scans of the cards physically in
stack 3, or every listing built from it describes a card somebody else gets.

The count alone does not guarantee that. A double feed or a card pulled out to
rescan moves every break after it, and nothing in the file count shows it. So
the break is not trusted, it is CHECKED: for each one, the operator is shown
the last card of one stack and the first of the next, finds that point in the
physical pile, and splits there. Where the pile and the scans disagree, the
break is moved to where the scans actually are — a stack of 49 that matches
beats a stack of 50 that does not.

Nothing here decides which scan is which card. That is `batch.plan_scans`,
unchanged, so a folder written here pairs exactly as the whole run did — a
card scanned as two files moves as two files, a card with both faces on one
scan moves as that one file.
"""

import os
import re
import shutil
from dataclasses import dataclass, field

from . import batch, folders

#: What a stack is, unless told otherwise.
PER_STACK = 50

#: How a stack folder is named. Fields: {date} the day as YY.MM.DD, {n} the
#: stack's number, {first} and {last} the card numbers it runs between,
#: {count} how many cards it holds.
#:
#: The default is the Scan folders tab's own format, so a split run lands as
#: the same `26.09.26 - 004` folders a day of 50-card runs would have made —
#: numbered on from any already there that day.
TEMPLATE = "{date}" + folders.SEPARATOR + "{n:0%dd}" % folders.DIGITS

#: Characters Windows will not have in a folder name. Checked up front, so a
#: bad template fails before anything has moved rather than on the first mkdir.
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class StackPlan:
    """
    A run of cards cut into stacks.

    `starts` holds the card index (0-based) each stack begins at. The first is
    always 0; the breaks the operator checks are the rest of them.

    `first_number` is the {n} the first stack gets, and `date` the {date}; see
    `number_from`.
    """
    cards: list = field(default_factory=list)
    leftover: list = field(default_factory=list)
    starts: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    first_number: int = 1
    date: str = field(default_factory=folders.date_label)

    def __len__(self):
        return len(self.starts)

    def span(self, k):
        """Card indices (start, end) of stack `k`, end exclusive."""
        end = self.starts[k + 1] if k + 1 < len(self.starts) else len(self.cards)
        return self.starts[k], end

    def stack(self, k):
        start, end = self.span(k)
        return self.cards[start:end]

    def sizes(self):
        return [len(self.stack(k)) for k in range(len(self))]

    def nudge(self, k, delta):
        """
        Move the break in front of stack `k` by `delta` cards, never so far
        that a stack either side of it ends up empty. Returns whether it moved.
        """
        if not 1 <= k < len(self.starts):
            return False
        low = self.starts[k - 1] + 1
        high = (self.starts[k + 1] if k + 1 < len(self.starts) else len(self.cards)) - 1
        new = max(low, min(high, self.starts[k] + delta))
        moved = new != self.starts[k]
        self.starts[k] = new
        return moved

    def number_from(self, dest_root, template=TEMPLATE):
        """
        Number the stacks on from the day's folders already in `dest_root`.

        Only for a template in the day format — anything else starts at 1.
        Fixed once filing starts, by not calling this again: the first stack's
        own folder would otherwise count as "already there" on a re-run, and a
        resumed split would carry on into a fresh set of folders.
        """
        if template.startswith("{date}" + folders.SEPARATOR):
            self.first_number = folders.next_number(dest_root, self.date)
        else:
            self.first_number = 1

    def rebreak(self, per_stack):
        """Cut the run afresh every `per_stack` cards, dropping any nudges."""
        per_stack = max(1, int(per_stack))
        self.starts = list(range(0, len(self.cards), per_stack)) or [0]


def plan_stacks(paths, per_stack=PER_STACK, split="auto", background="dark",
                probe=None, progress=None):
    """
    Pair a run of scans into cards, as the Crop tab would, and cut it every
    `per_stack` cards. `split`, `probe` and `progress` are `batch.plan_scans`'s.

    Which face is the front does not matter here — both are shown at a break
    and both move together — so it is not worked out, which saves reading the
    batch a second time.
    """
    pairs = batch.plan_scans(paths, split=split, front="first", probe=probe,
                             background=background, progress=progress)
    plan = StackPlan(cards=pairs.cards, leftover=pairs.leftover, notes=pairs.notes)
    plan.rebreak(per_stack)
    return plan


def folder_names(plan, template=TEMPLATE):
    """
    The folder each stack will be filed into.

    Raises ValueError with something a person can act on when the template
    does not produce usable, distinct names — a template without {n} names
    every stack the same, and the second would be filed on top of the first.
    """
    names = []
    for k in range(len(plan)):
        start, end = plan.span(k)
        try:
            name = template.format(n=plan.first_number + k, date=plan.date,
                                   first=start + 1, last=end, count=end - start)
        except (KeyError, IndexError, ValueError) as exc:
            raise ValueError(f"folder name {template!r} could not be filled in "
                             f"({exc}). Use {{date}}, {{n}}, {{first}}, "
                             f"{{last}} or {{count}}.") from exc
        name = name.strip().rstrip(".")
        if not name:
            raise ValueError("the folder name comes out empty")
        if _BAD_CHARS.search(name):
            raise ValueError(f"{name!r} contains a character Windows does not "
                             'allow in a folder name: < > : " / \\ | ? *')
        names.append(name)
    if len({n.lower() for n in names}) != len(names):
        raise ValueError("the folder name gives two stacks the same folder — "
                         "include {n} so each stack gets its own")
    return names


def card_moves(plan, dest_root, template=TEMPLATE):
    """
    Each card's (source, destination) moves, one list per card: two files for
    a card scanned as two, one for a scan holding both faces.

    Filenames are kept as they are. Natural order is what pairs them, so a
    stack folder pairs into exactly the cards it was cut from.
    """
    out = []
    for k, name in enumerate(folder_names(plan, template)):
        folder = os.path.join(dest_root, name)
        for card in plan.stack(k):
            out.append([(path, os.path.join(folder, os.path.basename(path)))
                        for path in card.sources])
    return out


def moves_for(plan, dest_root, template=TEMPLATE):
    """Every (source, destination) the split would make, in order."""
    return [move for card in card_moves(plan, dest_root, template) for move in card]


def problems(plan, dest_root, template=TEMPLATE):
    """
    What would stop the split, as sentences — empty when it is safe to run.

    Checked before the first file moves. A split that fails half-way leaves
    some stacks filed and some not, which is the one state worse than either.
    """
    if not plan.cards:
        return ["there are no cards to split"]
    try:
        moves = moves_for(plan, dest_root, template)
    except ValueError as exc:
        return [str(exc)]
    found = []
    for src, dst in moves:
        if os.path.exists(dst) and not _already_moved(src, dst):
            found.append(f"{dst} already exists")
    return found[:10] + ([f"…and {len(found) - 10} more"] if len(found) > 10 else [])


def _already_moved(src, dst):
    """A card a previous, interrupted split filed already."""
    return os.path.exists(dst) and not os.path.exists(src)


def apply(plan, dest_root, template=TEMPLATE, progress=None):
    """
    Create a folder per stack and MOVE each stack's scans into it.

    Moved rather than copied, unlike the crop run: this is the filing of the
    scans themselves, and a copy would leave the whole run behind in the
    parent folder to be paired in again. The same volume makes it a rename,
    which is instant and cannot half-write a file.

    A card moves whole. If its back will not move, its front is put back, so
    no folder ever holds a front without the back that pairs with it.

    Stops at the first card that fails rather than carrying on: every stack
    after a missing card would still look complete. Returns the moves made,
    which `undo` reverses; running again after a failure picks up where it
    stopped, skipping the cards already filed.

    `progress(done, total)` is called after each card.
    """
    issues = problems(plan, dest_root, template)
    if issues:
        raise ValueError("; ".join(issues))
    cards = card_moves(plan, dest_root, template)
    made = []
    total = len(cards)
    for i, pair in enumerate(cards):
        this_card = []
        try:
            for src, dst in pair:
                if _already_moved(src, dst):
                    continue
                batch._io(os.makedirs, os.path.dirname(dst), 0o777, True)
                batch._io(shutil.move, src, dst)
                this_card.append((src, dst))
        except OSError as exc:
            undo(this_card)
            raise SplitFailed(made, i, exc) from exc
        made.extend(this_card)
        if progress:
            progress(i + 1, total)
    return made


class SplitFailed(OSError):
    """
    A split that stopped part-way. `.moves` are the ones that were made and
    `.card_index` the card it stopped at, so that many cards were filed.
    """

    def __init__(self, moves, card_index, cause):
        super().__init__(f"stopped at card {card_index + 1}: {cause}")
        self.moves = moves
        self.card_index = card_index


def undo(moves):
    """
    Put moved scans back where they were, newest first. Stack folders left
    empty are removed; anything else is left alone. Returns what could NOT be
    moved back, so it can be named rather than lost.
    """
    stuck = []
    folders = set()
    for src, dst in reversed(moves):
        try:
            batch._io(shutil.move, dst, src)
            folders.add(os.path.dirname(dst))
        except OSError as exc:
            stuck.append((src, dst, exc))
    for folder in folders:
        try:
            os.rmdir(folder)            # only succeeds when it is empty
        except OSError:
            pass
    return stuck
