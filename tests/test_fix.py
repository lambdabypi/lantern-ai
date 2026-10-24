from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.fix_helper import draft_message, find_dealers
from app.main import create_app
from app.tools.web_search import WebSearch
from tests.test_agent_loop import make_loop
from tests.test_recall_chat import watch_and_store


class DealerTavily:
    def __init__(self, results=None):
        self.calls = []
        self.results = results if results is not None else [
            {"url": "https://www.examplefordboston.com/service", "title": "Example Ford of Boston: Service", "content": "Book service"},
            {"url": "https://www.examplefordboston.com/service", "title": "duplicate", "content": ""},
            {"url": "http://insecure.example/ford", "title": "Insecure", "content": ""},
        ]

    def search(self, query, **kw):
        self.calls.append(query)
        return {"results": self.results, "usage": {"credits": 1}}


def app_with(settings, tavily=None, replies=()):
    watch, store, _ = watch_and_store(settings, replies)
    loop, _ = make_loop(settings, [])
    tav = tavily or DealerTavily()
    c = TestClient(create_app(loop=loop, settings=settings, watch=watch, search=WebSearch(settings, tavily_client=tav)))
    return c, store, tav


def test_draft_for_a_car_lists_recalls_and_handles_do_not_drive():
    recalls = [{"id": "20V314000", "title": "Fuel System", "severity": "park_it"}, {"id": "18V421000", "title": "Labels", "severity": ""}]
    d = draft_message("vehicle", {"name": "2018 Honda Civic", "vin": "1HGCM82633A004352"}, recalls)
    assert "2018 Honda Civic (VIN 1HGCM82633A004352)" in d["body"] and "- NHTSA recall 20V314000: Fuel System" in d["body"]
    assert "should not be driven" in d["body"] and "parts are in stock" in d["body"]
    p = draft_message("product", {"name": "Voomf crib mattress"}, [{"id": "26669", "title": "Crib Mattresses Recalled"}])
    assert "CPSC recall 26669" in p["body"] and "refund, repair or replacement" in p["body"]


def test_find_dealers_keeps_https_unique_results_and_a_map_link(settings):
    out = find_dealers(WebSearch(settings, tavily_client=DealerTavily()), "Honda", "Boston, MA")
    assert [d["title"] for d in out["dealers"]] == ["Example Ford of Boston: Service"]
    q = parse_qs(urlparse(out["maps_url"]).query)
    assert q == {"api": ["1"], "query": ["Honda dealer service near Boston, MA"]}


def test_fix_endpoint_saves_location_and_searches_once(settings):
    c, store, tav = app_with(settings)
    uid = c.post("/api/auth/guest").json()["user_id"]
    c.post("/api/vehicles", json={"make": "Honda", "model": "Civic", "year": 2018})
    slug = c.get("/api/profile").json()["vehicles"][0]["slug"]
    first = c.post("/api/fix", json={"kind": "vehicle", "slug": slug}).json()
    assert first["location"] == "" and first["dealers"] == [] and "20V314000" in first["draft"]["body"]
    assert tav.calls == []                                          # no location yet: no search spent
    second = c.post("/api/fix", json={"kind": "vehicle", "slug": slug, "location": "Boston, MA"}).json()
    assert second["dealers"] and second["maps_url"] and store.get(uid, "profile").meta["location"] == "Boston, MA"
    assert tav.calls == ["authorized Honda dealer service department near Boston, MA"]


def test_fix_for_products_and_errors(settings):
    verdict = json.dumps({"matches": [{"recall": "26669", "match": True, "why": "same"}]})
    c, _, tav = app_with(settings, replies=[verdict])
    c.post("/api/auth/guest")
    c.post("/api/products", json={"brand": "Voomf", "product": "crib mattress"})
    slug = c.get("/api/profile").json()["products"][0]["slug"]
    out = c.post("/api/fix", json={"kind": "product", "slug": slug}).json()
    assert out["contact"] == "Call us." and "CPSC recall 26669" in out["draft"]["body"] and tav.calls == []
    assert c.post("/api/fix", json={"kind": "vehicle", "slug": "nope"}).status_code == 404
    c.post("/api/recalls/status", json={"kind": "product", "slug": slug, "recall_id": "26669", "status": "fixed"})
    assert c.post("/api/fix", json={"kind": "product", "slug": slug}).status_code == 422   # nothing open
