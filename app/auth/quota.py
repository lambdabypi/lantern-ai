"""Per-user daily counters (e.g. guest "Think harder" turns), stored in data/users/<id>/usage.json."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.auth.users import user_dir


class DailyQuota:
    def __init__(self, data_dir: Path, clock: Callable[[], float] = time.time):
        self.data_dir = Path(data_dir)
        self.clock = clock
        self._lock = threading.Lock()

    def _today(self) -> str:
        return datetime.fromtimestamp(self.clock(), tz=timezone.utc).date().isoformat()

    def _path(self, user_id: str) -> Path:
        return user_dir(self.data_dir, user_id) / "usage.json"

    def _load(self, user_id: str) -> tuple[Path, dict]:
        path = self._path(user_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if data.get("day") != self._today():
            data = {"day": self._today(), "counts": {}}
        return path, data

    def used(self, user_id: str, kind: str) -> int:
        return int(self._load(user_id)[1]["counts"].get(kind, 0))

    def try_consume(self, user_id: str, kind: str, limit: int | None, amount: int = 1) -> bool:
        """Count `amount` uses of `kind` if that keeps today's total within `limit` (None = unlimited,
        0 = none allowed). Resets at midnight UTC."""
        with self._lock:
            path, data = self._load(user_id)
            count = int(data["counts"].get(kind, 0))
            if limit is not None and count + amount > limit:
                return False
            data["counts"][kind] = count + amount
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, path)
            return True


class SiteQuota(DailyQuota):
    """Site-wide daily counters (all players together), stored in data/site_usage.json.
    A backstop so a public demo can't run through the Token Factory or Tavily budget in a day."""

    SITE = "site"

    def _path(self, user_id: str) -> Path:
        return self.data_dir / "site_usage.json"

    def try_consume(self, kind: str, limit: int | None, amount: int = 1) -> bool:  # type: ignore[override]
        return super().try_consume(self.SITE, kind, limit, amount)

    def used(self, kind: str) -> int:  # type: ignore[override]
        return super().used(self.SITE, kind)
