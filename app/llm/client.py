"""Nebius Token Factory client (OpenAI-compatible API)."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import openai
from openai import OpenAI

from app.cache import DiskCache, cache_key
from app.config import Settings


class LLMError(RuntimeError):
    """An LLM call failed. The message is safe to show to the user."""


class LLMTimeout(LLMError):
    """The model didn't answer within the call's timeout."""


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
            timeout=120.0,  # the SDK default is 10 minutes; Ultra with long reasoning takes ~20-40 s
            max_retries=2,
        )
        self._cache = DiskCache(settings.cache_dir, "llm", enabled=settings.cache_enabled)

    def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        use_cache: bool = True,
        on_delta: Callable[[str], None] | None = None,
        retries: int | None = None,
        **params: Any,
    ) -> ChatResult:
        """Send one chat completion. Identical calls are served from the dev cache.

        on_delta: if given, the reply is streamed and each piece of text is passed to it as it
        arrives (tool calls are still assembled and returned whole).
        """
        if not model:
            raise LLMError("No model ID given. Set MODEL_FAST / MODEL_REASONING in .env.")

        key = cache_key({"base_url": self.base_url, "model": model, "messages": messages, "params": params})
        if use_cache and (hit := self._cache.get(key)) is not None:
            cached = ChatResult(**{**hit, "latency_ms": 0, "cached": True})
            if on_delta and cached.text:
                on_delta(cached.text)
            return cached

        # retries: override the client's automatic retries for this call (0 = fail fast, so the router can try
        # another model instead of waiting through retries of a slow one)
        api = self._client.with_options(max_retries=retries)             if retries is not None and hasattr(self._client, "with_options") else self._client
        start = time.perf_counter()
        try:
            if on_delta is not None:
                result = self._stream(api, model, messages, on_delta, params)
            else:
                result = self._parse(model, api.chat.completions.create(model=model, messages=messages, **params))
        except openai.AuthenticationError as e:
            raise LLMError("Token Factory rejected the API key (TOKEN_FACTORY_API_KEY).") from e
        except openai.NotFoundError as e:
            raise LLMError(f"Token Factory doesn't know model '{model}'. Check the ID in the console.") from e
        except openai.RateLimitError as e:
            raise LLMError("Token Factory rate limit or quota hit. Wait a moment or check your credits.") from e
        except openai.APITimeoutError as e:
            raise LLMTimeout(f"Token Factory didn't answer in time (model '{model}').") from e
        except openai.APIConnectionError as e:
            raise LLMError(f"Couldn't reach Token Factory at {self.base_url}.") from e
        except openai.APIError as e:
            raise LLMError(f"Token Factory error for model '{model}': {e}") from e
        result.latency_ms = int((time.perf_counter() - start) * 1000)

        if use_cache:
            self._cache.set(key, {k: v for k, v in asdict(result).items() if k != "cached"})
        return result

    @staticmethod
    def _parse(model: str, resp: Any) -> ChatResult:
        if not resp.choices:
            raise LLMError(f"Token Factory returned no choices for model '{model}'.")
        usage = resp.usage
        choice = resp.choices[0]
        message = choice.message
        return ChatResult(
            text=message.content or "",
            model=resp.model or model,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=0,
            tool_calls=[
                {"id": c.id, "name": c.function.name, "arguments": c.function.arguments or ""}
                for c in (getattr(message, "tool_calls", None) or [])
            ],
            finish_reason=getattr(choice, "finish_reason", "") or "",
        )

    def _stream(self, api: Any, model: str, messages: list[dict[str, str]], on_delta: Callable[[str], None],
                params: dict[str, Any]) -> ChatResult:
        """Stream a completion: text goes to on_delta as it arrives; tool calls arrive in pieces and
        are assembled by index. Usage comes in the final chunk (stream_options.include_usage)."""
        stream = api.chat.completions.create(
            model=model, messages=messages, stream=True, stream_options={"include_usage": True}, **params)
        text: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        usage, finish, model_name = None, "", model
        for chunk in stream:
            model_name = getattr(chunk, "model", None) or model_name
            usage = getattr(chunk, "usage", None) or usage
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if getattr(delta, "content", None):
                text.append(delta.content)
                on_delta(delta.content)
            for piece in getattr(delta, "tool_calls", None) or []:
                call = calls.setdefault(piece.index, {"id": "", "name": "", "arguments": ""})
                call["id"] = piece.id or call["id"]
                if piece.function is not None:
                    call["name"] += piece.function.name or ""
                    call["arguments"] += piece.function.arguments or ""
            finish = choice.finish_reason or finish
        return ChatResult(
            text="".join(text), model=model_name,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=0, tool_calls=[calls[i] for i in sorted(calls)], finish_reason=finish,
        )
