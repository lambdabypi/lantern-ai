from __future__ import annotations

import json

import pytest

from app.memory.store import MemoryStore
from app.tools.game_knowledge import GameKnowledge, _blocked, make_game_tools, pick_sources
from app.tools.registry import ToolRegistry
from app.tools.web_search import WebSearch


def r(url, title="t", content="snippet"):
    return {"url": url, "title": title, "content": content}


class FakeTavily:
    """Scripted Tavily: search answers by query; extract returns passages for known URLs."""

    def __init__(self, searches: dict[str, list[dict]], extracts: dict[str, str] | None = None, extract_fails=False):
        self.searches, self.extracts, self.extract_fails = searches, extracts or {}, extract_fails
        self.search_calls, self.extract_calls = [], []

    def search(self, query, **kw):
        self.search_calls.append((query, kw.get("include_domains")))
        return {"results": self.searches.get(query, []), "usage": {"credits": 1}}

    def extract(self, urls, **kw):
        self.extract_calls.append((urls, kw))
        if self.extract_fails:
            raise RuntimeError("Failed to fetch url")
        return {"results": [{"url": u, "raw_content": self.extracts[u]} for u in urls if u in self.extracts],
                "failed_results": [], "usage": {"credits": 0}}


WARFRAME_WIKI = [r("https://warframe.fandom.com/wiki/Warframe"), r("https://wiki.warframe.com/w/Main"),
                 r("https://www.reddit.com/r/Warframe"), r("https://warframe.huijiwiki.com/x")]
WARFRAME_OFFICIAL = [r("https://www.youtube.com/watch?v=1"), r("https://www.warframe.com/updates")]
QUESTION = "where to farm Orokin Cells"
HITS = [r("https://warframe.fandom.com/f/p/123", "Forum: best way?", "forum chatter"),
        r("https://wiki.warframe.com/w/Orokin_Cell_Farming_Guide", "Orokin Cell Farming Guide", "Saturn and Ceres fissures..."),
        r("https://warframe.fandom.com/wiki/Orokin_Cell", "Orokin Cell", "Dropped by...")]


def make(settings, **fake_kw):
    tav = FakeTavily(**fake_kw)
    store = MemoryStore(settings.data_dir)
    return GameKnowledge(store, WebSearch(settings, tavily_client=tav), settings.data_dir), store, tav


def warframe_fake(**extra):
    return {"searches": {"Warframe wiki": WARFRAME_WIKI, "Warframe official site patch notes": WARFRAME_OFFICIAL,
                         QUESTION: HITS},
            "extracts": {"https://wiki.warframe.com/w/Orokin_Cell_Farming_Guide": "### Saturn and Ceres Fissures ..."},
            **extra}


def test_pick_sources_prefers_the_games_own_wikis_and_site():
    assert pick_sources("Warframe", WARFRAME_WIKI, WARFRAME_OFFICIAL) == \
        ["warframe.fandom.com", "wiki.warframe.com", "warframe.com"]


def test_pick_sources_falls_back_to_wiki_looking_hosts():
    wiki = [r("https://www.poewiki.net/wiki/Path_of_Exile"), r("https://www.reddit.com/r/pathofexile")]
    assert pick_sources("Path of Exile", wiki, []) == ["poewiki.net"]


def test_blocklist_matches_whole_domains():
    assert _blocked("x.com") and _blocked("m.youtube.com") and _blocked("store.steampowered.com")
    assert not _blocked("netflix.com") and not _blocked("eldenring.wiki.fextralife.com")


def test_ask_uses_the_games_sources_extract_and_ranks_wiki_pages_first(settings):
    gk, store, tav = make(settings, **warframe_fake())
    store.upsert("alice", "game", "Warframe", {"status": "playing", "progress": "Saturn"})
    store.upsert("alice", "profile", None, {"spoiler_preference": "strict"})

    out = gk.ask("alice", "Warframe", QUESTION)

    assert tav.search_calls[-1] == (QUESTION, ["warframe.fandom.com", "wiki.warframe.com", "warframe.com"])
    assert [p["url"] for p in out["passages"]][-1] == "https://warframe.fandom.com/f/p/123"  # forum last
    guide = out["passages"][0]
    assert guide["text"].startswith("### Saturn") and guide["depth"] == "page extract"
    assert out["passages"][1]["depth"] == "search snippet"
    assert (out["player_progress"], out["spoiler_preference"], out["fell_back_to_general_web"]) == ("Saturn", "strict", False)
    assert "Spoiler:" in out["instructions"] and "untrusted" in out["instructions"]
    # discovered sources are saved on the player's game, keeping its original source label
    assert store.get("alice", "game", "warframe").meta["sources"] == ["warframe.fandom.com", "wiki.warframe.com", "warframe.com"]


def test_discovery_happens_once_then_comes_from_cache(settings):
    gk, _, tav = make(settings, **warframe_fake())
    gk.ask("alice", "Warframe", QUESTION)
    gk.ask("bob", "Warframe", QUESTION)  # different user, shared public cache
    discovery = [q for q, _ in tav.search_calls if q.startswith("Warframe ")]
    assert discovery == ["Warframe wiki", "Warframe official site patch notes"]
    cache = json.loads((settings.data_dir / "game_sources.json").read_text())
    assert cache["warframe"]["sources"][0] == "warframe.fandom.com"


def test_players_own_sources_win(settings):
    gk, store, tav = make(settings, **warframe_fake())
    store.upsert("alice", "game", "Warframe", {"sources": ["wiki.warframe.com"]}, source="steam")
    gk.ask("alice", "Warframe", QUESTION)
    assert tav.search_calls == [(QUESTION, ["wiki.warframe.com"])]  # no discovery searches at all
    assert store.get("alice", "game", "warframe").meta["source"] == "steam"


def test_extract_failure_falls_back_to_snippets(settings):
    gk, _, _ = make(settings, **warframe_fake(extract_fails=True))
    out = gk.ask("alice", "Warframe", QUESTION)
    assert out["passages"] and all(p["depth"] == "search snippet" for p in out["passages"])


def test_falls_back_to_general_web_and_says_so(settings):
    fake = warframe_fake()
    fake["searches"][QUESTION] = []
    fake["searches"]["Warframe " + QUESTION] = [r("https://somesite.gg/guide", "Guide", "general web answer"),
                                                 r("https://www.reddit.com/r/Warframe/x")]
    gk, _, _ = make(settings, **fake)
    out = gk.ask("alice", "Warframe", QUESTION)
    assert out["fell_back_to_general_web"] is True
    assert [p["url"] for p in out["passages"]] == ["https://somesite.gg/guide"]  # reddit filtered out


def test_tool_through_registry(settings):
    gk, _, _ = make(settings, **warframe_fake())
    reg = ToolRegistry(settings.data_dir, make_game_tools(gk))
    ok = reg.call("alice", "ask_game", {"game": "Warframe", "question": QUESTION})
    assert ok.ok and "Orokin" in ok.content
    assert reg.call("alice", "ask_game", {"game": "Warframe"}).status == "invalid_args"
    assert reg.call("alice", "ask_game", {"game": "!!!", "question": "x"}).status == "error"
