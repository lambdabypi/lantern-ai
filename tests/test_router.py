from __future__ import annotations

import json
from dataclasses import replace

import pytest

from app.llm.client import LLMError, TokenFactoryClient
from app.llm.router import ModelRouter, Tier

MSGS = [{"role": "user", "content": "ping"}]


def make_router(settings, fake_openai):
    return ModelRouter(settings, client=TokenFactoryClient(settings, openai_client=fake_openai))


def test_defaults_to_fast_model(settings, fake_openai):
    make_router(settings, fake_openai).complete(MSGS)
    assert fake_openai.calls[0]["model"] == "nano-test"


def test_reasoning_tier_uses_ultra(settings, fake_openai):
    make_router(settings, fake_openai).complete(MSGS, Tier.REASONING)
    assert fake_openai.calls[0]["model"] == "ultra-test"


def test_missing_tier_model_fails_visibly(settings, fake_openai):
    router = make_router(replace(settings, model_reasoning=""), fake_openai)
    with pytest.raises(LLMError, match="MODEL_REASONING"):
        router.complete(MSGS, Tier.REASONING)


def test_every_call_is_logged_with_model_and_tokens(settings, fake_openai):
    router = make_router(settings, fake_openai)
    router.complete(MSGS, purpose="test")
    router.complete(MSGS, Tier.REASONING)

    lines = (settings.data_dir / "logs" / "llm_usage.jsonl").read_text().splitlines()
    entries = [json.loads(line) for line in lines]
    assert [(e["tier"], e["model"]) for e in entries] == [("fast", "nano-test"), ("reasoning", "ultra-test")]
    assert entries[0]["purpose"] == "test"
    assert entries[0]["prompt_tokens"] == 10 and entries[0]["completion_tokens"] == 2


def test_usage_summary_excludes_cached_tokens(settings, fake_openai):
    router = make_router(settings, fake_openai)
    router.complete(MSGS)
    router.complete(MSGS)  # identical -> served from cache
    assert router.usage_summary() == {
        "nano-test": {"calls": 1, "cached_calls": 1, "prompt_tokens": 10, "completion_tokens": 2}
    }
