"""Step 5 isolation guarantees: one user can never see or act as another."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.auth.sessions import COOKIE_NAME, SessionSigner
from app.config import ConfigError
from app.auth.accounts import AccountStore
from app.main import create_app
from tests.conftest import tool_call
from tests.test_agent_loop import make_loop


@pytest.fixture
def app_and_llm(settings):
    # Plenty of scripted replies; each test consumes what it needs.
    loop, llm = make_loop(settings, ["reply"] * 50)
    return create_app(loop=loop, settings=settings), llm


def signed_in(app, username: str, password: str = "password123") -> TestClient:
    client = TestClient(app)
    r = client.post("/api/auth/register", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return client


def contents(call: dict) -> list[str]:
    return [m.get("content") or "" for m in call["messages"]]


def test_everything_requires_sign_in(app_and_llm):
    app, llm = app_and_llm
    anon = TestClient(app)
    assert anon.get("/").status_code == 200  # the page itself is public
    for method, path, body in [("get", "/api/me", None), ("post", "/api/chat", {"message": "hi"}),
                               ("post", "/api/reset", None), ("get", "/api/tokens", None),
                               ("post", "/api/tokens", {"label": "x"})]:
        r = getattr(anon, method)(path, json=body) if body else getattr(anon, method)(path)
        assert r.status_code == 401, path
    assert llm.calls == []


def test_two_users_never_share_history(app_and_llm):
    app, llm = app_and_llm
    alice, bob = signed_in(app, "alice"), signed_in(app, "bob")
    alice.post("/api/chat", json={"message": "My secret is pineapple."})
    bob.post("/api/chat", json={"message": "What is my secret?"})
    assert "My secret is pineapple." not in contents(llm.calls[1])  # bob's prompt has no alice history
    alice.post("/api/chat", json={"message": "Remind me of my secret"})
    assert "My secret is pineapple." in contents(llm.calls[2])      # alice still has hers
    assert "What is my secret?" not in contents(llm.calls[2])


def test_reset_only_clears_own_history(app_and_llm):
    app, llm = app_and_llm
    alice, bob = signed_in(app, "alice"), signed_in(app, "bob")
    alice.post("/api/chat", json={"message": "alice note"})
    bob.post("/api/chat", json={"message": "bob note"})
    bob.post("/api/reset")
    alice.post("/api/chat", json={"message": "again"})
    assert "alice note" in contents(llm.calls[-1])


def test_audit_logs_are_per_user(settings):
    loop, llm = make_loop(settings, [[tool_call("calculator", {"expression": "1+1"})], "2",
                                     [tool_call("calculator", {"expression": "2+2"})], "4"])
    app = create_app(loop=loop, settings=settings)
    alice, bob = signed_in(app, "alice"), signed_in(app, "bob")
    alice.post("/api/chat", json={"message": "1+1?"})
    bob.post("/api/chat", json={"message": "2+2?"})
    users = settings.data_dir / "users"
    read = lambda u: [json.loads(x) for x in (users / u / "logs" / "audit.jsonl").read_text().splitlines()]  # noqa: E731
    assert [(e["user_id"], e["args"]["expression"]) for e in read("alice")] == [("alice", "1+1")]
    assert [(e["user_id"], e["args"]["expression"]) for e in read("bob")] == [("bob", "2+2")]


def test_guests_are_isolated_from_each_other(app_and_llm):
    app, llm = app_and_llm
    g1, g2 = TestClient(app), TestClient(app)
    id1 = g1.post("/api/auth/guest").json()["user_id"]
    id2 = g2.post("/api/auth/guest").json()["user_id"]
    assert id1 != id2 and id1.startswith("guest-")
    g1.post("/api/chat", json={"message": "guest one private"})
    g2.post("/api/chat", json={"message": "hello"})
    assert "guest one private" not in contents(llm.calls[1])


def test_tampered_or_forged_cookies_are_rejected(app_and_llm, settings):
    app, _ = app_and_llm
    alice = signed_in(app, "alice")
    signed_in(app, "bob")
    cookie = alice.cookies.get(COOKIE_NAME)
    payload, sig = cookie.split(".")

    for bad in [payload + "x." + sig, payload + "." + sig[:-2] + "AA",
                SessionSigner("another-secret-key-of-sufficient-len").issue("bob", 3600),
                "not-a-cookie"]:
        attacker = TestClient(app)
        attacker.cookies.set(COOKIE_NAME, bad)
        assert attacker.get("/api/me").status_code == 401

    # A correctly signed cookie for an account that doesn't exist is also refused.
    ghost = TestClient(app)
    ghost.cookies.set(COOKIE_NAME, SessionSigner(settings.app_secret_key).issue("nobody", 3600))
    assert ghost.get("/api/me").status_code == 401


def test_tokens_act_only_as_their_owner(app_and_llm):
    app, llm = app_and_llm
    alice, bob = signed_in(app, "alice"), signed_in(app, "bob")
    issued = alice.post("/api/tokens", json={"label": "laptop"}).json()
    desktop = TestClient(app)
    headers = {"Authorization": f"Bearer {issued['token']}"}
    assert desktop.get("/api/me", headers=headers).json()["user_id"] == "alice"
    assert bob.get("/api/tokens").json() == []                       # bob can't see alice's tokens
    assert bob.delete(f"/api/tokens/{issued['id']}").status_code == 404  # ...or revoke them

    assert alice.delete(f"/api/tokens/{issued['id']}").status_code == 200
    assert desktop.get("/api/me", headers=headers).status_code == 401
    assert desktop.get("/api/me", headers={"Authorization": "Bearer pat_alice.guess"}).status_code == 401


def test_expired_guest_loses_access_and_data(app_and_llm, settings):
    app, _ = app_and_llm
    guest = TestClient(app)
    user_id = guest.post("/api/auth/guest").json()["user_id"]
    guest.post("/api/chat", json={"message": "hi"})
    account_file = settings.data_dir / "users" / user_id / "account.json"
    data = json.loads(account_file.read_text())
    data["expires_ts"] = 1.0  # long past
    account_file.write_text(json.dumps(data))

    assert guest.get("/api/me").status_code == 401
    # The folder goes in the next sweep (at most hourly when guests are created, and in the daily job)
    AccountStore(settings.data_dir).create_guest()
    assert not (settings.data_dir / "users" / user_id).exists()


def test_guest_ultra_quota(settings):
    loop, llm = make_loop(settings, ["ok"] * 5)
    app = create_app(loop=loop, settings=replace(settings, guest_ultra_per_day=2))
    guest = TestClient(app)
    assert guest.post("/api/auth/guest").json()["ultra_left_today"] == 2
    for _ in range(2):
        assert guest.post("/api/chat", json={"message": "plan", "escalate": True}).json()["stop_reason"] == "done"
    blocked = guest.post("/api/chat", json={"message": "plan", "escalate": True}).json()
    assert blocked["stop_reason"] == "quota" and "Take more time to think" in blocked["answer"]
    assert len(llm.calls) == 2                                   # the blocked turn never reached the model
    assert guest.post("/api/chat", json={"message": "fast is fine"}).json()["stop_reason"] == "done"
    assert guest.get("/api/me").json()["ultra_left_today"] == 0


def test_registered_users_have_no_ultra_cap(settings):
    loop, llm = make_loop(settings, ["ok"] * 5)
    app = create_app(loop=loop, settings=replace(settings, guest_ultra_per_day=1))
    alice = signed_in(app, "alice")
    for _ in range(3):
        assert alice.post("/api/chat", json={"message": "plan", "escalate": True}).json()["stop_reason"] == "done"
    assert alice.get("/api/me").json()["ultra_left_today"] is None


def test_login_errors_are_clear(app_and_llm):
    app, _ = app_and_llm
    signed_in(app, "alice")
    client = TestClient(app)
    r = client.post("/api/auth/login", json={"username": "alice", "password": "wrong-password"})
    assert r.status_code == 401 and r.json()["detail"] == "Wrong username or password."
    r = client.post("/api/auth/register", json={"username": "alice", "password": "password123"})
    assert r.status_code == 409 and "taken" in r.json()["detail"]


def test_logout_ends_session(app_and_llm):
    app, _ = app_and_llm
    alice = signed_in(app, "alice")
    alice.post("/api/auth/logout")
    assert alice.get("/api/me").status_code == 401


def test_app_refuses_to_start_without_a_strong_secret(settings):
    loop, _ = make_loop(settings, [])
    with pytest.raises(ConfigError, match="APP_SECRET_KEY"):
        create_app(loop=loop, settings=replace(settings, app_secret_key=""))
    with pytest.raises(ConfigError, match="32 characters"):
        create_app(loop=loop, settings=replace(settings, app_secret_key="short"))
