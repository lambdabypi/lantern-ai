from __future__ import annotations

import json
from dataclasses import replace

from fastapi.testclient import TestClient

from app.agent.loop import AgentLoop
from app.agent.recall_planner import NO_CHECK_NOTE, RecallPlanner
from app.llm.client import TokenFactoryClient
from app.llm.router import ModelRouter
from app.main import create_app
from app.memory.store import MemoryStore
from app.recall_watch import RecallWatch
from app.tools.recall_tools import make_recall_tools
from app.tools.registry import ToolContext, ToolRegistry
from tests.conftest import ScriptedLLM
from tests.test_agent_loop import make_loop
from tests.test_recall_watch import FakeClient


def watch_and_store(settings, replies=()):
    llm = ScriptedLLM(list(replies))
    store = MemoryStore(settings.data_dir)
    router = ModelRouter(settings, client=TokenFactoryClient(settings, openai_client=llm))
    return RecallWatch(store, FakeClient(), router, settings.data_dir, plain_summaries=False), store, llm


def plan_with(settings, reply):
    llm = ScriptedLLM([reply])
    router = ModelRouter(settings, client=TokenFactoryClient(settings, openai_client=llm))
    return RecallPlanner(router, lambda uid: "Nothing saved yet.")("alice", "msg", []), llm


# ---- planner --------------------------------------------------------------------------------
def test_planner_saves_owned_things_and_checks(settings):
    plan, llm = plan_with(settings, json.dumps({
        "add": [{"type": "vehicle", "make": "Honda", "model": "Civic", "year": 2018, "vin": ""},
                {"type": "product", "brand": "", "product": "crib", "model": ""},
                {"type": "vehicle", "make": "Ford", "model": "", "year": None}],      # incomplete: dropped
        "check": True, "location": "02139"}))
    assert plan.tool == "recall_check"
    assert plan.args == {"add": [{"type": "vehicle", "make": "Honda", "model": "Civic", "year": 2018},
                                 {"type": "product", "product": "crib"}], "location": "02139"}
    assert llm.calls[0]["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False


def test_planner_small_talk_gets_a_no_facts_note(settings):
    plan, _ = plan_with(settings, json.dumps({"add": [], "check": False, "location": ""}))
    assert plan.tool is None and plan.note == NO_CHECK_NOTE


def test_planner_failure_is_safe(settings):
    plan, _ = plan_with(settings, "sorry, no JSON here")
    assert plan.tool is None and plan.note == NO_CHECK_NOTE


# ---- tool -----------------------------------------------------------------------------------
def test_recall_check_saves_then_reports_urgent_first(settings):
    watch, store, _ = watch_and_store(settings)
    [tool] = make_recall_tools(store, watch)
    out = tool.handler({"add": [{"type": "vehicle", "make": "honda", "model": "Civic", "year": 2018},
                                {"type": "vehicle", "make": "Ford"}], "location": "Boston"},
                       ToolContext(user_id="alice"))
    assert out["saved"] == ["2018 Honda Civic"] and "make, model and year" in out["problems"][0]
    [car] = out["items"]
    assert car["open_recalls"] == 2 and car["recalls"][0]["urgent"].startswith("DO NOT DRIVE")
    assert "not this exact car" in out["how_to_read_this"]
    assert store.get("alice", "profile").meta["location"] == "Boston"


def test_same_vin_updates_instead_of_duplicating(settings):
    watch, store, _ = watch_and_store(settings)
    watch.save_vehicle("alice", vin="1HGCM82633A004352")
    watch.save_vehicle("alice", vin="1hgcm82633a004352", note="new tyres")
    [car] = store.list("alice", "vehicle")
    assert car.meta["vin"] == "1HGCM82633A004352" and "new tyres" in car.body


def test_full_turn_runs_the_planned_check_before_answering(settings):
    watch, store, _ = watch_and_store(settings)
    llm = ScriptedLLM([json.dumps({"add": [{"type": "vehicle", "make": "Honda", "model": "Civic", "year": 2018}],
                                   "check": True, "location": ""}),
                       "Your 2018 Civic has a Do Not Drive recall."])
    router = ModelRouter(replace(settings, model_balanced="super-test"),
                         client=TokenFactoryClient(settings, openai_client=llm))
    loop = AgentLoop(router, ToolRegistry(settings.data_dir, make_recall_tools(store, watch)),
                     profile_context=store.context_for_prompt, planner=RecallPlanner(router, store.context_for_prompt))
    turn = loop.run_turn("alice", "we drive a 2018 Honda Civic")
    assert [c.name for c in turn.tool_calls] == ["recall_check"]
    tool_msg = next(m for m in llm.calls[1]["messages"] if m["role"] == "tool")
    assert "20V314000" in tool_msg["content"] and "DO NOT DRIVE" in tool_msg["content"]
    assert store.get("alice", "vehicle", "2018-honda-civic") is not None


def test_prompt_context_lists_things_without_the_vin(settings):
    _, store, _ = watch_and_store(settings)
    store.upsert("alice", "vehicle", "2018 Honda Civic", {"vin": "1HGCM82633A004352", "make": "Honda"})
    ctx = store.context_for_prompt("alice")
    assert "Car: 2018 Honda Civic (VIN on file)" in ctx and "1HGCM82633A004352" not in ctx


# ---- API ------------------------------------------------------------------------------------
def app_with_watch(settings, replies=(), cron_secret=""):
    watch, store, _ = watch_and_store(settings, replies)
    loop, _ = make_loop(settings, [])
    return create_app(loop=loop, settings=replace(settings, cron_secret=cron_secret), watch=watch), store


def test_add_vehicle_and_product_then_mark_status(settings):
    app, store = app_with_watch(settings, [json.dumps({"matches": [{"recall": "26669", "match": True, "why": "same"}]})])
    c = TestClient(app)
    c.post("/api/auth/guest")
    r = c.post("/api/vehicles", json={"vin": "1HGCM82633A004352"})
    assert r.status_code == 200 and r.json()["recalls"]["open"] == 2
    r = c.post("/api/products", json={"brand": "Voomf", "product": "crib mattress"})
    assert r.json()["recalls"]["open"] == 3
    slug = r.json()["recalls"]["items"][0]["slug"]
    r = c.post("/api/recalls/status", json={"kind": "vehicle", "slug": slug, "recall_id": "20V314000", "status": "fixed"})
    assert r.json()["open"] == 2 and r.json()["urgent"] == 1          # the crib notice says "stop using it"
    crib = next(i for i in r.json()["items"] if i["kind"] == "product")
    assert crib["recalls"][0]["severity"] == "stop_using"
    assert "Recall 20V314000 marked fixed." in c.get(f"/api/memory/vehicle/{slug}").json()["text"]
    r = c.post("/api/recalls/status", json={"kind": "vehicle", "slug": slug, "recall_id": "20V314000", "status": "open"})
    assert r.json()["urgent"] == 2
    profile = c.get("/api/profile").json()
    assert len(profile["vehicles"]) == 1 and len(profile["products"]) == 1


def test_bad_input_gets_readable_errors(settings):
    app, _ = app_with_watch(settings)
    c = TestClient(app)
    c.post("/api/auth/guest")
    assert "17 letters" in c.post("/api/vehicles", json={"vin": "123"}).json()["detail"]
    assert c.post("/api/recalls/status", json={"kind": "vehicle", "slug": "nope", "recall_id": "20V314000",
                                               "status": "fixed"}).status_code == 422
    assert c.post("/api/recalls/status", json={"kind": "game", "slug": "x", "recall_id": "20V314000",
                                               "status": "fixed"}).status_code == 422
    assert TestClient(app).get("/api/recalls").status_code == 401


def test_daily_recall_job_needs_the_secret(settings):
    app, store = app_with_watch(settings, cron_secret="s3cret-value-for-tests")
    c = TestClient(app)
    uid = c.post("/api/auth/guest").json()["user_id"]
    store.upsert(uid, "vehicle", "civic", {"make": "Honda", "model": "Civic", "year": 2018})
    assert c.post("/internal/recalls/run").status_code == 404
    r = c.post("/internal/recalls/run", headers={"X-Lantern-Cron": "s3cret-value-for-tests"})
    assert r.json() == {"users": 1, "open_recalls": 2, "alerts_sent": 0, "expired_guests_removed": 0}


def test_users_cannot_see_each_others_things(settings):
    app, _ = app_with_watch(settings)
    alice, bob = TestClient(app), TestClient(app)
    alice.post("/api/auth/guest")
    bob.post("/api/auth/guest")
    alice.post("/api/vehicles", json={"make": "Honda", "model": "Civic", "year": 2018})
    assert bob.get("/api/recalls").json()["items"] == [] and bob.get("/api/profile").json()["vehicles"] == []
    slug = alice.get("/api/profile").json()["vehicles"][0]["slug"]
    assert bob.get(f"/api/memory/vehicle/{slug}").status_code == 404


def things_app(settings, reply):
    app_watch, store, _ = watch_and_store(settings, [json.dumps({"matches": [{"recall": "26669", "match": True, "why": "same"}]})])
    loop, _ = make_loop(settings, [])
    llm = ScriptedLLM([reply])
    loop.planner = RecallPlanner(ModelRouter(settings, client=TokenFactoryClient(settings, openai_client=llm)))
    return TestClient(create_app(loop=loop, settings=settings, watch=app_watch)), store, llm


def test_what_do_you_own_box_saves_everything_named(settings):
    c, store, llm = things_app(settings, json.dumps({"add": [
        {"type": "vehicle", "make": "Honda", "model": "Civic", "year": 2018},
        {"type": "product", "brand": "Voomf", "product": "crib mattress"}], "check": True, "location": ""}))
    c.post("/api/auth/guest")
    r = c.post("/api/things", json={"text": "our 2018 Honda Civic and a Voomf crib mattress"})
    assert r.status_code == 200 and r.json()["saved"] == ["2018 Honda Civic", "Voomf crib mattress"]
    assert r.json()["recalls"]["open"] == 3
    assert "everything named in it is something they own" in llm.calls[0]["messages"][1]["content"]
    assert len(store.list(c.get("/api/me").json()["user_id"], "product")) == 1


def test_what_do_you_own_box_explains_when_nothing_is_found(settings):
    c, _, _ = things_app(settings, json.dumps({"add": [], "check": False, "location": ""}))
    c.post("/api/auth/guest")
    r = c.post("/api/things", json={"text": "hello there"})
    assert r.status_code == 422 and "couldn't find anything you own" in r.json()["detail"]
    c2, _, _ = things_app(settings, "not json")
    c2.post("/api/auth/guest")
    assert c2.post("/api/things", json={"text": "a crib"}).status_code == 502
