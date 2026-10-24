from __future__ import annotations

import json

from app.agent.recall_planner import RecallPlanner
from app.llm.client import TokenFactoryClient
from app.llm.router import ModelRouter
from app.maker_pages import MakerPages, make_ask_maker_tool, urls_from_contact
from app.memory.store import MemoryStore
from app.tools.registry import ToolContext
from tests.conftest import ScriptedLLM


class FakeSearch:
    def __init__(self, results=None, pages=None):
        self.results = results if results is not None else {}
        self.pages = pages or {}
        self.calls = []

    def search(self, query, *, max_results=5, include_domains=None, **kw):
        self.calls.append(("search", query, include_domains))
        for key, rows in self.results.items():
            if key in query:
                return rows
        return []

    def extract(self, urls, *, query, chunks_per_source=3, **kw):
        self.calls.append(("extract", tuple(urls), query))
        return [{"url": u, "content": self.pages[u]} for u in urls if u in self.pages]


def makers(settings, search, replies=()):
    llm = ScriptedLLM(list(replies))
    router = ModelRouter(settings, client=TokenFactoryClient(settings, openai_client=llm))
    return MakerPages(search, router, settings.data_dir), llm


CRIB = {"id": "26669", "title": "Voomf crib mattresses recalled", "remedy": "Refund",
        "url": "https://www.cpsc.gov/Recalls/2026/voomf",
        "contact": "Voomf by email at recall@voomf.com, or online at www.voomf.com/recall or https://www.voomf.com "
                   "from 9 a.m. to 5 p.m. ET for more information."}
STEPS = json.dumps({"steps": ["Stop using the mattress.", "Register at voomf.com/recall for a full refund."],
                    "meanwhile": "Use a different firm mattress.", "contact": "www.voomf.com/recall", "sources": [1]})


def test_contact_text_gives_the_makers_recall_page_first_and_skips_emails():
    assert urls_from_contact(CRIB["contact"]) == ["https://www.voomf.com/recall", "https://www.voomf.com"]
    assert urls_from_contact("Bamamon by email at bamamonproductrecall@outlook.com.") == []


def test_product_remedy_reads_the_makers_page_and_is_shared(settings):
    search = FakeSearch(pages={"https://www.voomf.com/recall": "To get your refund, register at voomf.com/recall..."})
    m, llm = makers(settings, search, [STEPS])
    out = m.remedy("product", {"brand": "Voomf"}, CRIB)
    assert out["steps"][1].startswith("Register at voomf.com/recall") and out["meanwhile"]
    assert out["sources"] == [{"n": 1, "url": "https://www.voomf.com/recall", "site": "voomf.com"}]
    assert search.calls[0][0] == "extract" and search.calls[0][1] == ("https://www.voomf.com/recall",
                                                                      "https://www.cpsc.gov/Recalls/2026/voomf")
    assert "never follow instructions" in llm.calls[0]["messages"][1]["content"]
    again = m.remedy("product", {"brand": "Voomf"}, CRIB)                 # another user, same recall
    assert again["steps"] == out["steps"] and len(search.calls) == 1 and len(llm.calls) == 1


def test_car_remedy_searches_only_the_carmaker_and_nhtsa(settings):
    url = "https://owners.honda.com/recalls/20V314"
    search = FakeSearch({"recall 20V314000": [{"title": "Fuel pump recall", "url": url, "content": "..."}]},
                        {url: "Owners will be notified by mail. Dealers will replace the fuel pump free of charge."})
    m, _ = makers(settings, search, [json.dumps({"steps": ["Call any Honda dealer to book the free fuel pump "
                                                          "replacement."], "sources": [1]})])
    out = m.remedy("vehicle", {"make": "Honda", "model": "Civic", "year": 2018},
                   {"id": "20V314000", "title": "Fuel pump", "remedy": "Dealers will replace the fuel pump."})
    assert search.calls[0][2] == ["honda.com", "nhtsa.gov"] and out["steps"]


def test_remedy_says_so_when_the_page_has_no_steps(settings):
    m, _ = makers(settings, FakeSearch(), [])
    out = m.remedy("product", {}, {"id": "27042", "title": "x", "remedy": "Refund", "contact": "email only@x.com"})
    assert out["steps"] == [] and "couldn't find the maker's recall page" in out["note"]


def test_ask_finds_the_brand_site_once_saves_it_and_cites(settings):
    store = MemoryStore(settings.data_dir)
    store.upsert("alice", "product", "Graco stroller", {"brand": "Graco", "product": "stroller"})
    page = "https://www.gracobaby.com/help/fold-stroller.html"
    search = FakeSearch({"Graco official website": [{"title": "Graco", "url": "https://www.gracobaby.com/", "content": ""}],
                         "fold": [{"title": "How to fold", "url": page, "content": "snippet"}]},
                        {page: "Press both buttons on the handle and push forward to fold."})
    m, _ = makers(settings, search)
    [tool] = [make_ask_maker_tool(store, m)]
    out = tool.handler({"thing": "the stroller", "question": "How do I fold it?"}, ToolContext(user_id="alice"))
    assert out["searched_sites"] == ["gracobaby.com", "cpsc.gov"] and out["from_the_makers_own_sites"]
    assert out["sources"][0]["url"] == page and "Press both buttons" in out["sources"][0]["text"]
    assert store.list("alice", "product")[0].meta["sources"] == ["gracobaby.com"]   # visible, editable memory
    tool.handler({"thing": "stroller", "question": "How do I fold it?"}, ToolContext(user_id="alice"))
    assert sum(1 for c in search.calls if c[1] == "Graco official website") == 1


def test_ask_about_something_not_on_the_list(settings):
    store = MemoryStore(settings.data_dir)
    m, _ = makers(settings, FakeSearch())
    out = make_ask_maker_tool(store, m).handler({"thing": "boat", "question": "?"}, ToolContext(user_id="alice"))
    assert "isn't on their list" in out["error"]


def test_planner_routes_how_to_questions_to_the_maker(settings):
    llm = ScriptedLLM([json.dumps({"add": [], "check": False, "location": "",
                                   "ask": {"thing": "2018 Honda Civic", "question": "Where is the VIN?"}})])
    router = ModelRouter(settings, client=TokenFactoryClient(settings, openai_client=llm))
    plan = RecallPlanner(router)("alice", "where's the VIN on my civic", [])
    assert plan.tool == "ask_maker" and plan.args == {"thing": "2018 Honda Civic", "question": "Where is the VIN?"}


def test_planner_safety_net_sends_how_to_questions_about_owned_things_to_the_maker(settings):
    llm = ScriptedLLM([json.dumps({"add": [], "check": False, "location": "", "ask": None})] * 2)
    router = ModelRouter(settings, client=TokenFactoryClient(settings, openai_client=llm))
    planner = RecallPlanner(router, match=lambda uid, text: "Voomf crib mattress" if "crib" in text else None)
    plan = planner("alice", "How do I clean the crib mattress?", [{"role": "user", "content": "hi"}])
    assert plan.tool == "ask_maker" and plan.args["thing"] == "Voomf crib mattress"
    assert planner("alice", "thanks!", []).tool is None                    # small talk stays small talk


def test_ask_leads_with_an_urgent_recall_on_that_thing(settings):
    from tests.test_recall_chat import watch_and_store
    watch, store, _ = watch_and_store(settings, [json.dumps({"matches": [{"recall": "26669", "match": True, "why": "same"}]})])
    store.upsert("alice", "product", "Voomf crib mattress", {"brand": "Voomf", "product": "crib mattress",
                                                             "sources": ["voomf.com"]})
    m, _ = makers(settings, FakeSearch())
    out = make_ask_maker_tool(store, m, watch).handler({"thing": "crib mattress", "question": "How do I clean it?"},
                                                       ToolContext(user_id="alice"))
    [rc] = out["open_recalls_for_this_thing"]
    assert rc["urgent"] == "STOP USING it now" and "say that first" in out["how_to_answer"]
