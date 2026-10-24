"""Decide before each turn whether to save things and check recalls, and run it ourselves.

Same reason as the gaming planner before it: with thinking off (needed for speed), small models often skip
tool calls and answer from memory. Recall facts must come from the official lists, so one small Nano call
(thinking off, JSON) reads the message, pulls out anything the user says they own, and the loop runs
recall_check itself.
"""

from __future__ import annotations

from typing import Any, Callable

from app.agent.lookup import Plan
from app.llm.client import LLMError
from app.llm.parsing import extract_json
from app.llm.router import ModelRouter, Tier

PROMPT = """You route messages for Lantern, an assistant that watches official U.S. safety recalls for the things
a person owns (cars, car parts, and household products such as cribs, strollers, heaters, appliances, toys).

Decide:
1. "add": things the person says THEY own or use in their latest message. A vehicle needs a make, model and
   year, or a VIN (17 characters). A product needs at least the kind of product (e.g. "crib"); add the brand and
   model if given. Don't add things they're only asking about, considering buying, or owned in the past.
2. "check": true if they add something, or ask anything about recalls, safety, defects, whether something of
   theirs is safe, or what needs attention. False for greetings, thanks, small talk, or unrelated questions.
3. "location": a city or ZIP code if they say where they live, else empty.

Reply with only JSON:
{"add": [{"type": "vehicle", "make": "", "model": "", "year": 2018, "vin": ""},
         {"type": "product", "brand": "", "product": "", "model": ""}],
 "check": true or false, "location": ""}"""

NO_CHECK_NOTE = ("(Note from the app, not the user: no recall data was looked up for this message. Don't state "
                 "recall or safety facts from memory. If they're just chatting or thanking you, reply briefly and "
                 "naturally; only offer a recall check if their message is about safety or their things.)")


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
    def __init__(self, router: ModelRouter, context: Callable[[str], str] | None = None):
        self.router = router
        self.context = context

    def __call__(self, user_id: str, message: str, history: list[dict[str, Any]]) -> Plan:
        recent = "\n".join(f"{m['role']}: {str(m['content'])[:300]}" for m in history[-4:])
        things = self.context(user_id) if self.context else "unknown"
        user = (f"What they own (from their files):\n{things}\n\nRecent conversation:\n{recent or '(none)'}\n\n"
                f"Latest message:\n{message[:2000]}")
        try:
            r = self.router.complete([{"role": "system", "content": PROMPT}, {"role": "user", "content": user}],
                                     Tier.FAST, purpose="agent:route", thinking=False, temperature=0, max_tokens=300)
            data = extract_json(r.text)
        except (LLMError, ValueError):
            return Plan(note=NO_CHECK_NOTE)
        if not isinstance(data, dict):
            return Plan(note=NO_CHECK_NOTE)
        add = [c for c in (_clean_thing(t) for t in (data.get("add") or [])[:5]) if c]
        location = str(data.get("location") or "").strip()
        if not (add or data.get("check")):
            return Plan(note=NO_CHECK_NOTE)
        args: dict[str, Any] = {}
        if add:
            args["add"] = add
        if location:
            args["location"] = location
        return Plan(tool="recall_check", args=args)
