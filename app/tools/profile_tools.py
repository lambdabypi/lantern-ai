"""Agent tools that read and update the player's visible memory. There is deliberately no delete tool."""

from __future__ import annotations

from typing import Any, Callable

from app.memory.store import GAME_STATUSES, SPOILER_PREFERENCES, MemoryStore, slugify
from app.tools.registry import Tool, ToolContext

_STR_LIST = {"type": "array", "items": {"type": "string"}}


def _pick(args: dict[str, Any], *names: str) -> dict[str, Any]:
    return {n: args[n] for n in names if args.get(n) not in (None, "", [])}


def make_profile_tools(store: MemoryStore, known_sources: Callable[[str], list[str]] | None = None) -> list[Tool]:
    """known_sources(title) returns a game's already-discovered sources, attached when it's first saved."""

    def save_game(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        fields = _pick(args, "status", "progress", "platform")
        existing = store.get(ctx.user_id, "game", slugify(args["title"]))
        if known_sources and not (existing and existing.meta.get("sources")):
            if sources := known_sources(args["title"]):
                fields["sources"] = sources
        item = store.upsert(ctx.user_id, "game", args["title"], fields, note=args.get("note"), source="chat")
        return {"saved": "game", "slug": item.slug, "fields": item.meta}

    def save_creator(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        item = store.upsert(ctx.user_id, "creator", args["name"], _pick(args, "platforms", "links"),
                            note=args.get("note"), source="chat")
        return {"saved": "creator", "slug": item.slug, "fields": item.meta}

    def update_profile(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        item = store.upsert(ctx.user_id, "profile", None,
                            _pick(args, "platforms", "play_style", "spoiler_preference"), source="chat")
        return {"saved": "profile", "fields": item.meta}

    def get_profile(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        profile = store.get(ctx.user_id, "profile")
        return {
            "profile": profile.meta if profile else {},
            "games": [g.summary() for g in store.list(ctx.user_id, "game")],
            "creators": [c.summary() for c in store.list(ctx.user_id, "creator")],
        }

    return [
        Tool(
            name="save_game",
            description=("Save or update a game the player mentions: its status, how far they've got, platform, "
                         "or a short note. Call it whenever they tell you about a game they play or their progress."),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The game's name, e.g. 'Warframe'."},
                    "status": {"type": "string", "enum": list(GAME_STATUSES)},
                    "progress": {"type": "string", "description": "Where they are, e.g. 'at Malenia', 'Saturn junction'."},
                    "platform": {"type": "string"},
                    "note": {"type": "string", "description": "A short fact worth remembering about this game."},
                },
                "required": ["title"],
            },
            handler=save_game,
            timeout_s=5.0,
        ),
        Tool(
            name="save_creator",
            description="Save a streamer or content creator the player follows, with their platforms or links.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "platforms": {**_STR_LIST, "description": "e.g. ['twitch', 'youtube']"},
                    "links": _STR_LIST,
                    "note": {"type": "string"},
                },
                "required": ["name"],
            },
            handler=save_creator,
            timeout_s=5.0,
        ),
        Tool(
            name="update_profile",
            description="Update the player's profile: platforms they play on, play style, or spoiler preference.",
            parameters={
                "type": "object",
                "properties": {
                    "platforms": _STR_LIST,
                    "play_style": {"type": "string"},
                    "spoiler_preference": {"type": "string", "enum": list(SPOILER_PREFERENCES)},
                },
            },
            handler=update_profile,
            timeout_s=5.0,
        ),
        Tool(
            name="get_profile",
            description="Read everything saved about the player: profile, games with progress, and creators.",
            parameters={"type": "object", "properties": {}},
            handler=get_profile,
            timeout_s=5.0,
        ),
    ]
