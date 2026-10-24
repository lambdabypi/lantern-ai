from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient

from app.agent.loop import AgentLoop
from app.llm.client import TokenFactoryClient
from app.llm.router import ModelRouter
from app.main import create_app
from app.memory.store import MemoryStore
from app.tools.recall_tools import make_recall_tools
from app.tools.registry import ToolRegistry
from tests.conftest import ScriptedLLM, tool_call
from tests.test_recall_chat import watch_and_store


def make_app(settings, replies):
    llm = ScriptedLLM(replies)
    watch, memory, _ = watch_and_store(settings)
    loop = AgentLoop(ModelRouter(settings, client=TokenFactoryClient(settings, openai_client=llm)),
                     ToolRegistry(settings.data_dir, make_recall_tools(memory, watch)),
                     profile_context=memory.context_for_prompt)
    return create_app(loop=loop, settings=settings, watch=watch), llm


def register(app, name):
    client = TestClient(app)
    assert client.post("/api/auth/register", json={"username": name, "password": "password123"}).status_code == 200
    return client


def test_agent_saves_a_car_and_it_shows_up_everywhere(settings):
    app, llm = make_app(settings, [
        [tool_call("recall_check", {"add": [{"type": "vehicle", "make": "Honda", "model": "Civic", "year": 2018}]})],
        "Saved. It has a Do Not Drive recall.",
        "You have a 2018 Honda Civic.",
    ])
    alice = register(app, "alice")
    alice.post("/api/chat", json={"message": "We drive a 2018 Honda Civic"})

    cars = alice.get("/api/profile").json()["vehicles"]
    assert [(c["name"], c["source"]) for c in cars] == [("2018 Honda Civic", "chat")]
    alice.post("/api/chat", json={"message": "What do I drive?"})
    assert "Car: 2018 Honda Civic" in llm.calls[2]["messages"][0]["content"]  # next turn's system prompt


def test_crud_edit_and_delete_from_the_ui(settings):
    app, _ = make_app(settings, [])
    alice = register(app, "alice")
    created = alice.post("/api/memory/product", json={"name": "Graco stroller", "fields": {"brand": "Graco"}}).json()
    assert created["slug"] == "graco-stroller" and created["source"] == "user"

    text = alice.get("/api/memory/product/graco-stroller").json()["text"]
    edited = text.replace("brand: Graco", "brand: Graco\nmodel: Modes Pramette")
    assert alice.put("/api/memory/product/graco-stroller", json={"text": edited}).json()["model"] == "Modes Pramette"
    bad = alice.put("/api/memory/product/graco-stroller", json={"text": "---\nname: x\ncolour: red\n---\n"})
    assert bad.status_code == 422 and "Unknown field 'colour'" in bad.json()["detail"]
    bad = alice.put("/api/memory/vehicle/x", json={"text": "---\nname: x\nrecall_status: {20V314000: maybe}\n---\n"})
    assert bad.status_code == 422 and "fixed, not_mine" in bad.json()["detail"]

    assert alice.delete("/api/memory/product/graco-stroller").status_code == 200
    assert alice.get("/api/memory/product/graco-stroller").status_code == 404
    assert alice.get("/api/profile").json()["products"] == []


def test_profile_memory_is_isolated(settings):
    app, _ = make_app(settings, [])
    alice, bob = register(app, "alice"), register(app, "bob")
    alice.post("/api/memory/product", json={"name": "Secret Crib", "fields": {"product": "crib"}})
    assert bob.get("/api/profile").json()["products"] == []
    assert bob.get("/api/memory/product/secret-crib").status_code == 404
    assert bob.delete("/api/memory/product/secret-crib").status_code == 404
    assert alice.get("/api/memory/product/secret-crib").status_code == 200  # untouched


def test_bad_kinds_and_slugs(settings):
    app, _ = make_app(settings, [])
    alice = register(app, "alice")
    assert alice.get("/api/memory/passwords/x").status_code == 404
    assert alice.get("/api/memory/game/x").status_code == 404           # gaming memory is gone
    assert alice.get("/api/memory/vehicle/..%2F..%2Faccount").status_code in (404, 422)
    assert alice.get("/api/memory/vehicle/UPPER").status_code == 422


def test_export_zip(settings):
    app, _ = make_app(settings, ["hello"])
    alice = register(app, "alice")
    alice.post("/api/memory/product", json={"name": "Space heater", "fields": {"product": "space heater"}})
    alice.post("/api/chat", json={"message": "hi"})
    r = alice.get("/api/export")
    assert r.headers["content-type"] == "application/zip"
    names = set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert {"memory/products/space-heater.md", "history.jsonl"} <= names
    assert not any("account" in n for n in names)


def test_history_survives_a_restart_and_a_second_server(settings):
    app1, _ = make_app(settings, ["Noted."])
    alice = register(app1, "alice")
    alice.post("/api/chat", json={"message": "My code word is pineapple."})

    app2, llm2 = make_app(settings, ["Your code word is pineapple."])  # a fresh process, same data dir
    alice2 = TestClient(app2)
    alice2.cookies.update(alice.cookies)
    alice2.post("/api/chat", json={"message": "What is my code word?"})
    assert "My code word is pineapple." in [m["content"] for m in llm2.calls[0]["messages"]]
    assert [m["role"] for m in alice2.get("/api/history").json()] == ["user", "assistant", "user", "assistant"]


def test_agent_cannot_delete_or_mark_recalls(settings):
    watch, memory, _ = watch_and_store(settings)
    names = [t.name for t in make_recall_tools(MemoryStore(settings.data_dir), watch)]
    assert names == ["recall_check"]
    assert not any(w in n for n in names for w in ("delete", "remove", "status", "fixed"))
