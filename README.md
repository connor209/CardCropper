# CardCropper

A Windows app that takes a folder of card scans, pairs them into cards, and
writes two extra photos per card — the corners and edges of the front, and the
corners and edges of the back — then leaves the folder numbered so every card's
four images sit together in order.

It is the pixel half of the `card-conditioning` skill, taken out of the skill
and put behind a window. Nothing here grades a card or calls a model: it
straightens, crops and files. The judgement stays with you.

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

## Using it

Double-click `CardCropper.exe`.

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

## From the command line

The same engine, without the window:

```
CardCropper.exe --cli "C:\scans" --out "C:\scans\cropped"
CardCropper.exe --cli "C:\scans" --out "C:\out" --style grading --naming sequence
```

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

It generates scans at known angles, runs both sheet styles end to end, and
checks the things that fail silently: that the deskew recovers the angle
actually applied, that an odd scan is reported rather than absorbed, that the
output filenames sort into the order they were meant to, and that the front
leads.
