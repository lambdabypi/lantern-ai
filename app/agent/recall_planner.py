"""Decide before each turn whether to save things and check recalls, and run it ourselves.

Why: with thinking off (needed for speed), small models often skip
tool calls and answer from memory. Recall facts must come from the official lists, so one small Nano call
(thinking off, JSON) reads the message, pulls out anything the user says they own, and the loop runs
recall_check itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from app.llm.client import LLMError
from app.llm.parsing import extract_json
from app.llm.router import ModelRouter, Tier

PROMPT = """You route messages for Lantern, an assistant that watches official U.S. safety recalls for the things
a person owns (cars, car parts, and household products such as cribs, strollers, heaters, appliances, toys).

Decide:
1. "add": things the person says THEY own or use in their latest message. A vehicle needs a make, model and
   year, or a VIN (17 characters). A product needs at least the kind of product (e.g. "crib"); add the brand and
   model if given. Don't add things they're only asking about, considering buying, or owned in the past, and
   never add something already in their files.
2. "check": true if they add something, or ask anything about recalls, safety, defects, whether something of
   theirs is safe, or what needs attention. False for greetings, thanks, small talk, or unrelated questions.
3. "location": a city or ZIP code if they say where they live, else empty.
4. "ask": if they ask how to do something with one of THEIR things, or about its parts, manual, settings, where
   its VIN or model number is, its warranty, or how to get a recall remedy done (things the recall lists don't
   answer), give {"thing": "its name as in their files", "question": "the question, written to stand alone"}.
   Otherwise null. When "ask" is set, "check" is false unless they also ask whether something is recalled or safe.

Reply with only JSON:
{"add": [{"type": "vehicle", "make": "", "model": "", "year": 2018, "vin": ""},
         {"type": "product", "brand": "", "product": "", "model": ""}],
 "check": true or false, "location": "", "ask": null}"""

NO_CHECK_NOTE = ("(Note from the app, not the user: no recall data was looked up for this message. Don't state "
                 "recall or safety facts from memory. If they're just chatting or thanking you, reply briefly and "
                 "naturally; only offer a recall check if their message is about safety or their things.)")


_QUESTION = re.compile(r"\?\s*$|^\s*(how|where|what|when|which|can|could|should|is|are|does|do)\b", re.I)


@dataclass
class Plan:
    tool: str | None = None              # a tool the loop should run before the model answers
    args: dict[str, Any] | None = None
    note: str | None = None              # appended to the user's message for the model only


def _clean_thing(t: Any) -> dict[str, Any] | None:
    if not isinstance(t, dict) or t.get("type") not in ("vehicle", "product"):
        return None
    keep = {k: v for k, v in t.items() if k in ("type", "make", "model", "year", "vin", "brand", "product")
            and v not in (None, "", 0)}
    if keep["type"] == "vehicle" and not (keep.get("vin") or (keep.get("make") and keep.get("model") and keep.get("year"))):
        return None
    if keep["type"] == "product" and not keep.get("product"):
        return None
    return keep


class RecallPlanner:
    def __init__(self, router: ModelRouter, context: Callable[[str], str] | None = None,
                 match: Callable[[str, str], str | None] | None = None):
        self.router = router
        self.context = context
        # match(user_id, text) -> the name of the thing on their list the text is about, or None. A safety net:
        # with earlier turns in the conversation, Nano sometimes leaves "ask" empty for a plain how-to question.
        self.match = match

    def _read(self, user: str, purpose: str) -> dict[str, Any] | None:
        try:
            r = self.router.complete([{"role": "system", "content": PROMPT}, {"role": "user", "content": user}],
                                     Tier.FAST, purpose=purpose, thinking=False, temperature=0, max_tokens=400)
            data = extract_json(r.text)
        except (LLMError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def owned(self, text: str) -> list[dict[str, Any]] | None:
        """The things named in the "what do you own?" box (typed or spoken). None if the model call failed."""
        data = self._read("What they own (from their files):\n(nothing yet)\n\nRecent conversation:\n(none)\n\n"
                          "Latest message (typed or spoken into the app's \"What do you own?\" box, so everything "
                          f"named in it is something they own):\n{text[:2000]}", purpose="things:read")
        if data is None:
            return None
        return [c for c in (_clean_thing(t) for t in (data.get("add") or [])[:5]) if c]

    def __call__(self, user_id: str, message: str, history: list[dict[str, Any]]) -> Plan:
        recent = "\n".join(f"{m['role']}: {str(m['content'])[:300]}" for m in history[-4:])
        things = self.context(user_id) if self.context else "unknown"
        user = (f"What they own (from their files):\n{things}\n\nRecent conversation:\n{recent or '(none)'}\n\n"
                f"Latest message:\n{message[:2000]}")
        data = self._read(user, purpose="agent:route")
        if data is None:
            return Plan(note=NO_CHECK_NOTE)
        add = [c for c in (_clean_thing(t) for t in (data.get("add") or [])[:5]) if c]
        location = str(data.get("location") or "").strip()
        ask = data.get("ask") if isinstance(data.get("ask"), dict) else {}
        thing, question = str(ask.get("thing") or "").strip()[:80], str(ask.get("question") or "").strip()[:300]
        if thing and question:  # the maker's own pages answer this (see app/maker_pages.py)
            return Plan(tool="ask_maker", args={"thing": thing, "question": question})
        if not (add or data.get("check")):
            thing = self.match(user_id, message) if self.match and _QUESTION.search(message) else None
            if thing:
                return Plan(tool="ask_maker", args={"thing": thing, "question": message.strip()[:300]})
            return Plan(note=NO_CHECK_NOTE)
        args: dict[str, Any] = {}
        if add:
            args["add"] = add
        if location:
            args["location"] = location
        return Plan(tool="recall_check", args=args)
