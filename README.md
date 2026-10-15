# CardCropper

A Windows app that takes a folder of card scans, pairs them into cards, and
writes two extra photos per card — the corners and edges of the front, and the
corners and edges of the back — then leaves the folder numbered so every card's
four images sit together in order.

It also runs the `card-conditioning` workflow on a CardUploader export: it
downloads every card's scans, ranks the batch by back-border wear so that as
few cards as possible need looking at, and writes your grades back into a copy
of the CSV. Nothing here grades a card or calls a model: it straightens, crops,
ranks and files. The judgement stays with you.

## What it does to a scan

1. **Deskews it.** Cards sit half a degree off in a sheet feeder. Crop the
   bounding box of a tilted card and take its corners and you do not get the
   card's corners — you get interior artwork, and it looks plausible enough to
   list from. Every crop is cut after a deskew.
2. **Finds the card on the bed** on the value channel — `max(R,G,B)` — rather
   than on brightness. A Pokémon back's navy border is as dark as the scanner
   bed by luminance, so a brightness mask finds the card's bright *interior*
   and reports the artwork as the border.
3. **Cuts the corners and edges**, keeping a margin of background so the card's
   outline shows. A corner that has been rounded off is read from its profile
   as much as from whitening on its face.
4. **Sharpens lightly, and never enhances contrast.** Auto-contrast on a navy
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

1. **Add scans…** or **Add folder…**. Files pair in filename order: the first
   is a front, the second is its back, and so on. `2.jpg` sorts before
   `10.jpg`, so a scanner that changes its zero-padding mid-batch does not
   scramble the pairs.
2. **Check the table.** Each row is one card. Where a pair is the wrong way
   round, select it and press **Swap front/back**; where the run is off by one,
   remove the offender. An odd file left over is called out in red rather than
   folded into a card.
3. **Pick an output folder.** It must not be a folder the scans came from — the
   crops would be read back as scans on the next run and paired into the batch.
   The app refuses that rather than letting it happen quietly.
4. **Crop cards.**

### Options

| Option | What it changes |
|---|---|
| **Crops** | `Clean listing photo` has no captions and frames the corners wider — a buyer has not been told what to look for, and at grading magnification paper fibre reads as damage. `Grading sheet` is the skill's labelled sheet, for your own eyes. |
| **Naming** | `Grouped` writes `0001_1_front.jpg`, `0001_2_back.jpg`, … — readable, and a wrong pair is obvious. `Continuous` renumbers the batch as one run, `0001.jpg` through `0008.jpg` for two cards. Both sort into the same sequence. |
| **Order** | Where the crops sit. Either way the **front leads** — the first image is the eBay gallery thumbnail, and that has to be the card, not a magnified corner. |
| **Copy originals** | On by default, so the output folder is the complete set of four images per card. Off writes only the two crops. |

The original scans are copied, never moved. If a pairing turns out to be off by
one, the fix is to pair again — only possible while the scans are still where
the scanner left them.

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

2. **Grade in `conditioning-review.csv`** against the house guide: `grade`
   (NM / LP / MP / HP / DMG or `HOLD`), a one-line `flaw_note`, `confidence`,
   the `evidence` you saw, and `looked` = `yes` if you opened a crop. **The
   score is not a grade and is back-only** — a card can score clean and still
   be LP. A re-run never overwrites a review sheet that has grades in it.

3. **Record grades & re-fit triage** (`--cli calibrate`). The `looked = yes`
   grades go into a calibration ledger with the scores they got, and new
   thresholds are proposed from everything graded so far. Grades taken off the
   score are left out: they would only confirm the thresholds already in use.
   A proposal is only used once you adopt it.

4. **Write grades to a copy of the CSV** (`--cli apply`). Writes
   `<stem>-conditioned.csv` with the three condition columns set, the grade at
   the end of the title updated, and the flaw note appended to the
   description. Pokémon NM is listed as LP (house rule); Yu-Gi-Oh NM stays NM.
   Cards that are held, ungraded, or need a condition whose strings are
   unknown go to `<stem>-held.csv` with the reason. The original export is
   never modified.

### Unlocking LP / MP / HP / DMG

Only the NM condition strings have been seen in a real export. A guessed value
does not fail loudly — it rejects the batch or lists every card at the wrong
condition — so nothing else is built in, and until it is filled in **every
Pokémon card is held** (graded NM lists as LP). To unlock a grade, condition
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

## From the command line

The same engine, without the window:

```
CardCropper.exe --cli "C:\scans" --out "C:\scans\cropped"
CardCropper.exe --cli "C:\scans" --out "C:\out" --style grading --naming sequence
```

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
```

It generates scans at known angles, runs both sheet styles end to end, and
checks the things that fail silently: that the deskew recovers the angle
actually applied, that an odd scan is reported rather than absorbed, that the
output filenames sort into the order they were meant to, and that the front
leads. It then runs the conditioning pipeline on a generated export served from
`file://` URLs: that a worn back ranks above a clean one, that no grade is
written with condition strings nobody has seen, that hosted photos are
appended rather than prepended, and that a filled-in review sheet survives a
re-run.
