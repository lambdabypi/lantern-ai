from __future__ import annotations

import io
import os
import zipfile

import pytest

from app.memory.history import HistoryStore
from app.memory.store import MemoryStore, MemoryStoreError, parse, slugify


@pytest.fixture
def store(settings):
    return MemoryStore(settings.data_dir)


def test_slugify():
    assert slugify("Elden Ring: Shadow of the Erdtree") == "elden-ring-shadow-of-the-erdtree"
    assert slugify("Warframe") == "warframe"
    with pytest.raises(MemoryStoreError):
        slugify("!!!")


def test_upsert_creates_readable_markdown(store, settings):
    store.upsert("alice", "game", "Elden Ring", {"status": "playing", "progress": "at Malenia"},
                 note="Struggling with Waterfowl Dance")
    text = (settings.data_dir / "users/alice/memory/games/elden-ring.md").read_text(encoding="utf-8")
    assert text.startswith("---\n") and "title: Elden Ring" in text and "progress: at Malenia" in text
    assert "Struggling with Waterfowl Dance" in text.split("---", 2)[2]
    item = store.get("alice", "game", "elden-ring")
    assert item.meta["status"] == "playing" and item.meta["source"] == "chat"
    assert {"created", "updated", "confidence"} <= set(item.meta)


def test_upsert_updates_fields_and_appends_notes(store):
    store.upsert("alice", "game", "Warframe", {"status": "playing", "progress": "Earth"}, note="first note")
    store.upsert("alice", "game", "warframe", {"progress": "Saturn"}, note="second note")
    item = store.get("alice", "game", "warframe")
    assert item.meta["progress"] == "Saturn" and item.meta["status"] == "playing"
    assert item.meta["title"] == "Warframe"  # name from the first save is kept
    assert "first note" in item.body and "second note" in item.body


@pytest.mark.parametrize("fields,message", [
    ({"status": "beaten"}, "status must be one of"),
    ({"password": "x"}, "Unknown field"),
    ({"steam_appid": "abc"}, "whole number"),
])
def test_upsert_rejects_bad_fields(store, fields, message):
    with pytest.raises(MemoryStoreError, match=message):
        store.upsert("alice", "game", "Hades", fields)


def test_profile_and_creators(store):
    store.upsert("alice", "profile", None, {"platforms": ["PC", "Switch"], "spoiler_preference": "strict"})
    store.upsert("alice", "creator", "Some Streamer", {"platforms": ["twitch"]})
    assert store.get("alice", "profile").meta["platforms"] == ["PC", "Switch"]
    assert [c.meta["name"] for c in store.list("alice", "creator")] == ["Some Streamer"]


def test_save_raw_validates_hand_edits(store):
    store.upsert("alice", "game", "Hades", {"status": "playing"})
    edited = store.raw("alice", "game", "hades").replace("status: playing", "status: finished")
    assert store.save_raw("alice", "game", "hades", edited).meta["status"] == "finished"
    with pytest.raises(MemoryStoreError, match="front matter"):
        store.save_raw("alice", "game", "hades", "no front matter here")
    with pytest.raises(MemoryStoreError, match="valid YAML"):
        store.save_raw("alice", "game", "hades", "---\ntitle: [unclosed\n---\n")
    with pytest.raises(MemoryStoreError, match="needs a 'title'"):
        store.save_raw("alice", "game", "hades", "---\nstatus: playing\n---\n")


def test_yaml_is_loaded_safely(store):
    evil = "---\ntitle: !!python/object/apply:os.system ['echo hi']\n---\n"
    with pytest.raises(MemoryStoreError):
        parse("game", "x", evil)


@pytest.mark.parametrize("slug", ["../escape", "a/b", "", "UPPER", "x" * 70])
def test_bad_slugs_are_rejected(store, slug):
    with pytest.raises(MemoryStoreError):
        store.get("alice", "game", slug)


@pytest.mark.skipif(os.name == "nt" and not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_symlinked_folder_is_refused(store, settings, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("---\ntitle: secret\n---\n")
    root = settings.data_dir / "users" / "alice" / "memory"
    root.mkdir(parents=True)
    try:
        os.symlink(outside, root / "games", target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks needs extra privileges on this machine")
    with pytest.raises(MemoryStoreError, match="outside"):
        store.get("alice", "game", "secret")


def test_delete(store):
    store.upsert("alice", "game", "Hades", {})
    assert store.delete("alice", "game", "hades") is True
    assert store.get("alice", "game", "hades") is None and store.delete("alice", "game", "hades") is False


def test_users_never_see_each_others_memory(store):
    store.upsert("alice", "game", "Secret Game", {"progress": "alice only"})
    assert store.list("bob", "game") == []
    assert store.get("bob", "game", "secret-game") is None
    assert "Secret Game" not in store.context_for_prompt("bob")


def test_export_zip_has_memory_history_audit_but_no_credentials(store, settings):
    base = settings.data_dir / "users" / "alice"
    store.upsert("alice", "game", "Hades", {"status": "finished"})
    HistoryStore(settings.data_dir).append("alice", [{"role": "user", "content": "hi"}])
    (base / "logs").mkdir(parents=True)
    (base / "logs" / "audit.jsonl").write_text("{}\n")
    (base / "account.json").write_text('{"pw_hash": "scrypt$..."}')
    names = set(zipfile.ZipFile(io.BytesIO(store.export_zip("alice"))).namelist())
    assert names == {"memory/games/hades.md", "history.jsonl", "logs/audit.jsonl"}


def test_context_for_prompt_lists_playing_games_first(store):
    store.upsert("alice", "game", "Zelda", {"status": "backlog"})
    store.upsert("alice", "game", "Warframe", {"status": "playing", "progress": "Saturn"})
    store.upsert("alice", "profile", None, {"spoiler_preference": "strict"})
    context = store.context_for_prompt("alice")
    assert context.index("Warframe") < context.index("Zelda")
    assert "progress: Saturn" in context and "spoilers: strict" in context
    assert store.context_for_prompt("nobody") == "Nothing saved yet."


def test_history_roundtrip_limit_and_clear(settings):
    h = HistoryStore(settings.data_dir)
    for i in range(30):
        h.append("alice", [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": f"a{i}"}])
    recent = h.recent("alice", limit=4)
    assert [m["content"] for m in recent] == ["q28", "a28", "q29", "a29"]
    assert h.recent("bob") == []
    h.clear("alice")
    assert h.recent("alice") == []
