"""Opt-in live tests: they call Token Factory and spend a few tokens.

Run with:  RUN_LIVE_TESTS=1 pytest tests/test_live.py      (PowerShell: $env:RUN_LIVE_TESTS=1; pytest tests/test_live.py)
"""

from __future__ import annotations

import os
import re
from dataclasses import replace
from pathlib import Path

import pytest

from app.agent.loop import AgentLoop
from app.config import load_settings
from app.llm.router import ModelRouter
from app.tools.calculator import CALCULATOR
from app.tools.registry import ToolRegistry

pytestmark = pytest.mark.skipif(os.environ.get("RUN_LIVE_TESTS") != "1",
                                reason="live test: set RUN_LIVE_TESTS=1 to run")

ROOT = Path(__file__).resolve().parents[1]


def test_live_turn_uses_calculator(tmp_path):
    settings = replace(load_settings(ROOT / ".env"), data_dir=tmp_path / "data", cache_enabled=False)
    loop = AgentLoop(ModelRouter(settings), ToolRegistry(settings.data_dir, [CALCULATOR]))

    turn = loop.run_turn("live-test", "What is 1234 * 5678?")

    assert turn.stop_reason == "done", turn.answer
    assert any(c.name == "calculator" and c.status == "ok" for c in turn.tool_calls)
    # Nano formats numbers variously (commas, narrow no-break spaces), so compare digits only.
    assert "7006652" in re.sub(r"[\s,]", "", turn.answer), turn.answer
