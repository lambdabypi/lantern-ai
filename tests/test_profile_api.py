from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient

from app.agent.loop import AgentLoop
from app.llm.client import TokenFactoryClient
from app.llm.router import ModelRouter
from app.main import create_app
from app.memory.store import MemoryStore
from app.tools.profile_tools import make_profile_tools
from app.tools.registry import ToolRegistry
from tests.conftest import ScriptedLLM, tool_call


def make_app(settings, replies):
    llm = ScriptedLLM(replies)
    memory = MemoryStore(settings.data_dir)
    loop = AgentLoop(ModelRouter(settings, client=TokenFactoryClient(settings, openai_client=llm)),
                     ToolRegistry(settings.data_dir, make_profile_tools(memory)),
                     profile_context=memory.context_for_prompt)
    return create_app(loop=loop, settings=settings), llm


def register(app, name):
    client = TestClient(app)
    assert client.post("/api/auth/register", json={"username": name, "password": "password123"}).status_code == 200
    return client


def test_agent_saves_game_and_it_shows_up_everywhere(settings):
    app, llm = make_app(settings, [
        [tool_call("save_game", {"title": "Elden Ring", "status": "playing", "progress": "at Malenia"})],
        "Saved! Good luck with Malenia.",
        "You're at Malenia.",
    ])
    alice = register(app, "alice")
    alice.post("/api/chat", json={"message": "I'm stuck on Malenia in Elden Ring"})

    games = alice.get("/api/profile").json()["games"]
    assert [(g["title"], g["status"], g["progress"], g["source"]) for g in games] == \
           [("Elden Ring", "playing", "at Malenia", "chat")]
    alice.post("/api/chat", json={"message": "Where am I in Elden Ring?"})
    assert "progress: at Malenia" in llm.calls[2]["messages"][0]["content"]  # next turn's system prompt


def test_crud_edit_and_delete_from_the_ui(settings):
    app, _ = make_app(settings, [])
    alice = register(app, "alice")
    created = alice.post("/api/memory/game", json={"name": "Hades", "fields": {"status": "playing"}}).json()
    assert created["slug"] == "hades" and created["source"] == "user"

    text = alice.get("/api/memory/game/hades").json()["text"]
    edited = text.replace("status: playing", "status: finished")
    assert alice.put("/api/memory/game/hades", json={"text": edited}).json()["status"] == "finished"
    bad = alice.put("/api/memory/game/hades", json={"text": "---\nstatus: beaten\ntitle: Hades\n---\n"})
    assert bad.status_code == 422 and "status must be one of" in bad.json()["detail"]

    assert alice.delete("/api/memory/game/hades").status_code == 200
    assert alice.get("/api/memory/game/hades").status_code == 404
    assert alice.get("/api/profile").json()["games"] == []


def test_profile_memory_is_isolated(settings):
    app, _ = make_app(settings, [])
    alice, bob = register(app, "alice"), register(app, "bob")
    alice.post("/api/memory/game", json={"name": "Secret Game", "fields": {"progress": "alice only"}})
    assert bob.get("/api/profile").json()["games"] == []
    assert bob.get("/api/memory/game/secret-game").status_code == 404
    assert bob.delete("/api/memory/game/secret-game").status_code == 404
    assert alice.get("/api/memory/game/secret-game").status_code == 200  # untouched


def test_bad_kinds_and_slugs(settings):
    app, _ = make_app(settings, [])
    alice = register(app, "alice")
    assert alice.get("/api/memory/passwords/x").status_code == 404
    assert alice.get("/api/memory/game/..%2F..%2Faccount").status_code in (404, 422)
    assert alice.get("/api/memory/game/UPPER").status_code == 422


def test_export_zip(settings):
    app, _ = make_app(settings, ["hello"])
    alice = register(app, "alice")
    alice.post("/api/memory/creator", json={"name": "Some Streamer", "fields": {"platforms": ["twitch"]}})
    alice.post("/api/chat", json={"message": "hi"})
    r = alice.get("/api/export")
    assert r.headers["content-type"] == "application/zip"
    names = set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert {"memory/creators/some-streamer.md", "history.jsonl"} <= names
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


def test_agent_has_no_delete_tool(settings):
    names = [t.name for t in make_profile_tools(MemoryStore(settings.data_dir))]
    assert names == ["save_game", "save_creator", "update_profile", "get_profile"]
    assert not any("delete" in n or "remove" in n for n in names)
