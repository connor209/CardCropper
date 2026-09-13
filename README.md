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
4. **Cuts the corners and edges**, keeping a margin of background so the card's
   outline shows. A corner that has been rounded off is read from its profile
   as much as from whitening on its face.
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

### Scans that hold both faces

The **Scans** setting decides how a folder is read, and it defaults to
detecting it per file, because getting this wrong is silent — nothing about a
combined scan run as a single card looks wrong until you open the crops.

| Setting | When |
|---|---|
| **Detect** | The default, and right for a mixed folder. Each file is examined once as it is added; the answer is remembered, so changing the other settings is instant. |
| **One face per file** | A folder you already know is front, back, front, back. Nothing is examined, so adding a folder of several hundred from a cloud-synced drive is immediate. A scan that looks like it holds two cards is still called out in the log. |
| **Both faces on every scan** | Every file holds a pair — including cards laid *touching*, where there is no strip of bed to find and the app will not divide on a guess of its own. Here it divides down the middle of what it found, which is the seam whenever both halves are the same card, and checks that two card-shaped halves came out of it. It only says anything if they did not. |

**Front is the left / top one** says which half of a combined scan leads. The
app does not try to work this out from the pixels: the honest signal — that
every back in a batch looks like every other back — needs the whole batch
before it can answer for one card, and the quick ones (a back is darker, a back
is symmetrical) are wrong on enough card games to put the wrong face in the
gallery thumbnail without telling you. The scanner lays them down the same way
every time, so it is one setting for the batch, and **Swap front/back** for the
row that is not.

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

It generates scans at known angles — one card to a scan and two, plain and
full-art — runs both sheet styles end to end, and checks the things that fail
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
