"""Signed session cookies (standard library only).

Cookie value: base64url(json {"uid", "exp"}) + "." + base64url(HMAC-SHA256(key, payload)).
The key is derived from APP_SECRET_KEY. Any edit to the payload, a different key, or an
expired timestamp makes verify() return None.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Callable

from app.auth.users import validate_user_id

COOKIE_NAME = "pai_session"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SessionSigner:
    def __init__(self, secret_key: str, clock: Callable[[], float] = time.time):
        # A purpose-specific key, so APP_SECRET_KEY can be reused safely for other things later.
        self._key = hashlib.sha256(b"pai-session-v1:" + secret_key.encode("utf-8")).digest()
        self.clock = clock

    def _sign(self, payload: str) -> str:
        return _b64(hmac.new(self._key, payload.encode("ascii"), hashlib.sha256).digest())

    def issue(self, user_id: str, ttl_s: float) -> str:
        validate_user_id(user_id)
        payload = _b64(json.dumps({"uid": user_id, "exp": int(self.clock() + ttl_s)},
                                  separators=(",", ":")).encode("utf-8"))
        return f"{payload}.{self._sign(payload)}"

    def verify(self, value: str | None) -> str | None:
        """The user id in a valid, unexpired cookie; otherwise None."""
        if not value or value.count(".") != 1:
            return None
        payload, signature = value.split(".")
        if not hmac.compare_digest(signature, self._sign(payload)):
            return None
        try:
            data = json.loads(_unb64(payload))
            user_id, expires = data["uid"], float(data["exp"])
            validate_user_id(user_id)
        except (ValueError, KeyError, TypeError):
            return None
        return user_id if self.clock() < expires else None
