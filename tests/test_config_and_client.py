from __future__ import annotations

from dataclasses import replace

import pytest

from app.config import ConfigError, load_settings
from app.llm.client import LLMError, TokenFactoryClient


def test_require_names_missing_env_vars(settings):
    s = replace(settings, model_fast="", tavily_api_key="  ")
    with pytest.raises(ConfigError, match="MODEL_FAST, TAVILY_API_KEY"):
        s.require("model_fast", "model_reasoning", "tavily_api_key")


def test_load_settings_reads_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_FAST", " nano ")
    monkeypatch.setenv("CACHE_ENABLED", "false")
    s = load_settings(env_file=tmp_path / "missing.env")
    assert s.model_fast == "nano"
    assert s.cache_enabled is False


def test_client_requires_credentials(settings):
    with pytest.raises(ConfigError, match="TOKEN_FACTORY_API_KEY"):
        TokenFactoryClient(replace(settings, token_factory_api_key=""))


def test_chat_returns_text_and_usage(settings, fake_openai):
    client = TokenFactoryClient(settings, openai_client=fake_openai)
    r = client.chat("nano-test", [{"role": "user", "content": "ping"}])
    assert (r.text, r.model, r.prompt_tokens, r.completion_tokens, r.cached) == ("pong", "nano-test", 10, 2, False)


def test_identical_calls_are_cached(settings, fake_openai):
    client = TokenFactoryClient(settings, openai_client=fake_openai)
    msgs = [{"role": "user", "content": "ping"}]
    client.chat("nano-test", msgs, temperature=0)
    second = client.chat("nano-test", msgs, temperature=0)
    assert second.cached is True
    assert len(fake_openai.calls) == 1
    client.chat("nano-test", msgs, temperature=0.5)  # different params -> miss
    assert len(fake_openai.calls) == 2


def test_cache_can_be_bypassed(settings, fake_openai):
    client = TokenFactoryClient(settings, openai_client=fake_openai)
    msgs = [{"role": "user", "content": "ping"}]
    client.chat("nano-test", msgs, use_cache=False)
    client.chat("nano-test", msgs, use_cache=False)
    assert len(fake_openai.calls) == 2


def test_empty_model_fails_visibly(settings, fake_openai):
    client = TokenFactoryClient(settings, openai_client=fake_openai)
    with pytest.raises(LLMError, match="MODEL_FAST"):
        client.chat("", [{"role": "user", "content": "ping"}])
