# CardCropper

A Windows app that takes a folder of card scans, works out which faces belong
to which card, and writes two extra photos per card — the corners and edges of
the front, and the corners and edges of the back — then leaves the folder
numbered so every card's four images sit together in order.

A card can arrive as two scans, front and back, or as **one scan holding both
faces side by side**, which is what a flatbed gives you when you lay the card
down, flip it, and scan once. Either way a card comes out as four images.

It also runs the `card-conditioning` workflow on a CardUploader export: it
downloads every card's scans, ranks the batch by back-border wear so that as
few cards as possible need looking at, and writes your grades back into a copy
of the CSV. Nothing here grades a card or calls a model: it straightens, crops,
ranks and files. The judgement stays with you.

## What it does to a scan

1. **Divides it, if it holds two cards.** Cut on the strip of scanner bed
   between them, not on their proportions: two portrait cards side by side
   measure 1.43:1 and one card measures 1.40:1 the other way up, so a combined
   scan run as a single card passes every check the app makes and yields four
   "corners" that are the outer corners of the *pair* — two real, two interior
   artwork. It looks like a working run. The gap is the thing that tells them
   apart, so the gap is what is looked for.
2. **Deskews it, from all four edges.** Cards sit half a degree off in a sheet
   feeder. Crop the bounding box of a tilted card and take its corners and you
   do not get the card's corners — you get interior artwork, and it looks
   plausible enough to list from. Every crop is cut after a deskew.

   A strip of background ONE column wide counts as the seam. Cards fed through
   a document scanner touch along most of their length and often leave exactly
   that, and refusing it as too thin was costing the very pixel the rule was
   meant to protect: the division fell through to halving the pair, which lands
   a column out on an odd width, and that column is the last column of the
   first card.

   Where the seam is visible but not empty — dim enough to see, lit enough to
   have been grown through as card — the division snaps onto it. It looks four
   columns either side of the middle and no further: halving is only ever wrong
   by a column or two, and a wider search finds an emptier column *inside* a
   dark second card and moves onto that, which is the same bleed the other way
   round.

   Where the cards genuinely touch, the division is halfway along the pair by width
   rather than the average of its two ends — those differ by one pixel on an
   even width, and the pixel in question is the last column of the first card.
   It lands at the inner edge of the second half, where the edge crop magnifies
   it into a visible strip of the wrong card. A gap between the cards would
   absorb that; touching cards have none.

   The angle is the one most of each edge agrees on, taken across all four
   edges, rather than a line fitted through the left edge alone. On a full-art
   card the artwork is often as dark as the scanner bed and runs right to the
   border, so the detector cannot see the edge there and reads the first lit
   pixel *inside* the card instead. Those readings all sit on one side of the
   truth, and a straight-line fit through them is dragged a long way — a card
   sitting square measured 22 degrees off, and every crop was then cut from a
   scan rotated by that. No single edge can claim more than 8 degrees, an edge
   lying on the image boundary does not get a vote, and where no edge can be
   measured the scan is left alone and the log says so.
3. **Finds the card on the bed** on the value channel — `max(R,G,B)` — rather
   than on brightness. A Pokémon back's navy border is as dark as the scanner
   bed by luminance, so a brightness mask finds the card's bright *interior*
   and reports the artwork as the border.

   The bar separating card from bed is read off each scan rather than fixed.
   Modern cards are not the navy-bordered back this was written for: a Lorcana
   card's black lower band measures 12 against a bed of 0, and a League back is
   near-black with a few gold lines on it. A fixed bar of 20 calls both of
   those background — and it does not fail loudly, it trims off whichever part
   of the card happens to be dark, or breaks a back into lit patches that look
   like two separate cards. The learned bar is never looser than the fixed one,
   so no scan reads worse than it did.

   On top of that, the card's extent is grown outward from the rows and columns
   that are certainly card, through the ones that still look like it — a column
   crossing a near-black back is barely lit at all, but the few lit pixels it
   has reach from the top of the card to the bottom, and scanner bed never does
   that.

   A sheet feeder's bed is cleaned first, because it is not black: dust on the
   sensor draws a line down every page, and once the card has passed the
   feeder's backing shows for the rest of it. Both clear the bar, and took the
   card's outline out to the furthest line or down to the end of the page. A
   line is recognised by being narrow and running top to bottom, the backing by
   spanning the scan edge to edge from one end, so a card against the edge of
   the scan is left alone.

   Each side is then measured onto the card's actual edge, the steepest nearby
   step in brightness, so the glow a scanner throws off a card is not counted
   as card. On a navy back the step from border to swirl is taller than the
   card's own edge, so any climb steep enough to be paper counts as an edge
   even beside a taller one, or the border is measured away.
4. **Cuts the corners and edges**, keeping a margin of background so the card's
   outline shows. A corner that has been rounded off is read from its profile
   as much as from whitening on its face. That margin is never invented: on a
   scan cropped flush to the card the crop runs to the edge of what was
   actually scanned, rather than padding out to a background the card was never
   photographed against. A corner crop showing an invented silhouette is worse
   than one showing none.
5. **Sharpens lightly, and never enhances contrast.** Auto-contrast on a navy
   border turns JPEG noise into speckle indistinguishable from whitening —
   damage the card does not have.

## Getting it running

There are two downloads, and which one you need depends on whether your machine
has **Smart App Control** switched on.

**`CardCropper-python.zip` — works everywhere, needs Python installed.** Unzip
it and double-click `CardCropper.bat`. The first run installs Pillow and numpy
and takes a minute; after that it just opens the window. If Python is missing
the launcher says so and points at the installer — get it from
[python.org](https://www.python.org/downloads/windows/), tick **Add python.exe
to PATH**, and leave **tcl/tk and IDLE** enabled.

**`CardCropper.exe` — nothing to install, but unsigned.** Windows treats it in
one of two ways:

- *SmartScreen* shows a blue "Windows protected your PC" box. Click **More
  info** → **Run anyway**. Normal for any unsigned app.
- *Smart App Control* shows "Smart App Control blocked this app" with **no way
  past it**. Microsoft's documentation is explicit that there is no per-app
  bypass — the only ways through are turning Smart App Control off entirely, or
  signing the executable with a certificate that has built up reputation.

Smart App Control governs *executables*, not scripts, which is why the `.bat`
launcher works on a machine where the `.exe` cannot: it runs the same code
through Python's own `pythonw.exe`, signed by the Python Software Foundation
and trusted already. Nothing has to be turned off.

If you would rather turn Smart App Control off, it is in Windows Security →
App & browser control → Smart App Control settings. Microsoft's current
documentation says it can be turned back on afterwards; it used to require
resetting Windows, so check that page says so on your machine before relying
on it.

## Updates

Every change to `main` that builds and passes the tests is published as the
[latest release](https://github.com/connor209/CardCropper/releases/latest) —
the same two downloads, at links that never change.

**`CardCropper-python.zip` updates itself.** A moment after the window opens it
checks the latest release, and if there is a newer build it says what changed
and asks whether to update. **Yes** downloads it, swaps it in and restarts the
app — a few seconds; **No** asks again next time. It never asks while a crop or
a split is running, since updating restarts the app. With no internet, or
GitHub unreachable, it says nothing and opens as normal.

**`CardCropper.exe` cannot replace itself** while it is running, and Smart App
Control would block the new one anyway, so it offers to open the download page
instead.

A copy run from a git checkout never updates: it would overwrite the working
tree. Only the builds CI makes carry the stamp the check compares.

The check needs the repository to be public — a release on a private one
cannot be downloaded without signing in, so the check fails and stays quiet.

## Using it

Double-click `CardCropper.bat` (or `CardCropper.exe`).

### The window

A sidebar lists the pages in the order the work runs, and **Home** shows
where things stand: how many cards have been graded, how far the golden set
has come, whether Comp Finder is logged in. Home also has a quick search
straight to a card's sold prices or catalogue entry.

| Stage | Pages |
|---|---|
| **1 · Scan** | **Scan folders** (the day's empty folders), **Split into stacks** (one long scanning run filed into stack folders), **Crop cards** |
| **2 · Grade** | **Condition a CSV** (rank an export by wear, and grade it beside the ranking), **Grade** (card by card, panel by panel), **Grade library** (every hand grade, and the golden sets) |
| **3 · List & price** | **Variants**, **Card lookup** (catalogue, printings, scan matching), **Market** (eBay sold listings), **Batch & price** |

Every page says in one line what it is for, and has a step-by-step guide
under **Guide ▾** (F1). **Hover over any control** for what it does — and, for
one that is greyed out, what it is waiting for. Each page has one primary
action in blue; the actions used now and then are behind its **More** menu.

Keys: **Ctrl+1…9** jump between pages in sidebar order, **Ctrl+0** goes Home,
**Ctrl+B** hides the sidebar, **Ctrl+,** opens the settings folder.
**Help → Keyboard shortcuts** lists every page's keys. The **Tools** menu
holds the Comp Finder login, importing grades into the library, and the
update check. The status bar at the bottom shows the build, the library's
size and the Comp Finder login.

### Crop cards

1. **Add scans…** or **Add folder…**. Every scan is looked at as it is added.
   One holding both faces becomes a card on its own; the rest pair in filename
   order, the first a front, the second its back, and so on. `2.jpg` sorts
   before `10.jpg`, so a scanner that changes its zero-padding mid-batch does
   not scramble the pairs.
2. **Check the table.** Each row is one card. A row read from a combined scan
   is tinted, and names the same file twice — `0001.jpg (1 of 2)` and
   `0001.jpg (2 of 2)`. Where a row is the wrong way round, select it and press
   **Swap front/back**, which swaps the two halves of a combined scan just as
   it swaps two files; where the run is off by one, remove the offender. A file
   left over is called out in red rather than folded into a card.
3. **Check the output folder.** It is filled in as a `cropped` folder beside
   the scans you added. It must not be a folder the scans came from — the
   crops would be read back as scans on the next run and paired into the batch.
   The app refuses that rather than letting it happen quietly.
4. **Crop cards.**

Then add the next folder of scans. It starts a new batch — the cards just
cropped are not carried into it — and the output moves to a `cropped` folder
beside the new scans, so a day of `26.09.26 - 001`, `- 002`, … is cropped one
folder at a time without browsing for the output each time. An output folder
you pick with **Browse…** or type in yourself stays put instead; clear the box
to hand it back to the scans.

### Backs too dark to find their own edge

Some backs are printed so close to black that the card's border and the bed
look like the same thing, and the detector finds the frame line printed a few
millimetres in instead — so the corner crops show the corners of that frame
rather than the corners of the card. On a real scan a Lorcana back detected as
693×987 where the front of the same card came out 746×1011.

Measured on a document scanner, they are *not* quite the same thing. Its
backing reads as exactly zero — every pixel, no noise at all — while the card's
black border has a third of its pixels above zero. That is a clean separation,
and what was stepping over it was a fixed margin of 4 added on top of the
measured bed level. The bed level is already the 98th percentile of the bed and
carries its own headroom, so the margin was counting the same noise twice. At
zero, the same back comes out 723×992 against a true 724×988.

That fixes the geometry. It does not make the edge *visible*: black card
against black backing is still black, so the corner crops are cut in the right
place but a person reading them cannot see the outline. For that the
background has to differ from the card — see below.

Where the edge really is invisible, a card's two faces are the same piece of
card at the same resolution, so the face that *does* detect knows the size the
other should have been. The smaller one is grown out to the larger, about its
own centre — which is where the visible frame is printed — and never past the
edge of the scan. The log says when it happens and what the two measurements
were.

Only where the smaller face came out inset on every side, though. A box that
stops short of the card looks exactly like a box stopped short by the edge of
the scan, and those want opposite treatment: the first is a missed edge worth
growing back, the second is simply the end of the pixels, and growing it pads
the card with background. Two faces of one card clipped differently by the same
scan are each right about their own half, and neither is a reference for the
other.

This recovers the card's real extent. It cannot recover its *outline*: a black
border against a black bed has nothing to see, whatever the crop is cut to.

### A white backing, for backs like those

Set **Laid on a white backing** (`--background light`) and the whole comparison
turns over: the card becomes what is *darker* than the background. Put a sheet
of white paper behind the cards and a black-bordered back goes from 0 against 0
to 0 against 240 — the clearest edge on the sheet. On a test pair the inky back
went from detecting 697×987 with no measurable edge at all, to 740×1030 exact
with all four edges usable.

Use it for backs printed to the edge in black, where you want the outline to be
*visible* and not merely correct. A dark bed is still right for everything else,
and is the default.

On a document scanner you cannot lay paper behind the card, but the same thing
is reachable two other ways: many scanners have a background colour in their
driver, and most ship a carrier sheet — put the card in it with a piece of white
paper behind, and the pair feeds as one page.

**It is a setting, not a detection, and that was measured before it was
decided.** A white backing sheet reads as a bright, flat border — ring median
243, spread 3. A yellow-bordered Pokémon card scanned flush to its edges reads
as a bright, flat border: median 250, spread 0, brighter than anything inside
it. There is no statistic separating a white bed from a white border, because
the difference is which side of the edge the paper is on and a scan does not
record that. Guessing would invert every judgement the app makes across a whole
batch of flush-cropped light-bordered cards, to save one click. So where
detection fails and the border looks like it could be a backing, the log says
so and leaves the choice to you.

### It also says when the outline cannot be SEEN

Cutting a crop in the right place and being able to read it are two different
things. A corner is judged from its profile — the card's outline against
whatever is behind it — and a black border on a black backing has a profile
that is exactly right and invisible.

So the app measures the card's outermost ink against the backing behind it and
says when the gap is too small to read, naming both numbers and which backing
would fix it. Measured on real cards, on a dark bed:

| | card edge | backing | gap |
|---|---|---|---|
| Bright front | 102 | 0 | 102 — reads fine |
| Navy back | 24 | 0 | 24 — too close |
| Near-black front | 15 | 0 | 15 — too close |
| Black back | 0 | 0 | 0 — nothing to see |

On a white backing every one of those lands between 141 and 243.

This is why there is no per-game setting. It is not a property of the game but
of the card's EDGE, it differs between the two faces of one card, and it cuts
both ways: a white-bordered card on a white backing is exactly as unreadable as
a black-bordered one on a dark bed. A measurement covers every game, including
ones that do not exist yet; a table of games would be wrong the first time
somebody prints a full-art variant that bleeds to the edge.

It is only asked where there is a margin to ask it of. On a scan cropped flush
the only background is a sliver in the corner arcs, and measuring against that
would report a healthy contrast for a card that has none anywhere it matters.

### Scan a little wider than the card

A scanner set to crop to the card takes the background with it, and the
background is half of what a corner crop is for: a corner that has been rounded
off or crushed is read from its *profile* — its outline against the bed — as
much as from whitening on its face. Crop flush and that outline is the one
thing missing.

A real scan measured 87.4mm tall against a card that is 88.0mm, so both cards
in it ran clean off the top and bottom. The crops still came out; they just
could not answer the question they were cut to answer. It costs the deskew too,
since an edge lying on the image boundary is a straight line at zero degrees
whatever the card is doing, so it gets no vote — a card clipped on three sides
is left measuring its angle from one.

The app says so in the log when it sees it, naming the sides. A few millimetres
of bed around the card is all it needs.

### Scans that hold both faces

The **Scans** setting decides how a folder is read, and it defaults to
detecting it per file, because getting this wrong is silent — nothing about a
combined scan run as a single card looks wrong until you open the crops.

| Setting | When |
|---|---|
| **Detect** | The default, and right for a mixed folder. Each file is examined once as it is added; the answer is remembered, so changing the other settings is instant. |
| **One face per file** | A folder you already know is front, back, front, back. Nothing is examined, so adding a folder of several hundred from a cloud-synced drive is immediate. A scan that looks like it holds two cards is still called out in the log. |
| **Both faces on every scan** | Every file holds a pair — including cards laid *touching*, where there is no strip of bed to find and the app will not divide on a guess of its own. Here it divides halfway along what it found by WIDTH, which is the seam whenever both halves are the same card, and checks that two card-shaped halves came out of it. It only says anything if they did not. |

**Front: work it out** reads which side is the front off the batch, and is the
default. No single card can say — it has two pictures and nothing to choose
between them — but a batch can: every card has a different front and the *same*
back, so the side that looks like itself across the batch is the back. That is
the only honest signal. The quick ones (a back is darker, plainer, symmetrical)
are wrong on enough card games to put the wrong face in the eBay gallery
thumbnail without telling you.

It declines rather than guesses. Fewer than three cards is not evidence, and
neither is the same card scanned over and over — a playset, a stack of bulk
commons — where the fronts match each other as exactly as the backs do and
being alike no longer picks anything out. In both cases it takes the first of
each pair, says so in the log, and leaves you to check. The explicit settings
are still there, and **Swap front/back** still fixes a single row.

A face cut out of a combined scan is written as the card itself — deskewed, cut
out, with the same thin margin of bed the crops keep — because there is no
original to copy: the file holds the other face too. Cards scanned separately
are still copied byte for byte.

### Options

| Option | What it changes |
|---|---|
| **Crops** | `Clean listing photo` has no captions and frames the corners wider — a buyer has not been told what to look for, and at grading magnification paper fibre reads as damage. `Grading sheet` is the skill's labelled sheet, for your own eyes. |
| **Naming** | `Grouped` writes `0001_1_front.jpg`, `0001_2_back.jpg`, … — readable, and a wrong pair is obvious. `Continuous` renumbers the batch as one run, `0001.jpg` through `0008.jpg` for two cards. Both sort into the same sequence. |
| **Order** | Where the crops sit. Either way the **front leads** — the first image is the eBay gallery thumbnail, and that has to be the card, not a magnified corner. |
| **Front & back photos** | On by default, so the output folder is the complete set of four images per card — the original scan where a card was scanned on its own, that face cut out of the scan where it was not. Off writes only the two crop sheets. |
| **Carry on from the cards already there** | On by default. A second batch cropped into a folder that already holds ten cards is numbered from `0011`, so a session split over several sittings still comes out as one continuous run. Off numbers from `0001` again and overwrites. |

The original scans are copied, never moved. If a pairing turns out to be off by
one, the fix is to pair again — only possible while the scans are still where
the scanner left them.

### Cropping a folder in more than one sitting

The numbering carries on by itself. Crop twelve cards into `cropped`, come back
with another eight, point the app at the same folder, and they are written as
`0013` to `0020` — the line under the options says so before you press Crop,
and the table shows each row's real filenames.

Because the output normally follows the scans, pick the shared folder with
**Browse…** for this — a folder chosen by hand is kept from batch to batch.

The next number is read back from the filenames rather than from a counter kept
somewhere, because the folder is the thing you edit: cards get deleted,
re-cropped, or dragged in from another run, and a stored counter would be wrong
the moment any of that happened. Both naming schemes are read every time, so a
folder filled one way and added to the other still cannot collide. Files the
app did not write — a stray export, `IMG_4021.jpg` — are not counted as cards.

To go back to `0001` and overwrite, untick **Carry on from the cards already in
the output folder** (`--restart` on the command line). That is the one case
that still asks before it overwrites.

## Making the day's scan folders

The **Scan folders** tab makes empty folders to scan into, named for the day and
numbered: `26.09.26 - 001`, `26.09.26 - 002`, …

1. **Location** — where the folders go. It is remembered, so it only needs
   setting once.
2. **Date** — today by default, in `YY.MM.DD` form.
3. **How many**, then **Create folders**.

Numbering carries on from whatever is already there for that date, so a second
session the same day starts at `004` rather than colliding with `001`. Existing
folders are never touched. The app reopens on whichever tab you used last.

From the command line, `CardCropper.exe --folders 5 "D:\Scans"` does the same;
leave out the location to reuse the last one, and add `--date 26.09.25` for
another day.

## Splitting a long run into stacks

The **Split into stacks** tab is the other way to get to the day's folders:
scan 500 cards in one run rather than ten runs of 50, then let the app file the
run into one folder per stack.

1. **Add folder…** and pick the folder the scanner wrote the whole run to.
   Set **Cards per stack** (50 by default). The **Scans** and bed settings are
   the Crop tab's own, so the run pairs exactly as it would there — a scan
   holding both faces is one card — and is then cut every 50 cards.
2. **Walk the breaks.** For each break the app shows the last card of one
   stack and the first card of the next, front and back. Count that many cards
   off the physical pile: the last one should be the card on the left, the
   next one the card on the right.
   - It matches → **Matches the pile — next**.
   - It does not (a double feed, a card pulled out to rescan) → **Break 1 card
     earlier / later** until it does. The scans decide which folder a card goes
     in, so a stack of 49 that matches the pile is right and a stack of 50 that
     does not is wrong. Stacks that are not the usual size show in red.
3. **Folders** go beside the run's folder unless you choose elsewhere, named
   in the Scan folders format — `26.09.26 - 004`, … — numbered on from that
   day's folders already there. The name can use `{date}`, `{n}` (stack
   number), `{first}` and `{last}` (card numbers) and `{count}`; `Box {n:02d}
   ({first}-{last})` gives `Box 01 (1-50)`.
4. **Create folders & move scans.** The scans are **moved**, not copied, and
   keep their filenames, so each stack folder pairs into exactly the cards it
   was cut from and is ready for the Crop tab. Nothing moves if any
   destination file already exists. A card moves whole — never a front without
   its back — and if a move fails part-way, running it again carries on into
   the same folders; **Undo** puts every scan back where it was.

**Crop each stack once it is filed** does the Crop tab's job on every stack
straight after filing it, into a `cropped` folder inside each stack's folder —
where the Crop tab would put it. It uses whatever is set on the Crop cards tab
(crops, naming, order, front, photos too), shown under the checkbox, so there
is one set of crop options rather than two. Each stack is paired and has its
front worked out from its own folder, exactly as cropping that folder by hand
would. **Stop** stops the cropping between cards — the filing is already done
by then — and **Undo** takes away the crops as well as putting the scans back.
Cards that fail are listed at the end, stack by stack.

A scan left without a partner is named in red and left where it is.

From the command line there is no walk through the breaks, so look at
`--dry-run` first:

```
CardCropper.exe --stacks "D:\Scans\big run" --per 50 --dry-run
CardCropper.exe --stacks "D:\Scans\big run" --per 50
CardCropper.exe --stacks "D:\Scans\big run" --per 50 --crop --style listing
```

`--split` and `--background` are as for `--cli`; `--into` puts the folders
somewhere other than beside the run; `--start` sets the first number, which is
how a stopped run is resumed into the same folders. `--crop` crops each stack
once filed, taking `--style`, `--naming`, `--order`, `--front` and
`--no-originals` as `--cli` does.

## Conditioning a CardUploader batch

The **Condition a CSV** tab (or `--cli condition`) takes a CardUploader export
and sits between the export and pricing. Conditioning comes before pricing on
purpose: copies of one card that grade differently are different products.

1. **Prepare the batch.** Pick the export and an output folder. For every row
   it downloads both scans from `PicURL` (cached, so a re-run is quick),
   deskews them, scores the **back** border and writes:
   - `triage.csv` — every card, **sorted worst corner first**, with its band:

     | Band | Rule (provisional) | What to do |
     |---|---|---|
     | `likely-worse` | worst corner > 3.0% or total > 1.0% | open `corners-back.jpg` |
     | `review` | everything else, deliberately wide | work down the list; stop when cards stop being interesting |
     | `likely-nm` | worst corner < 0.20% and total < 0.02% | leave |
     | `no-scan` / `error` | not exactly two scans, or unreadable | rescan; pre-filled as `HOLD` |

   - `cards/<sku>/` — `front.jpg`, `back.jpg` (deskewed), `corners-back.jpg`,
     `crops-back.jpg`, `crops-front.jpg`. Double-click a card in the table to
     open the one worth looking at.
   - `conditioning-review.csv` — one row per card, for the grades.

   WotC-era sets (1999–2003) are flagged: open front and back together, since a
   modern swirl back on a vintage front means the scanner paired the wrong
   images — a **HOLD**, not a grade. Yu-Gi-Oh and Japanese cards are always
   `review`: the score is fitted to the English Pokémon navy back only.

   If most `border_base` values come back well under 40, or nearly everything
   lands in `likely-worse`, the run says so: card-edge detection has failed and
   the scores mean nothing.

2. **Grade** — in the window, or in `conditioning-review.csv`. Select a card
   in the ranked table and the panel beside it shows its back corners (or
   either face's crops, or both faces). **1-6** pick NM / LP / MP / HP / DMG /
   HOLD, **Phrases** adds the usual flaw wording to the note, and **Enter**
   saves the card's row of the review sheet with `looked = yes` and moves to
   the next card. A grade above HP whose note mentions a crease is refused
   there and then. **Open prepared batch…** opens a batch prepared earlier,
   ready to grade, without preparing it again. Every grade saved here also
   goes into the [grade library](#the-grade-library-and-golden-sets).

   Or grade in `conditioning-review.csv` itself against the house guide: `grade`
   (NM / LP / MP / HP / DMG or `HOLD`), a one-line `flaw_note`, `confidence`,
   the `evidence` you saw, and `looked` = `yes` if you opened a crop. **The
   score is not a grade and is back-only** — a card can score clean and still
   be LP. A re-run never overwrites a review sheet that has grades in it.

   **Any crease is HP or worse** (house decision, 15 Oct 2026 — this replaces
   the guide's "a single light crease: MP"). A card graded NM, LP or MP whose
   flaw note or evidence mentions a crease is held at the write step rather
   than listed; "no crease" does not count.

3. **Record grades & re-fit triage** (`--cli calibrate`). The `looked = yes`
   grades go into a calibration ledger with the scores they got, and new
   thresholds are proposed from everything graded so far. Grades taken off the
   score are left out: they would only confirm the thresholds already in use.
   A proposal is only used once you adopt it.

4. **Write grades to a copy of the CSV** (`--cli apply`). Writes
   `<stem>-conditioned.csv` with the three condition columns set, the grade at
   the end of the title updated, and the flaw note appended to the
   description. Vintage Pokémon (WotC 1999-2003 and EX era 2003-2007) graded
   NM is listed as LP; modern Pokémon, Yu-Gi-Oh and MTG NM stay NM (house rule,
   21 Nov 2026).
   Cards that are held, ungraded, or need a condition whose strings are
   unknown go to `<stem>-held.csv` with the reason. The original export is
   never modified.

### Unlocking LP / MP / HP / DMG

Only the NM condition strings have been seen in a real export. A guessed value
does not fail loudly — it rejects the batch or lists every card at the wrong
condition — so nothing else is built in, and until it is filled in every card
that lists below NM is held (vintage Pokémon graded NM lists as LP). To unlock a grade, condition
a card at that grade by hand in CardUploader, export, and copy its
`*ConditionID`, `C:Card Condition` and `CD:Card Condition - (ID: 40001)` cells
into `condition-map.json` (**Settings folder** button; `%APPDATA%\CardCropper`
on Windows, `~/.cardcropper` elsewhere). The thresholds and the calibration
ledger live there too.

### Photos on the listings (optional)

With `NEXT_PUBLIC_SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` set (and
migration 012 applied, which creates the `listing-photos` bucket), **Host
corner photos** / `--upload` builds a clean listing photo per face, uploads it
under a fresh `conditioning/<sku>/<uuid>.jpg` (eBay caches by URL), checks it
can be fetched back, and writes `<stem>-with-photos.csv`. URLs are appended to
`PicURL`, never prepended, so the front stays the gallery thumbnail. A failed
upload leaves that card's `PicURL` unchanged and is reported. When this file
exists, **Write grades** starts from it, so grades and photos land in one
upload file.

### Excel

Opening the export in Excel turns a card number like `4/99` into `Apr-99`. The
app repairs those where the listing title confirms the original number, and
lists the ones it cannot. Better not to round-trip the CSV through a
spreadsheet at all.

## From cropped folders to CardUploader

The batch work that used to be done by hand or with an AI assistant —
batching, crop checks, grading, variant checks, pricing and the CardUploader
edits — runs here as rules. AI is only used where a rule cannot decide: the
optional single-card second opinion on the Grade tab.

### 1. Batches, codes and manifests (Batch & price tab, `--cli batch`)

Point it at the cropped folders, in run order, with the game and language.
Each folder is cut into batches of 50 and each batch gets the next unused
CardUploader code:

| Game | Codes | SKUs |
|---|---|---|
| Pokémon (EN and JP) | `AA` … `AZ`, `BA` … `ZZ` — `GA` follows `FZ` | `KA1` … `KA50` |
| Yu-Gi-Oh | `YGO-A` … `YGO-Z`, then `YGO-AA`, `YGO-AB` | `YGO-A1` … `YGO-A50` |
| MTG | `MTG-A` … as Yu-Gi-Oh | `MTG-I1` … |

The next code is the one after the **highest** code CardUploader already
holds, empty batches included (batch `IP` exists and is empty; it still
counts), so a code is never reused. That list comes from CardUploader's batch
history: save it with the extension's **Save batch history** and pass it as
`--history`, or set `CARDUPLOADER_TOKEN` and use `--fetch-history`. Without
one or the other it refuses to pick codes — a reused code puts two cards
under one SKU.

Each batch writes `manifest-<code>.json` and `.csv` (SKU, game, language, and
the paths of all four images) into its folder, then `checks-<code>.csv`:

- **Missing images** — every card needs front, back, front crops and back
  crops. `INCOMPLETE`.
- **Solid black edge strip** — the crop shows bed where the card's edge
  should be: a missed edge. That panel is `UNCHECKED` until re-cropped from
  the full scan, and the Grade tab will not accept NM over it.
- **Second card in frame, or bed showing inside the card** — `HOLD`.
- **Triage scores** (local, no AI): whitening points per panel on both faces
  (`front: corners 0, top 0, R 1, bottom 0, L 0; back: …`) counted only where
  brighter than the scanner's halo, ignoring the uniform scanner hairline
  along an edge and the printed dot on MTG backs; and for English Pokémon
  backs, marks against a reference back built from ~50 LP backs
  (`--cli reference <backs>`): 50px+ area or 30px+ long is `review`, 150px+ or
  60px+ long a `dmg-candidate`, and a huge difference usually means the two
  did not line up — check by eye.

**Scores only triage.** No game may skip a look on a score until it has been
calibrated on its golden set with zero LP-or-worse cards below the clean
threshold. Today that is no game, and Yu-Gi-Oh never: every card is opened.

### 2. Grade (Grade tab)

Open a manifest. One card at a time; each of the 16 panels (corners and
edges, front and back) at native size — zoomed by a whole number, never
shrunk — with the same panel of a clean and an LP reference card beside it.
Put the references in `references/<game line>/clean.jpg` and `lp.jpg` (or
`clean-front.jpg`, `clean-back.jpg`, …) in the settings folder.

Each card opens on its **front and back side by side**. **→** walks the card in
order — front + back, the front's crop sheet, the back's crop sheet, then the
16 panels one by one — and **←** goes back. Or jump: **S** front + back, **C**
the crop sheets (again for the back's), **P** the panels, **F** or **B** one face
alone. Keys **1-6** or **N L M H D X** grade it NM / LP / MP / HP / DMG / HOLD.
Whole images fit the window unless a zoom is picked; the panels keep their
own zoom. The back's wear counts are pre-filled from the checks; the front's
are shown but not pre-filled, since foil and artwork at a front's edge read
as wear (a Hoopa ex holo scored 58 points) — enter the front's yourself. The rules are enforced, not suggested:

- NM needs every panel of both faces opened, none unchecked, and fewer than 3
  wear points across both faces.
- Whitened tips on 3 or 4 corners of either face: LP at best.
- Any crease: never MP — HP if short, from an edge only, ink intact;
  otherwise DMG.
- Two passes that disagree: the worse wins (set **Pass** to 2 for a second).

Grades go to `grades-<code>.csv` beside the manifest. **Second opinion
(Claude)** sends that one card's two crop sheets to the Claude API in one
call (needs `pip install anthropic` and an `ANTHROPIC_API_KEY`); the answer is
advice and is never counted as a pass.

Listing rules, applied after grading: vintage Pokémon NM lists as LP, modern
NM as NM, Yu-Gi-Oh and MTG NM stay NM, and NM priced £15+ goes on the in-hand
check list before listing. Full rules: `claude/conditioning-guide-v2.md`.

### 3. Golden sets and audits (`--cli golden`, `--cli audit`)

A golden set per game line (`pokemon-en`, `pokemon-jp-vintage`, `yugioh`,
`mtg`) lives in `golden/<line>.csv` in the settings folder: 30-50 cards graded
in hand by Connor (`id, card, true_grade, borderline, front, back,
front_crops, back_crops`).

- **Before a batch:** `--cli golden pokemon-en --pick blind.csv` picks 10, 3+
  borderline. Grade them blind on the Grade tab (open `blind.csv`), then
  `--cli golden pokemon-en --score blind-grades-score.csv`. Any overgrade:
  stop.
- **After a batch:** `--cli audit --grades grades-KA.csv --pick audit.csv`
  picks 8 random NM and 3 LP for an in-hand check. Fill in `true_grade` (and
  root cause, fix, refund for any disagreement), then `--cli audit --record
  audit.csv --batch KA --game pokemon`. Every disagreement goes to
  `error-log.csv` (date, SKU, card, game, source, our grade, true grade, root
  cause, fix, refund).
- Target: NM overgrade rate under 2% per game. Above it, that game's NM lists
  as LP until two clean audits in a row; `--cli audit` shows the status.

### The grade library and golden sets

Every card graded by hand is recorded in the **grade library**
(`grade-library.sqlite` in the settings folder) as the grade is made: each
pass keyed on the Grade tab, and each grade in a conditioning review sheet,
whether it was saved in the window or recorded and written from the sheet.
The card's four images are copied into `grade-library/` beside it, so the
record outlives the batch folder. Machine opinions are never recorded as
grades — a triage band, a HOLD, a Claude second opinion nobody accepted. Blind
checks of golden cards are kept apart: they test the grader.

The **Grade library** page lists them all: each card's images, every grade it
has had (the worse pass stands), and how cards that measured like it were
graded. It marks the cards that look **borderline** and says why: passes
that disagreed, a keyed grade the rules capped, wear counts one short of a
rule's limit, a grade keyed with low confidence. A blind check needs three
borderline cards in ten, so those are the ones worth promoting.

**Add to golden set…** puts a card in its line's `golden/<line>.csv` — the
file the blind checks read — at the grade it has **in hand** (check the
physical card first), pointing at the library's own copies of its images.
The strip at the top shows how far each line's set has got: 30 cards, and
enough borderline ones for a blind check. **Import grades…** brings in grades
made before the library existed (every `grades-*.csv` and
`conditioning-review.csv` under a folder; safe to run twice), and **Re-read
golden-set files** picks up cards added to a golden file by hand.

The Grade tab and the Condition panel show the library's advice beside each
card — "graded cards with these back counts: LP 5, MP 1" — once it has enough
cards. It is advice from your own past grades, never a pre-selected grade,
and it stays hidden on a blind check.

### 4. Variants (Variants tab, `--cli variants`)

Once CardUploader has matched a batch, save its job data with the extension
and open it here. Every card whose catalog entry also comes as Reverse
Holofoil, Cosmos, stamped or 1st Edition is shown, scan beside catalog image:
**C** correct, **S** switch to the chosen variant, **X** HOLD (a WotC-era
front with a modern back; a title plainly not the card it was matched to,
such as a World Championship deck card matched to the set print). Nothing is
switched from the scans alone — in the 21 Nov 2026 audit, 24 of 69
scan-based reverse calls were wrong.

### 5. Price and report (Batch & price tab, `--cli price`, `--cli report`)

Prices come from eBay UK sold comps through Comp Finder (`POST
/api/batch-comps`, title form, 35 cards a call). It signs in with your Comp
Finder session cookie: **Comp Finder login** on the Batch & price tab opens
`comp-finder-cookie.txt` in the settings folder to paste it into (Windows'
`setx` cuts long values off at 1024 characters; `COMP_FINDER_COOKIE` still
works where it fits), and **Test** runs the golden prices. To copy the
cookie: open Comp Finder signed in, F12 → Network, reload, click any request
to the site and copy the `cookie` request header's value. The house rules are applied here
so every number can be redone by hand:

- every sale is checked against the card's printing (Cosmos, Reverse,
  1st Edition, stamped…) on top of Comp Finder's own filter;
- sale total = price + postage, postage capped at £4.00;
- the 40th percentile of the totals, the lowest if fewer than 4 sales;
- up onto the .49/.99 ladder, never below the floor (Pokémon £2.49,
  Yu-Gi-Oh £1.99);
- the condition scale; sensibility flags; and the decision: apply, Terapeak
  (a person checks — **always for a rise on fewer than 4 sales**), keep (no
  fresh comps), or hold (the lookup failed).

Values the feature notes do not spell out — the condition multipliers, the
MTG floor, which exclusions are put back, the flag thresholds and how recent
a register row must be — are Comp Finder's house values or marked
PROVISIONAL at the top of `cardcropper/pricing.py`. Check them against
`claude/pricing-standard.md`. The golden pricing tests (Riolu Cosmos BW33
~£50.49, Riolu BW33 ~£14.99, Numel Peelable Ditto ~£15.99, Lugia 78/124
~£4.49, Exeggutor 109/108 ~£5.99, Aron 44/100 £2.49, Fearow 36/100 £2.49) run
with `--cli price-golden`, and must pass after every change to the rules.

Every price is recorded in the price register (`price-register.sqlite` in the
settings folder); a row is reused only if it came from eBay sold comps and is
recent. The run writes `pricing-<job>.csv`, `terapeak-<job>.csv` (fill in
`checked` and `terapeak_price`, then **Report again**) and the run report:
SKUs priced / total, cards with 10+, 4-9, 1-3 and 0 sales, Terapeak checks,
recommendations not applied, the Connor list, and cards kept at an old price
without fresh comps. **A batch is never called READY while anything was
skipped.**

**Market page** (was the Prices tab). Every eBay sale behind a price, in the
window. Type a card or listing title and **Search sold** (Enter); or open a
`priced-<job>.json` (**See sales in Market** on Batch & price, or **More →
Open priced batch…**); or run the golden test from **More**. For the card
selected:

- **Figures** — the suggested price, its basis (the 40th percentile, or the
  lowest of fewer than 4), the middle sale, the cheapest and dearest, how
  many sales counted out of how many found, and the last sale date.
- **Chart** — every sale by date and total: filled dots counted, hollow ones
  were left out, with the suggested price and its basis drawn across. A sale
  far above the rest (a graded slab, say) is pinned to the top with ▲ rather
  than squashing the scale. Hover a dot for the listing; click it to select it.
- **Sold listings** — each with whether it counted and why not (graded,
  another printing, a lot, abroad…). Select one for its details, its
  picture when the evidence carries one, and **Open on eBay ↗**.
- **Leave out** (or Delete) works the price again without that sale — the
  "what if that one was a mistake?" — and **Put back** / **Reset what-if**
  undo it. A what-if changes nothing on any listing.
- **How this price was worked out** — the working, step by step.

**History** keeps every search with the listings it found (`market.sqlite` in
the settings folder), so a card can be looked at again later without
searching or logging in; it is priced again under today's rules when opened.
**eBay ↗** opens eBay's own UK sold search for the title in the browser —
always available, no login. **More → Import sales from CSV…** prices sales
copied out of Terapeak or eBay's sold search (any CSV with a title and price
column; postage, date, URL and condition are used when present) under exactly
the same rules.

**Signing in to Comp Finder.** **More → Comp Finder sign-in…** (or click the
Comp Finder figure in the status bar) takes your Comp Finder email and
password — the same as on its website — and signs in the way its own login
page does. The password is sent once and never saved. The app keeps the
session (`comp-finder-session.json` in the settings folder) and refreshes it
by itself before every Comp Finder call, so it does not run out the way a
pasted cookie does after about an hour. Batch & price and the command line
use the same session. Pasting a cookie from the browser is still there in the
same window, under **Paste a cookie from the browser instead**. If Comp Finder
ever refuses the session, the Market page says so and offers to sign in
again.

### Card lookup

Search the catalogue by name, number and set — or paste a listing title. Pokémon
comes from pokemontcg.io, Yu-Gi-Oh from YGOPRODeck; answers are cached in
`catalog-cache/` in the settings folder for a week, so a repeat lookup works
offline. (pokemontcg.io is sometimes slow or returns errors; search again. A
key in `POKEMONTCG_API_KEY` raises its rate limit.) For each candidate:

- **Printings** the card exists in (Normal, Holo, Reverse Holo, 1st Edition…)
  and **reference prices** (TCGplayer market in USD, Cardmarket trend in EUR —
  a cross-check, not eBay UK prices);
- the **variant check**: a printing named in the search ("Reverse Holo") is
  checked against the printings that exist — *Base Set Pikachu has no Reverse
  Holo* — and a card that also exists in another printing says so, to check
  the foil before listing;
- **Match a scan…** compares a scan with every candidate's catalogue image
  and ranks them by likeness. It tells you **which card** a scan is — not
  which printing, since a scan cannot show foil. The thresholds (80% "likely
  the same card", 60-80% "uncertain") are provisional, tuned on simulated
  scans;
- **Price this on eBay** sends the card to the Market page.

### 6. Push to CardUploader (`--cli changes`, then the extension)

**Write change file** gathers the condition changes (from the grades, after
the listing rules), the prices and the variant switches into
`changes-<job>.json`. Apply it with the Chrome extension in `extension/`: it
shows the diff, applies only on a click, a few cards a second, stops at the
first error, waits for eBay sync on listed cards and re-reads to verify. See
[extension/README.md](extension/README.md).

## From the command line

The same engine, without the window:

```
CardCropper.exe --cli "C:\scans" --out "C:\scans\cropped"
CardCropper.exe --cli "C:\scans" --out "C:\out" --style grading --naming sequence
CardCropper.exe --cli "C:\scans" --out "C:\out" --split combined --front second
```

`--split` is `auto` (the default), `single` or `combined`, and `--front` is
`first` or `second` — the same three settings as the window's **Scans** row.
Repeated runs into one `--out` folder carry on numbering where the last left
off; `--restart` numbers from `0001` and overwrites instead.

Or from a checkout: `python -m cardcropper --cli scans --out out`.

Conditioning:

```
CardCropper.exe --cli condition export.csv --out batch-GA
CardCropper.exe --cli condition export.csv --out batch-GA --limit 10 --upload
CardCropper.exe --cli calibrate --review batch-GA\conditioning-review.csv [--adopt]
CardCropper.exe --cli apply export.csv --review batch-GA\conditioning-review.csv
```

## Building the .exe

The executable cannot be cross-compiled — PyInstaller bundles a platform
bootloader and the host CPython — so it is built on Windows.

**From CI (no Windows machine needed).** Every push runs
`.github/workflows/build-windows.yml`, which runs the smoke test, builds, and
attaches `CardCropper.exe` to the run. Download it from the Actions tab under
the run's **Artifacts**.

**On a Windows machine.**

```
py -3.12 -m pip install -r requirements.txt pyinstaller
py -3.12 -m tests.smoke
pyinstaller packaging/CardCropper.spec --noconfirm
```

`dist\CardCropper.exe` is a single file with Python, Pillow and numpy inside;
nothing needs installing on the machine that runs it. Windows SmartScreen will
warn about an unsigned executable the first time — *More info* → *Run anyway*.

## Running from source

```
pip install -r requirements.txt
python -m cardcropper
```

Needs Tk, which ships with the python.org Windows and macOS builds. On Debian
or Ubuntu: `sudo apt install python3-tk`.

## Testing

```
python -m tests.smoke
node extension/test/push.test.js
```

`tests.smoke` also runs the conditioning, features, catalogue and window
suites. With a display, the window suite builds the whole window and every
page, checks that every tooltip in `cardcropper/guide.py` still finds the
control it was written for (a renamed button would otherwise lose its
explanation silently), that Ctrl+1 changes page without grading a card, and
that grading on the Condition tab writes the review sheet and the library.

One real scan is kept under `tests/fixtures/`, byte for byte, because the
failure it carries cannot be reconstructed: re-encoding it at any quality —
even the same size at quality 95 — loses it, so every synthetic stand-in
written for it passed on the broken code. Its provenance and the reason it must
not be recompressed are in `tests/fixtures/README.md`.

Everything else it generates: scans at known angles — one card to a scan and
two, plain and full-art — runs both sheet styles end to end, and checks the things that fail
silently: that the deskew recovers the angle actually applied even when dark
artwork hides one of the edges it could have measured, that a card filling its
whole scan is left unrotated rather than rotated by a guess, that a scan holding two cards is
divided and one holding a single card is not, that the two halves add back up
to the whole scan so nothing of either card was cut away, that the two faces
written out are actually different rather than one half twice, that an odd scan
is reported rather than absorbed, that the output filenames sort into the order
they were meant to, and that the front leads. It also runs two batches into one
folder and checks they come out as a single continuous run rather than the
second writing over the first.

For splitting a run into stacks: that each stack folder pairs back into the
cards it was cut from, a scan holding both faces moving as its one file, that
the folders are numbered on from the day's, that a clash moves nothing, that a
failure never leaves half a card filed, that cropping the stacks writes each
stack's own cards into its own folder without them being read back as scans,
and that undo puts everything back.

The conditioning pipeline runs on a generated export served from `file://`
URLs: that a worn back ranks above a clean one, that no grade is written with
condition strings nobody has seen, that a creased card graded above HP is held,
that hosted photos are appended rather than prepended, and that a filled-in
review sheet survives a re-run.
