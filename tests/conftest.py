from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        token_factory_base_url="https://tokenfactory.test/v1/",
        token_factory_api_key="test-key",
        model_fast="nano-test",
        model_reasoning="ultra-test",
        tavily_api_key="tavily-test",
        app_secret_key="secret",
        data_dir=tmp_path / "data",
        cache_enabled=True,
        cache_dir=tmp_path / "cache",
    )


class FakeOpenAI:
    """Stands in for openai.OpenAI; records calls and returns a canned reply."""

    def __init__(self, reply: str = "pong", prompt_tokens: int = 10, completion_tokens: int = 2):
        self.calls: list[dict] = []
        self.reply = reply
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            model=kwargs["model"],
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))],
            usage=SimpleNamespace(prompt_tokens=self.prompt_tokens, completion_tokens=self.completion_tokens),
        )


@pytest.fixture
def fake_openai() -> FakeOpenAI:
    return FakeOpenAI()


def tool_call(name: str, arguments: str | dict, call_id: str = "call_1") -> dict:
    """Build one scripted tool call for ScriptedLLM."""
    args = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return {"id": call_id, "name": name, "arguments": args}


class ScriptedLLM:
    """Stands in for openai.OpenAI, returning scripted replies in order (no tokens spent).

    Each reply is either a string (final answer) or a list of tool calls from tool_call().
    """

    def __init__(self, replies: list, prompt_tokens: int = 100, completion_tokens: int = 20):
        self.replies = list(replies)
        self.calls: list[dict] = []
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.replies:
            raise AssertionError("ScriptedLLM ran out of replies")
        reply = self.replies.pop(0)
        if isinstance(reply, str):
            message, finish = SimpleNamespace(content=reply, tool_calls=None), "stop"
        else:
            calls = [
                SimpleNamespace(id=c["id"], type="function",
                                function=SimpleNamespace(name=c["name"], arguments=c["arguments"]))
                for c in reply
            ]
            message, finish = SimpleNamespace(content="", tool_calls=calls), "tool_calls"
        return SimpleNamespace(
            model=kwargs["model"],
            choices=[SimpleNamespace(message=message, finish_reason=finish)],
            usage=SimpleNamespace(prompt_tokens=self.prompt_tokens, completion_tokens=self.completion_tokens),
        )
