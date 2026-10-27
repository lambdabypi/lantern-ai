"""Choose Nemotron Nano or Ultra per call, and log every call's model and token counts."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from app.config import Settings
from app.llm.client import ChatResult, LLMError, LLMTimeout, TokenFactoryClient


class Tier(str, Enum):
    FAST = "fast"            # Nemotron Nano: the default for everyday calls
    BALANCED = "balanced"    # Nemotron Super: writing answers from looked-up sources (falls back to Nano)
    REASONING = "reasoning"  # Nemotron Ultra: multi-step planning and heavy reasoning only
    VISION = "vision"        # Nemotron Super VL: reading photos (product labels)


_TIER_ENV = {Tier.FAST: "MODEL_FAST", Tier.BALANCED: "MODEL_BALANCED", Tier.REASONING: "MODEL_REASONING",
             Tier.VISION: "MODEL_VISION"}

# Token Factory list prices in US dollars per million tokens (input, output), 27 Oct 2026. Used only to keep the
# site's daily spend under SITE_MODEL_DOLLARS_PER_DAY; Super VL's price wasn't listed, so it is counted at Ultra's.
PRICES = {Tier.FAST: (0.06, 0.24), Tier.BALANCED: (0.30, 0.90), Tier.REASONING: (1.00, 3.00), Tier.VISION: (1.00, 3.00)}
MICRO = 1_000_000
# Nano is the first step of every chat turn and usually answers in about a second, but on 27 Oct it took 30-198 s
# at times while Super answered in under 2 s. So a Nano call that takes longer than this is retried once on Super.
FAST_TIMEOUT_S = 6.0
log = logging.getLogger(__name__)


class SpendCapReached(LLMError):
    """The site's daily model budget is used up. The message is safe to show to the user."""


def call_cost(tier: Tier, prompt_tokens: int, completion_tokens: int) -> float:
    price_in, price_out = PRICES[tier]
    return (prompt_tokens * price_in + completion_tokens * price_out) / MICRO


class ModelRouter:
    def __init__(
        self,
        settings: Settings,
        client: TokenFactoryClient | None = None,
        usage_log: Path | None = None,
        spend: Any = None,
    ):
        self.models = {Tier.FAST: settings.model_fast, Tier.REASONING: settings.model_reasoning,
                       Tier.BALANCED: settings.model_balanced or settings.model_fast, Tier.VISION: settings.model_vision}
        self.client = client or TokenFactoryClient(settings)
        self.usage_log = usage_log or settings.data_dir / "logs" / "llm_usage.jsonl"
        self._totals: dict[str, dict[str, int]] = {}
        # A SiteQuota: every call's estimated cost is counted in millionths of a dollar, and calls stop for the day
        # once the site has spent site_model_dollars_per_day (0 = no cap). Covers chat, photos and the daily job.
        self.spend = spend
        self.spend_cap = int(getattr(settings, "site_model_dollars_per_day", 0) * MICRO)

    def complete(
        self,
        messages: list[dict[str, Any]],
        tier: Tier = Tier.FAST,
        *,
        purpose: str = "",
        thinking: bool | None = None,
        **params: Any,
    ) -> ChatResult:
        """Run one chat call on the model for `tier` (Nano unless told otherwise).

        `thinking`: None keeps the model default (Nemotron 3 reasons by default),
        False turns reasoning off for simple calls such as extraction, True forces it on.
        Uses `chat_template_kwargs.enable_thinking`, confirmed on Token Factory. It is
        on/off only: `reasoning_budget` and `reasoning_effort` had no reliable effect.
        """
        model = self.models[tier]
        if not model:
            raise LLMError(f"No model configured for the {tier.value} tier. Set {_TIER_ENV[tier]} in .env.")
        if thinking is not None:
            extra = dict(params.pop("extra_body", None) or {})
            extra["chat_template_kwargs"] = {**extra.get("chat_template_kwargs", {}), "enable_thinking": thinking}
            params["extra_body"] = extra
        if self.spend is not None and self.spend_cap and self.spend.used("model_spend") >= self.spend_cap:
            raise SpendCapReached("Lantern's demo has used today's budget for AI answers. It resets at midnight "
                                  "UTC. The recall lists still work.")
        backup = self.models[Tier.BALANCED]
        if tier is Tier.FAST and backup and backup != model and "timeout" not in params:
            try:
                result = self.client.chat(model, messages, timeout=FAST_TIMEOUT_S, retries=0, **params)
            except LLMTimeout:
                log.warning("Nano took over %.0f s for %s; asking Super instead", FAST_TIMEOUT_S, purpose or "a call")
                tier, model = Tier.BALANCED, backup
                result = self.client.chat(model, messages, **params)
        else:
            result = self.client.chat(model, messages, **params)
        self._record(tier, purpose, result, thinking)
        if self.spend is not None and not result.cached:
            cost = call_cost(tier, result.prompt_tokens, result.completion_tokens)
            self.spend.try_consume("model_spend", None, max(1, round(cost * MICRO)))
        return result

    def usage_summary(self) -> dict[str, dict[str, int]]:
        """Per-model totals for this router's lifetime. Cached calls cost no tokens."""
        return {model: dict(t) for model, t in self._totals.items()}

    def _record(self, tier: Tier, purpose: str, r: ChatResult, thinking: bool | None) -> None:
        t = self._totals.setdefault(
            r.model, {"calls": 0, "cached_calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        )
        if r.cached:
            t["cached_calls"] += 1
        else:
            t["calls"] += 1
            t["prompt_tokens"] += r.prompt_tokens
            t["completion_tokens"] += r.completion_tokens

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tier": tier.value,
            "model": r.model,
            "purpose": purpose,
            "thinking": "default" if thinking is None else ("on" if thinking else "off"),
            "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens,
            "latency_ms": r.latency_ms,
            "cached": r.cached,
            "tool_calls": len(r.tool_calls),
        }
        self.usage_log.parent.mkdir(parents=True, exist_ok=True)
        with self.usage_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
