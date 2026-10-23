"""Decide before each turn whether the player's message needs a game lookup, and run it ourselves.

Why: with thinking off (needed for speed), Nano often answered game questions from its own memory
instead of calling ask_game, and invented facts (live: a "Griffin mutator" in Witcher 3, a
"Monarch Wings tutorial" in Hollow Knight). So the decision no longer rests with the chat model:
one small Nano call (thinking off, JSON) classifies the message, and the loop runs ask_game itself.
When no lookup is needed, the model is told so next to the message, where small models pay attention.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.llm.client import LLMError
from app.llm.parsing import extract_json
from app.llm.router import ModelRouter, Tier

PROMPT = """You route messages for a gaming assistant. Decide if answering the player's latest message needs facts
about a specific game (mechanics, items, builds, quests, bosses, locations, where to farm, what to do next,
shops or traders, patch changes, tips, whether something is worth doing).

No lookup for: greetings, thanks, small talk, the player just telling you what they play or where they are
(without asking anything), questions about their own profile or library, or general non-game questions.

Use the conversation and the player's games to work out which game they mean (e.g. a trader or character
from one of their games). If unsure which game, use null.

Reply with only JSON:
{"lookup": true or false, "game": "game name or null", "question": "a short specific search, including where
they are when it matters", "alternative": "another phrasing if the question could mean two things, else empty"}"""

NO_LOOKUP_NOTE = ("(Note from the app, not the player: no game facts were looked up for this message. Don't state "
                  "game facts or tips from memory; if they'd help, offer to look them up.)")


@dataclass
class Plan:
    tool: str | None = None              # a tool the loop should run before the model answers
    args: dict[str, Any] | None = None
    note: str | None = None              # appended to the player's message for the model only


class LookupPlanner:
    def __init__(self, router: ModelRouter, profile_context: Callable[[str], str] | None = None):
        self.router = router
        self.profile_context = profile_context

    def __call__(self, user_id: str, message: str, history: list[dict[str, Any]]) -> Plan:
        recent = "\n".join(f"{m['role']}: {str(m['content'])[:300]}" for m in history[-4:])
        games = self.profile_context(user_id) if self.profile_context else "unknown"
        user = f"Player's games:\n{games}\n\nRecent conversation:\n{recent or '(none)'}\n\nLatest message:\n{message[:2000]}"
        try:
            r = self.router.complete([{"role": "system", "content": PROMPT}, {"role": "user", "content": user}],
                                     Tier.FAST, purpose="agent:route", thinking=False, temperature=0, max_tokens=200)
            data = extract_json(r.text)
        except (LLMError, ValueError):
            return Plan(note=NO_LOOKUP_NOTE)  # can't tell: be safe, don't let the model improvise facts
        if not isinstance(data, dict) or not data.get("lookup"):
            return Plan(note=NO_LOOKUP_NOTE)
        game, question = str(data.get("game") or "").strip(), str(data.get("question") or "").strip()
        if not game or game.lower() == "null" or not question:
            return Plan(note=("(Note from the app, not the player: this needs game facts but it's unclear which "
                              "game. Ask which game they mean; don't state game facts from memory.)"))
        args = {"game": game, "question": question}
        if str(data.get("alternative") or "").strip():
            args["alternative"] = str(data["alternative"]).strip()
        return Plan(tool="ask_game", args=args)
