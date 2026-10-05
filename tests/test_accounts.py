from __future__ import annotations

import json

import pytest

from app.auth.accounts import AccountStore, AuthError, hash_password, verify_password


class Clock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(settings, clock):
    return AccountStore(settings.data_dir, guest_ttl_days=7, clock=clock)


def test_password_hash_roundtrip_and_salt():
    h1, h2 = hash_password("correct horse"), hash_password("correct horse")
    assert h1 != h2 and h1.startswith("scrypt$")
    assert verify_password("correct horse", h1)
    assert not verify_password("wrong horse", h1)
    assert not verify_password("x", "garbage") and not verify_password("x", None)


def test_register_and_authenticate(store, settings):
    store.register("Alice", "password123")
    stored = json.loads((settings.data_dir / "users" / "alice" / "account.json").read_text())
    assert stored["kind"] == "user" and "password123" not in json.dumps(stored)
    assert store.authenticate("alice", "password123").user_id == "alice"
    with pytest.raises(AuthError, match="Wrong username or password") as e:
        store.authenticate("alice", "nope-nope")
    assert e.value.status == 401


@pytest.mark.parametrize("username,password,message", [
    ("../etc", "password123", "Usernames"),
    ("guest-abc", "password123", "reserved"),
    ("demo", "password123", "reserved"),
    ("bob", "short", "Passwords"),
])
def test_register_rejects_bad_input(store, username, password, message):
    with pytest.raises(AuthError, match=message):
        store.register(username, password)


def test_duplicate_username(store):
    store.register("carol", "password123")
    with pytest.raises(AuthError, match="taken") as e:
        store.register("CAROL", "password456")
    assert e.value.status == 409


def test_login_throttle(store):
    store.register("dave", "password123")
    for _ in range(5):
        with pytest.raises(AuthError):
            store.authenticate("dave", "wrong-pass")
    with pytest.raises(AuthError, match="Too many") as e:
        store.authenticate("dave", "password123")  # even the right password is refused while throttled
    assert e.value.status == 429


def test_login_throttle_resets_after_window(store, clock):
    store.register("erin", "password123")
    for _ in range(5):
        with pytest.raises(AuthError):
            store.authenticate("erin", "wrong-pass")
    clock.t += 601
    assert store.authenticate("erin", "password123").user_id == "erin"


def test_guest_expires_and_is_deleted(store, settings, clock):
    guest = store.create_guest()
    store.register("frank", "password123")
    assert guest.user_id.startswith("guest-") and store.get(guest.user_id) is not None
    folder = settings.data_dir / "users" / guest.user_id
    (folder / "memory").mkdir()
    (folder / "memory" / "note.md").write_text("private")

    clock.t += 7 * 86400
    assert store.get(guest.user_id) is None          # expired guests can't be used...
    assert store.cleanup_expired_guests() == 1        # ...and their whole folder is removed
    assert not folder.exists()
    assert store.get("frank") is not None             # registered users are never touched


def test_guest_cannot_sign_in_with_password(store):
    guest = store.create_guest()
    with pytest.raises(AuthError):
        store.authenticate(guest.user_id, "anything-at-all")


def test_tokens_issue_verify_revoke(store, settings):
    store.register("gina", "password123")
    store.register("hank", "password123")
    token, token_id = store.issue_token("gina", "laptop")
    assert token.startswith("pat_gina.")
    assert store.verify_token(token).user_id == "gina"
    raw = (settings.data_dir / "users" / "gina" / "account.json").read_text()
    assert token.split(".", 1)[1] not in raw            # only the hash is stored
    assert store.list_tokens("gina") == [{"id": token_id, "label": "laptop", "created_ts": 1_000_000.0}]

    forged = "pat_hank." + token.split(".", 1)[1]       # gina's secret can't unlock hank
    assert store.verify_token(forged) is None
    assert store.verify_token("pat_gina.wrong") is None
    assert store.verify_token("nonsense") is None

    assert store.revoke_token("gina", token_id) is True
    assert store.verify_token(token) is None
    assert store.revoke_token("gina", token_id) is False
