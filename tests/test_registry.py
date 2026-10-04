from __future__ import annotations

import json
import time

import pytest

from app.tools.registry import CallMeta, Tool, ToolRegistry, redact

SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}


def make(settings, **tool_kw):
    calls = []

    def handler(args, ctx):
        calls.append((args, ctx.user_id))
        return {"echo": args["x"]}

    tool = Tool(name="echo", description="Echo x.", parameters=SCHEMA, handler=handler, **tool_kw)
    return ToolRegistry(settings.data_dir, [tool]), calls


def audit_lines(settings, user="demo"):
    path = settings.data_dir / "users" / user / "logs" / "audit.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_schemas_use_openai_tools_format(settings):
    reg, _ = make(settings)
    assert reg.schemas() == [{"type": "function",
                              "function": {"name": "echo", "description": "Echo x.", "parameters": SCHEMA}}]


def test_ok_call_runs_handler_with_user(settings):
    reg, calls = make(settings)
    r = reg.call("demo", "echo", {"x": "hi"})
    assert r.ok and json.loads(r.content) == {"echo": "hi"}
    assert calls == [({"x": "hi"}, "demo")]


@pytest.mark.parametrize("name,args,status", [
    ("nope", {"x": "1"}, "unknown_tool"),
    ("echo", ["x"], "invalid_args"),
    ("echo", {}, "invalid_args"),
])
def test_bad_calls_return_status_without_running(settings, name, args, status):
    reg, calls = make(settings)
    r = reg.call("demo", name, args)
    assert r.status == status and "error" in json.loads(r.content)
    assert calls == []
    assert audit_lines(settings)[0]["status"] == status


def test_confirmation_tools_are_refused(settings):
    reg, calls = make(settings, requires_confirmation=True)
    r = reg.call("demo", "echo", {"x": "send it"})
    assert r.status == "needs_confirmation"
    assert calls == []


def test_timeout(settings):
    tool = Tool("slow", "Sleeps.", {"type": "object", "properties": {}},
                handler=lambda a, c: time.sleep(2), timeout_s=0.1)
    r = ToolRegistry(settings.data_dir, [tool]).call("demo", "slow", {})
    assert r.status == "timeout" and "timed out" in r.content


def test_handler_exception_becomes_error_result(settings):
    def boom(args, ctx):
        raise RuntimeError("upstream down")
    tool = Tool("boom", "Fails.", {"type": "object", "properties": {}}, handler=boom)
    r = ToolRegistry(settings.data_dir, [tool]).call("demo", "boom", {})
    assert r.status == "error" and "upstream down" in r.content


def test_large_results_are_truncated(settings):
    tool = Tool("big", "Big.", {"type": "object", "properties": {}}, handler=lambda a, c: "x" * 500)
    r = ToolRegistry(settings.data_dir, [tool], max_result_chars=100).call("demo", "big", {})
    assert r.content.startswith('"xxx') and "truncated" in r.content and len(r.content) < 140


def test_audit_entry_fields_and_redaction(settings):
    reg, _ = make(settings)
    long_text = "memory " * 100
    reg.call("demo", "echo", {"x": "hi", "api_key": "abc", "note": "Bearer s3cr3t", "body": long_text},
             CallMeta(model="nano-test", tier="fast", thinking="default"))
    e = audit_lines(settings)[0]
    assert {k for k in e} == {"ts", "user_id", "tool", "args", "status", "result_chars", "duration_ms",
                              "model", "tier", "thinking"}
    assert (e["user_id"], e["tool"], e["status"], e["model"], e["tier"], e["thinking"]) == \
           ("demo", "echo", "ok", "nano-test", "fast", "default")
    assert e["args"]["api_key"] == "[REDACTED]" and e["args"]["note"] == "[REDACTED]"
    assert len(e["args"]["body"]) < 250 and "chars]" in e["args"]["body"]
    assert e["result_chars"] == len('{"echo": "hi"}')


def test_audit_is_per_user(settings):
    reg, _ = make(settings)
    reg.call("alice", "echo", {"x": "1"})
    reg.call("bob", "echo", {"x": "2"})
    assert [e["user_id"] for e in audit_lines(settings, "alice")] == ["alice"]
    assert [e["user_id"] for e in audit_lines(settings, "bob")] == ["bob"]


@pytest.mark.parametrize("bad", ["../etc", "Demo", "", "a/b", "x" * 65])
def test_unsafe_user_ids_are_rejected(settings, bad):
    reg, calls = make(settings)
    with pytest.raises(ValueError):
        reg.call(bad, "echo", {"x": "1"})
    assert calls == []


def test_redact_handles_nesting():
    assert redact({"a": [{"token": "t"}, "sk-abcdefghijk"]}) == {"a": [{"token": "[REDACTED]"}, "[REDACTED]"]}
