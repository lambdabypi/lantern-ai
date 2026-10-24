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
    assert slugify("2018 Honda Civic: Hatchback") == "2018-honda-civic-hatchback"
    assert slugify("Crib") == "crib"
    with pytest.raises(MemoryStoreError):
        slugify("!!!")


def test_upsert_creates_readable_markdown(store, settings):
    store.upsert("alice", "vehicle", "2018 Honda Civic", {"make": "Honda", "model": "Civic", "year": 2018},
                 note="Bought used in 2022")
    text = (settings.data_dir / "users/alice/memory/vehicles/2018-honda-civic.md").read_text(encoding="utf-8")
    assert text.startswith("---\n") and "name: 2018 Honda Civic" in text and "year: 2018" in text
    assert "Bought used in 2022" in text.split("---", 2)[2]
    item = store.get("alice", "vehicle", "2018-honda-civic")
    assert item.meta["make"] == "Honda" and item.meta["source"] == "chat"
    assert {"created", "updated", "confidence"} <= set(item.meta)


def test_upsert_updates_fields_and_appends_notes(store):
    store.upsert("alice", "product", "Graco stroller", {"brand": "Graco", "product": "stroller"}, note="first note")
    store.upsert("alice", "product", "graco-stroller", {"model": "Modes"}, note="second note")
    item = store.get("alice", "product", "graco-stroller")
    assert item.meta["model"] == "Modes" and item.meta["brand"] == "Graco"
    assert item.meta["name"] == "Graco stroller"  # name from the first save is kept
    assert "first note" in item.body and "second note" in item.body


@pytest.mark.parametrize("fields,message", [
    ({"recall_status": {"20V314000": "maybe"}}, "fixed, not_mine"),
    ({"recall_status": {"../x": "fixed"}}, "doesn't look like a recall number"),
    ({"password": "x"}, "Unknown field"),
    ({"year": "soon"}, "whole number"),
])
def test_upsert_rejects_bad_fields(store, fields, message):
    with pytest.raises(MemoryStoreError, match=message):
        store.upsert("alice", "vehicle", "Civic", fields)


def test_recall_status_keys_are_normalised(store):
    item = store.upsert("alice", "product", "Crib", {"recall_status": {"26669": "fixed", "20v314000": "not_mine"}})
    assert item.meta["recall_status"] == {"26669": "fixed", "20V314000": "not_mine"}
    # CPSC numbers look like integers; they must survive a round trip through YAML as strings
    assert store.get("alice", "product", "crib").meta["recall_status"]["26669"] == "fixed"


def test_profile_holds_only_location(store):
    store.upsert("alice", "profile", None, {"location": "Boston, MA"})
    assert store.get("alice", "profile").meta["location"] == "Boston, MA"
    with pytest.raises(MemoryStoreError, match="Unknown field"):
        store.upsert("alice", "profile", None, {"platforms": ["PC"]})


def test_save_raw_validates_hand_edits(store):
    store.upsert("alice", "product", "Crib", {"product": "crib"})
    edited = store.raw("alice", "product", "crib").replace("product: crib", "product: crib\nbrand: Voomf")
    assert store.save_raw("alice", "product", "crib", edited).meta["brand"] == "Voomf"
    with pytest.raises(MemoryStoreError, match="front matter"):
        store.save_raw("alice", "product", "crib", "no front matter here")
    with pytest.raises(MemoryStoreError, match="valid YAML"):
        store.save_raw("alice", "product", "crib", "---\nname: [unclosed\n---\n")
    with pytest.raises(MemoryStoreError, match="needs a 'name'"):
        store.save_raw("alice", "product", "crib", "---\nbrand: Voomf\n---\n")


def test_yaml_is_loaded_safely(store):
    evil = "---\nname: !!python/object/apply:os.system ['echo hi']\n---\n"
    with pytest.raises(MemoryStoreError):
        parse("vehicle", "x", evil)


def test_unknown_kinds_are_refused(store):
    for kind in ("game", "creator", "account"):
        with pytest.raises(MemoryStoreError, match="Unknown memory type"):
            store.get("alice", kind, "x")


@pytest.mark.parametrize("slug", ["../escape", "a/b", "", "UPPER", "x" * 70])
def test_bad_slugs_are_rejected(store, slug):
    with pytest.raises(MemoryStoreError):
        store.get("alice", "vehicle", slug)


@pytest.mark.skipif(os.name == "nt" and not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_symlinked_folder_is_refused(store, settings, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("---\nname: secret\n---\n")
    root = settings.data_dir / "users" / "alice" / "memory"
    root.mkdir(parents=True)
    try:
        os.symlink(outside, root / "vehicles", target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks needs extra privileges on this machine")
    with pytest.raises(MemoryStoreError, match="outside"):
        store.get("alice", "vehicle", "secret")


def test_delete(store):
    store.upsert("alice", "product", "Crib", {})
    assert store.delete("alice", "product", "crib") is True
    assert store.get("alice", "product", "crib") is None and store.delete("alice", "product", "crib") is False


def test_users_never_see_each_others_memory(store):
    store.upsert("alice", "vehicle", "Secret Car", {"make": "Honda"})
    assert store.list("bob", "vehicle") == []
    assert store.get("bob", "vehicle", "secret-car") is None
    assert "Secret Car" not in store.context_for_prompt("bob")


def test_export_zip_has_memory_history_audit_but_no_credentials(store, settings):
    base = settings.data_dir / "users" / "alice"
    store.upsert("alice", "product", "Crib", {"product": "crib"})
    HistoryStore(settings.data_dir).append("alice", [{"role": "user", "content": "hi"}])
    (base / "logs").mkdir(parents=True)
    (base / "logs" / "audit.jsonl").write_text("{}\n")
    (base / "account.json").write_text('{"pw_hash": "scrypt$..."}')
    names = set(zipfile.ZipFile(io.BytesIO(store.export_zip("alice"))).namelist())
    assert names == {"memory/products/crib.md", "history.jsonl", "logs/audit.jsonl"}


def test_context_for_prompt_lists_location_cars_and_products(store):
    store.upsert("alice", "profile", None, {"location": "Boston, MA"})
    store.upsert("alice", "vehicle", "2018 Honda Civic", {"recall_status": {"20V314000": "fixed"}})
    store.upsert("alice", "product", "Voomf crib", {"product": "crib"})
    context = store.context_for_prompt("alice")
    assert context.splitlines() == ["Location: Boston, MA",
                                    "- Car: 2018 Honda Civic (1 recall(s) marked fixed or not theirs)",
                                    "- Product: Voomf crib"]
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
