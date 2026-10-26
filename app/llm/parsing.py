"""Pull a JSON value out of model output that may include reasoning or code fences."""

from __future__ import annotations

import ast
import json
import re
from typing import Any

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_JSON_WORDS = {"true": "True", "false": "False", "null": "None"}


def extract_json(text: str) -> Any:
    """Return the first JSON object or array in `text`.

    Handles <think>...</think> blocks, ```json fences and surrounding prose.
    Raises ValueError if no valid JSON is found.
    """
    cleaned = _THINK.sub("", text)
    # Some templates drop the opening tag: keep only what follows the last </think>.
    if "</think>" in cleaned.lower():
        cleaned = cleaned[cleaned.lower().rindex("</think>") + len("</think>") :]
    # An unterminated <think> means the model never finished reasoning.
    if "<think>" in cleaned.lower():
        cleaned = cleaned[: cleaned.lower().index("<think>")]

    decoder = json.JSONDecoder()
    for i, ch in enumerate(cleaned):
        if ch in "{[":
            try:
                value, _ = decoder.raw_decode(cleaned, i)
                return value
            except json.JSONDecodeError:
                continue
    # Models sometimes write single-quoted strings when the text itself holds double quotes ('"voomf" printed on
    # a label'). That's a Python literal, not JSON: literal_eval reads literals only and never runs code.
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if 0 <= start < end:
        source = re.sub(r"\b(true|false|null)\b", lambda m: _JSON_WORDS[m.group(1)], cleaned[start:end + 1])
        try:
            value = ast.literal_eval(source)
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            value = None
        if isinstance(value, dict):
            return value
    raise ValueError("No JSON found in model output.")
