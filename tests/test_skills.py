from __future__ import annotations

import yaml
from fastapi.testclient import TestClient

from app.followups import daily_alerts
from app.main import create_app
from app.memory.store import MemoryStore
from app.skill_files import SKILLS_DIR, Skills, load_skills
from app.tools.registry import Tool, ToolContext
from tests.test_agent_loop import make_loop
from tests.test_recall_chat import watch_and_store


def test_every_skill_has_a_valid_skill_md():
    skills = load_skills()
    assert {"recall-watch", "unit-check", "owner-reports", "check-before-buying", "receipts", "makers-pages",
            "book-the-fix", "follow-ups"} <= set(skills)
    for s in skills.values():
        front = yaml.safe_load(s.text.split("---")[1])
        assert front["name"] == s.id and len(front["description"]) > 30 and s.sends and s.uses
        assert (SKILLS_DIR / s.id / "SKILL.md").exists()
    assert skills["recall-watch"].required and not skills["unit-check"].required
    assert skills["check-before-buying"].tools == ("check_before_buying",)


def test_turning_a_skill_off_turns_off_its_tool(settings):
    store = MemoryStore(settings.data_dir)
    skills = Skills(store)
    tool = skills.guard_tool(Tool(name="ask_maker", description="", parameters={}, handler=lambda a, c: {"ok": 1}))
    assert tool.handler({}, ToolContext("alice")) == {"ok": 1}
    skills.set("alice", "makers-pages", False)
    assert "turned this skill off" in tool.handler({}, ToolContext("alice"))["error"]
    assert tool.handler({}, ToolContext("bob")) == {"ok": 1}                          # only for that user
    assert store.get("alice", "profile").meta["skills_off"] == ["makers-pages"]      # visible in their profile file


def test_skills_api_and_endpoint_guards(settings):
    watch, store, _ = watch_and_store(settings)
    loop, _ = make_loop(settings, [])
    c = TestClient(create_app(loop=loop, settings=settings, watch=watch))
    c.post("/api/auth/guest")
    slug = c.post("/api/vehicles", json={"make": "Honda", "model": "Civic", "year": 2018}).json()["item"]["slug"]
    listing = {s["id"]: s for s in c.get("/api/skills").json()}
    assert listing["unit-check"]["on"] and listing["recall-watch"]["required"]
    off = {s["id"]: s["on"] for s in c.post("/api/skills", json={"id": "unit-check", "on": False}).json()}
    assert off["unit-check"] is False
    r = c.post("/api/recalls/unit/guide", json={"kind": "vehicle", "slug": slug, "recall_id": "20V314000"})
    assert r.status_code == 403 and "turned off" in r.json()["detail"]
    assert c.post("/api/skills", json={"id": "recall-watch", "on": False}).status_code == 403
    assert c.post("/api/skills", json={"id": "nope", "on": False}).status_code == 404
    assert "# Is mine affected?" in c.get("/api/skills/unit-check").json()["text"]
    c.post("/api/skills", json={"id": "unit-check", "on": True})
    assert c.post("/api/recalls/unit/guide", json={"kind": "vehicle", "slug": slug, "recall_id": "20V314000"}).status_code != 403


def test_follow_ups_stop_when_the_skill_is_off(settings):
    class Push:
        def announce_new_recalls(self, uid, report):
            return 0

    class Follow:
        calls = 0

        def remind(self, uid, report):
            Follow.calls += 1
            return 1

    skills = Skills(MemoryStore(settings.data_dir))
    alerts = daily_alerts(Push(), Follow(), skills)
    assert alerts("alice", {}) == 1
    skills.set("alice", "follow-ups", False)
    assert alerts("alice", {}) == 0 and Follow.calls == 1
