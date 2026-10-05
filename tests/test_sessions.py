from __future__ import annotations

import base64
import json

import pytest

from app.auth.sessions import SessionSigner

SECRET = "s" * 40


def test_roundtrip():
    s = SessionSigner(SECRET, clock=lambda: 1000.0)
    assert s.verify(s.issue("alice", ttl_s=60)) == "alice"


def test_expired():
    t = [1000.0]
    s = SessionSigner(SECRET, clock=lambda: t[0])
    cookie = s.issue("alice", ttl_s=60)
    t[0] += 61
    assert s.verify(cookie) is None


def test_other_secret_is_rejected():
    cookie = SessionSigner(SECRET).issue("alice", ttl_s=60)
    assert SessionSigner("t" * 40).verify(cookie) is None


def test_tampered_payload_is_rejected():
    s = SessionSigner(SECRET)
    payload, sig = s.issue("alice", ttl_s=60).split(".")
    forged = base64.urlsafe_b64encode(json.dumps({"uid": "bob", "exp": 9e12}).encode()).rstrip(b"=").decode()
    assert s.verify(f"{forged}.{sig}") is None


@pytest.mark.parametrize("value", [None, "", "abc", "a.b.c", "....", "x.y"])
def test_malformed(value):
    assert SessionSigner(SECRET).verify(value) is None


def test_unsafe_user_id_cannot_be_issued():
    with pytest.raises(ValueError):
        SessionSigner(SECRET).issue("../etc", ttl_s=60)
