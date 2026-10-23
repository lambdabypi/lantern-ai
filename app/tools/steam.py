"""Steam Web API client: resolve a profile, list owned games, summarise achievements.

Endpoints (Steamworks Web API reference, partner.steamgames.com/doc/webapi):
- ISteamUser/ResolveVanityURL/v1        key, vanityurl
- IPlayerService/GetOwnedGames/v1       key, steamid, include_appinfo, include_played_free_games
- ISteamUserStats/GetPlayerAchievements/v1   key, steamid, appid, l

The API key travels as a query parameter, so it would leak through any httpx error that
includes the request URL. Every error raised here is a SteamError with a message we wrote
ourselves, raised `from None` so the original httpx exception (and its URL) isn't chained.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

BASE_URL = "https://api.steampowered.com/"
TIMEOUT_S = 10.0

PRIVATE_MESSAGE = (
    "Your Steam profile or game details are private. In Steam: Edit Profile -> "
    "Privacy Settings -> set 'Game details' to Public."
)
BAD_KEY_MESSAGE = "Steam rejected the API key. Check STEAM_API_KEY (get one at steamcommunity.com/dev/apikey)."
RATE_LIMIT_MESSAGE = "Steam is rate limiting requests right now. Please try again in a minute."
NETWORK_MESSAGE = "Couldn't reach Steam (network error or timeout). Please try again."
UNAVAILABLE_MESSAGE = "Steam's API is temporarily unavailable. Please try again later."

_STEAMID64 = re.compile(r"^7656\d{13}$")
_VANITY = re.compile(r"^[A-Za-z0-9_-]{2,32}$")
_COMMUNITY_HOSTS = {"steamcommunity.com", "www.steamcommunity.com"}

InputKind = Literal["steamid", "vanity"]


class SteamError(RuntimeError):
    """A Steam lookup failed. The message is safe to show to the user and never contains the key."""


@dataclass(frozen=True)
class OwnedGame:
    appid: int
    name: str
    playtime_hours: float
    last_played: str | None  # ISO date (UTC), or None if never played / unknown


@dataclass(frozen=True)
class RecentAchievement:
    name: str
    unlocked_on: str  # ISO date (UTC)


@dataclass(frozen=True)
class AchievementSummary:
    appid: int
    unlocked: int
    total: int
    recent: list[RecentAchievement] = field(default_factory=list)  # newest first, at most 5


def parse_steam_id_input(text: str) -> tuple[InputKind, str]:
    """Classify what the user typed as ("steamid", <17 digits>) or ("vanity", <name>)."""
    s = (text or "").strip()
    if not s:
        raise SteamError("Please enter your Steam profile URL, custom URL name or SteamID64.")
    if _STEAMID64.match(s):
        return "steamid", s

    if "/" in s or "." in s:
        url = s if "://" in s else "https://" + s
        parsed = urlparse(url)
        parts = [p for p in parsed.path.split("/") if p]
        if (parsed.scheme in ("http", "https") and (parsed.hostname or "").lower() in _COMMUNITY_HOSTS
                and len(parts) == 2):
            kind, value = parts
            if kind == "profiles" and _STEAMID64.match(value):
                return "steamid", value
            if kind == "id" and _VANITY.match(value):
                return "vanity", value
        raise SteamError(
            "That doesn't look like a Steam profile URL. Use steamcommunity.com/id/<name> "
            "or steamcommunity.com/profiles/<SteamID64>."
        )

    if _VANITY.match(s) and not s.isdigit():
        return "vanity", s
    raise SteamError(
        "That doesn't look like a Steam ID. Enter a 17-digit SteamID64, a profile URL, "
        "or your custom URL name (2-32 letters, digits, _ or -)."
    )


def _iso_date(ts: Any) -> str | None:
    try:
        ts = int(ts or 0)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()


class SteamClient:
    def __init__(self, api_key: str, http: httpx.Client | None = None):
        if not api_key:
            raise SteamError("Steam isn't configured: STEAM_API_KEY is missing.")
        self._key = api_key
        self._http = http or httpx.Client(base_url=BASE_URL, timeout=TIMEOUT_S)

    def __repr__(self) -> str:  # never show the key
        return "SteamClient(api_key=***)"

    # -- public API -------------------------------------------------------------------

    def resolve(self, text: str) -> str:
        """Return the SteamID64 for a SteamID64, profile URL or vanity name."""
        kind, value = parse_steam_id_input(text)
        if kind == "steamid":
            return value
        data = self._get("ISteamUser/ResolveVanityURL/v1/", {"vanityurl": value})
        resp = data.get("response") or {}
        steamid = str(resp.get("steamid") or "")
        if resp.get("success") == 1 and _STEAMID64.match(steamid):
            return steamid
        raise SteamError(f"No Steam profile found with the custom URL name {value!r}.")

    def owned_games(self, steamid: str) -> list[OwnedGame]:
        """Owned games (including played free games), most played first."""
        data = self._get("IPlayerService/GetOwnedGames/v1/", {
            "steamid": steamid, "include_appinfo": 1, "include_played_free_games": 1,
        })
        resp = data.get("response")
        if not isinstance(resp, dict) or ("games" not in resp and "game_count" not in resp):
            # An empty response object is what Steam returns for private game details.
            raise SteamError(PRIVATE_MESSAGE)
        games = []
        for g in resp.get("games") or []:
            appid = int(g.get("appid", 0))
            games.append(OwnedGame(
                appid=appid,
                name=str(g.get("name") or f"App {appid}"),
                playtime_hours=round(int(g.get("playtime_forever") or 0) / 60, 1),
                last_played=_iso_date(g.get("rtime_last_played")),
            ))
        games.sort(key=lambda x: (-x.playtime_hours, x.name.lower()))
        return games

    def achievements(self, steamid: str, appid: int) -> AchievementSummary:
        """How many achievements are unlocked, plus the 5 most recent."""
        data = self._get("ISteamUserStats/GetPlayerAchievements/v1/", {
            "steamid": steamid, "appid": int(appid), "l": "english",
        })
        stats = data.get("playerstats") or {}
        if not stats.get("success"):
            raise SteamError(self._playerstats_error(stats))
        items = stats.get("achievements") or []
        unlocked = [a for a in items if int(a.get("achieved") or 0) == 1]
        unlocked.sort(key=lambda a: int(a.get("unlocktime") or 0), reverse=True)
        recent = [
            RecentAchievement(name=str(a.get("name") or a.get("apiname") or "?"),
                              unlocked_on=_iso_date(a.get("unlocktime")) or "unknown")
            for a in unlocked[:5]
        ]
        return AchievementSummary(appid=int(appid), unlocked=len(unlocked), total=len(items),
                                  recent=recent)

    # -- internals --------------------------------------------------------------------

    @staticmethod
    def _playerstats_error(stats: dict) -> str:
        err = str(stats.get("error") or "").lower()
        if "not public" in err or "private" in err:
            return PRIVATE_MESSAGE
        if "no stats" in err:
            return "This game has no achievements or stats on Steam."
        return "Steam couldn't return achievements for this game."

    def player_name(self, steamid: str) -> str | None:
        """The player's public persona name (ISteamUser/GetPlayerSummaries/v2), or None if unavailable."""
        try:
            data = self._get("ISteamUser/GetPlayerSummaries/v2/", {"steamids": steamid})
        except SteamError:
            return None  # a missing display name must never block sign-in
        players = (data.get("response") or {}).get("players") or []
        name = players[0].get("personaname") if players and isinstance(players[0], dict) else None
        return str(name)[:64] if name else None

    def _get(self, path: str, params: dict[str, Any]) -> dict:
        try:
            r = self._http.get(BASE_URL + path, params={"key": self._key, **params},
                               timeout=TIMEOUT_S)
        except httpx.TimeoutException:
            raise SteamError(NETWORK_MESSAGE) from None
        except httpx.HTTPError:
            raise SteamError(NETWORK_MESSAGE) from None

        try:
            data = r.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            data = {}

        if r.status_code == 200:
            if not data:
                raise SteamError("Steam returned an unexpected response. Please try again.")
            return data

        # GetPlayerAchievements reports private profiles / no-stats games as 4xx with a body.
        stats = data.get("playerstats")
        if isinstance(stats, dict) and stats.get("error"):
            raise SteamError(self._playerstats_error(stats))
        if r.status_code in (401, 403):
            raise SteamError(BAD_KEY_MESSAGE)
        if r.status_code == 429:
            raise SteamError(RATE_LIMIT_MESSAGE)
        if r.status_code == 404:
            raise SteamError("Steam couldn't find that profile or API method.")
        if r.status_code >= 500:
            raise SteamError(UNAVAILABLE_MESSAGE)
        raise SteamError(f"Steam request failed (HTTP {r.status_code}).")
