"""Reading the text in a photo the user takes (a product label, a receipt) with Nemotron Super VL on Token Factory.

The photo arrives as a data URL, already shrunk in the browser. It is checked (type and size), sent once with thinking
off (thinking on used all its tokens on a test label and returned nothing), and never stored or cached.
"""

from __future__ import annotations

import base64
import binascii
import re

from app.llm.client import LLMError
from app.llm.router import ModelRouter, Tier

MAX_PHOTO_BYTES = 6 * 1024 * 1024
PHOTO_TYPES = ("image/jpeg", "image/png", "image/webp")
_DATA_URL = re.compile(r"^data:(image/[a-z]+);base64,([A-Za-z0-9+/=\s]+)$")


class PhotoError(RuntimeError):
    """The photo couldn't be read. The message is safe to show to the user."""


def read_photo_text(router: ModelRouter, data_url: str, prompt: str, purpose: str, max_tokens: int = 500) -> str:
    m = _DATA_URL.match(data_url or "")
    if not m or m.group(1) not in PHOTO_TYPES:
        raise PhotoError("Please send a JPEG, PNG or WebP photo.")
    try:
        size = len(base64.b64decode(m.group(2), validate=False))
    except (binascii.Error, ValueError):
        raise PhotoError("That photo couldn't be read. Try taking it again.") from None
    if size > MAX_PHOTO_BYTES:
        raise PhotoError("That photo is too large. Try again (Lantern shrinks photos before sending).")
    try:
        r = router.complete(
            [{"role": "user", "content": [{"type": "text", "text": prompt},
                                          {"type": "image_url", "image_url": {"url": data_url}}]}],
            Tier.VISION, purpose=purpose, thinking=False, temperature=0, max_tokens=max_tokens, use_cache=False)
    except LLMError as e:
        raise PhotoError("Couldn't read the photo just now. Try again, or type what it says.") from e
    text = r.text.strip()
    if not text or text.upper().startswith("NO TEXT"):
        raise PhotoError("No writing could be read in that photo. Get closer, in good light, or type what it says.")
    return text
