"""ToolRegistry: the ONLY path from the agent to a tool.

Every call goes through ToolRegistry.call(user_id, tool_name, args), which:
- rejects unknown tools and malformed arguments
- refuses tools flagged requires_confirmation (the confirmation UI comes later)
- runs the tool with a timeout
- appends an audit entry to data/users/<user_id>/logs/audit.jsonl

Build step 8a turns this into the full permission broker (grants, read/write split)
without changing the call signature.
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from app.auth.users import user_dir

Status = Literal["ok", "error", "timeout", "needs_confirmation", "unknown_tool", "invalid_args"]

# Timed-out tool threads can't be killed in Python; they finish in the background
# and their results are discarded. Keep tools short and give them their own timeouts too.
_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="tool")

_SECRET_KEY = re.compile(r"(api[_-]?key|token|secret|password|passwd|authorization|cookie|credential)", re.I)
_SECRET_VALUE = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|tvly-[A-Za-z0-9_-]{8,}|Bearer\s+\S+)")
_MAX_LOGGED_STR = 200


@dataclass(frozen=True)
class ToolContext:
    """What a tool handler knows about the call."""
    user_id: str


@dataclass(frozen=True)
class CallMeta:
    """Which model asked for the call, for the audit log."""
    model: str = ""
    tier: str = ""
    thinking: str = "default"


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for the arguments
    handler: Callable[[dict[str, Any], ToolContext], Any]
    requires_confirmation: bool = False
    timeout_s: float = 20.0
    status: str = ""  # shown while it runs, e.g. "Searching the {game} wiki…" (filled from the arguments)


@dataclass
class ToolResult:
    status: Status
    content: str  # JSON string sent back to the model
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def redact(value: Any) -> Any:
    """Copy of `value` safe for logs: secret-looking keys/values masked, long strings cut."""
    if isinstance(value, dict):
        return {k: "[REDACTED]" if _SECRET_KEY.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        value = _SECRET_VALUE.sub("[REDACTED]", value)
        if len(value) > _MAX_LOGGED_STR:
            return value[:_MAX_LOGGED_STR] + f"...[+{len(value) - _MAX_LOGGED_STR} chars]"
    return value


class ToolRegistry:
    def __init__(self, data_dir: Path, tools: list[Tool] | None = None, max_result_chars: int = 8000):
        self.data_dir = Path(data_dir)
        self.max_result_chars = max_result_chars
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name!r} is already registered.")
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return list(self._tools)

    def status_for(self, tool_name: str, args: Any) -> str:
        """A short, friendly line describing what the tool is doing, for live progress in the UI."""
        tool = self._tools.get(tool_name)
        if tool is None or not tool.status:
            return "Working…"
        try:
            return tool.status.format(**{k: str(v)[:60] for k, v in (args if isinstance(args, dict) else {}).items()})
        except (KeyError, IndexError, ValueError):
            return tool.status.split("{")[0].strip() or "Working…"

    def schemas(self) -> list[dict[str, Any]]:
        """Tool definitions in the OpenAI `tools` format."""
        return [
            {"type": "function",
             "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in self._tools.values()
        ]

    def call(self, user_id: str, tool_name: str, args: Any, meta: CallMeta | None = None) -> ToolResult:
        """Run one tool call for `user_id` and audit it. Never raises for tool problems."""
        audit_dir = user_dir(self.data_dir, user_id) / "logs"  # validates user_id first
        start = time.perf_counter()
        result = self._run(user_id, tool_name, args)
        result.duration_ms = int((time.perf_counter() - start) * 1000)
        self._audit(audit_dir, user_id, tool_name, args, result, meta or CallMeta())
        return result

    def _run(self, user_id: str, tool_name: str, args: Any) -> ToolResult:
        tool = self._tools.get(tool_name)
        if tool is None:
            return _fail("unknown_tool", f"Unknown tool {tool_name!r}. Available: {', '.join(self._tools)}.")
        if not isinstance(args, dict):
            return _fail("invalid_args", "Arguments must be a JSON object.")
        missing = [k for k in tool.parameters.get("required", []) if k not in args]
        if missing:
            return _fail("invalid_args", f"Missing required argument(s): {', '.join(missing)}.")
        if tool.requires_confirmation:
            return _fail("needs_confirmation",
                         f"{tool_name} needs the user's confirmation, which isn't available yet. "
                         "Tell the user what you would do instead of doing it.")

        future = _EXECUTOR.submit(tool.handler, args, ToolContext(user_id=user_id))
        try:
            output = future.result(timeout=tool.timeout_s)
        except FutureTimeout:
            return _fail("timeout", f"{tool_name} timed out after {tool.timeout_s:g}s.")
        except Exception as e:  # a tool failure is reported to the model, not raised
            return _fail("error", f"{tool_name} failed: {e}")

        content = json.dumps(output, ensure_ascii=False, default=str)
        if len(content) > self.max_result_chars:
            content = content[: self.max_result_chars] + f"...[truncated {len(content) - self.max_result_chars} chars]"
        return ToolResult("ok", content)

    def _audit(self, audit_dir: Path, user_id: str, tool_name: str, args: Any,
               result: ToolResult, meta: CallMeta) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "user_id": user_id,
            "tool": tool_name,
            "args": redact(args),
            "status": result.status,
            "result_chars": len(result.content),  # size only: results may hold personal data
            "duration_ms": result.duration_ms,
            "model": meta.model,
            "tier": meta.tier,
            "thinking": meta.thinking,
        }
        audit_dir.mkdir(parents=True, exist_ok=True)
        with (audit_dir / "audit.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fail(status: Status, message: str) -> ToolResult:
    return ToolResult(status, json.dumps({"error": message, "status": status}))
