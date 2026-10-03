from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from app.llm.client import TokenFactoryClient
from app.llm.router import ModelRouter

_spec = importlib.util.spec_from_file_location(
    "research_sprint", Path(__file__).resolve().parents[1] / "scripts" / "research_sprint.py"
)
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)


ITEMS = [
    {"kind": "complaint", "text": "Forgets context", "source_url": "u1"},
    {"kind": "wish", "text": "Editable memory", "source_url": "u2"},
    {"kind": "complaint", "text": "Shares data", "source_url": "u3"},
]


def test_build_themes_counts_in_code_and_ignores_bad_ids():
    data = {"themes": [
        {"name": "Memory", "summary": "s1", "item_ids": [1, 2, 99, "x"]},
        {"name": "Privacy", "summary": "s2", "item_ids": [3, 1]},  # 1 already used
        {"name": "Empty", "summary": "s3", "item_ids": [42]},
    ]}
    themes, unassigned = rs.build_themes(data, ITEMS)
    assert [(t["name"], t["count"], t["complaints"], t["wishes"]) for t in themes] == [
        ("Memory", 2, 1, 1),
        ("Privacy", 1, 1, 0),
    ]
    assert unassigned == 0


def test_findings_contain_no_urls_or_item_text():
    themes, unassigned = rs.build_themes({"themes": [{"name": "Memory", "summary": "s", "item_ids": [1, 2, 3]}]}, ITEMS)
    md = rs.render_findings(themes, unassigned=unassigned, stats={
        "date": "2026-10-03", "queries": 1, "query_list": ["q"], "results": 3, "complaints": 2, "wishes": 1,
    })
    assert "| 1 | Memory | 3 | 2 | 1 | s |" in md
    assert "u1" not in md and "Forgets context" not in md


def test_shuffled_order_keeps_run1_and_is_reproducible():
    assert rs.shuffled_order(5, 1) == [1, 2, 3, 4, 5]
    assert sorted(rs.shuffled_order(50, 2)) == list(range(1, 51))
    assert rs.shuffled_order(50, 2) == rs.shuffled_order(50, 2) != rs.shuffled_order(50, 3)


def test_mark_stability_matches_by_items_not_names():
    run1 = [{"name": "Memory", "item_ids": [1, 2, 3, 4]}, {"name": "Cost", "item_ids": [5, 6]}]
    run2 = [{"name": "Recall problems", "item_ids": [1, 2, 3]}, {"name": "Misc", "item_ids": [5, 7, 8, 9]}]
    run3 = [{"name": "Context", "item_ids": [2, 3, 4]}, {"name": "Pricing", "item_ids": [5, 6, 7]}]
    marked = rs.mark_stability([run1, run2, run3])
    assert [(t["name"], t["runs_present"], t["stable"]) for t in marked] == [
        ("Memory", 3, True),
        ("Cost", 2, False),  # run 2's best overlap is 1/5 < 0.5
    ]


def test_findings_show_stable_column_only_for_multiple_runs():
    themes = [{"name": "Memory", "summary": "s", "count": 3, "complaints": 2, "wishes": 1,
               "item_ids": [1, 2, 3], "stable": True, "runs_present": 3}]
    stats = {"date": "d", "queries": 1, "query_list": ["q"], "results": 3, "complaints": 2, "wishes": 1}
    assert "Stable" not in rs.render_findings(themes, unassigned=0, stats=stats)
    md = rs.render_findings(themes, unassigned=0, stats={**stats, "runs": 3})
    assert "| 1 | Memory | 3 | 2 | 1 | yes (3/3) | s |" in md


class ScriptedOpenAI:
    """Returns replies in order: Nano extraction batches, then the Ultra clustering call."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.models = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.models.append(kw["model"])
        text = self.replies.pop(0)
        return SimpleNamespace(
            model=kw["model"],
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=5),
        )


def test_extract_then_cluster_uses_nano_then_ultra(settings):
    fake = ScriptedOpenAI([
        json.dumps({"items": [{"kind": "complaint", "text": "Forgets things", "source": 1},
                              {"kind": "bogus", "text": "dropped"}]}),
        "not json at all",  # second batch: first reply is invalid...
        json.dumps({"items": [{"kind": "wish", "text": "Export memory", "source": 1}]}),  # ...retry works
        json.dumps({"themes": [{"name": "Memory", "summary": "s", "item_ids": [1, 2]}]}),
    ])
    router = ModelRouter(settings, client=TokenFactoryClient(settings, openai_client=fake))
    results = [{"title": "a", "url": "https://a", "content": "x"}, {"title": "b", "url": "https://b", "content": "y"}]

    items, failed = rs.extract_items(router, results, batch_size=1)
    assert failed == 0
    assert [(i["kind"], i["source_url"]) for i in items] == [("complaint", "https://a"), ("wish", "https://b")]

    (themes, unassigned), _ = rs.cluster_themes(router, items)
    assert themes[0]["count"] == 2 and unassigned == 0
    assert fake.models == ["nano-test", "nano-test", "nano-test", "ultra-test"]


def test_cluster_order_changes_listing_but_not_ids(settings):
    fake = ScriptedOpenAI([json.dumps({"themes": [{"name": "Memory", "summary": "s", "item_ids": [2]}]})])
    fake.prompts = []
    original = fake._create
    fake.chat.completions.create = lambda **kw: (fake.prompts.append(kw["messages"][1]["content"]), original(**kw))[1]
    router = ModelRouter(settings, client=TokenFactoryClient(settings, openai_client=fake))

    (themes, _), _ = rs.cluster_themes(router, ITEMS, order=[3, 1, 2])
    assert fake.prompts[0].splitlines()[1:] == [
        "3. (complaint) Shares data", "1. (complaint) Forgets context", "2. (wish) Editable memory",
    ]
    assert themes[0]["item_ids"] == [2] and themes[0]["wishes"] == 1
