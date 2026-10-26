"""Visible memory: one Markdown file (YAML front matter + notes) per thing the user owns, and their profile.

Layout inside data/users/<id>/memory/:
    profile.md
    vehicles/<slug>.md     make, model, year, optional VIN; recall_status per NHTSA campaign
    products/<slug>.md     brand, product, model; recall_status per CPSC recall number

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

KINDS = {"vehicle": "vehicles", "product": "products"}
SOURCES = ("chat", "user", "vin")
RECALL_STATUSES = ("fixed", "not_mine")  # anything else is open
UNIT_CHECKS = ("affected", "not_affected")  # what the owner's own check of their unit showed (see unit_check.py)
FIX_STEPS = ("contacted", "booked")         # how far the owner got with a recall's free fix (see followups.py)
_FIX_PROGRESS = re.compile(r"^(contacted|booked) \d{4}-\d{2}-\d{2}$")

# Fields each kind may hold (beyond the automatic created/updated/source/confidence). "owner" is set when the thing
# belongs to someone the user looks after ("Mom"), so Lantern says "Mom's 2012 Toyota Camry".
FIELDS: dict[str, dict[str, type | tuple]] = {
    "vehicle": {"name": str, "owner": str, "make": str, "model": str, "year": int, "vin": str, "trim": str,
                "nhtsa_model": str, "recall_status": dict, "unit_check": dict, "fix_progress": dict, "sources": list},
    "product": {"name": str, "owner": str, "brand": str, "product": str, "model": str, "bought": str, "recall_status": dict,
                "unit_check": dict, "fix_progress": dict, "sources": list},  # sources: the maker's own sites, used to answer questions about this thing
    "profile": {"location": str, "skills_off": list},   # skills_off: skills the user turned off (app/skill_files.py)
}
NAME_FIELD = {"vehicle": "name", "product": "name"}
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
        if expected is dict and key == "fix_progress":
            value = _fix_progress(value)
        elif expected is dict:
            value = _recall_map(key, value, UNIT_CHECKS if key == "unit_check" else RECALL_STATUSES)
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
    return out


def _recall_map(name: str, value: Any, allowed: tuple[str, ...]) -> dict[str, str]:
    """{recall id: one of `allowed`}, e.g. recall_status {id: fixed | not_mine}. Recall ids are NHTSA campaign or
    CPSC recall numbers."""
    if not isinstance(value, dict):
        raise MemoryStoreError(f"{name} must be a list of 'recall number: {' or '.join(allowed)}' lines.")
    out = {}
    for key, status in value.items():
        key, status = str(key).strip().upper(), str(status).strip()
        if not re.fullmatch(r"[0-9A-Z]{3,15}", key):
            raise MemoryStoreError(f"'{key}' doesn't look like a recall number.")
        if status not in allowed:
            raise MemoryStoreError(f"Each {name} value must be one of: {', '.join(allowed)}.")
        out[key] = status
    return out


def _fix_progress(value: Any) -> dict[str, str]:
    """{recall id: 'contacted YYYY-MM-DD' | 'booked YYYY-MM-DD'}."""
    if not isinstance(value, dict):
        raise MemoryStoreError("fix_progress must be a list of 'recall number: booked 2026-10-26' lines.")
    out = {}
    for key, step in value.items():
        key, step = str(key).strip().upper(), " ".join(str(step).split())
        if not re.fullmatch(r"[0-9A-Z]{3,15}", key):
            raise MemoryStoreError(f"'{key}' doesn't look like a recall number.")
        if not _FIX_PROGRESS.match(step):
            raise MemoryStoreError("Each fix_progress value must be 'contacted' or 'booked' and a date, like "
                                   "'booked 2026-10-26'.")
        out[key] = step
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
    def context_for_prompt(self, user_id: str, max_items: int = 30) -> str:
        """A compact list of the household's things for the system prompt. VINs are never included."""
        lines = []
        profile = self.get(user_id, "profile")
        if profile and profile.meta.get("location"):
            lines.append(f"Location: {profile.meta['location']}")
        for kind, label in (("vehicle", "Car"), ("product", "Product")):
            for item in self.list(user_id, kind)[:max_items]:
                m = item.meta
                extra = [f"{m['owner']}'s, not theirs" if m.get("owner") else "",
                         "VIN on file" if m.get("vin") else "",
                         f"{len(m['recall_status'])} recall(s) marked fixed or not theirs" if m.get("recall_status") else ""]
                detail = "; ".join(e for e in extra if e)
                lines.append(f"- {label}: {m.get('name', item.slug)}" + (f" ({detail})" if detail else ""))
        return "\n".join(lines) if lines else "Nothing saved yet."
