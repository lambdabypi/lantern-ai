"""Follow-through: the daily watch checks in until a recall is actually fixed.

Announcing a recall is where most recall tools stop, and most recalls then stay unrepaired. Lantern keeps going, by
push notification (at most one a day per person, and the same reminder at most once a week):

- an urgent open recall (Do not drive, Park outside, Stop using) with nothing booked after URGENT_DAYS:
  "Your 2006 Ford Ranger still has a Do not drive recall. The repair is free: book it."
- "contacted" for CONTACTED_DAYS: "Did you hear back about the ... recall?"
- "booked" for BOOKED_DAYS: "Was the ... recall repaired? Mark it fixed in Lantern."

The person records progress themselves (contacted, booked, fixed) in the app; nothing here changes a recall.
When each reminder was sent is kept in data/users/<id>/recalls/followups.json.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from app.auth.users import user_dir
from app.fileio import write_json

URGENT_DAYS = 3
CONTACTED_DAYS = 7
BOOKED_DAYS = 14
REPEAT_DAYS = 7
URGENT_WORDS = {"park_it": "Do not drive", "park_outside": "Park outside", "stop_using": "Stop using"}


def due(report: dict[str, Any], state: dict[str, Any], today: date) -> tuple[tuple[str, str, str] | None, dict[str, Any]]:
    """The most important check-in due today (key, title, body), and the updated state. Pure, for testing."""
    state = {k: dict(v) for k, v in state.items() if isinstance(v, dict)}
    candidates = []
    for item in report.get("items", []):
        for r in item.get("recalls", []):
            if r.get("status") != "open":
                continue
            key = f"{item['kind']}:{item['slug']}|{r['id']}"
            entry = state.setdefault(key, {"first": today.isoformat()})
            last = entry.get("last")
            if last and (today - date.fromisoformat(last)).days < REPEAT_DAYS:
                continue
            what = (r.get("plain") or {}).get("title") or r.get("title") or "safety"
            name = item.get("label") or item["name"]
            progress = r.get("progress") or {}
            since = _days(progress.get("since"), today)
            if progress.get("step") == "booked" and since >= BOOKED_DAYS:
                candidates.append((1, key, f"Was it repaired? {name}",
                                   f"You booked the free fix for \"{what}\" {since} days ago. If it's done, mark it "
                                   "fixed in Lantern."))
            elif progress.get("step") == "contacted" and since >= CONTACTED_DAYS:
                candidates.append((2, key, f"Did you hear back? {name}",
                                   f"You contacted them about \"{what}\" {since} days ago. Tap to book the free fix."))
            elif not progress and r.get("severity") in URGENT_WORDS and _days(entry["first"], today) >= URGENT_DAYS:
                candidates.append((0, key, f"{URGENT_WORDS[r['severity']]}: {name}",
                                   f"\"{what}\" is still open. The fix is free: tap to see how to get it."))
    known = {f"{i['kind']}:{i['slug']}|{r['id']}" for i in report.get("items", []) for r in i.get("recalls", [])
             if r.get("status") == "open"}
    state = {k: v for k, v in state.items() if k in known}   # fixed or removed: forget it
    if not candidates:
        return None, state
    _, key, title, body = min(candidates)
    state[key]["last"] = today.isoformat()
    return (key, title, body), state


def _days(iso: str | None, today: date) -> int:
    try:
        return (today - date.fromisoformat(str(iso))).days
    except ValueError:
        return 0


class FollowUps:
    def __init__(self, data_dir: Path, push: Any):
        self.data_dir = Path(data_dir)
        self.push = push

    def _path(self, user_id: str) -> Path:
        return user_dir(self.data_dir, user_id) / "recalls" / "followups.json"

    def remind(self, user_id: str, report: dict[str, Any], today: date | None = None) -> int:
        """Send today's check-in, if one is due and the user turned alerts on. Returns how many devices got it."""
        if not self.push.enabled_for(user_id):
            return 0
        try:
            state = json.loads(self._path(user_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        reminder, state = due(report, state if isinstance(state, dict) else {}, today or date.today())
        sent = self.push.send(user_id, reminder[1], reminder[2], "/#attention") if reminder else 0
        write_json(self._path(user_id), state)
        return sent


def daily_alerts(push: Any, followups: FollowUps | None):
    """on_report for RecallWatch.run_all: new recalls first; a check-in only on days with no new-recall alert."""
    def on_report(user_id: str, report: dict[str, Any]) -> int:
        sent = push.announce_new_recalls(user_id, report)
        if not sent and followups is not None:
            sent = followups.remind(user_id, report)
        return sent
    return on_report

