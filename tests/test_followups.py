from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.followups import FollowUps, daily_alerts, due
from app.main import create_app
from tests.test_agent_loop import make_loop
from tests.test_recall_chat import watch_and_store

TODAY = date(2026, 11, 20)


def report(severity="park_it", progress=None, status="open"):
    return {"items": [{"kind": "vehicle", "slug": "ranger", "name": "2006 Ford Ranger", "recalls": [
        {"id": "18V022000", "status": status, "severity": severity, "title": "AIR BAGS",
         "plain": {"title": "Passenger air bag may explode"}, "progress": progress}]}]}


def test_urgent_recall_with_nothing_booked_gets_a_nudge_after_three_days():
    first, state = due(report(), {}, TODAY)
    assert first is None                                        # day one: the new-recall alert covers it
    reminder, state = due(report(), state, TODAY + timedelta(days=3))
    assert reminder[1] == "Do not drive: 2006 Ford Ranger" and "free" in reminder[2]
    again, state = due(report(), state, TODAY + timedelta(days=5))
    assert again is None                                        # at most once a week
    assert due(report(), state, TODAY + timedelta(days=10))[0] is not None


def test_contacted_and_booked_get_their_own_check_ins():
    contacted = {"step": "contacted", "since": (TODAY - timedelta(days=8)).isoformat()}
    assert due(report(progress=contacted), {}, TODAY)[0][1].startswith("Did you hear back?")
    booked = {"step": "booked", "since": (TODAY - timedelta(days=13)).isoformat()}
    assert due(report(progress=booked), {}, TODAY)[0] is None   # not yet
    booked["since"] = (TODAY - timedelta(days=15)).isoformat()
    reminder, _ = due(report(progress=booked), {}, TODAY)
    assert reminder[1] == "Was it repaired? 2006 Ford Ranger" and "15 days ago" in reminder[2]


def test_fixed_recalls_are_forgotten_and_ordinary_ones_never_nag():
    _, state = due(report(), {}, TODAY)
    assert due(report(status="fixed"), state, TODAY + timedelta(days=9)) == (None, {})
    assert due(report(severity=""), {}, TODAY + timedelta(days=30))[0] is None


class FakePush:
    def __init__(self, new=0):
        self.sent, self.new = [], new

    def enabled_for(self, uid):
        return True

    def send(self, uid, title, body, url):
        self.sent.append(title)
        return 1

    def announce_new_recalls(self, uid, rep):
        return self.new


def test_a_check_in_only_on_days_without_a_new_recall_alert(settings):
    push = FakePush(new=1)
    alerts = daily_alerts(push, FollowUps(settings.data_dir, push))
    assert alerts("alice", report()) == 1 and push.sent == []
    push.new = 0
    f = FollowUps(settings.data_dir, push)
    f.remind("alice", report(), TODAY)
    assert f.remind("alice", report(), TODAY + timedelta(days=3)) == 1 and push.sent == ["Do not drive: 2006 Ford Ranger"]


def test_progress_endpoint_saves_in_the_file_and_fixing_clears_it(settings):
    watch, store, _ = watch_and_store(settings)
    loop, _ = make_loop(settings, [])
    c = TestClient(create_app(loop=loop, settings=settings, watch=watch))
    c.post("/api/auth/guest")
    slug = c.post("/api/vehicles", json={"make": "Honda", "model": "Civic", "year": 2018}).json()["item"]["slug"]
    r = c.post("/api/recalls/progress", json={"kind": "vehicle", "slug": slug, "recall_ids": ["20V314000"], "step": "booked"})
    rc = next(x for x in r.json()["items"][0]["recalls"] if x["id"] == "20V314000")
    assert rc["progress"] == {"step": "booked", "since": date.today().isoformat()}
    text = c.get(f"/api/memory/vehicle/{slug}").json()["text"]
    assert f"20V314000: booked {date.today().isoformat()}" in text and "Booked the free fix for recall 20V314000." in text
    c.post("/api/recalls/status", json={"kind": "vehicle", "slug": slug, "recall_id": "20V314000", "status": "fixed"})
    assert "20V314000: booked" not in c.get(f"/api/memory/vehicle/{slug}").json()["text"]
    bad = c.post("/api/recalls/progress", json={"kind": "vehicle", "slug": slug, "recall_ids": ["99V999000"], "step": "booked"})
    assert bad.status_code == 404
