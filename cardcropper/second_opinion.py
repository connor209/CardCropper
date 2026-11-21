"""
The grading screen's optional "second opinion": ONE card's crops sent to the
Claude API in ONE call. Never a whole batch — the point of the batch tools is
that AI is only used where a rule cannot decide.

The answer is advice. It is recorded with source "ai" and never counts as a
pass unless the grader accepts it (source "ai-accepted"), and the house rules
in `grading.enforce` still apply to whatever is accepted.

Needs the `anthropic` package (`pip install anthropic`) and credentials the
SDK can find — ANTHROPIC_API_KEY, or an `ant auth login` profile. It is
imported only when the button is pressed, so the app runs without it.
"""

import base64
import io
import json
import re

from PIL import Image

MODEL = "claude-opus-5-5"
#: The API's image limit is generous, but a crop sheet larger than this is
#: downscaled by the API anyway; sending it at this size keeps it native.
MAX_SIDE = 1568

PROMPT = """You are grading ONE trading card for an eBay UK listing.
Images: the {faces} corner-and-edge crop sheets of the same card.
Game: {game}. Scale: NM, LP, MP, HP, DMG (or HOLD if the scans cannot be
graded: a second card in frame, scanner bed showing, front and back from
different eras).

House rules:
- A countable wear point is a distinct nick, chip, whitening patch or scratch.
  NM needs fewer than 3 across both faces.
- Whitened tips on 3 or 4 corners of either face: LP at best.
- Any crease is never MP: HP if short, from an edge only, ink intact;
  otherwise DMG.
- NOT wear: off-centring, scanner streaks, holo moire, a uniform unbroken
  scanner hairline along an edge, the printed dot near the top-left inner
  corner of MTG backs, dust.
Pre-counted bright points per panel (triage only, may be wrong): {counts}

Reply with JSON only:
{{"grade": "NM|LP|MP|HP|DMG|HOLD", "wear_points": <int>,
  "crease": "none|short-edge-ink-intact|other",
  "evidence": "<one line: which panel, what you saw>"}}"""


def _jpeg_b64(path):
    with Image.open(path) as im:
        im = im.convert("RGB")
        if max(im.size) > MAX_SIDE:
            im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=92)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def ask(crop_paths, game="pokemon", counts="", client=None):
    """
    {grade, wear_points, crease, evidence, model} for one card, or raises.
    `crop_paths` is {"front": path, "back": path} (either may be missing).
    """
    import anthropic

    client = client or anthropic.Anthropic()
    content = []
    faces = []
    for face in ("front", "back"):
        path = crop_paths.get(face)
        if path:
            faces.append(face)
            content.append({"type": "text", "text": f"{face.upper()} crops:"})
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg", "data": _jpeg_b64(path)}})
    if not faces:
        raise ValueError("no crops to send")
    content.append({"type": "text", "text": PROMPT.format(
        faces=" and ".join(faces), game=game, counts=counts or "none")})

    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        output_config={"effort": "high"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": content}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("the model declined to grade this card")
    text = "".join(b.text for b in response.content if b.type == "text")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError(f"no JSON in the answer: {text[:200]}")
    out = json.loads(m.group(0))
    out["grade"] = str(out.get("grade", "")).upper()
    out["model"] = response.model
    return out
