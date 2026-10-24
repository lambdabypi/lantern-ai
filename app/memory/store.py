"""Visible memory: one Markdown file (YAML front matter + notes) per thing the user owns, and their profile.

Layout inside data/users/<id>/memory/:
    profile.md
    vehicles/<slug>.md     make, model, year, optional VIN; recall_status per NHTSA campaign
    products/<slug>.md     brand, product, model; recall_status per CPSC recall number
    games/, creators/      from the earlier gaming version (kept until the UI moves over)

Files are plain text the user can read, edit, delete and export. Every path is resolved and
checked to stay inside the user's memory folder, and only known fields are accepted.
"""

from __future__ import annotations

import io
import re
import threading
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from app.auth.users import user_dir
from app.fileio import write_atomic

KINDS = {"vehicle": "vehicles", "product": "products", "game": "games", "creator": "creators"}
GAME_STATUSES = ("playing", "backlog", "finished", "dropped")
SPOILER_PREFERENCES = ("strict", "relaxed")
SOURCES = ("chat", "steam", "user", "vin")
RECALL_STATUSES = ("fixed", "not_mine")  # anything else is open

# Fields each kind may hold (beyond the automatic created/updated/source/confidence).
FIELDS: dict[str, dict[str, type | tuple]] = {
    "vehicle": {"name": str, "make": str, "model": str, "year": int, "vin": str, "trim": str,
                "nhtsa_model": str, "recall_status": dict},
    "product": {"name": str, "brand": str, "product": str, "model": str, "bought": str, "recall_status": dict},
    "game": {"title": str, "status": str, "progress": str, "platform": str, "steam_appid": int,
             "playtime_hours": (int, float), "achievements": str, "last_played": str, "sources": list},
    "creator": {"name": str, "platforms": list, "links": list},
    "profile": {"location": str, "platforms": list, "play_style": str, "spoiler_preference": str, "steam_id": str},
}
NAME_FIELD = {"vehicle": "name", "product": "name", "game": "title", "creator": "name"}
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_FRONT = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.DOTALL)
MAX_NOTE_CHARS = 2000
MAX_FILE_CHARS = 20_000


class MemoryStoreError(ValueError):
    """Invalid memory content or request. The message is safe to show to the user."""


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:64].strip("-")
    if not slug:
        raise MemoryStoreError("A name is needed (letters or numbers).")
    return slug


@dataclass
class MemoryItem:
    kind: str
    slug: str
    meta: dict[str, Any] = field(default_factory=dict)
    body: str = ""

    def to_text(self) -> str:
        front = yaml.safe_dump(self.meta, sort_keys=False, allow_unicode=True).strip()
        return f"---\n{front}\n---\n{self.body.strip()}\n"

    def summary(self) -> dict[str, Any]:
        return {"kind": self.kind, "slug": self.slug, **self.meta, "notes": self.body.strip()}


def parse(kind: str, slug: str, text: str) -> MemoryItem:
    """Parse a memory file. Raises MemoryStoreError with a readable reason if it's malformed."""
    if len(text) > MAX_FILE_CHARS:
        raise MemoryStoreError(f"Memory files are limited to {MAX_FILE_CHARS:,} characters.")
    match = _FRONT.match(text.strip() + "\n")
    if not match:
        raise MemoryStoreError("The file must start with front matter between two '---' lines.")
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as e:
        raise MemoryStoreError(f"The front matter isn't valid YAML: {e}") from None
    if not isinstance(meta, dict):
        raise MemoryStoreError("The front matter must be a list of 'key: value' lines.")
    return MemoryItem(kind, slug, _clean(kind, meta, strict=True), match.group(2).strip())


def _clean(kind: str, values: dict[str, Any], strict: bool) -> dict[str, Any]:
    """Keep known fields with the right types. strict=True also checks automatic fields."""
    allowed = FIELDS[kind]
    automatic = {"created", "updated", "source", "confidence"}
    out: dict[str, Any] = {}
    for key, value in values.items():
        if key in automatic and strict:
            out[key] = str(value)
            continue
        if key not in allowed:
            raise MemoryStoreError(f"Unknown field '{key}' for a {kind}. Allowed: {', '.join(allowed)}.")
        if value is None or value == "":
            continue
        expected = allowed[key]
        if expected is dict:
            value = _recall_status(value)
        elif expected is list:
            value = [str(v) for v in value] if isinstance(value, list) else [str(value)]
        elif expected is int:
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise MemoryStoreError(f"'{key}' must be a whole number.") from None
        elif expected == (int, float):
            try:
                value = round(float(value), 1)
            except (TypeError, ValueError):
                raise MemoryStoreError(f"'{key}' must be a number.") from None
        else:
            value = str(value).strip()[:500]
        out[key] = value
    if kind == "game" and "status" in out and out["status"] not in GAME_STATUSES:
        raise MemoryStoreError(f"status must be one of: {', '.join(GAME_STATUSES)}.")
    if kind == "profile" and "spoiler_preference" in out and out["spoiler_preference"] not in SPOILER_PREFERENCES:
        raise MemoryStoreError(f"spoiler_preference must be one of: {', '.join(SPOILER_PREFERENCES)}.")
    return out


def _recall_status(value: Any) -> dict[str, str]:
    """{recall id: 'fixed' | 'not_mine'}. Recall ids are NHTSA campaign or CPSC recall numbers."""
    if not isinstance(value, dict):
        raise MemoryStoreError("recall_status must be a list of 'recall number: fixed or not_mine' lines.")
    out = {}
    for key, status in value.items():
        key, status = str(key).strip().upper(), str(status).strip()
        if not re.fullmatch(r"[0-9A-Z]{3,15}", key):
            raise MemoryStoreError(f"'{key}' doesn't look like a recall number.")
        if status not in RECALL_STATUSES:
            raise MemoryStoreError(f"A recall's status must be one of: {', '.join(RECALL_STATUSES)}.")
        out[key] = status
    return out


class MemoryStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self._lock = threading.Lock()

    # ---- paths -----------------------------------------------------------------------------
    def root(self, user_id: str) -> Path:
        return user_dir(self.data_dir, user_id) / "memory"

    def _path(self, user_id: str, kind: str, slug: str | None = None) -> Path:
        root = self.root(user_id)
        if kind == "profile":
            path = root / "profile.md"
        elif kind in KINDS:
            if not slug or not _SLUG.fullmatch(slug):
                raise MemoryStoreError("Invalid name.")
            path = root / KINDS[kind] / f"{slug}.md"
        else:
            raise MemoryStoreError(f"Unknown memory type '{kind}'.")
        resolved_root = root.resolve()
        if path.parent.exists() and not path.resolve().is_relative_to(resolved_root):
            raise MemoryStoreError("That path is outside your memory folder.")
        return path

    def _write(self, path: Path, text: str) -> None:
        write_atomic(path, text)

    # ---- read ------------------------------------------------------------------------------
    def get(self, user_id: str, kind: str, slug: str | None = None) -> MemoryItem | None:
        path = self._path(user_id, kind, slug)
        if not path.exists() or path.is_symlink():
            return None
        return parse(kind, slug or "profile", path.read_text(encoding="utf-8"))

    def raw(self, user_id: str, kind: str, slug: str | None = None) -> str | None:
        path = self._path(user_id, kind, slug)
        return path.read_text(encoding="utf-8") if path.exists() and not path.is_symlink() else None

    def list(self, user_id: str, kind: str) -> list[MemoryItem]:
        folder = self.root(user_id) / KINDS[kind]
        items = []
        for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
            if path.is_symlink():
                continue
            try:
                items.append(parse(kind, path.stem, path.read_text(encoding="utf-8")))
            except MemoryStoreError:
                continue  # a hand-edited file with a mistake is skipped, not fatal; the UI flags it
        return items

    # ---- write -----------------------------------------------------------------------------
    def upsert(self, user_id: str, kind: str, name: str | None, fields: dict[str, Any],
               note: str | None = None, source: str = "chat") -> MemoryItem:
        """Create or update one memory. Given fields replace old values; a note is appended."""
        if source not in SOURCES:
            raise MemoryStoreError(f"Unknown source '{source}'.")
        slug = None if kind == "profile" else slugify(name or "")
        today = date.today().isoformat()
        with self._lock:
            item = self.get(user_id, kind, slug) or MemoryItem(kind, slug or "profile", {"created": today})
            if kind in NAME_FIELD and NAME_FIELD[kind] not in item.meta:
                item.meta[NAME_FIELD[kind]] = (name or "").strip()[:200]
            item.meta.update(_clean(kind, fields, strict=False))
            item.meta.update({"updated": today, "source": source, "confidence": "high"})
            if note and note.strip():
                line = f"- {today}: {note.strip()[:MAX_NOTE_CHARS]}"
                item.body = f"{item.body}\n{line}".strip()
            self._write(self._path(user_id, kind, slug), item.to_text())
        return item

    def save_raw(self, user_id: str, kind: str, slug: str | None, text: str) -> MemoryItem:
        """Save a file the user edited by hand, after checking it parses."""
        item = parse(kind, slug or "profile", text)
        if kind in NAME_FIELD and not item.meta.get(NAME_FIELD[kind]):
            raise MemoryStoreError(f"A {kind} needs a '{NAME_FIELD[kind]}'.")
        item.meta["updated"] = date.today().isoformat()
        item.meta.setdefault("source", "user")
        with self._lock:
            self._write(self._path(user_id, kind, slug), item.to_text())
        return item

    def delete(self, user_id: str, kind: str, slug: str | None = None) -> bool:
        """Only reachable from the UI: the agent has no delete tool."""
        path = self._path(user_id, kind, slug)
        if not path.exists() or path.is_symlink():
            return False
        path.unlink()
        return True

    # ---- export ----------------------------------------------------------------------------
    def export_zip(self, user_id: str) -> bytes:
        """Everything the assistant knows about the user: memory files, chat history and the audit log.
        Credentials (account.json) are never included."""
        base = user_dir(self.data_dir, user_id)
        wanted = [p for p in (base / "memory").rglob("*.md") if not p.is_symlink()] if (base / "memory").is_dir() else []
        for extra in (base / "history.jsonl", base / "logs" / "audit.jsonl"):
            if extra.exists() and not extra.is_symlink():
                wanted.append(extra)
        for folder in ("briefings", "recalls"):
            if (base / folder).is_dir():
                wanted += [p for p in sorted((base / folder).glob("*.json")) if not p.is_symlink()]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in wanted:
                zf.write(path, path.relative_to(base).as_posix())
        return buf.getvalue()

    # ---- prompt context --------------------------------------------------------------------
    def context_for_prompt(self, user_id: str, max_games: int = 15) -> str:
        """A compact summary of the profile for the system prompt (progress drives spoiler safety)."""
        lines = []
        profile = self.get(user_id, "profile")
        if profile and profile.meta.get("location"):
            lines.append(f"Location: {profile.meta['location']}")
        for kind, label in (("vehicle", "Car"), ("product", "Product")):
            for item in self.list(user_id, kind)[:30]:
                m = item.meta
                extra = ["VIN on file" if m.get("vin") else "",  # never send the VIN itself to the model
                         f"{len(m['recall_status'])} recall(s) marked fixed or not theirs" if m.get("recall_status") else ""]
                detail = "; ".join(e for e in extra if e)
                lines.append(f"- {label}: {m.get('name', item.slug)}" + (f" ({detail})" if detail else ""))
        if profile and any(profile.meta.get(k) for k in ("platforms", "play_style", "spoiler_preference")):
            p = profile.meta
            bits = [f"platforms: {', '.join(p['platforms'])}" if p.get("platforms") else "",
                    f"play style: {p['play_style']}" if p.get("play_style") else "",
                    f"spoilers: {p.get('spoiler_preference', 'strict')}"]
            lines.append("Profile: " + "; ".join(b for b in bits if b))
        games = sorted(self.list(user_id, "game"),
                       key=lambda g: (g.meta.get("status") != "playing", g.meta.get("title", "").lower()))
        for g in games[:max_games]:
            m = g.meta
            detail = ", ".join(b for b in [m.get("status", ""), f"progress: {m['progress']}" if m.get("progress") else "",
                                           m.get("platform", "")] if b)
            lines.append(f"- Game: {m.get('title', g.slug)} ({detail})" if detail else f"- Game: {m.get('title', g.slug)}")
        creators = self.list(user_id, "creator")
        if creators:
            lines.append("Favourite creators: " + ", ".join(c.meta.get("name", c.slug) for c in creators[:20]))
        return "\n".join(lines) if lines else "Nothing saved yet."
