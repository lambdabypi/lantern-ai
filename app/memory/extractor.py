"""Memory extractor: after each reply, save the games, progress, platforms and creators the player mentioned.

Runs separately from the chat turn (in the background, after the reply is sent) because the chat
model was unreliable at remembering to call save_game: live, it skipped saves in about 1 turn in 3
and, with thinking off, even claimed "I've recorded that" without saving. One small Nano call with
thinking off, JSON out; every write goes through MemoryStore's validation and shows on My games.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from app.llm.client import LLMError
from app.llm.parsing import extract_json
from app.llm.router import ModelRouter, Tier
from app.memory.store import GAME_STATUSES, MemoryStore, MemoryStoreError, slugify

log = logging.getLogger(__name__)

PROMPT = """Extract durable facts about the player's gaming from their message. Only things they clearly state about
THEMSELVES: games they play or played and how far they've got, the platform they play on, streamers or creators
they follow. Ignore questions about games they don't say they play, hypotheticals, and anything not about gaming.
Never include personal details (names, places, contact info).

Reply with only JSON, no other text:
{"games": [{"title": "...", "status": "playing|backlog|finished|dropped or empty", "progress": "where they are, or empty",
            "platform": "or empty"}],
 "creators": [{"name": "...", "platforms": ["twitch", "youtube", ...]}],
 "platforms": ["PC", "PS5", ...]}
Use empty lists when there's nothing. Keep progress short (e.g. "Saturn", "beat Margit", "White Orchard").
Only fill a field when the player says it in THIS message: never guess a platform or status. If they don't name
a platform, leave platform empty and don't add it to "platforms"."""

MAX_ITEMS = 5


class MemoryExtractor:
    def __init__(self, router: ModelRouter, store: MemoryStore,
                 known_sources: Callable[[str], list[str]] | None = None):
        self.router = router
        self.store = store
        self.known_sources = known_sources

    def run(self, user_id: str, message: str) -> list[str]:
        """Extract and save; returns what was saved (e.g. ['game:warframe']). Never raises: it runs in the
        background after the reply, so failures are logged, and the player can always add things by hand."""
        try:
            return self._run(user_id, message)
        except Exception:  # noqa: BLE001 - a background job must not crash the server
            log.exception("memory extraction failed for %s", user_id)
            return []

    def _run(self, user_id: str, message: str) -> list[str]:
        if len(message.strip()) < 8:
            return []
        try:
            r = self.router.complete([{"role": "system", "content": PROMPT}, {"role": "user", "content": message[:4000]}],
                                     Tier.FAST, purpose="memory:extract", thinking=False, temperature=0, max_tokens=600)
            data = extract_json(r.text)
        except (LLMError, ValueError):
            return []
        if not isinstance(data, dict):
            return []

        said = message.lower()
        stated = lambda value: bool(value) and str(value).lower() in said  # noqa: E731
        saved: list[str] = []
        for g in (data.get("games") or [])[:MAX_ITEMS]:
            if not isinstance(g, dict) or not str(g.get("title", "")).strip():
                continue
            fields: dict[str, Any] = {"progress": str(g["progress"]).strip()} if g.get("progress") else {}
            if stated(g.get("platform")):  # the model guessed "PC" live; keep only platforms actually named
                fields["platform"] = str(g["platform"]).strip()
            if g.get("status") in GAME_STATUSES:
                fields["status"] = g["status"]
            saved += self._save(user_id, "game", str(g["title"]).strip(), fields)
        for c in (data.get("creators") or [])[:MAX_ITEMS]:
            if isinstance(c, dict) and str(c.get("name", "")).strip():
                platforms = [str(p) for p in c.get("platforms") or [] if p]
                saved += self._save(user_id, "creator", str(c["name"]).strip(), {"platforms": platforms} if platforms else {})
        platforms = [str(p) for p in data.get("platforms") or [] if stated(p)][:MAX_ITEMS]
        if platforms:
            existing = self.store.get(user_id, "profile")
            merged = list(dict.fromkeys(((existing.meta.get("platforms") if existing else None) or []) + platforms))
            saved += self._save(user_id, "profile", None, {"platforms": merged})
        return saved

    def _save(self, user_id: str, kind: str, name: str | None, fields: dict[str, Any]) -> list[str]:
        try:
            if kind == "game" and self.known_sources:
                existing = self.store.get(user_id, "game", slugify(name or ""))
                if not (existing and existing.meta.get("sources")) and (sources := self.known_sources(name or "")):
                    fields["sources"] = sources
            item = self.store.upsert(user_id, kind, name, fields, source="chat")
        except MemoryStoreError:
            return []
        return [f"{kind}:{item.slug}"]
