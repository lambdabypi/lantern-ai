"""Live check: does Nemotron on Token Factory support OpenAI-style tool calls?

For each model setting it runs:
  1. a question that needs a tool  -> expects a tool_call with valid JSON arguments,
     then sends the tool result back and expects a final answer that uses it
  2. a question that needs no tool -> expects a plain answer, no tool_call
     (a wrong answer is flagged separately: it's a reasoning issue, not a tool-calling one)

Usage:
    python scripts/smoke_tools.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import openai  # noqa: E402
from openai import OpenAI  # noqa: E402

from app.config import ConfigError, load_settings  # noqa: E402

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_reminders",
            "description": "List the user's reminders for a given date.",
            "parameters": {
                "type": "object",
                "properties": {"date": {"type": "string", "description": "ISO date, e.g. 2026-10-05"}},
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]
SYSTEM = {"role": "system", "content": "You are a personal assistant. Today is 2026-10-03. Use tools when needed."}
NEEDS_TOOL = {"role": "user", "content": "What reminders do I have on 2026-10-05?"}
NO_TOOL = {"role": "user", "content": "What is 12 times 7? Answer directly."}
FAKE_RESULT = json.dumps({"reminders": ["Dentist at 3pm", "Submit expense report"]})


def check(client: OpenAI, label: str, model: str, extra_body: dict | None) -> bool:
    kw = {"model": model, "tools": TOOLS, "temperature": 0, "max_tokens": 4096}
    if extra_body:
        kw["extra_body"] = extra_body
    ok = True
    try:
        t0 = time.perf_counter()
        r = client.chat.completions.create(messages=[SYSTEM, NEEDS_TOOL], **kw)
        msg, fin = r.choices[0].message, r.choices[0].finish_reason
        calls = msg.tool_calls or []
        if not calls:
            print(f"  FAIL {label}: no tool_call (finish={fin}, content={(msg.content or '')[:80]!r})")
            return False
        call = calls[0]
        args = json.loads(call.function.arguments)  # raises if not valid JSON
        right = call.function.name == "get_reminders" and args.get("date") == "2026-10-05"
        ok &= right
        print(f"  {'ok  ' if right else 'FAIL'} {label}: tool_call {call.function.name}({args}) "
              f"finish={fin} calls={len(calls)} out={r.usage.completion_tokens}")

        follow = [SYSTEM, NEEDS_TOOL,
                  {"role": "assistant", "content": msg.content or "",
                   "tool_calls": [c.model_dump(include={"id", "type", "function"}) for c in calls]},
                  {"role": "tool", "tool_call_id": call.id, "content": FAKE_RESULT}]
        r2 = client.chat.completions.create(messages=follow, **kw)
        answer = r2.choices[0].message.content or ""
        used = "Dentist" in answer or "dentist" in answer
        ok &= used
        print(f"  {'ok  ' if used else 'FAIL'} {label}: answer after tool result uses it: {answer.strip()[:90]!r}")

        r3 = client.chat.completions.create(messages=[SYSTEM, NO_TOOL], **kw)
        m3 = r3.choices[0].message
        no_call = not m3.tool_calls
        ok &= no_call
        reply = (m3.content or "").strip()
        note = "" if "84" in reply else "  (no tool call, but WRONG answer: reasoning quality, not tool support)"
        print(f"  {'ok  ' if no_call else 'FAIL'} {label}: no-tool question -> "
              f"{'tool_call!' if m3.tool_calls else repr(reply[:40])}{note}"
              f"  ({time.perf_counter() - t0:.1f}s total)")
    except (openai.APIError, json.JSONDecodeError) as e:
        print(f"  FAIL {label}: {type(e).__name__}: {str(e)[:200]}")
        return False
    return ok


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        s = load_settings(ROOT / ".env")
        s.require("token_factory_base_url", "token_factory_api_key", "model_fast", "model_reasoning")
    except ConfigError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    client = OpenAI(base_url=s.token_factory_base_url, api_key=s.token_factory_api_key)
    off = {"chat_template_kwargs": {"enable_thinking": False}}
    results = {
        "Nano, thinking off": check(client, "Nano, thinking off", s.model_fast, off),
        "Nano, thinking default": check(client, "Nano, thinking default", s.model_fast, None),
        "Ultra, thinking default": check(client, "Ultra, thinking default", s.model_reasoning, None),
        "Ultra, thinking off": check(client, "Ultra, thinking off", s.model_reasoning, off),
    }
    print("\nSummary:")
    for label, passed in results.items():
        print(f"  {'PASS' if passed else 'FAIL'}  {label}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
