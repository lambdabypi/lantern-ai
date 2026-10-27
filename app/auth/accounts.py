"""Accounts: registered users, temporary guests, and personal access tokens.

Stored as data/users/<user_id>/account.json (one file per user, inside that user's folder).
Standard library only:
- passwords: scrypt (n=2^14, r=8, p=1) with a per-user salt, compared in constant time
- tokens: "pat_<user_id>.<secret>"; only a SHA-256 of the secret is stored, shown once at creation
- guests: "guest-<hex>" ids; the whole folder is deleted once the account expires
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Literal

from app.auth.users import user_dir, validate_user_id
from app.fileio import write_atomic

GUEST_PREFIX = "guest-"
STEAM_PREFIX = "steam-"  # reserved: accounts from the earlier Steam sign-in still exist
MIN_PASSWORD, MAX_PASSWORD = 8, 256
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}
_LOGIN_WINDOW_S, _LOGIN_MAX_FAILURES = 600, 5


class AuthError(Exception):
    """An auth problem with a message safe to show to the user and an HTTP status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class Account:
    user_id: str
    kind: Literal["user", "guest"]
    created_ts: float
    expires_ts: float | None = None
    pw_hash: str | None = None
    tokens: list[dict] = field(default_factory=list)  # {"id", "hash", "label", "created_ts"}
    display_name: str | None = None

    @property
    def is_guest(self) -> bool:
        return self.kind == "guest"

    def expired(self, now: float) -> bool:
        return self.expires_ts is not None and now >= self.expires_ts


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, **_SCRYPT)
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    try:
        scheme, n, r, p, salt, digest = (stored or "").split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _token_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class AccountStore:
    def __init__(self, data_dir: Path, guest_ttl_days: int = 7, clock: Callable[[], float] = time.time):
        self.data_dir = Path(data_dir)
        self.guest_ttl_s = guest_ttl_days * 86400
        self.clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}
        self._last_cleanup = float("-inf")

    # ---- storage -------------------------------------------------------------------------
    def _path(self, user_id: str) -> Path:
        return user_dir(self.data_dir, user_id) / "account.json"

    def _save(self, account: Account) -> None:
        write_atomic(self._path(account.user_id), json.dumps(asdict(account), indent=2))  # never half-written

    def get(self, user_id: str) -> Account | None:
        """The account, or None if it doesn't exist, is unreadable, or is an expired guest."""
        try:
            path = self._path(user_id)
        except ValueError:
            return None
        if not path.exists():
            return None
        try:
            account = Account(**json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        return None if account.expired(self.clock()) else account

    # ---- registered users ----------------------------------------------------------------
    def register(self, username: str, password: str) -> Account:
        user_id = (username or "").strip().lower()
        try:
            validate_user_id(user_id)
        except ValueError:
            raise AuthError("Usernames are 1-64 characters: a-z, 0-9, '_' or '-'.") from None
        if user_id.startswith((GUEST_PREFIX, STEAM_PREFIX)) or user_id == "demo":
            raise AuthError("That username is reserved.")
        if not (MIN_PASSWORD <= len(password or "") <= MAX_PASSWORD):
            raise AuthError(f"Passwords must be {MIN_PASSWORD}-{MAX_PASSWORD} characters.")
        with self._lock:
            if self._path(user_id).exists():
                raise AuthError("That username is taken.", status=409)
            account = Account(user_id=user_id, kind="user", created_ts=self.clock(),
                              pw_hash=hash_password(password))
            self._save(account)
        return account

    def keep_guest_data(self, guest: Account, username: str, password: str) -> Account:
        """Turn a guest session into a real account: register it, then move the guest's files (memory, history,
        recall status, alerts) across and delete the guest. Nothing moves if the username is refused."""
        if not guest.is_guest:
            raise AuthError("You already have an account.")
        account = self.register(username, password)
        source, target = user_dir(self.data_dir, guest.user_id), user_dir(self.data_dir, account.user_id)
        with self._lock:
            for item in source.iterdir() if source.is_dir() else []:
                if item.name != "account.json" and not item.is_symlink():
                    shutil.move(str(item), str(target / item.name))
            shutil.rmtree(source, ignore_errors=True)
        return account

    def authenticate(self, username: str, password: str) -> Account:
        user_id = (username or "").strip().lower()
        now = self.clock()
        recent = [t for t in self._failures.get(user_id, []) if now - t < _LOGIN_WINDOW_S]
        if len(recent) >= _LOGIN_MAX_FAILURES:
            raise AuthError("Too many failed sign-ins. Try again in a few minutes.", status=429)
        account = self.get(user_id)
        if account is None or account.is_guest or not verify_password(password or "", account.pw_hash):
            if len(self._failures) > 10_000:   # forget old failures so the dict can't grow without end
                self._failures = {k: v for k, v in self._failures.items() if v and now - v[-1] < _LOGIN_WINDOW_S}
            self._failures[user_id] = recent + [now]
            raise AuthError("Wrong username or password.", status=401)
        self._failures.pop(user_id, None)
        return account

    # ---- guests --------------------------------------------------------------------------
    def create_guest(self) -> Account:
        now = self.clock()
        if now - self._last_cleanup > 3600:   # it walks every user folder in the bucket, so at most hourly
            self._last_cleanup = now
            self.cleanup_expired_guests()
        account = Account(user_id=GUEST_PREFIX + secrets.token_hex(6), kind="guest",
                          created_ts=now, expires_ts=now + self.guest_ttl_s)
        self._save(account)
        return account

    def cleanup_expired_guests(self) -> int:
        """Delete every expired guest's whole folder. Registered users are never touched."""
        users_root = self.data_dir / "users"
        if not users_root.is_dir():
            return 0
        now, removed = self.clock(), 0
        for folder in users_root.iterdir():
            if not folder.name.startswith(GUEST_PREFIX) or folder.is_symlink() or not folder.is_dir():
                continue
            try:
                data = json.loads((folder / "account.json").read_text(encoding="utf-8"))
                expired = data.get("kind") == "guest" and now >= float(data.get("expires_ts") or 0)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                expired = True  # a guest folder without a valid account is stale
            if expired:
                shutil.rmtree(folder)
                removed += 1
        return removed

    # ---- personal access tokens --------------------------------------
    def issue_token(self, user_id: str, label: str = "") -> tuple[str, str]:
        """Create a token. Returns (token, token_id); the token is shown to the user once."""
        with self._lock:
            account = self.get(user_id)
            if account is None:
                raise AuthError("Account not found.", status=404)
            token_id, secret = secrets.token_hex(4), secrets.token_urlsafe(32)
            account.tokens.append({"id": token_id, "hash": _token_hash(secret),
                                   "label": (label or "")[:60], "created_ts": self.clock()})
            self._save(account)
        return f"pat_{user_id}.{secret}", token_id

    def list_tokens(self, user_id: str) -> list[dict]:
        account = self.get(user_id)
        return [{k: t[k] for k in ("id", "label", "created_ts")} for t in (account.tokens if account else [])]

    def revoke_token(self, user_id: str, token_id: str) -> bool:
        with self._lock:
            account = self.get(user_id)
            if account is None:
                return False
            kept = [t for t in account.tokens if t["id"] != token_id]
            if len(kept) == len(account.tokens):
                return False
            account.tokens = kept
            self._save(account)
        return True

    def verify_token(self, token: str) -> Account | None:
        if not token or not token.startswith("pat_") or "." not in token:
            return None
        user_id, _, secret = token[4:].partition(".")
        account = self.get(user_id)
        if account is None:
            return None
        digest = _token_hash(secret)
        return account if any(hmac.compare_digest(digest, t["hash"]) for t in account.tokens) else None
