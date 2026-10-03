"""One live call to Token Factory to prove the connection works.

Usage:
    python scripts/smoke_llm.py               # Nano (MODEL_FAST)
    python scripts/smoke_llm.py --reasoning   # also Ultra (MODEL_REASONING)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import ConfigError, load_settings  # noqa: E402
from app.llm.client import LLMError  # noqa: E402
from app.llm.router import ModelRouter, Tier  # noqa: E402

PROMPT = [{"role": "user", "content": "Reply with exactly one word: pong"}]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reasoning", action="store_true", help="also test MODEL_REASONING")
    args = parser.parse_args()

    try:
        settings = load_settings()
        router = ModelRouter(settings)
        tiers = [Tier.FAST] + ([Tier.REASONING] if args.reasoning else [])
        for tier in tiers:
            r = router.complete(PROMPT, tier, purpose="smoke", use_cache=False, max_tokens=512)
            print(f"OK  {tier.value:<9} {r.model}  reply={r.text.strip()[:80]!r}  "
                  f"tokens={r.prompt_tokens}+{r.completion_tokens}  {r.latency_ms} ms")
        print(f"Logged to {router.usage_log}")
    except (ConfigError, LLMError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
