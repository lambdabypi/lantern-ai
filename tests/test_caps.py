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
    assert "Create an account for more" in blocked["answer"]
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
    assert "Too many guest sessions" in r.json()["detail"] and "Create an account" in r.json()["detail"]
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


def test_guest_keeps_their_data_by_creating_an_account(settings):
    from tests.test_recall_chat import watch_and_store
    watch, store, _ = watch_and_store(settings)
    loop, _ = make_loop(settings, [])
    c = TestClient(create_app(loop=loop, settings=settings, watch=watch))
    guest = c.post("/api/auth/guest").json()["user_id"]
    c.post("/api/vehicles", json={"make": "Honda", "model": "Civic", "year": 2018})
    me = c.post("/api/auth/keep", json={"username": "maria", "password": "password123"}).json()
    assert me["user_id"] == "maria" and me["kind"] == "user"
    assert [v["name"] for v in c.get("/api/profile").json()["vehicles"]] == ["2018 Honda Civic"]
    assert not (settings.data_dir / "users" / guest).exists()
    assert c.post("/api/auth/keep", json={"username": "maria2", "password": "password123"}).status_code == 400
    other = TestClient(create_app(loop=loop, settings=settings, watch=watch))
    other.post("/api/auth/guest")
    taken = other.post("/api/auth/keep", json={"username": "maria", "password": "password123"})
    assert taken.status_code == 409 and other.get("/api/me").json()["kind"] == "guest"   # nothing moved


def test_daily_dollar_cap_stops_model_calls(settings):
    from app.llm.client import TokenFactoryClient
    from app.llm.router import ModelRouter, SpendCapReached, Tier, call_cost
    from tests.conftest import ScriptedLLM
    site = SiteQuota(settings.data_dir)
    capped = replace(settings, site_model_dollars_per_day=0.00005, cache_enabled=False)  # 50 millionths of a dollar
    router = ModelRouter(capped, client=TokenFactoryClient(capped, openai_client=ScriptedLLM(["a"] * 5)), spend=site)
    assert round(call_cost(Tier.FAST, 200, 40) * 1_000_000) == 22   # 200 in x $0.06 + 40 out x $0.24, per million
    for i in range(5):
        router.complete([{"role": "user", "content": f"hi {i}"}])
    assert site.used("model_spend") == 55                # 5 calls of the scripted model's 100 in / 20 out = 11 each
    with pytest.raises(SpendCapReached, match="today's budget"):
        router.complete([{"role": "user", "content": "one more"}])


def test_ultra_is_capped_for_signed_in_users_and_site_wide(settings):
    app, _ = app_with(settings, user_ultra_per_day=1, site_ultra_per_day=2)
    a, b = TestClient(app), TestClient(app)
    a.post("/api/auth/register", json={"username": "alice", "password": "password123"})
    b.post("/api/auth/register", json={"username": "bobby", "password": "password123"})
    think = {"message": "plan it", "escalate": True}
    assert a.post("/api/chat", json=think).json()["stop_reason"] == "done"
    assert "1 \"Take more time to think\" turns" in a.post("/api/chat", json=think).json()["answer"]
    assert b.post("/api/chat", json=think).json()["stop_reason"] == "done"
    c = TestClient(app)
    c.post("/api/auth/register", json={"username": "carol", "password": "password123"})
    assert "across all users" in c.post("/api/chat", json=think).json()["answer"]


def test_new_accounts_are_capped_per_network_and_site_wide(settings):
    app, _ = app_with(settings, signups_per_ip_per_day=2, site_signups_per_day=3, site_guests_per_day=1)
    c = TestClient(app)
    codes = [c.post("/api/auth/register", json={"username": f"user{i}", "password": "password123"}).status_code
             for i in range(3)]
    assert codes == [200, 200, 429]
    other = TestClient(app, headers={"x-forwarded-for": "203.0.113.9"})
    assert other.post("/api/auth/register", json={"username": "user9", "password": "password123"}).status_code == 200
    assert "all the new accounts" in other.post(
        "/api/auth/register", json={"username": "user10", "password": "password123"}).json()["detail"]
    assert TestClient(app).post("/api/auth/guest").status_code == 200
    assert "guest visits" in TestClient(app, headers={"x-forwarded-for": "198.51.100.7"}).post(
        "/api/auth/guest").json()["detail"]


def test_things_per_user_are_capped(settings, monkeypatch):
    from app.memory import store as store_mod
    from app.memory.store import MemoryStore, MemoryStoreError
    monkeypatch.setattr(store_mod, "MAX_ITEMS", 2)
    s = MemoryStore(settings.data_dir)
    s.upsert("alice", "product", "crib", {"product": "crib"})
    s.upsert("alice", "product", "stroller", {"product": "stroller"})
    s.upsert("alice", "product", "crib", {"brand": "Voomf"})                 # updating one is fine
    with pytest.raises(MemoryStoreError, match="watching 2 products"):
        s.upsert("alice", "product", "heater", {"product": "heater"})
    with pytest.raises(MemoryStoreError, match="watching 2 products"):
        s.save_raw("alice", "product", "heater", "---\nname: heater\nproduct: heater\n---\n")


def test_big_bodies_are_refused_and_pages_get_security_headers(settings):
    app, _ = app_with(settings)
    c = guest(app)
    r = c.post("/api/things/photo", content=b"x" * 16, headers={"content-length": str(20 * 1024 * 1024),
                                                                 "content-type": "application/json"})
    assert r.status_code == 413
    page = c.get("/")
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["x-content-type-options"] == "nosniff"


def test_sign_in_attempts_are_capped_per_network(settings):
    app, _ = app_with(settings, logins_per_ip_per_day=3)
    c = TestClient(app)
    codes = [c.post("/api/auth/login", json={"username": f"nobody{i}", "password": "password123"}).status_code
             for i in range(4)]
    assert codes == [401, 401, 401, 429]


def test_a_slow_nano_call_is_retried_on_super(settings):
    from app.llm.client import ChatResult, LLMTimeout
    from app.llm.router import FAST_TIMEOUT_S, ModelRouter, Tier

    class SlowNano:
        calls = []

        def chat(self, model, messages, **params):
            SlowNano.calls.append((model, params.get("timeout"), params.get("retries")))
            if model == "nano-test":
                raise LLMTimeout("Token Factory didn't answer in time")
            return ChatResult(text="routed", model=model, prompt_tokens=10, completion_tokens=2, latency_ms=5)

    router = ModelRouter(replace(settings, model_balanced="super-test"), client=SlowNano())
    out = router.complete([{"role": "user", "content": "hi"}], Tier.FAST, purpose="agent:route")
    assert out.text == "routed"
    assert SlowNano.calls == [("nano-test", FAST_TIMEOUT_S, 0), ("super-test", None, None)]   # fail fast, then Super
    assert "super-test" in router.usage_summary()


def test_one_network_cannot_use_up_the_site_cap(settings):
    # 28 Oct: 30 guests per network x 30 messages each was enough to use up the whole site's day, locking out judges.
    app, llm = app_with(settings, messages_per_ip_per_day=3)
    here, there = {"X-Forwarded-For": "203.0.113.7"}, {"X-Forwarded-For": "198.51.100.9"}
    a, b = TestClient(app), TestClient(app)
    a.post("/api/auth/guest", headers=here)
    b.post("/api/auth/guest", headers=here)   # a second guest on the same network shares its count
    outcomes = [x.post("/api/chat", json={"message": f"hello {i}"}, headers=here).json()["stop_reason"]
                for i, x in enumerate((a, b, a, b))]
    assert outcomes == ["done", "done", "done", "quota"] and len(llm.calls) == 3
    blocked = a.post("/api/chat", json={"message": "again"}, headers=here).json()["answer"]
    assert "from your network today" in blocked
    other = TestClient(app)
    other.post("/api/auth/guest", headers=there)
    assert other.post("/api/chat", json={"message": "hi"}, headers=there).json()["stop_reason"] == "done"
