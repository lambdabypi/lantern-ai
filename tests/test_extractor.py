from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.llm.client import LLMError, TokenFactoryClient
from app.llm.router import ModelRouter
from app.main import create_app
from app.memory.extractor import MemoryExtractor
from app.memory.store import MemoryStore
from tests.conftest import ScriptedLLM
from tests.test_agent_loop import make_loop


def extractor_with(settings, replies, known_sources=None):
    llm = ScriptedLLM(replies)
    store = MemoryStore(settings.data_dir)
    router = ModelRouter(settings, client=TokenFactoryClient(settings, openai_client=llm))
    return MemoryExtractor(router, store, known_sources), store, llm


EXTRACTED = {
    "games": [{"title": "Warframe", "status": "playing", "progress": "Saturn", "platform": "PC"},
              {"title": "Hades", "status": "beaten", "progress": "", "platform": ""}],
    "creators": [{"name": "PixelPaladin", "platforms": ["twitch"]}],
    "platforms": ["PC", "Switch"],
}


def test_saves_games_creators_and_platforms(settings):
    ex, store, llm = extractor_with(settings, [json.dumps(EXTRACTED)], known_sources=lambda t: ["wiki.warframe.com"] if t == "Warframe" else [])
    saved = ex.run("alice", "I'm on Saturn in Warframe on PC, also finished Hades on Switch. I watch PixelPaladin on Twitch.")
    assert saved == ["game:warframe", "game:hades", "creator:pixelpaladin", "profile:profile"]
    wf = store.get("alice", "game", "warframe").meta
    assert (wf["status"], wf["progress"], wf["platform"], wf["sources"]) == ("playing", "Saturn", "PC", ["wiki.warframe.com"])
    assert "status" not in store.get("alice", "game", "hades").meta  # invalid status dropped, game still saved
    assert store.get("alice", "profile").meta["platforms"] == ["PC", "Switch"]
    call = llm.calls[0]
    assert call["model"] == "nano-test" and call["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False


def test_platforms_are_merged_not_replaced(settings):
    ex, store, _ = extractor_with(settings, [json.dumps({"games": [], "creators": [], "platforms": ["PS5"]})])
    store.upsert("alice", "profile", None, {"platforms": ["PC"]})
    ex.run("alice", "I also play on my PS5 these days")
    assert store.get("alice", "profile").meta["platforms"] == ["PC", "PS5"]


def test_bad_output_and_failures_are_harmless(settings):
    ex, store, _ = extractor_with(settings, ["not json at all", json.dumps(["a", "list"])])
    assert ex.run("alice", "I play Warframe on PC") == []
    assert ex.run("alice", "I play Warframe on PC") == []
    assert store.list("alice", "game") == []

    class Broken:
        def complete(self, *a, **k):
            raise LLMError("down")
    assert MemoryExtractor(Broken(), store).run("alice", "I play Warframe on PC") == []

    class Exploding:
        def complete(self, *a, **k):
            raise RuntimeError("unexpected")
    assert MemoryExtractor(Exploding(), store).run("alice", "I play Warframe on PC") == []  # logged, not raised


def test_short_messages_are_skipped(settings):
    ex, _, llm = extractor_with(settings, [])
    assert ex.run("alice", "thanks!") == [] and llm.calls == []


def test_chat_endpoint_saves_in_the_background_after_replying(settings):
    loop, chat_llm = make_loop(settings, ["Saturn has great Orokin Cell farms."])
    ex, _, _ = extractor_with(settings, [json.dumps({"games": [{"title": "Warframe", "progress": "Saturn"}],
                                                     "creators": [], "platforms": []})])
    client = TestClient(create_app(loop=loop, settings=settings, extractor=ex))
    client.post("/api/auth/guest")
    reply = client.post("/api/chat", json={"message": "I'm on Saturn in Warframe, where are Orokin Cells?"}).json()
    assert reply["answer"] == "Saturn has great Orokin Cell farms."
    games = client.get("/api/profile").json()["games"]
    assert [(g["title"], g["progress"], g["source"]) for g in games] == [("Warframe", "Saturn", "chat")]


def test_platforms_must_actually_be_named(settings):
    guessed = {"games": [{"title": "Warframe", "progress": "Saturn", "platform": "PC"}], "creators": [],
               "platforms": ["PC"]}
    ex, store, _ = extractor_with(settings, [json.dumps(guessed)])
    ex.run("alice", "I'm on Saturn in Warframe. Where should I farm Orokin Cells?")
    assert "platform" not in store.get("alice", "game", "warframe").meta
    assert store.get("alice", "profile") is None


def test_extractor_asks_for_sources_for_newly_saved_games(settings):
    seen = []
    llm_reply = json.dumps({"games": [{"title": "Hades", "status": "playing"}], "creators": [], "platforms": []})
    ex, _, _ = extractor_with(settings, [llm_reply])
    ex.on_games_saved = lambda uid, titles: seen.append((uid, titles))
    ex.run("alice", "I just started Hades")
    assert seen == [("alice", ["Hades"])]
