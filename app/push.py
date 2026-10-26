"""Browser push notifications ("Alerts on this device"): free, no phone number, no third-party account.

Works on Android and desktop browsers, and on iPhone (iOS 16.4+) once Lantern is added to the Home Screen.
Subscriptions live in data/users/<id>/push.json with the recall ids already announced, so a recall is
pushed once, not every day.

The browser chooses the push endpoint URL, and the server POSTs to it, so endpoints are restricted to the
known push services over https: a crafted subscription can't make the server call internal addresses.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

from app.auth.users import user_dir
from app.config import ConfigError
from app.fileio import write_atomic

log = logging.getLogger(__name__)

PUSH_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "push.services.mozilla.com",
              "web.push.apple.com", "notify.windows.com", "push.apple.com")
MAX_SUBSCRIPTIONS = 5
MAX_SENT_IDS = 500


class PushError(ValueError):
    """A subscription was refused. The message is safe to show to the user."""


def generate_key() -> str:
    """A new VAPID private key for VAPID_PRIVATE_KEY (base64 of the PEM, so it fits on one line)."""
    v = Vapid()
    v.generate_keys()
    return base64.b64encode(v.private_pem()).decode()


def _allowed_endpoint(url: str) -> bool:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    return p.scheme == "https" and any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


class Push:
    def __init__(self, data_dir: Path, private_key_b64: str, subject: str):
        self.data_dir = Path(data_dir)
        try:
            self._vapid = Vapid.from_pem(base64.b64decode(private_key_b64, validate=True))
        except (ValueError, TypeError) as e:
            raise ConfigError("VAPID_PRIVATE_KEY isn't a valid key. Generate one with: python -m app.push") from e
        self.subject = subject
        raw = self._vapid.public_key.public_bytes(serialization.Encoding.X962,
                                                  serialization.PublicFormat.UncompressedPoint)
        self.public_key = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()  # the browser's applicationServerKey

    # ---- storage ----------------------------------------------------------------------------
    def _path(self, user_id: str) -> Path:
        return user_dir(self.data_dir, user_id) / "push.json"

    def _load(self, user_id: str) -> dict[str, Any]:
        try:
            data = json.loads(self._path(user_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        return {"subscriptions": list(data.get("subscriptions") or []), "sent": list(data.get("sent") or [])}

    def _save(self, user_id: str, data: dict[str, Any]) -> None:
        data["sent"] = data["sent"][-MAX_SENT_IDS:]
        write_atomic(self._path(user_id), json.dumps(data))

    def subscribe(self, user_id: str, subscription: dict[str, Any]) -> None:
        endpoint = str(subscription.get("endpoint") or "")
        keys = subscription.get("keys") if isinstance(subscription.get("keys"), dict) else {}
        if not _allowed_endpoint(endpoint) or len(endpoint) > 1000:
            raise PushError("This browser's notification service isn't supported.")
        if not (isinstance(keys.get("p256dh"), str) and isinstance(keys.get("auth"), str)
                and len(keys["p256dh"]) < 200 and len(keys["auth"]) < 100):
            raise PushError("The browser sent an incomplete notification subscription.")
        data = self._load(user_id)
        subs = [s for s in data["subscriptions"] if s.get("endpoint") != endpoint]
        subs.append({"endpoint": endpoint, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}})
        data["subscriptions"] = subs[-MAX_SUBSCRIPTIONS:]
        self._save(user_id, data)

    def unsubscribe(self, user_id: str, endpoint: str) -> None:
        data = self._load(user_id)
        data["subscriptions"] = [s for s in data["subscriptions"] if s.get("endpoint") != endpoint]
        self._save(user_id, data)

    def enabled_for(self, user_id: str) -> bool:
        return bool(self._load(user_id)["subscriptions"])

    # ---- sending ----------------------------------------------------------------------------
    def send(self, user_id: str, title: str, body: str, url: str = "/") -> int:
        """Send to every device the user subscribed. Returns how many accepted it; dead subscriptions are removed."""
        data = self._load(user_id)
        payload = json.dumps({"title": title[:80], "body": body[:200], "url": url})
        alive, delivered = [], 0
        for sub in data["subscriptions"]:
            if not _allowed_endpoint(sub.get("endpoint", "")):
                continue
            try:
                webpush(sub, data=payload, vapid_private_key=self._vapid,
                        vapid_claims={"sub": self.subject}, timeout=10, ttl=86400)
                delivered += 1
                alive.append(sub)
            except WebPushException as e:
                status = getattr(e.response, "status_code", None)
                if status not in (404, 410):  # 404/410: the user turned notifications off; forget it
                    alive.append(sub)
                    log.warning("push to %s failed: HTTP %s", urlparse(sub["endpoint"]).hostname, status)
        if len(alive) != len(data["subscriptions"]):
            data["subscriptions"] = alive
            self._save(user_id, data)
        return delivered

    def announce_new_recalls(self, user_id: str, report: dict[str, Any]) -> int:
        """Push each new open recall once. Do Not Drive recalls say so in the title."""
        data = self._load(user_id)
        if not data["subscriptions"]:
            return 0
        sent = set(data["sent"])
        fresh = [(item, r) for item in report.get("items", []) for r in item["recalls"]
                 if r.get("new") and r["status"] == "open" and f"{item['slug']}|{r['id']}" not in sent]
        if not fresh:
            return 0
        urgent = [x for x in fresh if x[1]["severity"]]
        item, r = (urgent or fresh)[0]
        prefix = {"park_it": "Do not drive: ", "park_outside": "Park outside: ", "stop_using": "Stop using: "}
        title = prefix.get(r["severity"], "New recall: ") + (item.get("label") or item["name"])
        more = f" (+{len(fresh) - 1} more)" if len(fresh) > 1 else ""
        what = (r.get("plain") or {}).get("title") or r["title"]
        delivered = self.send(user_id, title, f"{what}. Tap to see what to do.{more}", "/#attention")
        data = self._load(user_id)  # send() may have dropped dead subscriptions
        data["sent"] += [f"{i['slug']}|{x['id']}" for i, x in fresh]
        self._save(user_id, data)
        return delivered


if __name__ == "__main__":  # python -m app.push  -> prints a new VAPID_PRIVATE_KEY value
    print(generate_key())
