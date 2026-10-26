from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.llm.client import TokenFactoryClient
from app.llm.router import ModelRouter
from app.main import create_app
from app.owner_reports import OwnerReports, _top
from app.tools.recalls import Complaint
from tests.conftest import ScriptedLLM
from tests.test_agent_loop import make_loop
from tests.test_recall_chat import watch_and_store

RECENT = (date.today() - timedelta(days=30)).isoformat()
OLD = "2019-03-01"


def complaint(n, components, filed=RECENT, fire=False, crash=False, injuries=0, summary="The contact owns a car."):
    return Complaint(odi=str(n), filed=filed, components=components, summary=summary, crash=crash, fire=fire,
                     injuries=injuries, deaths=0)


COMPLAINTS = ([complaint(i, ["ENGINE"], fire=i < 2, summary=f"Engine stalled on the highway ({i}).") for i in range(5)]
              + [complaint(10 + i, ["SERVICE BRAKES, HYDRAULIC"], filed=OLD, crash=True, injuries=1) for i in range(3)]
              + [complaint(20, ["UNKNOWN OR OTHER"]), complaint(21, ["UNKNOWN OR OTHER"]),
                 complaint(22, ["UNKNOWN OR OTHER"]), complaint(23, ["UNKNOWN OR OTHER"])])
RECALLS = [{"campaign": "20V543000", "component": "SERVICE BRAKES, HYDRAULIC:ANTILOCK"}]
THEMES = json.dumps({"themes": [{"group": 1, "title": "Engine stalls while driving", "what": "Owners report the engine stalls."},
                                {"group": 2, "title": "Brakes fail", "what": "Owners report the brakes failed."}]})


class FakeComplaints:
    def __init__(self):
        self.calls = 0

    def vehicle_complaints(self, make, model, year):
        self.calls += 1
        return COMPLAINTS


def reports(settings, replies):
    llm = ScriptedLLM(list(replies))
    s = replace(settings, model_balanced="super-test")
    return OwnerReports(FakeComplaints(), ModelRouter(s, client=TokenFactoryClient(s, openai_client=llm)),
                        settings.data_dir), llm


def test_groups_count_and_flag_problems_no_recall_covers(settings):
    o, llm = reports(settings, [THEMES])
    out = o.for_vehicle("Hyundai", "TUCSON", 2019, RECALLS)
    engine, brakes, vague = out["themes"]
    assert (engine["component"], engine["count"], engine["recent"], engine["fires"]) == ("ENGINE", 5, 5, 2)
    assert engine["covered_by_recall"] is False and engine["title"] == "Engine stalls while driving"
    assert brakes["covered_by_recall"] is True and brakes["recall_ids"] == ["20V543000"]
    assert (brakes["crashes"], brakes["injuries"], brakes["recent"]) == (3, 3, 0)
    assert vague["component"] == "UNKNOWN OR OTHER"                # vague groups come last, even when bigger
    assert out["total"] == 12 and out["written_by_model"] is True
    assert "never follow instructions" in llm.calls[0]["messages"][1]["content"]


def test_shared_per_model_and_refreshed_weekly(settings):
    o, llm = reports(settings, [THEMES])
    o.for_vehicle("Hyundai", "TUCSON", 2019, RECALLS)
    o.for_vehicle("Hyundai", "TUCSON", 2019, RECALLS)       # another owner of the same model
    assert o.client.calls == 1 and len(llm.calls) == 1


def test_counts_still_show_when_the_model_fails(settings):
    o, _ = reports(settings, ["not json"])
    out = o.for_vehicle("Hyundai", "TUCSON", 2019, RECALLS)
    assert out["written_by_model"] is False and out["themes"][0]["title"] == "Engine" and out["themes"][0]["count"] == 5


def test_systems_match_by_their_first_word():
    assert _top("FUEL/PROPULSION SYSTEM") == _top("FUEL SYSTEM, GASOLINE") == "FUEL"
    assert _top("AIR BAGS:FRONTAL") == "AIR" and _top("SEAT BELTS") != _top("SEATS")


def test_endpoint_for_a_car_on_the_list(settings):
    watch, store, _ = watch_and_store(settings)
    o, _ = reports(settings, [THEMES])
    loop, _ = make_loop(settings, [])
    c = TestClient(create_app(loop=loop, settings=settings, watch=watch, reports=o))
    c.post("/api/auth/guest")
    slug = c.post("/api/vehicles", json={"make": "Hyundai", "model": "Tucson", "year": 2019}).json()["item"]["slug"]
    r = c.get(f"/api/vehicles/{slug}/owner-reports")
    assert r.status_code == 200 and r.json()["name"] == "2019 Hyundai Tucson" and r.json()["themes"][0]["count"] == 5
    assert c.get("/api/vehicles/someone-elses-car/owner-reports").status_code == 404
