"""Small file helpers shared by the stores."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def write_atomic(path: Path, text: str) -> None:
    """Write a file so readers never see it half-written (write a temp file, then rename it over).
    On failure the temp file is removed and the error is re-raised for the caller to handle."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def write_json(path: Path, value: Any) -> None:
    """write_atomic for a JSON value (UTF-8, not ASCII-escaped)."""
    write_atomic(path, json.dumps(value, ensure_ascii=False))
