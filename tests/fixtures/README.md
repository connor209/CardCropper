# Fixtures

Real scans, kept because the failures they carry could not be reconstructed.

## `flush-pair.jpg`

A document scanner's duplex output: both faces of one card side by side,
1486×1037 at 300dpi — 125.8 × 87.8mm for two 63mm cards, so the pair fills the
sheet and every face is clipped on all four sides. There is no scanner bed
anywhere in it, and the cards touch, so there is no gap to divide on either.

It is here because it broke two things at once and both showed up as hard
straight lines through the corner crops:

- Three sides of each half are the scan's own boundary and the fourth is the
  seam against the next card. One edge slipped the boundary test, reported
  0.23° on its own, and the card was rotated by it — bringing in a wedge of
  fill that landed in every crop taken afterwards.
- The margin of background kept around the card was cropped past the edge of
  the image and padded, which on a dark-bedded scan looks exactly like bed. A
  card cropped flush came out with a tidy dark margin and a corner that
  appeared photographed against a clean background it never touched.

**Do not re-encode it.** This was measured: saving it again at *any* quality,
even at the same dimensions and quality 95, loses the first of those two —
every re-encoded copy reports zero measurable edges where the original reports
one. The fault lives in the exact coefficients, so a helpful recompression to
save a few hundred kilobytes would quietly turn this file into a test that
passes on the broken code.

## `noisy-edge-front.jpg`, `noisy-edge-back.jpg`

Both faces of one card (Mega Feraligatr ex), 863×1152 on a backing that reads
exactly 0. The front's scan has a strip of sensor noise down its left edge —
fifteen columns of faint 1s to 8s the full height of the scan, then a clean
band of black, then the card. The back's scan has none.

With a backing of 0 the bar is 0, so the strip is certainly card, and taking
the card from the first certainly-card column to the last ran the front's box
out across the black band to the edge of the scan: 805 wide against a true
746. The back measured right at 745 — and was then grown to the front's width
because two faces of one card are the same size, so BOTH faces' crops sat out
in the backing, the left and right edge strips entirely black.

The same noisy strip sat the front's located box in a faint glow beside the
card, and the check that warns an outline cannot be seen against the backing
read that glow as the card's edge and warned about a plainly visible border.
