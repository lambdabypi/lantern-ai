from __future__ import annotations

import io
import json
import zipfile
from dataclasses import replace

from fastapi.testclient import TestClient

from app.briefing import Briefings
from app.llm.client import TokenFactoryClient
from app.llm.router import ModelRouter
from app.main import create_app
from app.memory.store import MemoryStore
from app.tools.web_search import WebSearch
from tests.conftest import ScriptedLLM
from tests.test_agent_loop import make_loop

WF_NEWS = [
    {"url": "https://www.warframe.com/news/update-44", "title": "Warframe Update 44.1.3 patch notes",
     "content": "Warframe hotfix nerfs Sobek acid shells", "published_date": "Fri, 23 Oct 2026 05:00:00 GMT"},
    {"url": "https://example-news.com/wow-maintenance", "title": "WoW Midnight maintenance October 20",
     "content": "World of Warcraft servers down", "published_date": "Tue, 20 Oct 2026 13:30:00 GMT"},
]


class NewsTavily:
    def __init__(self, results=None, fail=False):
        self.results = WF_NEWS if results is None else results
        self.calls, self.fail = [], fail

    def search(self, query, **kw):
        self.calls.append((query, kw))
        return {"results": self.results, "usage": {"credits": 1}}


def summary(*urls):
    return json.dumps({"items": [{"headline": f"News {i}", "summary": "Something changed.", "url": u}
                                 for i, u in enumerate(urls)]})


def make(settings, replies, tavily=None, guard=None):
    tav = tavily or NewsTavily()
    llm = ScriptedLLM(replies)
    s = replace(settings, model_balanced="super-test")
    store = MemoryStore(s.data_dir)
    router = ModelRouter(s, client=TokenFactoryClient(s, openai_client=llm))
    return Briefings(store, WebSearch(s, tavily_client=tav, credit_guard=guard), router, s.data_dir), store, tav, llm


def test_game_news_filters_other_games_and_trusts_tavily_dates(settings):
    b, _, tav, llm = make(settings, [summary("https://www.warframe.com/news/update-44", "https://made-up.example/x")])
    news = b.game_news("Warframe")
    assert tav.calls[0][1]["topic"] == "news" and tav.calls[0][1]["time_range"] == "week"
    sent = llm.calls[0]["messages"][1]["content"]
    assert "Sobek" in sent and "World of Warcraft" not in sent        # other games filtered before the model
    assert llm.calls[0]["model"] == "super-test"
    assert [(i["url"], i["date"]) for i in news["items"]] == [("https://www.warframe.com/news/update-44", "2026-10-23")]


def test_news_is_shared_per_game_per_day(settings):
    b, store, tav, llm = make(settings, [summary("https://www.warframe.com/news/update-44")])
    for uid in ("alice", "bob"):
        store.upsert(uid, "game", "Warframe", {"status": "playing"})
        b.build(uid)
    assert len(tav.calls) == 1 and len(llm.calls) == 1               # second player costs nothing


def test_briefing_uses_playing_games_and_falls_back_to_recent(settings):
    b, store, _, _ = make(settings, [summary(), summary()], tavily=NewsTavily(results=[]))
    store.upsert("alice", "game", "Warframe", {"status": "playing"})
    store.upsert("alice", "game", "Hades", {"status": "finished"})
    assert b.games_for("alice") == ["Warframe"]
    store.upsert("bob", "game", "Hades", {"status": "backlog"})
    assert b.games_for("bob") == ["Hades"]
    assert b.games_for("nobody") == []


def test_no_relevant_news_means_no_model_call(settings):
    b, store, _, llm = make(settings, [], tavily=NewsTavily(results=[WF_NEWS[1]]))
    store.upsert("alice", "game", "Warframe", {"status": "playing"})
    assert b.build("alice")["games"] == [{"title": "Warframe", "items": []}]
    assert llm.calls == []


def test_spent_search_budget_is_reported_in_the_briefing(settings):
    b, store, _, _ = make(settings, [], guard=lambda n: False)
    store.upsert("alice", "game", "Warframe", {"status": "playing"})
    briefing = b.build("alice")
    assert "daily web-search budget" in briefing["note"]


def test_today_reuses_the_saved_briefing(settings):
    b, store, tav, _ = make(settings, [summary("https://www.warframe.com/news/update-44")])
    store.upsert("alice", "game", "Warframe", {"status": "playing"})
    first = b.today("alice")
    assert b.today("alice") == first and len(tav.calls) == 1
    assert (settings.data_dir / "users" / "alice" / "briefings" / f"{first['date']}.json").exists()


def app_with_briefings(settings, cron_secret=""):
    b, store, tav, llm = make(settings, [summary("https://www.warframe.com/news/update-44")] * 3)
    loop, _ = make_loop(settings, [])
    app = create_app(loop=loop, settings=replace(settings, cron_secret=cron_secret), briefings=b)
    return app, store, tav


def test_briefing_endpoints_and_export(settings):
    app, store, _ = app_with_briefings(settings)
    c = TestClient(app)
    uid = c.post("/api/auth/guest").json()["user_id"]
    store.upsert(uid, "game", "Warframe", {"status": "playing"})
    body = c.get("/api/briefing").json()
    assert body["games"][0]["title"] == "Warframe" and body["games"][0]["items"][0]["date"] == "2026-10-23"
    assert c.post("/api/briefing/refresh").status_code == 200
    names = zipfile.ZipFile(io.BytesIO(c.get("/api/export").content)).namelist()
    assert any(n.startswith("briefings/") for n in names)
    assert TestClient(app).get("/api/briefing").status_code == 401


def test_daily_job_needs_the_secret_and_skips_expired_guests(settings):
    app, store, tav = app_with_briefings(settings, cron_secret="s3cret-value-for-tests")
    c = TestClient(app)
    alive = c.post("/api/auth/guest").json()["user_id"]
    store.upsert(alive, "game", "Warframe", {"status": "playing"})
    expired = TestClient(app).post("/api/auth/guest").json()["user_id"]
    store.upsert(expired, "game", "Warframe", {"status": "playing"})
    acct = settings.data_dir / "users" / expired / "account.json"
    data = json.loads(acct.read_text()); data["expires_ts"] = 1.0; acct.write_text(json.dumps(data))

    assert c.post("/internal/briefings/run").status_code == 404
    assert c.post("/internal/briefings/run", headers={"X-Lantern-Cron": "wrong"}).status_code == 404
    r = c.post("/internal/briefings/run", headers={"X-Lantern-Cron": "s3cret-value-for-tests"})
    assert r.json() == {"players": 1, "games": 1}
    assert (settings.data_dir / "users" / alive / "briefings").is_dir()
    assert not (settings.data_dir / "users" / expired / "briefings").exists()


def test_daily_job_is_disabled_without_a_secret(settings):
    app, _, _ = app_with_briefings(settings, cron_secret="")
    assert TestClient(app).post("/internal/briefings/run", headers={"X-Lantern-Cron": ""}).status_code == 404
