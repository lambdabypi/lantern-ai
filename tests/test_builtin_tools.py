from __future__ import annotations

import json

import pytest

from app.tools.builtin import builtin_tools
from app.tools.calculator import CalculatorError, evaluate
from app.tools.registry import ToolRegistry
from app.tools.web_search import WebSearch
from tests.test_parsing_and_search import FakeTavily


@pytest.mark.parametrize("expr,expected", [
    ("12 * 7", 84),
    ("0.1 + 0.2", 0.3),
    ("2 ^ 10", 1024),
    ("-(3 + 4) * 2", -14),
    ("sqrt(16) + max(1, 5)", 9.0),
    ("round(pi, 2)", 3.14),
    ("17 // 5 + 17 % 5", 5),
])
def test_calculator_evaluates(expr, expected):
    assert evaluate(expr) == expected


@pytest.mark.parametrize("expr", [
    "__import__('os')", "open('x')", "(1).real", "a + 1", "9 ** 9 ** 9", "1 / 0",
    "x" * 301, "", "lambda: 1", "[1, 2]", "'a' * 3", "True + 1",
])
def test_calculator_rejects_unsafe_or_invalid(expr):
    with pytest.raises(CalculatorError):
        evaluate(expr)


def test_builtin_tools_through_registry(settings):
    tavily = FakeTavily()
    reg = ToolRegistry(settings.data_dir, builtin_tools(settings, WebSearch(settings, tavily_client=tavily)))
    assert reg.names() == ["calculator", "web_search"]

    calc = reg.call("demo", "calculator", {"expression": "12*7"})
    assert calc.ok and json.loads(calc.content) == {"expression": "12*7", "result": 84}

    bad = reg.call("demo", "calculator", {"expression": "1/0"})
    assert bad.status == "error" and "Division by zero" in bad.content

    web = reg.call("demo", "web_search", {"query": "nemotron", "max_results": 50})
    body = json.loads(web.content)
    assert web.ok and body["results"][0]["url"] == "https://x.test/nemotron"
    assert "untrusted" in body["note"]
    assert tavily.calls[0][1]["search_depth"] == "basic"
    assert tavily.calls[0][1]["max_results"] == 10  # clamped
