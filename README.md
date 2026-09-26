# CardCropper

A Windows app that takes a folder of card scans, works out which faces belong
to which card, and writes two extra photos per card — the corners and edges of
the front, and the corners and edges of the back — then leaves the folder
numbered so every card's four images sit together in order.

A card can arrive as two scans, front and back, or as **one scan holding both
faces side by side**, which is what a flatbed gives you when you lay the card
down, flip it, and scan once. Either way a card comes out as four images.

It is the pixel half of the `card-conditioning` skill, taken out of the skill
and put behind a window. Nothing here grades a card or calls a model: it
straightens, crops and files. The judgement stays with you.

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

## Using it

Double-click `CardCropper.bat` (or `CardCropper.exe`).

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
3. **Pick an output folder.** It must not be a folder the scans came from — the
   crops would be read back as scans on the next run and paired into the batch.
   The app refuses that rather than letting it happen quietly.
4. **Crop cards.**

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
```

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
