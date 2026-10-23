"""Opt-in live Steam test: calls the real Steam Web API.

Run with:  RUN_LIVE_TESTS=1 STEAM_API_KEY=... pytest tests/test_live_steam.py
Optional:  STEAM_TEST_PROFILE=<profile URL, vanity or SteamID64> (must have public game details)
"""

from __future__ import annotations

import os

import pytest

from app.tools.steam import SteamClient

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1" or not os.environ.get("STEAM_API_KEY"),
    reason="live test: set RUN_LIVE_TESTS=1 and STEAM_API_KEY to run",
)


def test_live_resolve_and_owned_games():
    client = SteamClient(os.environ["STEAM_API_KEY"])
    steamid = client.resolve(os.environ.get("STEAM_TEST_PROFILE", "gabelogannewell"))
    assert len(steamid) == 17
    if "STEAM_TEST_PROFILE" in os.environ:
        games = client.owned_games(steamid)
        assert all(g.playtime_hours >= 0 for g in games)
        if games:
            client.achievements(steamid, games[0].appid)  # may raise "no achievements": fine to see
