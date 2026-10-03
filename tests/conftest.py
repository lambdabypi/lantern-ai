from __future__ import annotations

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
