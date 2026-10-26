from __future__ import annotations

import json

from app.agent.loop import AgentLoop, LoopLimits
from app.llm.client import LLMError, TokenFactoryClient
from app.llm.router import ModelRouter
from app.tools.builtin import builtin_tools
from app.tools.registry import Tool, ToolRegistry
from app.tools.web_search import WebSearch
from tests.conftest import ScriptedLLM, tool_call
from tests.test_parsing_and_search import FakeTavily


def make_loop(settings, replies, limits=None, extra_tools=(), **llm_kw):
    llm = ScriptedLLM(replies, **llm_kw)
    router = ModelRouter(settings, client=TokenFactoryClient(settings, openai_client=llm))
    tools = builtin_tools(settings, WebSearch(settings, tavily_client=FakeTavily())) + list(extra_tools)
    loop = AgentLoop(router, ToolRegistry(settings.data_dir, tools), limits)
    return loop, llm


def audit(settings, user="demo"):
    path = settings.data_dir / "users" / user / "logs" / "audit.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_full_turn_question_tool_result_answer(settings):
    loop, llm = make_loop(settings, [
        [tool_call("calculator", {"expression": "1234 * 5678"})],
        "1234 × 5678 = 7,006,652.",
    ])
    turn = loop.run_turn("demo", "What is 1234 * 5678?")

    assert (turn.stop_reason, turn.answer) == ("done", "1234 × 5678 = 7,006,652.")
    assert [(c.name, c.status) for c in turn.tool_calls] == [("calculator", "ok")]
    assert (turn.prompt_tokens, turn.completion_tokens, turn.model, turn.tier) == (200, 40, "nano-test", "fast")

    first, second = llm.calls
    assert first["model"] == "nano-test" and "extra_body" not in first  # Nano, default thinking
    assert [t["function"]["name"] for t in first["tools"]] == ["calculator", "web_search"]
    assistant, tool_msg = second["messages"][-2:]
    assert assistant["tool_calls"][0]["function"]["name"] == "calculator"
    assert tool_msg == {"role": "tool", "tool_call_id": "call_1",
                        "content": json.dumps({"expression": "1234 * 5678", "result": 7006652})}

    (entry,) = audit(settings)
    assert (entry["tool"], entry["status"], entry["model"], entry["tier"], entry["thinking"]) == \
           ("calculator", "ok", "nano-test", "fast", "default")


def test_system_prompt_states_identity_tools_and_limits(settings):
    loop, llm = make_loop(settings, ["Hi."])
    loop.run_turn("demo", "who are you?")
    prompt = llm.calls[0]["messages"][0]["content"]
    assert "You are Lantern, a personal AI that watches official U.S. safety recalls" in prompt
    assert "not a general-purpose assistant" in prompt
    assert "- calculator: Evaluate an arithmetic expression exactly." in prompt
    assert "- web_search:" in prompt
    assert "Never state recall or safety facts unless recall_check or check_before_buying returned them" in prompt
    assert "Never say their car has or hasn't" in prompt and "https://www.nhtsa.gov/recalls" in prompt
    assert "Nothing saved yet." in prompt  # no profile provider -> neutral placeholder


def test_profile_context_reaches_the_prompt_per_user(settings):
    llm = ScriptedLLM(["ok", "ok"])
    router = ModelRouter(settings, client=TokenFactoryClient(settings, openai_client=llm))
    profiles = {"alice": "- Game: Warframe (playing, progress: Saturn {x})", "bob": "Nothing saved yet."}
    loop = AgentLoop(router, ToolRegistry(settings.data_dir, []), profile_context=profiles.__getitem__)
    loop.run_turn("alice", "hi")
    loop.run_turn("bob", "hi")
    assert "Warframe (playing, progress: Saturn {x})" in llm.calls[0]["messages"][0]["content"]  # braces kept
    assert "Warframe" not in llm.calls[1]["messages"][0]["content"]


def test_system_prompt_without_tools(settings):
    loop = AgentLoop(ModelRouter(settings, client=TokenFactoryClient(settings, openai_client=ScriptedLLM([]))),
                     ToolRegistry(settings.data_dir, []))
    assert "- None: you can only talk." in loop.system_prompt()


def test_escalate_uses_ultra(settings):
    loop, llm = make_loop(settings, ["Planned."])
    turn = loop.run_turn("demo", "Plan my week", escalate=True)
    assert llm.calls[0]["model"] == "ultra-test" and turn.tier == "reasoning"


def test_bad_tool_args_are_reported_to_model_and_loop_continues(settings):
    loop, llm = make_loop(settings, [
        [tool_call("calculator", "{not json")],
        [tool_call("nonexistent", {})],
        "Sorry, I couldn't compute that.",
    ])
    turn = loop.run_turn("demo", "?")
    assert turn.stop_reason == "done"
    assert [c.status for c in turn.tool_calls] == ["invalid_args", "unknown_tool"]


def test_confirmation_tool_is_not_run(settings):
    ran = []
    send = Tool("send_email", "Send an email.", {"type": "object", "properties": {}},
                handler=lambda a, c: ran.append(a), requires_confirmation=True)
    loop, llm = make_loop(settings, [[tool_call("send_email", {})], "I'd need your confirmation."],
                          extra_tools=[send])
    turn = loop.run_turn("demo", "Email Bob")
    assert ran == [] and turn.tool_calls[0].status == "needs_confirmation"
    assert "needs_confirmation" in llm.calls[1]["messages"][-1]["content"]


def test_max_tool_calls_stops_with_clear_message(settings):
    replies = [[tool_call("calculator", {"expression": f"{i}+1"}, f"c{i}")] for i in range(10)]
    loop, llm = make_loop(settings, replies, limits=LoopLimits(max_tool_calls=6))
    turn = loop.run_turn("demo", "loop forever")
    assert turn.stop_reason == "max_tool_calls"
    assert len(turn.tool_calls) == 6 and len(llm.calls) == 7
    assert "6 tool calls" in turn.answer and "limit is 6" in turn.answer


def test_parallel_calls_count_toward_the_limit(settings):
    many = [tool_call("calculator", {"expression": "1+1"}, f"c{i}") for i in range(4)]
    loop, _ = make_loop(settings, [many, many], limits=LoopLimits(max_tool_calls=6))
    turn = loop.run_turn("demo", "?")
    assert turn.stop_reason == "max_tool_calls" and len(turn.tool_calls) == 4


def test_token_budget_stops_with_clear_message(settings):
    loop, llm = make_loop(settings, [[tool_call("calculator", {"expression": "1+1"})], "never reached"],
                          limits=LoopLimits(max_tokens_per_turn=500), prompt_tokens=600)
    turn = loop.run_turn("demo", "?")
    assert turn.stop_reason == "token_budget" and "budget of 500" in turn.answer
    assert len(llm.calls) == 1


def test_llm_failure_is_visible(settings):
    class Broken:
        def chat(self, *a, **k):
            raise LLMError("Token Factory rate limit or quota hit.")

    router = ModelRouter(settings, client=Broken())
    loop = AgentLoop(router, ToolRegistry(settings.data_dir, []))
    turn = loop.run_turn("demo", "hi")
    assert turn.stop_reason == "llm_error" and "rate limit" in turn.answer


def test_history_keeps_only_question_and_answer(settings):
    loop, _ = make_loop(settings, [[tool_call("calculator", {"expression": "2+2"})], "4"])
    turn = loop.run_turn("demo", "2+2?")
    assert turn.history_messages("2+2?") == [{"role": "user", "content": "2+2?"},
                                             {"role": "assistant", "content": "4"}]


def _thinking(call):
    return call.get("extra_body", {}).get("chat_template_kwargs", {}).get("enable_thinking")


def test_answer_after_tool_results_is_written_with_thinking_off(settings):
    loop, llm = make_loop(settings, [[tool_call("calculator", {"expression": "2+2"})], "4"])
    loop.run_turn("demo", "2+2?")
    assert _thinking(llm.calls[0]) is None      # tool choice: model default
    assert _thinking(llm.calls[1]) is False     # writing the answer from tool results


def test_escalated_turns_keep_full_thinking(settings):
    loop, llm = make_loop(settings, [[tool_call("calculator", {"expression": "2+2"})], "4"])
    loop.run_turn("demo", "2+2?", escalate=True)
    assert _thinking(llm.calls[1]) is None


def test_empty_reply_is_retried_without_thinking(settings):
    loop, llm = make_loop(settings, ["", "Here's the answer."])
    turn = loop.run_turn("demo", "hi")
    assert turn.answer == "Here's the answer." and turn.stop_reason == "done"
    assert _thinking(llm.calls[1]) is False and turn.prompt_tokens == 200  # both calls counted


def test_two_empty_replies_fail_visibly(settings):
    loop, llm = make_loop(settings, [[tool_call("calculator", {"expression": "1+1"})], "", ""])
    turn = loop.run_turn("demo", "1+1?")
    # after tools the first answer call is already thinking-off, so it isn't retried again
    assert turn.stop_reason == "llm_error" and "couldn't put an answer together" in turn.answer


def test_on_event_streams_status_then_answer_text(settings):
    loop, llm = make_loop(settings, [[tool_call("calculator", {"expression": "12*7"})], "12 × 7 = 84."])
    events = []
    turn = loop.run_turn("demo", "12*7?", on_event=events.append)
    assert events[0] == {"type": "status", "text": "Calculating…"}
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert "".join(deltas) == "12 × 7 = 84." == turn.answer and len(deltas) == 2   # arrived in pieces
    assert all(c.get("stream") for c in llm.calls) and turn.prompt_tokens == 200   # usage still counted


def test_answers_after_tools_are_capped(settings):
    loop, llm = make_loop(settings, [[tool_call("calculator", {"expression": "1+1"})], "2"])
    loop.run_turn("demo", "1+1?")
    assert llm.calls[0]["max_tokens"] == 4096 and llm.calls[1]["max_tokens"] == 900


def test_answers_from_tool_results_use_the_answer_tier(settings):
    from dataclasses import replace
    from app.llm.router import Tier
    s = replace(settings, model_balanced="super-test")
    llm = ScriptedLLM([[tool_call("calculator", {"expression": "2+2"})], "4", [tool_call("calculator", {"expression": "2+2"})], "4"])
    router = ModelRouter(s, client=TokenFactoryClient(s, openai_client=llm))
    loop = AgentLoop(router, ToolRegistry(s.data_dir, builtin_tools(s, WebSearch(s, tavily_client=FakeTavily()))),
                     answer_tier=Tier.BALANCED)
    turn = loop.run_turn("demo", "2+2?")
    assert [c["model"] for c in llm.calls] == ["nano-test", "super-test"] and turn.tier == "balanced"
    loop.run_turn("demo", "2+2?", escalate=True)
    assert [c["model"] for c in llm.calls[2:]] == ["ultra-test", "ultra-test"]  # "Think harder" stays on Ultra


def test_answer_tier_falls_back_to_fast_when_super_is_not_configured(settings):
    from app.llm.router import Tier
    llm = ScriptedLLM([[tool_call("calculator", {"expression": "2+2"})], "4"])
    router = ModelRouter(settings, client=TokenFactoryClient(settings, openai_client=llm))
    loop = AgentLoop(router, ToolRegistry(settings.data_dir, builtin_tools(settings, WebSearch(settings, tavily_client=FakeTavily()))),
                     answer_tier=Tier.BALANCED)
    loop.run_turn("demo", "2+2?")
    assert [c["model"] for c in llm.calls] == ["nano-test", "nano-test"]
