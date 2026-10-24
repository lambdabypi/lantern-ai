from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pywebpush import WebPushException

import app.push as push_module
from app.main import create_app
from app.push import Push, PushError, generate_key
from tests.test_agent_loop import make_loop
from tests.test_recall_chat import watch_and_store

KEY = generate_key()
SUB = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc123", "keys": {"p256dh": "BPk" + "a" * 80, "auth": "x" * 22}}


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def fake_webpush(sub, data=None, **kw):
        calls.append((sub["endpoint"], json.loads(data), kw))
        if "gone" in sub["endpoint"]:
            raise WebPushException("gone", response=SimpleNamespace(status_code=410))
    monkeypatch.setattr(push_module, "webpush", fake_webpush)
    return calls


def test_public_key_is_derived_for_the_browser(settings):
    p = Push(settings.data_dir, KEY, "https://lantern.example")
    assert len(p.public_key) == 87 and "=" not in p.public_key   # uncompressed P-256 point, base64url


@pytest.mark.parametrize("endpoint", ["http://fcm.googleapis.com/x", "https://169.254.169.254/latest/meta-data",
                                      "https://evil.example/push", "https://fcm.googleapis.com.evil.example/x"])
def test_only_known_push_services_over_https(settings, endpoint):
    p = Push(settings.data_dir, KEY, "https://lantern.example")
    with pytest.raises(PushError):
        p.subscribe("alice", {**SUB, "endpoint": endpoint})


def test_send_and_forget_dead_subscriptions(settings, sent):
    p = Push(settings.data_dir, KEY, "https://lantern.example")
    p.subscribe("alice", SUB)
    p.subscribe("alice", {**SUB, "endpoint": "https://updates.push.services.mozilla.com/wpush/v2/gone"})
    assert p.send("alice", "Hi", "Body") == 1
    assert p.enabled_for("alice") and len(p._load("alice")["subscriptions"]) == 1   # 410 removed
    assert sent[0][2]["vapid_claims"] == {"sub": "https://lantern.example"}


def test_new_recalls_are_announced_once_urgent_first(settings, sent):
    p = Push(settings.data_dir, KEY, "https://lantern.example")
    p.subscribe("alice", SUB)
    report = {"items": [{"slug": "civic", "name": "2018 Honda Civic", "recalls": [
        {"id": "26V1", "title": "Air Bags", "new": True, "status": "open", "severity": ""},
        {"id": "26V2", "title": "Fuel Pump", "new": True, "status": "open", "severity": "park_it"},
        {"id": "18V1", "title": "Labels", "new": False, "status": "open", "severity": ""}]}]}
    assert p.announce_new_recalls("alice", report) == 1
    payload = sent[-1][1]
    assert payload["title"] == "Do not drive: 2018 Honda Civic" and "+1 more" in payload["body"]
    assert p.announce_new_recalls("alice", report) == 0 and len(sent) == 1         # not repeated tomorrow


def test_api_subscribe_sends_a_test_and_daily_job_alerts(settings, sent):
    watch, store, _ = watch_and_store(settings)
    loop, _ = make_loop(settings, [])
    s = replace(settings, vapid_private_key=KEY, cron_secret="s3cret-value-for-tests")
    c = TestClient(create_app(loop=loop, settings=s, watch=watch))
    uid = c.post("/api/auth/guest").json()["user_id"]
    info = c.get("/api/push").json()
    assert info["available"] and not info["enabled"] and info["public_key"]
    assert c.post("/api/push/subscribe", json=SUB).json() == {"ok": True, "test_sent": 1}
    assert c.post("/api/push/subscribe", json={**SUB, "endpoint": "https://evil.example/x"}).status_code == 422
    store.upsert(uid, "vehicle", "civic", {"make": "Honda", "model": "Civic", "year": 2018})
    c.post("/internal/recalls/run", headers={"X-Lantern-Cron": "s3cret-value-for-tests"})   # baseline: no alert
    watch.client.vehicle.append(watch.client.vehicle[0].__class__(
        campaign="26V900000", component="BRAKES", summary="s", consequence="c", remedy="r", reported="2026-10-24",
        park_it=False, park_outside=False, over_the_air=False, url="u"))
    for f in (settings.data_dir / "shared_cache" / "recalls").glob("*.json"):
        f.unlink()
    r = c.post("/internal/recalls/run", headers={"X-Lantern-Cron": "s3cret-value-for-tests"})
    assert r.json()["alerts_sent"] == 1 and sent[-1][1]["title"] == "New recall: civic"


def test_push_off_without_a_key(settings):
    loop, _ = make_loop(settings, [])
    c = TestClient(create_app(loop=loop, settings=settings))
    c.post("/api/auth/guest")
    assert c.get("/api/push").json()["available"] is False
    assert c.post("/api/push/subscribe", json=SUB).status_code == 503


def test_bad_key_stops_startup_with_a_clear_message(settings):
    from app.config import ConfigError
    with pytest.raises(ConfigError, match="python -m app.push"):
        Push(settings.data_dir, "not a key", "https://lantern.example")
