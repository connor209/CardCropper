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
