"""Choose Nemotron Nano or Ultra per call, and log every call's model and token counts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from app.config import Settings
from app.llm.client import ChatResult, LLMError, TokenFactoryClient


class Tier(str, Enum):
    FAST = "fast"            # Nemotron Nano: the default for everyday calls
    REASONING = "reasoning"  # Nemotron Ultra: multi-step planning and heavy reasoning only


_TIER_ENV = {Tier.FAST: "MODEL_FAST", Tier.REASONING: "MODEL_REASONING"}


class ModelRouter:
    def __init__(
        self,
        settings: Settings,
        client: TokenFactoryClient | None = None,
        usage_log: Path | None = None,
    ):
        self.models = {Tier.FAST: settings.model_fast, Tier.REASONING: settings.model_reasoning}
        self.client = client or TokenFactoryClient(settings)
        self.usage_log = usage_log or settings.data_dir / "logs" / "llm_usage.jsonl"
        self._totals: dict[str, dict[str, int]] = {}

    def complete(
        self,
        messages: list[dict[str, str]],
        tier: Tier = Tier.FAST,
        *,
        purpose: str = "",
        **params: Any,
    ) -> ChatResult:
        """Run one chat call on the model for `tier` (Nano unless told otherwise)."""
        model = self.models[tier]
        if not model:
            raise LLMError(f"No model configured for the {tier.value} tier. Set {_TIER_ENV[tier]} in .env.")
        result = self.client.chat(model, messages, **params)
        self._record(tier, purpose, result)
        return result

    def usage_summary(self) -> dict[str, dict[str, int]]:
        """Per-model totals for this router's lifetime. Cached calls cost no tokens."""
        return {model: dict(t) for model, t in self._totals.items()}

    def _record(self, tier: Tier, purpose: str, r: ChatResult) -> None:
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
            "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens,
            "latency_ms": r.latency_ms,
            "cached": r.cached,
        }
        self.usage_log.parent.mkdir(parents=True, exist_ok=True)
        with self.usage_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
