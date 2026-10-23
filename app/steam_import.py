"""Import a public Steam library into the player's visible memory.

Steam data never overwrites what the player told Lantern: progress and notes are untouched,
and status is only set (to "playing", if played in the last 30 days) when the game has none.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.memory.store import MemoryStore, MemoryStoreError, slugify
from app.tools.steam import SteamClient, SteamError

MAX_GAMES = 25            # most-played games imported per run
ACHIEVEMENT_LOOKUPS = 5   # achievements fetched for the most recently played games (one API call each)
RECENT_DAYS = 30


@dataclass
class ImportResult:
    steam_id: str
    imported: int
    updated: int
    total_owned: int


def import_steam_library(store: MemoryStore, steam: SteamClient, user_id: str, profile_text: str) -> ImportResult:
    steam_id = steam.resolve(profile_text)
    library = steam.owned_games(steam_id)
    owned = [g for g in library if g.playtime_hours > 0][:MAX_GAMES]
    recent_cutoff = (date.today() - timedelta(days=RECENT_DAYS)).isoformat()

    by_recency = sorted((g for g in owned if g.last_played), key=lambda g: g.last_played, reverse=True)
    achievements: dict[int, str] = {}
    for game in by_recency[:ACHIEVEMENT_LOOKUPS]:
        try:
            summary = steam.achievements(steam_id, game.appid)
        except SteamError:
            continue  # games without stats, or achievements hidden; not worth failing the import
        if summary.total:
            latest = f" (latest: {summary.recent[0].name})" if summary.recent else ""
            achievements[game.appid] = f"{summary.unlocked}/{summary.total}{latest}"

    imported = updated = 0
    for game in owned:
        try:
            existing = store.get(user_id, "game", slugify(game.name))
        except MemoryStoreError:
            continue  # a title with no letters or digits can't be a file name
        fields = {"steam_appid": game.appid, "playtime_hours": game.playtime_hours,
                  "last_played": game.last_played or "", "platform": "PC (Steam)"}
        if existing and existing.meta.get("platform"):
            fields.pop("platform")  # keep what the player said
        if game.appid in achievements:
            fields["achievements"] = achievements[game.appid]
        if not (existing and existing.meta.get("status")) and game.last_played and game.last_played >= recent_cutoff:
            fields["status"] = "playing"
        store.upsert(user_id, "game", game.name, fields, source="steam")
        updated += 1 if existing else 0
        imported += 0 if existing else 1

    store.upsert(user_id, "profile", None, {"steam_id": steam_id}, source="steam")
    return ImportResult(steam_id=steam_id, imported=imported, updated=updated, total_owned=len(library))

