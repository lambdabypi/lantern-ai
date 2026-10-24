from __future__ import annotations

import json
from dataclasses import replace

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import tool_call
from tests.test_agent_loop import make_loop


def sse_events(response) -> list[dict]:
    return [json.loads(line[len("data: "):]) for line in response.text.splitlines() if line.startswith("data: ")]


def signed_in(app) -> TestClient:
    c = TestClient(app)
    c.post("/api/auth/guest")
    return c


def test_stream_sends_status_then_text_then_done(settings):
    loop, _ = make_loop(settings, [[tool_call("calculator", {"expression": "12*7"})], "12 × 7 = 84."])
    c = signed_in(create_app(loop=loop, settings=settings))
    r = c.post("/api/chat/stream", json={"message": "12*7?"})
    assert r.headers["content-type"].startswith("text/event-stream")
    events = sse_events(r)
    assert [e["type"] for e in events] == ["status", "delta", "delta", "done"]
    assert events[0]["text"] == "Calculating…"
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "12 × 7 = 84."
    done = events[-1]
    assert done["answer"] == "12 × 7 = 84." and done["tool_calls"][0]["name"] == "calculator"
    assert [m["content"] for m in c.get("/api/history").json()] == ["12*7?", "12 × 7 = 84."]


def test_stream_respects_the_guest_ultra_quota(settings):
    loop, llm = make_loop(settings, ["ok"])
    c = signed_in(create_app(loop=loop, settings=replace(settings, guest_ultra_per_day=0)))
    events = sse_events(c.post("/api/chat/stream", json={"message": "plan", "escalate": True}))
    assert [e["type"] for e in events] == ["done"] and events[0]["stop_reason"] == "quota"
    assert llm.calls == []


def test_stream_reports_unexpected_failures_instead_of_hanging(settings):
    loop, _ = make_loop(settings, [])

    def boom(*a, **k):
        raise RuntimeError("bug")
    loop.run_turn = boom
    c = signed_in(create_app(loop=loop, settings=settings))
    events = sse_events(c.post("/api/chat/stream", json={"message": "hi"}))
    assert events == [{"type": "error", "text": "Something went wrong on our side. Please try again."}]


def test_stream_requires_sign_in_and_a_message(settings):
    loop, _ = make_loop(settings, [])
    app = create_app(loop=loop, settings=settings)
    assert TestClient(app).post("/api/chat/stream", json={"message": "hi"}).status_code == 401
    assert signed_in(app).post("/api/chat/stream", json={"message": "  "}).status_code == 422
