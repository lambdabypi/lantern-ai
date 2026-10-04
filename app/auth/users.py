"""User identity helpers. Accounts and login come in build step 5; ids are validated now."""

from __future__ import annotations

import re
from pathlib import Path

# Until auth exists, the web app acts as this single demo user.
DEMO_USER_ID = "demo"

_USER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def validate_user_id(user_id: str) -> str:
    """Return `user_id` if it is safe to use as a directory name, else raise ValueError."""
    if not isinstance(user_id, str) or not _USER_ID.fullmatch(user_id):
        raise ValueError(f"Invalid user id {user_id!r}: use 1-64 chars of a-z, 0-9, '_' or '-'.")
    return user_id


def user_dir(data_dir: Path, user_id: str) -> Path:
    """The root of everything stored for one user: data/users/<user_id>/."""
    return Path(data_dir) / "users" / validate_user_id(user_id)
