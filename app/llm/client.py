"""Nebius Token Factory client (OpenAI-compatible API)."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

import openai
from openai import OpenAI

from app.cache import DiskCache, cache_key
from app.config import Settings


class LLMError(RuntimeError):
    """An LLM call failed. The message is safe to show to the user."""


@dataclass
class ChatResult:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    cached: bool = False
    # OpenAI-style tool calls requested by the model: [{"id", "name", "arguments" (JSON string)}]
    tool_calls: list[dict[str, str]] = field(default_factory=list)
    finish_reason: str = ""


class TokenFactoryClient:
    def __init__(self, settings: Settings, openai_client: Any | None = None):
        settings.require("token_factory_base_url", "token_factory_api_key")
        self.base_url = settings.token_factory_base_url
        self._client = openai_client or OpenAI(
            base_url=settings.token_factory_base_url,
            api_key=settings.token_factory_api_key,
        )
        self._cache = DiskCache(settings.cache_dir, "llm", enabled=settings.cache_enabled)

    def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        use_cache: bool = True,
        **params: Any,
    ) -> ChatResult:
        """Send one chat completion. Identical calls are served from the dev cache."""
        if not model:
            raise LLMError("No model ID given. Set MODEL_FAST / MODEL_REASONING in .env.")

        key = cache_key({"base_url": self.base_url, "model": model, "messages": messages, "params": params})
        if use_cache and (hit := self._cache.get(key)) is not None:
            return ChatResult(**{**hit, "latency_ms": 0, "cached": True})

        start = time.perf_counter()
        try:
            resp = self._client.chat.completions.create(model=model, messages=messages, **params)
        except openai.AuthenticationError as e:
            raise LLMError("Token Factory rejected the API key (TOKEN_FACTORY_API_KEY).") from e
        except openai.NotFoundError as e:
            raise LLMError(f"Token Factory doesn't know model '{model}'. Check the ID in the console.") from e
        except openai.RateLimitError as e:
            raise LLMError("Token Factory rate limit or quota hit. Wait a moment or check your credits.") from e
        except openai.APIConnectionError as e:
            raise LLMError(f"Couldn't reach Token Factory at {self.base_url}.") from e
        except openai.APIError as e:
            raise LLMError(f"Token Factory error for model '{model}': {e}") from e
        latency_ms = int((time.perf_counter() - start) * 1000)

        if not resp.choices:
            raise LLMError(f"Token Factory returned no choices for model '{model}'.")
        usage = resp.usage
        choice = resp.choices[0]
        message = choice.message
        result = ChatResult(
            text=message.content or "",
            model=resp.model or model,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=latency_ms,
            tool_calls=[
                {"id": c.id, "name": c.function.name, "arguments": c.function.arguments or ""}
                for c in (getattr(message, "tool_calls", None) or [])
            ],
            finish_reason=getattr(choice, "finish_reason", "") or "",
        )
        if use_cache:
            self._cache.set(key, {k: v for k, v in asdict(result).items() if k != "cached"})
        return result
