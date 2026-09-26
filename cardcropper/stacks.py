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

Nothing here decides which scan is which card. That is `batch.pair_sequential`,
unchanged, so a folder written here pairs exactly as the whole run did.
"""

import os
import re
import shutil
from dataclasses import dataclass, field

from . import batch

#: What a stack is, unless told otherwise.
PER_STACK = 50

#: How a stack folder is named. Fields: {n} the stack's number, {first} and
#: {last} the card numbers it runs between, {count} how many cards it holds.
TEMPLATE = "Stack {n:02d}"

#: Characters Windows will not have in a folder name. Checked up front, so a
#: bad template fails before anything has moved rather than on the first mkdir.
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class StackPlan:
    """
    A run of cards cut into stacks.

    `starts` holds the card index (0-based) each stack begins at. The first is
    always 0; the breaks the operator checks are the rest of them.
    """
    cards: list = field(default_factory=list)
    leftover: list = field(default_factory=list)
    starts: list = field(default_factory=list)

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

    def rebreak(self, per_stack):
        """Cut the run afresh every `per_stack` cards, dropping any nudges."""
        per_stack = max(1, int(per_stack))
        self.starts = list(range(0, len(self.cards), per_stack)) or [0]


def plan_stacks(paths, per_stack=PER_STACK):
    """Pair a run of scans into cards and cut it every `per_stack` cards."""
    pairs = batch.pair_sequential(paths)
    plan = StackPlan(cards=pairs.cards, leftover=pairs.leftover)
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
            name = template.format(n=k + 1, first=start + 1, last=end,
                                   count=end - start)
        except (KeyError, IndexError, ValueError) as exc:
            raise ValueError(f"folder name {template!r} could not be filled in "
                             f"({exc}). Use {{n}}, {{first}}, {{last}} or "
                             f"{{count}}.") from exc
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


def moves_for(plan, dest_root, template=TEMPLATE):
    """
    Every (source, destination) the split would make, stack by stack.

    Filenames are kept as they are. Natural order is what pairs them, so a
    stack folder pairs into exactly the cards it was cut from.
    """
    out = []
    for k, name in enumerate(folder_names(plan, template)):
        folder = os.path.join(dest_root, name)
        for card in plan.stack(k):
            out.append((card.front, os.path.join(folder, os.path.basename(card.front))))
            out.append((card.back, os.path.join(folder, os.path.basename(card.back))))
    return out


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

    A card moves as a pair. If its back will not move, its front is put back,
    so no folder ever holds a front without the back that pairs with it.

    Stops at the first card that fails rather than carrying on: every stack
    after a missing card would still look complete. Returns the moves made,
    which `undo` reverses; running again after a failure picks up where it
    stopped, skipping the cards already filed.

    `progress(done, total)` is called after each card.
    """
    issues = problems(plan, dest_root, template)
    if issues:
        raise ValueError("; ".join(issues))
    moves = moves_for(plan, dest_root, template)
    made = []
    total = len(moves) // 2
    for i in range(total):
        pair = moves[i * 2:i * 2 + 2]
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
    """A split that stopped part-way. `.moves` are the ones that were made."""

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
