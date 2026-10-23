from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.main import create_app
from app.memory.store import MemoryStore
from app.steam_import import import_steam_library
from app.tools.steam import AchievementSummary, OwnedGame, RecentAchievement, SteamError
from tests.test_agent_loop import make_loop

TODAY = date.today()
RECENT = (TODAY - timedelta(days=3)).isoformat()
OLD = (TODAY - timedelta(days=400)).isoformat()


class FakeSteam:
    def __init__(self, games=None, fail=None):
        self.games = games if games is not None else [
            OwnedGame(230410, "Warframe", 110.6, RECENT),
            OwnedGame(292030, "The Witcher 3: Wild Hunt", 49.5, OLD),
            OwnedGame(1, "Never Played", 0.0, None),
        ]
        self.fail = fail
        self.achievement_calls = []

    def resolve(self, text):
        if self.fail:
            raise SteamError(self.fail)
        return "76561198000000000"

    def owned_games(self, steamid):
        return self.games

    def player_name(self, steamid):
        return None

    def achievements(self, steamid, appid):
        self.achievement_calls.append(appid)
        if appid == 292030:
            raise SteamError("Requested app has no stats")
        return AchievementSummary(appid, 133, 193, [RecentAchievement("Heavy Weapon Mastery III", RECENT)])


def test_import_writes_games_and_keeps_player_data(settings):
    store = MemoryStore(settings.data_dir)
    store.upsert("alice", "game", "Warframe", {"status": "backlog", "progress": "Saturn", "platform": "PC"},
                 note="told Lantern myself")

    result = import_steam_library(store, FakeSteam(), "alice", "someone")

    assert (result.imported, result.updated, result.total_owned) == (1, 1, 3)
    wf = store.get("alice", "game", "warframe").meta
    assert wf["status"] == "backlog" and wf["progress"] == "Saturn" and wf["platform"] == "PC"  # untouched
    assert wf["playtime_hours"] == 110.6 and wf["steam_appid"] == 230410
    assert wf["achievements"] == "133/193 (latest: Heavy Weapon Mastery III)"
    assert "told Lantern myself" in store.get("alice", "game", "warframe").body

    witcher = store.get("alice", "game", "the-witcher-3-wild-hunt").meta
    assert "status" not in witcher                     # not played recently -> no status guessed
    assert witcher["platform"] == "PC (Steam)" and "achievements" not in witcher  # no stats -> skipped
    assert store.get("alice", "game", "never-played") is None                     # 0 h games aren't imported
    assert store.get("alice", "profile").meta["steam_id"] == "76561198000000000"


def test_recently_played_new_game_is_marked_playing(settings):
    store = MemoryStore(settings.data_dir)
    import_steam_library(store, FakeSteam(), "bob", "someone")
    assert store.get("bob", "game", "warframe").meta["status"] == "playing"


def test_endpoint(settings):
    loop, _ = make_loop(settings, [])
    app = create_app(loop=loop, settings=settings, steam=FakeSteam())
    c = TestClient(app)
    assert c.post("/api/auth/guest").json()["steam_enabled"] is True
    r = c.post("/api/steam/import", json={"profile": "https://steamcommunity.com/id/someone/"})
    assert r.status_code == 200 and r.json()["imported"] == 2
    titles = {g["title"] for g in c.get("/api/profile").json()["games"]}
    assert titles == {"Warframe", "The Witcher 3: Wild Hunt"}


def test_endpoint_errors_are_clear(settings):
    loop, _ = make_loop(settings, [])
    private = create_app(loop=loop, settings=settings, steam=FakeSteam(fail="Your Steam profile is private."))
    c = TestClient(private)
    c.post("/api/auth/guest")
    r = c.post("/api/steam/import", json={"profile": "someone"})
    assert r.status_code == 422 and "private" in r.json()["detail"]

    unconfigured = create_app(loop=loop, settings=replace(settings, steam_api_key=""))
    c2 = TestClient(unconfigured)
    assert c2.post("/api/auth/guest").json()["steam_enabled"] is False
    r = c2.post("/api/steam/import", json={"profile": "someone"})
    assert r.status_code == 503 and "STEAM_API_KEY" in r.json()["detail"]


def test_import_requires_sign_in(settings):
    loop, _ = make_loop(settings, [])
    c = TestClient(create_app(loop=loop, settings=settings, steam=FakeSteam()))
    assert c.post("/api/steam/import", json={"profile": "someone"}).status_code == 401


def test_import_endpoint_prewarms_sources_for_new_games(settings):
    class Recorder:
        def __init__(self):
            self.calls = []

        def prewarm(self, user_id, titles):
            self.calls.append((user_id, sorted(titles)))

        def cached_sources(self, title):
            return []

    rec = Recorder()
    loop, _ = make_loop(settings, [])
    c = TestClient(create_app(loop=loop, settings=settings, steam=FakeSteam(), knowledge=rec))
    uid = c.post("/api/auth/guest").json()["user_id"]
    c.post("/api/steam/import", json={"profile": "someone"})
    assert rec.calls == [(uid, ["The Witcher 3: Wild Hunt", "Warframe"])]
    c.post("/api/memory/game", json={"name": "Hades"})
    assert rec.calls[-1] == (uid, ["Hades"])
