"""Daily limits that protect a public demo's Token Factory and Tavily credits."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.auth.quota import SiteQuota
from app.main import create_app
from app.tools.web_search import SearchError, WebSearch
from tests.test_agent_loop import make_loop
from tests.test_parsing_and_search import FakeTavily


def app_with(settings, replies=("ok",) * 20, **caps):
    loop, llm = make_loop(settings, list(replies))
    return create_app(loop=loop, settings=replace(settings, **caps)), llm


def guest(app):
    c = TestClient(app)
    c.post("/api/auth/guest")
    return c


def test_guest_message_cap(settings):
    app, llm = app_with(settings, guest_messages_per_day=2)
    c = guest(app)
    assert c.get("/api/me").json()["messages_left_today"] == 2
    for _ in range(2):
        assert c.post("/api/chat", json={"message": "hi there"}).json()["stop_reason"] == "done"
    blocked = c.post("/api/chat", json={"message": "hi there"}).json()
    assert blocked["stop_reason"] == "quota" and "limit of 2 messages" in blocked["answer"]
    assert "sign in with Steam" in blocked["answer"]
    assert len(llm.calls) == 2 and c.get("/api/me").json()["messages_left_today"] == 0


def test_registered_players_get_their_own_cap(settings):
    app, _ = app_with(settings, guest_messages_per_day=1, user_messages_per_day=3)
    c = TestClient(app)
    c.post("/api/auth/register", json={"username": "alice", "password": "password123"})
    results = [c.post("/api/chat", json={"message": "hello"}).json()["stop_reason"] for _ in range(4)]
    assert results == ["done", "done", "done", "quota"]


def test_site_wide_cap_across_players(settings):
    app, llm = app_with(settings, site_messages_per_day=3)
    a, b = guest(app), guest(app)
    outcomes = [x.post("/api/chat", json={"message": f"hello {i}"}).json()["stop_reason"]  # unique: no dev-cache hits
                for i, x in enumerate((a, b, a, b))]
    assert outcomes == ["done", "done", "done", "quota"] and len(llm.calls) == 3


def test_zero_means_unlimited_for_message_caps(settings):
    app, _ = app_with(settings, guest_messages_per_day=0, site_messages_per_day=0)
    c = guest(app)
    assert c.get("/api/me").json()["messages_left_today"] is None
    assert all(c.post("/api/chat", json={"message": "hello"}).json()["stop_reason"] == "done" for _ in range(5))


def test_stream_endpoint_respects_caps(settings):
    app, llm = app_with(settings, guest_messages_per_day=0, site_messages_per_day=1)
    c = guest(app)
    c.post("/api/chat/stream", json={"message": "first"})
    events = [json.loads(l[6:]) for l in c.post("/api/chat/stream", json={"message": "second"}).text.splitlines()
              if l.startswith("data: ")]
    assert events[-1]["type"] == "done" and events[-1]["stop_reason"] == "quota" and len(llm.calls) == 1


def test_tavily_budget_blocks_live_calls_but_not_cached_ones(settings):
    site = SiteQuota(settings.data_dir)
    tav = FakeTavily()
    ws = WebSearch(settings, tavily_client=tav, credit_guard=lambda n: site.try_consume("tavily", 2, n))
    ws.search("one")
    ws.search("two")
    with pytest.raises(SearchError, match="daily web-search budget"):
        ws.search("three")
    assert ws.search("one")[0]["url"].endswith("/one")   # cached: no credit needed
    assert len(tav.calls) == 2 and site.used("tavily") == 2


def test_site_quota_resets_each_day(settings):
    t = [1_000_000.0]
    site = SiteQuota(settings.data_dir, clock=lambda: t[0])
    assert site.try_consume("messages", 1) and not site.try_consume("messages", 1)
    t[0] += 86400
    assert site.try_consume("messages", 1)


def test_guest_creation_is_limited_per_network(settings):
    app, _ = app_with(settings, guests_per_ip_per_day=2)
    c = TestClient(app)
    hdr = {"X-Forwarded-For": "203.0.113.7"}
    assert [c.post("/api/auth/guest", headers=hdr).status_code for _ in range(3)] == [200, 200, 429]
    r = c.post("/api/auth/guest", headers=hdr)
    assert "Too many guest accounts" in r.json()["detail"] and "Steam" in r.json()["detail"]
    assert c.post("/api/auth/guest", headers={"X-Forwarded-For": "198.51.100.9"}).status_code == 200  # other network


def test_spoofed_forwarded_for_entries_do_not_bypass_the_limit(settings):
    app, _ = app_with(settings, guests_per_ip_per_day=1)
    c = TestClient(app)
    # The visitor controls the left part of the header; the proxy appends the real address on the right.
    assert c.post("/api/auth/guest", headers={"X-Forwarded-For": "1.1.1.1, 203.0.113.7"}).status_code == 200
    assert c.post("/api/auth/guest", headers={"X-Forwarded-For": "2.2.2.2, 203.0.113.7"}).status_code == 429


def test_addresses_are_stored_only_as_hashes(settings):
    app, _ = app_with(settings)
    TestClient(app).post("/api/auth/guest", headers={"X-Forwarded-For": "203.0.113.7"})
    stored = (settings.data_dir / "site_usage.json").read_text()
    assert "203.0.113.7" not in stored and '"guests:' in stored


def test_client_ip_helper():
    from types import SimpleNamespace
    from app.main import client_ip

    def req(xff=None, peer="10.0.0.1"):
        return SimpleNamespace(client=SimpleNamespace(host=peer), headers={"x-forwarded-for": xff} if xff else {})
    assert client_ip(req("9.9.9.9, 203.0.113.7"), 1) == "203.0.113.7"
    assert client_ip(req("9.9.9.9, 203.0.113.7, 35.0.0.1"), 2) == "203.0.113.7"
    assert client_ip(req(None), 1) == "10.0.0.1"         # no header: the direct peer
    assert client_ip(req("9.9.9.9"), 0) == "10.0.0.1"    # 0 hops: ignore the header entirely
