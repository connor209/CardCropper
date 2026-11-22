# Testing phase — checklist

Work through this on real batches, in roughly the order a day runs. Tick what
works; for anything that does not, note **what you did, what you expected,
what happened**, and send a screenshot. If the window freezes, also send the
newest file from **Help → Freeze reports…** — it names the exact line.

Before starting: update to the latest build (status bar → Check for updates),
and reload the Chrome extension (`chrome://extensions` → reload).

## 0. Start-up and the window
- [ ] The app opens on the page you used last; Home shows **Today**.
- [ ] Hover a few controls on different pages — every one explains itself.
- [ ] F1 opens/closes a page's guide; Ctrl+1…9 move between pages.
- [ ] Status bar: build, graded count, Comp Finder login all look right.
- [ ] Leave it open 10+ minutes on Home, Market and Batches — no freeze.

## 1. Scan
- [ ] **Scan folders**: make tomorrow's folders; numbering carries on.
- [ ] **Split into stacks** on a real long run: breaks, move a break, file, Undo.
- [ ] **Crop cards** on a real folder: pairing, a swap, crop, outputs look right.

## 2. Batches page
- [ ] Set the Batches folder to where your batches really live.
- [ ] Every batch you expect is listed; the stage columns match reality.
- [ ] **Go to: next step** opens the right page with the right file loaded.
- [ ] Refresh on a big folder is quick (seconds, not a hang).

## 3. CardUploader jobs
- [ ] Extension panel shows **Copy token for CardCropper**; it copies.
- [ ] Tools → CardUploader jobs → paste → **List my jobs** shows your jobs.
- [ ] **Fetch job data** saves `job-<batch>.json` beside the right manifest.
- [ ] Save job data from the extension, then **Collect from Downloads** files it.
- [ ] An expired token gives a clear message, not a crash.
- [ ] **CardUploader page** (List & price): every fetched job is in the Job
      list; the table matches what CardUploader shows; grade, house price
      and Δ are filled where you have them; the filters and Find work.
- [ ] Pick a card: your scan and the catalogue picture load; Sold prices /
      Card lookup / eBay ↗ go to the right card.
- [ ] Open a batch from a different place (e.g. copy a batch folder
      elsewhere): its scans still show.

## 4. Condition a CSV
- [ ] Prepare a real export; ranking looks sensible (worst first).
- [ ] Grade in the side panel with keys 1-6 and Enter; the review sheet updates.
- [ ] Ctrl+Z undoes the last save and goes back to the card.
- [ ] A note mentioning a crease on an LP is refused, with the reason.
- [ ] **Open prepared batch…** / Recent reopen an earlier batch.
- [ ] **Check against catalogue…**: do the MISMATCH cards really not match? Are
      any real mismatches missed? (This tunes the image thresholds.)

## 5. Grade tab and the Grade library
- [ ] Grade a manifest batch; each grade appears in the Grade library with
      its images.
- [ ] Ctrl+Z on the Grade tab takes back the last grade.
- [ ] Library advice ("graded cards with these counts…") appears after a few
      dozen grades and seems sensible.
- [ ] Promote a few cards to the golden set; `golden/pokemon-en.csv` updated.
- [ ] Once 10+ golden (3 borderline): Blind checks → Start, grade, Score.

## 6. Variants and Card lookup
- [ ] Variants on a real job: catalogue images load, decisions save.
- [ ] Card lookup: English (with and without set), Japanese (`sv4a 056`,
      `… Japanese`), Yu-Gi-Oh (`LOB-EN005`).
- [ ] When pokemontcg.io errors, results still come (from TCGdex).
- [ ] Cardmarket line: does **exact** match what Cardmarket's site shows for
      that print? Check 5 cards, including a 1st Edition / reverse / alt art.

## 7. Market
- [ ] Search sold for 5 cards you know well; do the suggested prices agree
      with your judgement?
- [ ] Language: English only vs All languages on a Japanese card.
- [ ] Leave out / Count it anyway / Reset what-if behave.
- [ ] Use this price… then Batch & price uses it ("your price").
- [ ] ☆ Watch two cards; Check prices now; change shows.
- [ ] Pictures: do any listing photos appear? (Expected: often not — eBay
      blocks; tell me if never.)
- [ ] Several searches in a row, then idle — no freeze.

## 8. Batch & price → Terapeak → change file
- [ ] Price & report a real job (try Count every language once).
- [ ] **See sales in Market** opens it card by card.
- [ ] Terapeak checks lists the job's checks; Open Terapeak is already searched.
- [ ] Save prices / Keep current; the CSV is right; **Report again** runs.
- [ ] Write change file; apply with the extension on a test batch; diff is right.

## 9. Housekeeping
- [ ] File → Back up settings… makes a zip; `backups/` gets a weekly one.
- [ ] Today list is accurate after a day's work.

## Known gaps (not bugs)
- Japanese sets TCGdex lists without cards (e.g. Eevee Heroes) can't be found.
- Yu-Gi-Oh prints sharing a name show a Cardmarket price **range**.
- Image-matching thresholds are tuned on simulated scans only.
- Listing photos depend on eBay letting the app see the page.
