"""Chat history on disk (data/users/<id>/history.jsonl), so it survives restarts and works
across server processes. Only questions and final answers are kept."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.auth.users import user_dir

MAX_MESSAGE_CHARS = 8000


class HistoryStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self._lock = threading.Lock()

    def _path(self, user_id: str) -> Path:
        return user_dir(self.data_dir, user_id) / "history.jsonl"

    def append(self, user_id: str, messages: list[dict[str, str]]) -> None:
        path = self._path(user_id)
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                for m in messages:
                    f.write(json.dumps({"ts": ts, "role": m["role"],
                                        "content": m["content"][:MAX_MESSAGE_CHARS]}, ensure_ascii=False) + "\n")

    def recent(self, user_id: str, limit: int = 20) -> list[dict[str, str]]:
        """The last `limit` messages as {role, content}, oldest first. Corrupt lines are skipped."""
        path = self._path(user_id)
        if not path.exists():
            return []
        messages = []
        for line in path.read_text(encoding="utf-8").splitlines()[-limit * 2:]:
            try:
                m = json.loads(line)
                if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
                    messages.append({"role": m["role"], "content": m["content"]})
            except json.JSONDecodeError:
                continue
        return messages[-limit:]

    def clear(self, user_id: str) -> None:
        with self._lock:
            self._path(user_id).unlink(missing_ok=True)
