from __future__ import annotations

import pytest

from app.llm.parsing import extract_json
from app.tools.web_search import SearchError, WebSearch


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1}',
        '<think>maybe {"a": 0}</think>{"a": 1}',
        'reasoning without an opening tag {"a": 0}</think>\n{"a": 1}',
        'Here you go:\n```json\n{"a": 1}\n```',
        'Sure. {not json} then {"a": 1}',
    ],
)
def test_extract_json_handles_model_noise(text):
    assert extract_json(text) == {"a": 1}


def test_extract_json_raises_when_absent():
    with pytest.raises(ValueError):
        extract_json("<think>still thinking {\"a\": 1}")


class FakeTavily:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def search(self, query, **kwargs):
        self.calls.append((query, kwargs))
        if self.fail:
            raise RuntimeError("boom")
        return {
            "results": [{"title": "T", "url": f"https://x.test/{query}", "content": "C", "score": 0.9}],
            "usage": {"credits": 1},
        }


def test_search_defaults_to_basic_and_counts_credits(settings):
    tav = FakeTavily()
    ws = WebSearch(settings, tavily_client=tav)
    results = ws.search("q1")
    assert results == [{"title": "T", "url": "https://x.test/q1", "content": "C"}]
    assert tav.calls[0][1]["search_depth"] == "basic"
    assert ws.credits_used == 1


def test_cached_search_costs_no_credits(settings):
    tav = FakeTavily()
    ws = WebSearch(settings, tavily_client=tav)
    ws.search("q1")
    ws.search("q1")
    assert len(tav.calls) == 1
    assert (ws.credits_used, ws.live_searches, ws.cached_searches) == (1, 1, 1)


def test_search_failure_is_visible(settings):
    ws = WebSearch(settings, tavily_client=FakeTavily(fail=True))
    with pytest.raises(SearchError, match="q1"):
        ws.search("q1")


def test_single_quoted_strings_holding_double_quotes_are_read():
    from app.llm.parsing import extract_json
    text = """{"covers_all": false, "look_for": ['"Product Model: BYL001" on a label', '"voomf" on the side'], "x": null}"""
    assert extract_json(text) == {"covers_all": False, "look_for": ['"Product Model: BYL001" on a label',
                                                                    '"voomf" on the side'], "x": None}
    import pytest
    with pytest.raises(ValueError):
        extract_json("{__import__('os').system('echo hi')}")    # literals only: never evaluated
