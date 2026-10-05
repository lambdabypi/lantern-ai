from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import tool_call
from tests.test_agent_loop import make_loop


def client_for(settings, replies, sign_in=True):
    """A browser-like client (own cookie jar), signed in as a fresh guest unless sign_in=False."""
    loop, llm = make_loop(settings, replies)
    client = TestClient(create_app(loop=loop, settings=settings))
    if sign_in:
        assert client.post("/api/auth/guest").status_code == 200
    return client, llm


def test_index_serves_chat_page(settings):
    client, _ = client_for(settings, [])
    r = client.get("/")
    assert r.status_code == 200 and "<textarea" in r.text


def test_chat_runs_a_full_turn(settings):
    client, _ = client_for(settings, [[tool_call("calculator", {"expression": "12*7"})], "84"])
    r = client.post("/api/chat", json={"message": "What is 12*7?"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "84" and body["stop_reason"] == "done"
    assert body["tool_calls"][0]["name"] == "calculator" and body["tool_calls"][0]["status"] == "ok"
    assert body["tier"] == "fast" and body["tokens"] == 240


def test_history_carries_over_and_reset_clears_it(settings):
    client, llm = client_for(settings, ["Hi Sam.", "Your name is Sam.", "I don't know your name."])
    client.post("/api/chat", json={"message": "I'm Sam"})
    client.post("/api/chat", json={"message": "What's my name?"})
    second = [m["content"] for m in llm.calls[1]["messages"]]
    assert "I'm Sam" in second and "Hi Sam." in second

    client.post("/api/reset")
    client.post("/api/chat", json={"message": "What's my name?"})
    assert "I'm Sam" not in [m["content"] for m in llm.calls[2]["messages"]]


def test_escalate_flag_routes_to_ultra(settings):
    client, llm = client_for(settings, ["ok"])
    client.post("/api/chat", json={"message": "plan", "escalate": True})
    assert llm.calls[0]["model"] == "ultra-test"


def test_empty_or_huge_messages_are_rejected(settings):
    client, llm = client_for(settings, [])
    assert client.post("/api/chat", json={"message": ""}).status_code == 422
    assert client.post("/api/chat", json={"message": "   "}).status_code == 422
    assert client.post("/api/chat", json={"message": "x" * 4001}).status_code == 422
    assert llm.calls == []
