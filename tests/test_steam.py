"""Steam Web API client tests. Fully mocked with httpx.MockTransport: no network."""

from __future__ import annotations

import httpx
import pytest

from app.tools.steam import (
    BAD_KEY_MESSAGE,
    NETWORK_MESSAGE,
    PRIVATE_MESSAGE,
    RATE_LIMIT_MESSAGE,
    SteamClient,
    SteamError,
    parse_steam_id_input,
)

KEY = "SECRETKEY0123456789ABCDEF"
SID = "76561197960287930"


def client_with(handler) -> tuple[SteamClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return SteamClient(KEY, http=httpx.Client(transport=httpx.MockTransport(wrapped))), seen


def json_handler(payload, status=200):
    return lambda request: httpx.Response(status, json=payload)


# -- input parsing ------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    (SID, ("steamid", SID)),
    (f"  {SID}  ", ("steamid", SID)),
    (f"https://steamcommunity.com/profiles/{SID}/", ("steamid", SID)),
    (f"steamcommunity.com/profiles/{SID}", ("steamid", SID)),
    ("https://steamcommunity.com/id/gabelogannewell/", ("vanity", "gabelogannewell")),
    ("http://www.steamcommunity.com/id/some_name-1", ("vanity", "some_name-1")),
    ("gabelogannewell", ("vanity", "gabelogannewell")),
    ("ab", ("vanity", "ab")),
])
def test_parse_accepts(text, expected):
    assert parse_steam_id_input(text) == expected


@pytest.mark.parametrize("text", [
    "", "   ", "a", "x" * 33, "has space", "bad!chars", "12345",
    "https://evil.example.com/id/someone/",
    "https://steamcommunity.com/id/",
    "https://steamcommunity.com/profiles/123/",
    "https://steamcommunity.com/id/name/extra/",
    "https://steamcommunity.com/groups/somegroup",
    "ftp://steamcommunity.com/id/name",
])
def test_parse_rejects(text):
    with pytest.raises(SteamError):
        parse_steam_id_input(text)


# -- resolve ------------------------------------------------------------------------------

def test_resolve_steamid_needs_no_request():
    c, seen = client_with(json_handler({}))
    assert c.resolve(SID) == SID
    assert seen == []


def test_resolve_vanity_success():
    c, seen = client_with(json_handler({"response": {"steamid": SID, "success": 1}}))
    assert c.resolve("https://steamcommunity.com/id/gabe/") == SID
    req = seen[0]
    assert req.url.path == "/ISteamUser/ResolveVanityURL/v1/"
    assert req.url.params["vanityurl"] == "gabe"
    assert req.url.params["key"] == KEY


def test_resolve_vanity_no_match():
    c, _ = client_with(json_handler({"response": {"success": 42, "message": "No match"}}))
    with pytest.raises(SteamError, match="No Steam profile found"):
        c.resolve("nobodyhere")


# -- owned games --------------------------------------------------------------------------

def test_owned_games_normalised_and_sorted():
    payload = {"response": {"game_count": 3, "games": [
        {"appid": 10, "name": "Counter-Strike", "playtime_forever": 90, "rtime_last_played": 0,
         "img_icon_url": "abc"},
        {"appid": 620, "name": "Portal 2", "playtime_forever": 1234,
         "rtime_last_played": 1700000000},
        {"appid": 440, "name": "Team Fortress 2", "playtime_forever": 0},
    ]}}
    c, seen = client_with(json_handler(payload))
    games = c.owned_games(SID)

    params = seen[0].url.params
    assert seen[0].url.path == "/IPlayerService/GetOwnedGames/v1/"
    assert params["include_appinfo"] == "1" and params["include_played_free_games"] == "1"
    assert [g.appid for g in games] == [620, 10, 440]
    assert games[0].playtime_hours == 20.6 and games[0].last_played == "2023-11-14"
    assert games[1].playtime_hours == 1.5 and games[1].last_played is None
    assert games[2].playtime_hours == 0.0


def test_owned_games_empty_response_means_private():
    c, _ = client_with(json_handler({"response": {}}))
    with pytest.raises(SteamError) as e:
        c.owned_games(SID)
    assert str(e.value) == PRIVATE_MESSAGE


def test_owned_games_zero_games_is_not_an_error():
    c, _ = client_with(json_handler({"response": {"game_count": 0}}))
    assert c.owned_games(SID) == []


# -- achievements -------------------------------------------------------------------------

def test_achievements_summary():
    achs = [
        {"apiname": f"A{i}", "achieved": 1, "unlocktime": 1700000000 + i * 86400,
         "name": f"Ach {i}", "description": "d"} for i in range(7)
    ] + [{"apiname": "L", "achieved": 0, "unlocktime": 0, "name": "Locked", "description": ""}]
    payload = {"playerstats": {"steamID": SID, "gameName": "Portal 2", "achievements": achs,
                               "success": True}}
    c, seen = client_with(json_handler(payload))
    s = c.achievements(SID, 620)

    assert seen[0].url.path == "/ISteamUserStats/GetPlayerAchievements/v1/"
    assert seen[0].url.params["appid"] == "620" and seen[0].url.params["l"] == "english"
    assert (s.appid, s.unlocked, s.total) == (620, 7, 8)
    assert [r.name for r in s.recent] == ["Ach 6", "Ach 5", "Ach 4", "Ach 3", "Ach 2"]
    assert s.recent[0].unlocked_on == "2023-11-20"


def test_achievements_game_without_stats():
    payload = {"playerstats": {"error": "Requested app has no stats", "success": False}}
    c, _ = client_with(json_handler(payload, status=400))
    with pytest.raises(SteamError, match="no achievements"):
        c.achievements(SID, 999)


def test_achievements_private_profile():
    payload = {"playerstats": {"error": "Profile is not public", "success": False}}
    c, _ = client_with(json_handler(payload, status=403))
    with pytest.raises(SteamError) as e:
        c.achievements(SID, 620)
    assert str(e.value) == PRIVATE_MESSAGE


# -- HTTP and network errors --------------------------------------------------------------

@pytest.mark.parametrize("status, message", [
    (401, BAD_KEY_MESSAGE), (403, BAD_KEY_MESSAGE), (429, RATE_LIMIT_MESSAGE),
])
def test_http_errors(status, message):
    c, _ = client_with(lambda r: httpx.Response(status, text="<html>Forbidden</html>"))
    with pytest.raises(SteamError) as e:
        c.owned_games(SID)
    assert str(e.value) == message


def test_server_error():
    c, _ = client_with(lambda r: httpx.Response(503))
    with pytest.raises(SteamError, match="temporarily unavailable"):
        c.owned_games(SID)


@pytest.mark.parametrize("exc", [httpx.ReadTimeout, httpx.ConnectError])
def test_network_errors(exc):
    def boom(request):
        raise exc(f"failed for {request.url}", request=request)

    c, _ = client_with(boom)
    with pytest.raises(SteamError) as e:
        c.owned_games(SID)
    assert str(e.value) == NETWORK_MESSAGE


def test_api_key_never_leaks():
    """Even when httpx's error embeds the full URL (with ?key=...), the key stays hidden."""
    def boom(request):
        assert KEY in str(request.url)
        raise httpx.ConnectError(f"Connection failed: {request.url}", request=request)

    errors: list[SteamError] = []
    c, _ = client_with(boom)
    for call in (lambda: c.owned_games(SID), lambda: c.resolve("someone"),
                 lambda: c.achievements(SID, 620)):
        with pytest.raises(SteamError) as e:
            call()
        errors.append(e.value)

    for status in (400, 401, 403, 404, 429, 500):
        c2, _ = client_with(lambda r, s=status: httpx.Response(s, text=f"bad {r.url}"))
        with pytest.raises(SteamError) as e:
            c2.owned_games(SID)
        errors.append(e.value)

    for err in errors:
        assert KEY not in str(err) and KEY not in repr(err)
        # The httpx error (whose message holds the URL) must not be chained into tracebacks.
        assert err.__cause__ is None
        assert err.__context__ is None or err.__suppress_context__
    assert KEY not in repr(c)


def test_missing_key_is_rejected():
    with pytest.raises(SteamError, match="STEAM_API_KEY"):
        SteamClient("")
