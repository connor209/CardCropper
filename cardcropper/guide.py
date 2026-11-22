"""
What every page is for, how to use it, and what each of its controls does.

Kept apart from the pages themselves so the explanations read as one voice
and live in one place: a page's code says how it works, this says how to work
it. `shell` puts the title, lead and guide above each page, and hands the
tooltips to `ui.attach_tips`, which finds each control by the text it shows.

A tip names the control's effect and, where it matters, what it will NOT do
— the question a person hovering is usually asking.
"""

#: Pages that are one big workspace: their guide starts shut (F1 opens it).
GUIDE_CLOSED = {"grade", "variants", "prices", "lookup", "library"}

#: (group, [page keys]) — the sidebar, in the order the work is done.
GROUPS = (
    ("", ("home", "batches")),
    ("1 · Scan", ("folders", "split", "crop")),
    ("2 · Grade", ("condition", "grade", "library")),
    ("3 · List & price", ("variants", "lookup", "prices", "batch")),
)

PAGES = {
    "home": dict(
        label="Home", icon="⌂",
        title="CardCropper",
        lead="From a pile of cards to listed, graded and priced — one stage at a time.",
        guide=""),

    "batches": dict(
        label="Batches", icon="☰",
        title="Batches",
        lead="Every batch under a folder, how far each has got, and its next step.",
        guide="""\
Choose the folder your batches live under (once — it is remembered).

Each row is a batch: one coded for CardUploader (from its manifest), or one conditioned
from an export. The columns say how far each stage has got — batched, graded, variants,
priced, change file — read off the files those steps write, so it is always current.

Select a batch and Go to: its next step opens the page that step happens on, with the
batch's own file loaded."""),

    "folders": dict(
        label="Scan folders", icon="▣",
        title="Scan folders",
        lead="Make the day's empty, numbered folders to scan into — one stack per folder.",
        guide="""\
1. Location — where the folders go. Remembered, so set it once.
2. Date — today by default (YY.MM.DD).
3. How many — then Create folders.

Numbering carries on from the folders already there for that date, so a second session
the same day starts at 004, not 001. Existing folders are never touched."""),

    "split": dict(
        label="Split into stacks", icon="⫶",
        title="Split into stacks",
        lead="Scanned a long run in one go? File it into one folder per 50-card stack.",
        guide="""\
1. Add folder… — the folder the scanner wrote the whole run to. Set Cards per stack.
2. Walk the breaks. Each break shows the last card of one stack and the first of the
   next. Count that many off the physical pile: if it matches, press Matches the pile —
   next. If not (a double feed, a pulled card), move the break 1 card earlier / later.
3. Check where the folders go and what they are called.
4. Create folders & move scans. Scans are MOVED and keep their names; Undo puts them back.

Tick Crop each stack to crop every stack straight after filing, with the Crop cards
settings."""),

    "crop": dict(
        label="Crop cards", icon="✂",
        title="Crop cards",
        lead="Pair each card's front and back scans, and write the corner & edge photos.",
        guide="""\
1. Add folder… (or Add scans…). Scans pair in filename order — first a front, then its
   back — and a scan holding both faces becomes one card on its own.
2. Check the table. Wrong way round? Select the row and Swap front/back. Off by one?
   Remove the stray. A tinted row came from one scan holding both faces.
3. Check the output folder — a 'cropped' folder beside the scans, by default.
4. Crop cards.

Adding the next folder starts a new batch, and the output follows it."""),

    "condition": dict(
        label="Condition a CSV", icon="◐",
        title="Condition a CardUploader CSV",
        lead="Rank a whole export by back-border wear, so as few cards as possible need a look.",
        guide="""\
1. Choose the CardUploader export; the output folder follows it.
2. Prepare batch — downloads every card's scans, scores the back border, writes
   triage.csv (worst first) and conditioning-review.csv.
3. Work down the table. Select a card and grade it in the panel beside it: keys 1-6
   pick the grade, Enter saves it to conditioning-review.csv and moves on. (Or grade in
   the sheet itself — Open review sheet.) Stop when the cards stop being interesting.
   A batch prepared earlier: Open prepared batch….
4. Record grades & re-fit triage — teaches the triage from what you saw, and puts every
   grade in the Grade library.
5. Write grades to a copy of the CSV — the original export is never changed.

The score sorts; it never grades. It cannot see a crease or the front."""),

    "grade": dict(
        label="Grade", icon="◎",
        title="Grade",
        lead="One card at a time, every corner and edge at full size, beside clean and LP references.",
        guide="""\
Open… a batch manifest (manifest-XX.json) or a golden pick list.

Keys:  1-6 or N L M H D X — grade NM / LP / MP / HP / DMG / HOLD
       → / ← (Space / Backspace) — walk the card: both faces, crop sheets, then the 16 panels
       S both faces · C crop sheets · P panels · F front · B back

The rules decide what is recorded: NM is refused until every panel has been opened, a
capped grade says why, and a crease is never MP. Each grade is also kept in the Grade
library, with the card's images."""),

    "library": dict(
        label="Grade library", icon="★",
        title="Grade library & golden sets",
        lead="Every card graded by hand, kept with its images — and the golden sets chosen from them.",
        guide="""\
Grades arrive on their own: from the Grade tab as you key them, and from review sheets
when their grades are recorded or written. Import grades… brings in older ones.

Borderline? lists close calls — passes that disagreed, a capped grade, wear one short of a
rule. A blind check needs 3 borderline cards in every 10, so those are worth promoting.

Add to golden set… puts a card in its line's golden set at the grade it has IN HAND
(check the physical card). Blind checks read the set from golden/<line>.csv."""),

    "variants": dict(
        label="Variants", icon="◇",
        title="Variants",
        lead="Check every card whose catalogue entry also comes as Reverse Holo, Cosmos, stamped or 1st Edition.",
        guide="""\
Open… a CardUploader job's data (saved by the extension).

For each card the scan is shown beside the catalogue image. One key decides it:
   C  correct          S  switch to the chosen variant          X  HOLD

Nothing is switched from the scans alone. HOLD a vintage front with a modern back, or a
title that is plainly not the card. Write change file turns the switches into a file for
the extension."""),

    "lookup": dict(
        label="Card lookup", icon="⌕",
        title="Card lookup",
        lead="Find a card in the catalogue: its printings, reference prices, and whether a scan matches it.",
        guide="""\
Search by name, number and set — or paste a listing title.

Select a result for its printings and reference prices. A printing word in your search
(Reverse Holo, 1st Edition…) is checked against the printings that exist.

Match a scan… compares a scan with every result and ranks them — it tells you WHICH card
it is, not which printing (a scan cannot show foil).

Price this on eBay sends the card to the Market page."""),

    "prices": dict(
        label="Market", icon="£",
        title="Market — eBay sold listings",
        lead="Recent UK sales for any card, the price they make, and every listing behind it.",
        guide="""\
Type a card or listing title and Search sold (Enter). Language: English only, or All
languages to count the sales Comp Finder marks foreign (Japanese cards and the like). Comp Finder finds the sales; the
house rules make the price.

• Figures — the suggested price, what it is built on, the spread of the sales.
• Chart — every sale over time; click a dot for that listing.
• Listings — why each counted or was left out. Double-click to open on eBay.
• Leave out a sale that looks wrong — or Count it anyway when Comp Finder left out one
  that is plainly the card — to see the price either way (a what-if only).

History keeps every search with its listings. More ▾ has the golden test, priced batches,
importing sales from a Terapeak CSV, and Comp Finder sign-in."""),

    "batch": dict(
        label="Batch & price", icon="⚙",
        title="Batch & price",
        lead="Cut cropped folders into coded 50-card batches, price a job, and write the change file.",
        guide="""\
1. Make batches — add the cropped folders and CardUploader's job history; Preview, then
   Make batches & check. Codes follow the highest one CardUploader has seen.
2. Price & report — a CardUploader job's data priced from Comp Finder's sold comps.
   See sales in Market opens the result card by card, with every listing behind each price.
3. Write change file — conditions, prices and variant switches for the extension.

Each button runs the same code as the command line and prints what it did."""),
}


# ---------------------------------------------------------------- tooltips

_BROWSE = "Choose with a file browser instead of typing the path."
_LOG = "What happened, line by line. Problems are repeated at the end of a run."

TIPS = {
    "crop": {
        "Add scans…": "Add scan files by hand. They pair in filename order: front, back, "
                      "front, back… A scan holding both faces is one card on its own.",
        "Add folder…": "Add every scan in a folder (sorted so 2.jpg comes before 10.jpg). "
                       "After a crop, this starts a new batch.",
        "Clear": "Empty the table. No files are touched.",
        "Swap front/back": "Turn the selected rows round — for a card whose back was "
                           "scanned first. Works on combined scans too.",
        "Remove": "Drop the selected rows — for a stray scan that has knocked the pairing "
                  "one out. The file is left where it is.",
        "▲": "Move the selected rows up.",
        "▼": "Move the selected rows down.",
        "Scans": "How each scan file is read. Detect looks at every file: one face, or both "
                 "side by side. Getting this wrong is silent, so Detect is the default.",
        "Output": "Where the crops go, and what they look like.",
        "Folder": "Where the cropped cards are written. Follows the scans (a 'cropped' "
                  "folder beside them) unless you choose one — clear the box to hand it back.",
        "Browse…": _BROWSE,
        "Crops": "Labelled sheet: captions on every tile, for grading. Clean photo: no "
                 "labels and wider corners, for the listing.",
        "Naming": "Grouped: 0001_1_front.jpg… readable, and a wrong pair is obvious. "
                  "Continuous: one run of numbers. Both sort the same way.",
        "Order": "Where the crop sheets sit among a card's images. The front always leads — "
                 "it is the eBay gallery picture.",
        "^Write the front and back photos too": "Also write each face as a photo — the "
                                                "original scan, or the face cut out of a "
                                                "combined one — so the folder holds all four "
                                                "images per card.",
        "^Carry on from the cards already": "Number this batch on from the cards already in "
                                            "the output folder (0013, 0014…) instead of "
                                            "overwriting from 0001.",
        "Crop cards": "Write the corner and edge crops for every card in the table. Press "
                      "again to stop between cards.",
        "Open folder": "Open the output folder.",
        "Log": _LOG,
    },
    "folders": {
        "Location": "Where the day's folders are made. Remembered.",
        "Browse…": _BROWSE,
        "Date": "The date in the folder names, as YY.MM.DD so they sort by date.",
        "Today": "Back to today's date.",
        "How many": "How many empty folders to make. Numbering carries on from the folders "
                    "already there for this date.",
        "Create folders": "Make the folders. Existing folders are never touched.",
        "Open location": "Open the location in your file manager.",
        "Log": _LOG,
    },
    "split": {
        "Add folder…": "The folder the scanner wrote the whole run to.",
        "Add scans…": "Add scan files by hand, in order.",
        "Clear": "Start again with nothing added. No files are moved.",
        "Cards per stack": "How many cards make a stack — usually 50.",
        "Re-cut": "Cut the run again at the new stack size. Breaks you moved are reset.",
        "Scans": "Read the run exactly as the Crop tab would, so each stack folder pairs "
                 "there into the cards it was cut as.",
        "◀ Previous break": "Back to the previous break.",
        "Break 1 card earlier": "The pile says this stack ends one card sooner — e.g. a card "
                                "was pulled out to rescan.",
        "Break 1 card later": "The pile says this stack ends one card later — e.g. a double "
                              "feed.",
        "Matches the pile — next ▶": "The last card on the left and the first on the right "
                                     "match the physical pile. Confirm and go to the next "
                                     "break.",
        "Create in": "Where the stack folders are made — beside the run's folder unless you "
                     "choose.",
        "Browse…": _BROWSE,
        "Named": "Folder names. {date} {n} {first} {last} {count} are filled in; the default "
                 "is the Scan folders format.",
        "^Crop each stack once it is filed": "After filing, crop every stack into a "
                                             "'cropped' folder inside it, with the Crop cards "
                                             "tab's settings.",
        "Create folders & move scans": "Make the folders and MOVE each stack's scans into "
                                       "them. Nothing moves if any file would be "
                                       "overwritten.",
        "Undo": "Put every scan back where it was, and take away any crops made.",
        "Open folder": "Open where the stack folders were made.",
        "Log": _LOG,
    },
    "condition": {
        "CardUploader export": "The CSV exported from CardUploader. It is never changed — "
                               "every file is written beside it.",
        "Output folder": "Where triage.csv, the review sheet and each card's crops go. "
                         "Follows the export unless you choose.",
        "Browse…": _BROWSE,
        "First": "Only the first N cards — for a quick trial. Blank does the whole export.",
        "Write crops": "Write each card's corner and crop sheets — what you open to grade.",
        "Re-download scans": "Fetch every scan again instead of using the cached copies.",
        "Host corner photos for the listings (Supabase)": "Upload a clean corner photo per "
                                                          "face and add it to the listing's "
                                                          "pictures (after the front). Needs "
                                                          "the Supabase settings.",
        "Settings folder": "Open where thresholds, the calibration ledger, the condition map, "
                           "the grade library and golden sets are kept.",
        "Prepare batch": "Download, straighten, score and rank every card. Press again to "
                         "stop.",
        "Open folder": "Open the output folder.",
        "Open review sheet": "Open conditioning-review.csv to type the grades in.",
        "Check against catalogue…": "Compare every card's front scan with the catalogue "
                                    "pictures its title finds, and list the ones that do not "
                                    "look like their title — before they are listed.",
        "Open prepared batch…": "Open a batch prepared earlier — its output folder — to "
                                "grade it here without preparing it again.",
        "Record grades & re-fit triage": "Record the looked-at grades, propose better triage "
                                         "thresholds from them, and add every grade to the "
                                         "Grade library.",
        "Write grades to a copy of the CSV": "Write the grades into <export>-conditioned.csv. "
                                             "Cards that cannot be written honestly go to "
                                             "-held.csv with the reason.",
        "^Triage thresholds": "Where the likely-nm / review / likely-worse bands fall, and "
                              "what they were fitted from.",
        "Log": _LOG,
    },
    "grade": {
        "Manifest / golden picks": "A batch's manifest-XX.json, or a golden pick list (CSV) "
                                   "for a blind check.",
        "Open…": "Open a manifest or golden pick list.",
        "Grader": "Your name, recorded with every grade.",
        "Pass": "Which grading pass this is. When two passes disagree, the worse grade wins.",
        "Zoom": "Fit shows the whole image; 1-4 show it at that many times its real size — "
                "panels are never shrunk.",
        "^1 Front + back": "Both faces side by side (S).",
        "^2 Front crops": "The front's crop sheet (C).",
        "^3 Back crops": "The back's crop sheet (C again).",
        "^4 Panels": "The 16 corners and edges one by one, at full size (P). Stepping onto a "
                     "panel marks it opened.",
        "^Front only": "The front alone (F).",
        "^Back only": "The back alone (B).",
        "Clean reference": "The same panel of a clean card of this game, for comparison.",
        "LP reference": "The same panel of an LP card of this game, for comparison.",
        "wear points": "Wear points on each face. The back is pre-filled from the crop "
                       "checks; 3 or more across both faces rules out NM.",
        "whitened corners": "Corners with whitened tips. 3 or 4 on one face is LP at best.",
        "Crease": "Any crease is HP at best: HP if short, from an edge only, ink intact — "
                  "otherwise DMG.",
        "Note": "A short note kept with the grade (and in the Grade library).",
        "^NM (1)": "Near Mint (1 / N). Refused until every panel has been opened.",
        "^LP (2)": "Lightly Played (2 / L).",
        "^MP (3)": "Moderately Played (3 / M).",
        "^HP (4)": "Heavily Played (4 / H).",
        "^DMG (5)": "Damaged (5 / D).",
        "^HOLD (6)": "Hold back from listing — a rescan, a wrong pair, a doubt (6 / X).",
        "◀ Card": "Previous card.",
        "Card ▶": "Next card.",
        "Second opinion (Claude)": "Ask Claude for a grade from the crop sheets. Advice only — "
                                   "it is not recorded as a grade.",
    },
    "variants": {
        "Job data (JSON)": "A CardUploader job's data, saved by the extension.",
        "Open…": "Open a job's data.",
        "Write change file": "Turn the switches into changes-<job>-variants.json for the "
                             "extension to apply.",
        "Switch to": "The printing to switch this card to.",
        "Correct (C)": "The matched printing is right.",
        "Switch (S)": "Switch to the printing chosen beside it.",
        "HOLD (X)": "Do not list yet — a vintage front with a modern back, or a title that is "
                    "plainly not this card.",
        "◀": "Previous card.",
        "▶": "Next card.",
        "Note": "A note kept with the decision.",
        "Scan": "The card as scanned.",
        "Catalog image": "The catalogue's picture of the printing it was matched to.",
    },
    "batch": {
        "1. Make batches": "Cropped folders in; coded 50-card batches, manifests and crop "
                           "checks out.",
        "Cropped folders": "One or more cropped folders, in order.",
        "Add…": "Add a cropped folder.",
        "CardUploader history (JSON)": "CardUploader's job history — the next batch code is "
                                       "the one after the highest it has used.",
        "Browse…": _BROWSE,
        "Game": "Which game the batches are — it decides the code sequence.",
        "Language": "English or Japanese.",
        "Codes (optional)": "Force the codes to use, instead of reading the next ones from "
                            "the history.",
        "Preview": "Say what would be made, without making anything.",
        "Make batches & check": "Cut the batches, write the manifests and run the crop checks.",
        "2. Price  ·  3. Change file": "Price a CardUploader job and write the change file.",
        "Job data (JSON)": "A CardUploader job's data, saved by the extension.",
        "Grades (grades-XX.csv)": "The Grade tab's grades for this batch — conditions come "
                                  "from it.",
        "Variant queue (optional)": "The Variants tab's decisions, to include the switches.",
        "Fresh comps (skip Comp Finder's 24h cache)": "Search eBay again instead of using "
                                                      "sales Comp Finder found in the last day.",
        "Count every language": "Also count the sales Comp Finder leaves out as another "
                                "language (foreign) — for Japanese and other non-English "
                                "batches. Graded slabs, other printings and lots stay out.",
        "Price & report": "Price every card from sold comps under the house rules, and write "
                          "the run report.",
        "Report again": "Rewrite the report — after filling in Terapeak checks.",
        "Write change file": "Conditions, prices and variant switches in one file for the "
                             "extension.",
        "See sales in Market": "Open the priced batch on the Market page: each card's price, "
                               "chart and every sold listing behind it.",
        "Test (golden prices)": "Price the golden cards live and check the rules still give "
                                "the expected prices.",
        "Comp Finder login": "Open the file a pasted Comp Finder cookie goes in. Easier: sign in "
                             "with your email and password from Market → More → Comp "
                             "Finder sign-in…, and the app keeps the session fresh.",
    },
}

#: The one primary action on each page, and the housekeeping buttons that
#: should step back.
PRIMARY = {
    "crop": ("Crop cards",), "folders": ("Create folders",),
    "split": ("Create folders & move scans",), "condition": ("Prepare batch",),
    "batch": ("Make batches & check", "Price & report"),
    "variants": ("Correct (C)",),
}
SUBTLE = {
    "crop": ("Clear", "Open folder", "Browse…"),
    "folders": ("Open location", "Browse…", "Today"),
    "split": ("Clear", "Open folder", "Browse…", "Undo"),
    "condition": ("Open folder", "Settings folder", "Browse…"),
    "batch": ("Browse…", "Add…", "Preview", "Comp Finder login"),
    "grade": ("Open…",),
    "variants": ("Open…",),
}

SHORTCUTS = """\
Anywhere
  Ctrl+1 … Ctrl+9      go to a page, in sidebar order (Ctrl+0: Home)
  F1                   show or hide the guide for this page
  Ctrl+B               hide or show the sidebar
  Ctrl+, (comma)       open the settings folder

Grade
  1-6  or  N L M H D X     NM / LP / MP / HP / DMG / HOLD
  → ←  (Space Backspace)   walk the card: both faces, crop sheets, the 16 panels
  S  C  P  F  B            both faces · crop sheets · panels · front · back

Variants
  C  S  X                  correct · switch · HOLD

Condition a CSV (table selected)
  1-6                      pick NM / LP / MP / HP / DMG / HOLD
  Enter                    save the grade and move to the next card

Market
  Enter                    search sold listings
  Delete                   leave the selected sale out, or count a left-out one — a what-if
  Double-click a sale      open the listing on eBay
"""

#: Buttons given a style of their own: the grade keys take their grade's colour.
STYLES = {"grade": {f"{g} ({i})": f"{g}.Grade.TButton"
                    for i, g in enumerate(("NM", "LP", "MP", "HP", "DMG", "HOLD"), 1)}}
