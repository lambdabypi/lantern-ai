from __future__ import annotations

import json

from app.agent.lookup import NO_LOOKUP_NOTE, LookupPlanner, Plan
from app.agent.loop import AgentLoop
from app.llm.client import LLMError, TokenFactoryClient
from app.llm.router import ModelRouter
from app.tools.registry import Tool, ToolRegistry
from tests.conftest import ScriptedLLM


def planner_with(settings, replies, games="- Game: Warframe (playing)"):
    llm = ScriptedLLM(replies)
    router = ModelRouter(settings, client=TokenFactoryClient(settings, openai_client=llm))
    return LookupPlanner(router, lambda uid: games), llm


def test_lookup_needed_becomes_an_ask_game_plan(settings):
    planner, llm = planner_with(settings, [json.dumps(
        {"lookup": True, "game": "Warframe", "question": "Baro Ki'Teer current items", "alternative": ""})])
    plan = planner("alice", "what's baro selling?", [])
    assert plan == Plan(tool="ask_game", args={"game": "Warframe", "question": "Baro Ki'Teer current items"})
    call = llm.calls[0]
    assert "Warframe (playing)" in call["messages"][1]["content"]           # the player's games inform the guess
    assert call["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False and call["max_tokens"] == 200


def test_alternative_is_passed_through(settings):
    planner, _ = planner_with(settings, [json.dumps({"lookup": True, "game": "The Witcher 3",
                                                     "question": "griffin contract White Orchard",
                                                     "alternative": "Beast of White Orchard main quest"})])
    assert planner("alice", "griffin?", []).args["alternative"] == "Beast of White Orchard main quest"


def test_no_lookup_gives_the_model_a_note(settings):
    planner, _ = planner_with(settings, [json.dumps({"lookup": False, "game": None, "question": ""})])
    assert planner("alice", "I just started Hollow Knight on Switch.", []) == Plan(note=NO_LOOKUP_NOTE)


def test_unknown_game_asks_which_one(settings):
    planner, _ = planner_with(settings, [json.dumps({"lookup": True, "game": None, "question": "best build"})])
    plan = planner("alice", "what's the best build?", [])
    assert plan.tool is None and "unclear which game" in plan.note


def test_planner_failures_are_safe(settings):
    planner, _ = planner_with(settings, ["not json"])
    assert planner("alice", "hi there", []) == Plan(note=NO_LOOKUP_NOTE)

    class Broken:
        def complete(self, *a, **k):
            raise LLMError("down")
    assert LookupPlanner(Broken())("alice", "hi there", []) == Plan(note=NO_LOOKUP_NOTE)


def loop_with_plan(settings, plan, replies):
    ran = []
    tool = Tool("ask_game", "Look up.", {"type": "object", "properties": {}},
                handler=lambda a, c: ran.append(a) or {"passages": ["Saturn fissures"]}, status="Searching the {game} wiki…")
    llm = ScriptedLLM(replies)
    router = ModelRouter(settings, client=TokenFactoryClient(settings, openai_client=llm))
    loop = AgentLoop(router, ToolRegistry(settings.data_dir, [tool]), planner=lambda uid, msg, hist: plan)
    return loop, llm, ran


def test_loop_runs_the_planned_lookup_before_the_model_answers(settings):
    plan = Plan(tool="ask_game", args={"game": "Warframe", "question": "orokin cells"})
    loop, llm, ran = loop_with_plan(settings, plan, ["Farm Saturn fissures [wiki]."])
    events = []
    turn = loop.run_turn("alice", "where are orokin cells?", on_event=events.append)

    assert ran == [{"game": "Warframe", "question": "orokin cells"}]
    assert events[0] == {"type": "status", "text": "Searching the Warframe wiki…"}
    assert len(llm.calls) == 1                                    # straight to writing the answer
    sent = llm.calls[0]["messages"]
    assert sent[-2]["tool_calls"][0]["function"]["name"] == "ask_game" and "Saturn fissures" in sent[-1]["content"]
    assert llm.calls[0]["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    assert [c.name for c in turn.tool_calls] == ["ask_game"] and turn.answer == "Farm Saturn fissures [wiki]."


def test_loop_adds_the_note_for_the_model_only(settings):
    loop, llm, ran = loop_with_plan(settings, Plan(note=NO_LOOKUP_NOTE), ["Have fun! Want me to look up tips?"])
    turn = loop.run_turn("alice", "I just started Hollow Knight.")
    assert ran == []
    assert llm.calls[0]["messages"][-1]["content"].endswith(NO_LOOKUP_NOTE)
    assert turn.history_messages("I just started Hollow Knight.")[0]["content"] == "I just started Hollow Knight."
