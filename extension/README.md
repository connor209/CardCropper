# CardCropper for CardUploader (Chrome extension)

Pushes CardCropper's results (prices, conditions, variant switches) into your
own CardUploader batches, from your own logged-in tab, after you have seen a
diff and clicked **Apply**.

**Unofficial.** CardUploader has no public API or bulk import. This uses the
JSON backend the CardUploader web app itself calls (read from the app's code,
21 Nov 2026). It can change without notice, so check CardUploader's terms
before relying on it. Every call is made with your session, from the
CardUploader page, exactly as the batch page would make it.

## Install

1. Chrome → `chrome://extensions` → turn on **Developer mode**.
2. **Load unpacked** → pick this `extension` folder.
3. Open a batch on carduploader.com and click the CardCropper toolbar button.

## Use

- **Save job data** writes `job-<batch>.json` (`GET /jobs/{id}/data`). CardCropper's
  Variants tab, `--cli price` and `--cli changes` read it.
- **Save batch history** writes `carduploader-history.json` (`GET /jobs/history`).
  `--cli batch --history` reads it so a batch code is never reused.
- **Apply a change file** (`changes-<batch>.json` from CardCropper):
  1. The job is read fresh, and each change is checked against it. A change
     whose old value no longer matches CardUploader, an unknown SKU, a
     Yu-Gi-Oh price under £1.99, or a title the variant words cannot be found
     in is **refused** and listed — never sent.
  2. The diff is shown: SKU, field, old → new, including the rebuilt title.
     **Listed** cards are shown apart: edits to them push to eBay, and a
     variant switch ends, creates or merges eBay listings.
  3. **Apply** sends `POST /inventory/batch` three cards at a time, about one
     request a second, `_delta: true` (only the fields sent change), and
     **stops at the first error**.
  4. For listed cards it waits on `POST /automatic-inventory/row-sync-status`
     for "Synced" (about 30-60 s), then re-reads the job and reports every
     field that does not read back as sent.

Only the SKUs in the change file are touched. A condition or variant change
carries the TCGplayer SKU for the new variant × condition (from the card's
`skus[]`) and the current title with only the variant words and/or the
trailing condition code swapped — titles are never rebuilt from scratch.

## Testing a change to the payloads

`node extension/test/push.test.js` checks the request bodies against the ones
captured from the batch page. To capture a new one without touching the
server: in a logged-in CardUploader tab, wrap `window.fetch` so non-GET
requests are logged and answered with a fake error, make the edit in the UI,
read the log, then close the tab and choose "Leave".
