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

    def _load(self, user_id: str) -> tuple[Path, dict]:
        path = user_dir(self.data_dir, user_id) / "usage.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if data.get("day") != self._today():
            data = {"day": self._today(), "counts": {}}
        return path, data

    def used(self, user_id: str, kind: str) -> int:
        return int(self._load(user_id)[1]["counts"].get(kind, 0))

    def try_consume(self, user_id: str, kind: str, limit: int) -> bool:
        """Count one use of `kind` if today's total is below `limit`. Resets at midnight UTC."""
        with self._lock:
            path, data = self._load(user_id)
            count = int(data["counts"].get(kind, 0))
            if count >= limit:
                return False
            data["counts"][kind] = count + 1
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, path)
            return True
