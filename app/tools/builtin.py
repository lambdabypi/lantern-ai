"""The core tools every user gets. Skills add more in build step 8."""

from __future__ import annotations

from app.config import Settings
from app.tools.calculator import CALCULATOR
from app.tools.registry import Tool
from app.tools.web_search import WebSearch, make_web_search_tool


def builtin_tools(settings: Settings, search: WebSearch | None = None) -> list[Tool]:
    return [CALCULATOR, make_web_search_tool(search or WebSearch(settings))]
