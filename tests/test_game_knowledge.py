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

    sources = ["warframe.fandom.com", "wiki.warframe.com", "warframe.com"]
    assert (QUESTION, sources) in tav.search_calls and ("farm Orokin Cells", sources) in tav.search_calls
    assert out["searches"] == [QUESTION, "farm Orokin Cells"]
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
    assert tav.search_calls == [(QUESTION, ["wiki.warframe.com"]), ("farm Orokin Cells", ["wiki.warframe.com"])]  # no discovery
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


def test_fallback_answers_must_say_they_used_the_general_web(settings):
    fake = warframe_fake()
    fake["searches"][QUESTION] = []
    fake["searches"]["Warframe " + QUESTION] = [r("https://somesite.gg/guide")]
    gk, _, _ = make(settings, **fake)
    assert gk.ask("alice", "Warframe", QUESTION)["instructions"].startswith("The game's own sources had nothing")
    from dataclasses import replace
    fresh = replace(settings, cache_dir=settings.cache_dir.parent / "cache2", data_dir=settings.data_dir.parent / "data2")
    gk2, _, _ = make(fresh, **warframe_fake())  # separate dev cache: the first half cached an empty search
    assert "general web" not in gk2.ask("alice", "Warframe", QUESTION)["instructions"]


def test_saving_a_game_after_asking_attaches_known_sources(settings):
    from app.tools.profile_tools import make_profile_tools
    gk, store, _ = make(settings, **warframe_fake())
    gk.ask("alice", "Warframe", QUESTION)                       # not in the profile yet: sources only cached
    assert store.get("alice", "game", "warframe") is None
    reg = ToolRegistry(settings.data_dir, make_profile_tools(store, gk.cached_sources))
    reg.call("alice", "save_game", {"title": "Warframe", "progress": "Saturn"})
    assert store.get("alice", "game", "warframe").meta["sources"][0] == "warframe.fandom.com"


GRIFFIN_Q = "griffin contract in White Orchard"
CONTRACT_HITS = [r("https://witcher.fandom.com/wiki/Contract:_The_Griffin_from_the_Highlands", "Contract: Griffin", "contract text"),
                 r("https://witcher.fandom.com/wiki/Witcher_contracts", "Contracts", "list of contracts")]
MAIN_QUEST_HITS = [r("https://witcher.fandom.com/wiki/The_Beast_of_White_Orchard", "The Beast of White Orchard", "main quest griffin"),
                   r("https://witcher.fandom.com/wiki/Contract:_The_Griffin_from_the_Highlands", "dup", "dup")]


def witcher_fake(**extra):
    return {"searches": {"The Witcher 3 wiki": [r("https://witcher.fandom.com/wiki/The_Witcher_3")],
                         "The Witcher 3 official site patch notes": [r("https://www.thewitcher.com/en/witcher3")],
                         GRIFFIN_Q: CONTRACT_HITS, "griffin White Orchard": MAIN_QUEST_HITS,
                         "Beast of White Orchard main quest": MAIN_QUEST_HITS},
            "extracts": {h["url"]: "page: " + h["title"] for h in CONTRACT_HITS + MAIN_QUEST_HITS[:1]}, **extra}


def test_broaden_drops_narrowing_words():
    from app.tools.game_knowledge import broaden
    assert broaden(GRIFFIN_Q) == "griffin White Orchard"
    assert broaden("where to farm Orokin Cells Saturn") == "farm Orokin Cells Saturn"
    assert broaden("best guide") == ""  # too little left to search on


def test_both_readings_reach_the_model_and_both_get_read_in_depth(settings):
    gk, _, tav = make(settings, **witcher_fake())
    out = gk.ask("alice", "The Witcher 3", GRIFFIN_Q)
    urls = [p["url"] for p in out["passages"]]
    assert urls[:2] == [CONTRACT_HITS[0]["url"], MAIN_QUEST_HITS[0]["url"]]   # interleaved, deduped
    assert len(urls) == len(set(urls))
    extracted = tav.extract_calls[0][0]
    assert MAIN_QUEST_HITS[0]["url"] in extracted and CONTRACT_HITS[0]["url"] in extracted
    assert "cover both or ask which" in out["instructions"]


def test_model_alternative_is_added_and_the_broad_search_is_kept(settings):
    gk, _, _ = make(settings, **witcher_fake())
    out = gk.ask("alice", "The Witcher 3", GRIFFIN_Q, alternative="Beast of White Orchard main quest")
    assert out["searches"] == [GRIFFIN_Q, "Beast of White Orchard main quest", "griffin White Orchard"]
    same = gk.ask("alice", "The Witcher 3", GRIFFIN_Q, alternative=GRIFFIN_Q.upper())  # a mere rewording
    assert same["searches"] == [GRIFFIN_Q, "griffin White Orchard"]


def test_the_and_no_the_share_cached_sources(settings):
    gk, _, tav = make(settings, **witcher_fake())
    gk.ask("alice", "The Witcher 3", GRIFFIN_Q)
    before = len(tav.search_calls)
    gk.ask("alice", "Witcher 3", GRIFFIN_Q)
    discovery = [q for q, _ in tav.search_calls[before:] if "wiki" in q or "official" in q]
    assert discovery == []  # "Witcher 3" reused "The Witcher 3"'s sources


def test_shared_cache_skips_searching_for_the_next_player_but_keeps_their_own_progress(settings):
    gk, store, tav = make(settings, **warframe_fake())
    store.upsert("alice", "game", "Warframe", {"progress": "Saturn"})
    store.upsert("bob", "game", "Warframe", {"progress": "Earth"})
    a = gk.ask("alice", "Warframe", QUESTION)
    calls_after_alice = (len(tav.search_calls), len(tav.extract_calls))
    b = gk.ask("bob", "Warframe", QUESTION)
    assert (len(tav.search_calls), len(tav.extract_calls)) == calls_after_alice   # served from the shared cache
    assert a["passages"] == b["passages"]
    assert (a["player_progress"], b["player_progress"]) == ("Saturn", "Earth")       # per-player, never shared


def test_time_sensitive_questions_are_never_shared(settings):
    q = "current Baro items"
    fake = warframe_fake()
    fake["searches"][q] = [r("https://wiki.warframe.com/w/Baro_Ki%27Teer", "Baro", "rotating stock")]
    gk, _, _ = make(settings, **fake)
    gk.ask("alice", "Warframe", q)
    shared = settings.data_dir / "shared_cache" / "ask_game"
    assert not (shared.exists() and list(shared.glob("*.json")))   # "current" never goes in the shared cache
    gk.ask("alice", "Warframe", QUESTION)
    assert len(list(shared.glob("*.json"))) == 1                     # an ordinary question does


def test_slow_page_reads_fall_back_to_snippets_and_are_not_shared(settings, monkeypatch):
    import time as _time
    from app.tools import game_knowledge as gkmod
    monkeypatch.setattr(gkmod, "EXTRACT_WAIT_S", 0.05)
    fake = warframe_fake()
    gk, _, tav = make(settings, **fake)
    real_extract = tav.extract
    tav.extract = lambda urls, **kw: (_time.sleep(0.3), real_extract(urls, **kw))[1]
    out = gk.ask("alice", "Warframe", QUESTION)
    assert all(p["depth"] == "search snippet" for p in out["passages"])
    assert not (settings.data_dir / "shared_cache" / "ask_game").exists()  # incomplete: not pinned for a day


def test_prewarm_finds_sources_once_and_attaches_them(settings):
    gk, store, tav = make(settings, **warframe_fake())
    store.upsert("alice", "game", "Warframe", {"status": "playing"}, source="steam")
    assert gk.prewarm("alice", ["Warframe", "Warframe", ""]) == 1
    assert store.get("alice", "game", "warframe").meta["sources"][0] == "warframe.fandom.com"
    assert store.get("alice", "game", "warframe").meta["source"] == "steam"      # label untouched
    searches = len(tav.search_calls)
    gk.prewarm("bob", ["Warframe"])                                              # known game: no new searches
    assert len(tav.search_calls) == searches


def test_prewarm_skips_bad_titles_and_respects_the_limit(settings):
    gk, _, tav = make(settings, **warframe_fake())
    assert gk.prewarm("alice", ["!!!", "Warframe"]) == 1
    assert gk.prewarm("alice", [f"Game {i}" for i in range(40)], limit=3) == 3


@pytest.mark.parametrize("title,wiki,official,expected", [
    ("Albion Online",
     [r("https://en.wikipedia.org/wiki/Albion_Online"), r("https://yogscast.fandom.com/wiki/Albion"),
      r("https://wiki.albiononline.com/wiki/Main_Page")],
     [r("https://albiononline.com/en/update")],
     ["wiki.albiononline.com", "albiononline.com"]),
    ("Call of Duty®",
     [r("https://callofduty.fandom.com/wiki/Call_of_Duty")],
     [r("https://phonecall.app/x"), r("https://www.callofduty.com/patchnotes")],
     ["callofduty.fandom.com", "callofduty.com"]),
    ("AdventureQuest 3D",
     [r("https://en.wikipedia.org/wiki/AdventureQuest_3D")],
     [],
     ["en.wikipedia.org"]),                      # last resort only
    ("Albion Online",
     [r("https://yogscast.fandom.com/wiki/Albion")], [], []),   # unrelated fandom wiki is never used
])
def test_source_picking_regressions(title, wiki, official, expected):
    assert pick_sources(title, wiki, official) == expected


def test_games_with_no_sources_are_not_rediscovered_every_time(settings):
    gk, _, tav = make(settings, searches={}, extracts={})
    gk.ask("alice", "Obscure Game", "how do I jump")
    discovery = lambda: [q for q, _ in tav.search_calls if q.startswith("Obscure Game ")]  # noqa: E731
    first = len(discovery())
    gk.ask("alice", "Obscure Game", "how do I run")
    assert first == 2 + 1 and len(discovery()) == first + 1  # 2 discovery + 1 general-web fallback, then fallback only


def test_shared_cache_only_accepts_hex_digest_keys(settings):
    gk, _, _ = make(settings, searches={})
    good = "a" * 64
    gk._shared_set(good, {"passages": []})
    assert gk._shared_get(good) == {"passages": []}
    for bad in ("../../users/alice/account", "A" * 64, "a" * 63):
        assert gk._shared_get(bad) is None
        with pytest.raises(ValueError):
            gk._shared_set(bad, {})


def test_failed_shared_cache_write_does_not_break_the_answer(settings, monkeypatch):
    gk, _, _ = make(settings, searches={})
    gk.answers_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.tools.game_knowledge.os.replace", lambda *a: (_ for _ in ()).throw(OSError("bucket")))
    gk._shared_set("b" * 64, {"passages": []})          # logged, not raised
    assert list(gk.answers_dir.iterdir()) == []         # no stray .tmp left behind
