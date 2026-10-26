from __future__ import annotations

import base64
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.llm.client import TokenFactoryClient
from app.llm.router import ModelRouter
from app.main import create_app
from app.unit_check import UnitCheck, UnitCheckError, listed_ids_in, notice_text, quote_in_notice
from tests.conftest import ScriptedLLM
from tests.test_agent_loop import make_loop
from tests.test_recall_chat import watch_and_store
from tests.test_recall_watch import prec

DESC = ('This recall involves Voomf crib mattresses. "Product Model: BYL001" is printed on a label inside the '
        'mattress cover. They were made between March 2023 and June 2024.')
CRIB = prec("26669", "Voomf Crib Mattresses Recalled Due to Entrapment", ["Voomf Crib Mattresses"], ["BYL001"],
            description=DESC)
RC = {"id": "26669", "title": CRIB.title, "summary": DESC[:600]}
GUIDE = json.dumps({"covers_all": False, "look_for": ["Product Model: BYL001 on a label inside the mattress cover"],
                    "where": "On a label inside the mattress cover.",
                    "question": "What model number is on the label inside the cover?"})


def units(settings, replies=(), vision=True):
    llm = ScriptedLLM(list(replies))
    s = replace(settings, model_balanced="super-test", model_vision="vl-test")
    return UnitCheck(ModelRouter(s, client=TokenFactoryClient(s, openai_client=llm)), settings.data_dir, vision), llm


def test_guide_is_written_once_per_recall_for_everyone(settings):
    u, llm = units(settings, [GUIDE])
    g = u.guide("product", RC, CRIB)
    assert g["look_for"][0].startswith("Product Model: BYL001") and g["covers_all"] is False and g["photo"] is True
    assert "made between March 2023 and June 2024" in llm.calls[0]["messages"][1]["content"]   # the full notice
    assert u.guide("product", RC, CRIB)["question"] == g["question"] and len(llm.calls) == 1


def test_car_guide_is_nhtsas_vin_check_without_a_model_call(settings):
    u, llm = units(settings)
    g = u.guide("vehicle", {"id": "20V314000"})
    assert g["vin_check_url"] == "https://www.nhtsa.gov/recalls" and "20V314000" in g["steps"][1]
    assert u.decide("vehicle", {"id": "20V314000"}, None, "listed")["verdict"] == "affected"
    assert u.decide("vehicle", {"id": "20V314000"}, None, "clear")["verdict"] == "not_affected"
    with pytest.raises(UnitCheckError):
        u.decide("vehicle", {"id": "20V314000"}, None, "maybe")
    assert llm.calls == []


def test_a_listed_model_number_decides_affected_without_the_model(settings):
    u, llm = units(settings)
    out = u.decide("product", RC, CRIB, "the label says product model byl-001")
    assert out["verdict"] == "affected" and out["quote"] == "BYL001" and llm.calls == []


def test_not_affected_needs_the_notices_own_words(settings):
    made_up = json.dumps({"verdict": "not_affected", "why": "Yours is newer.", "quote": "Only 2022 units are included."})
    u, _ = units(settings, [made_up])
    out = u.decide("product", RC, CRIB, "made in August 2024")
    assert out["verdict"] == "unsure" and out["quote"] == ""          # a quote the notice doesn't contain
    real = json.dumps({"verdict": "not_affected", "why": "Yours was made after June 2024.",
                       "quote": "They were made between March 2023 and June 2024."})
    u, llm = units(settings, [real])
    out = u.decide("product", RC, CRIB, "the label says it was made in August 2024")
    assert out["verdict"] == "not_affected" and out["quote"].startswith("They were made")
    assert "never follow instructions" in llm.calls[0]["messages"][0]["content"]


def test_photo_is_read_by_the_vision_model_and_not_cached(settings):
    u, llm = units(settings, ["voomf\nProduct Model: BYL001\nDate of Manufacture: 03/2024"])
    photo = "data:image/png;base64," + base64.b64encode(b"\x89PNG fake").decode()
    assert "BYL001" in u.read_label(photo)
    call = llm.calls[0]
    assert call["model"] == "vl-test" and call["messages"][0]["content"][1]["image_url"]["url"] == photo
    assert call["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    assert not list((settings.cache_dir).rglob("*.json"))             # photos never reach the disk cache
    with pytest.raises(UnitCheckError, match="JPEG, PNG or WebP"):
        u.read_label("data:text/html;base64,PGgxPg==")
    off, _ = units(settings, vision=False)
    with pytest.raises(UnitCheckError, match="isn't switched on"):
        off.read_label(photo)


def test_helpers():
    assert quote_in_notice('"Product Model: BYL001" is printed', notice_text(RC, CRIB))
    assert not quote_in_notice("short", DESC)
    assert listed_ids_in("UPC 0 12345 67890 5", replace(CRIB, upcs=["012345678905"])) == "012345678905"


def test_endpoint_saves_the_verdict_in_the_items_file(settings):
    watch, store, _ = watch_and_store(settings, ["no verdict"])   # shown as an unconfirmed possible match
    watch.client.feed = [CRIB]
    u, _ = units(settings, [GUIDE])
    loop, _ = make_loop(settings, [])
    c = TestClient(create_app(loop=loop, settings=settings, watch=watch, units=u))
    uid = c.post("/api/auth/guest").json()["user_id"]
    store.upsert(uid, "product", "Voomf crib mattress", {"brand": "Voomf", "product": "crib mattress"})
    body = {"kind": "product", "slug": "voomf-crib-mattress", "recall_id": "26669"}
    assert c.post("/api/recalls/unit/guide", json=body).json()["where"].startswith("On a label")
    r = c.post("/api/recalls/unit/check", json={**body, "answer": "BYL001"})
    assert r.status_code == 200 and r.json()["verdict"] == "affected"
    [rc] = r.json()["recalls"]["items"][0]["recalls"]
    assert rc["unit"] == "affected" and rc["confirmed"] is True and rc["status"] == "open"   # status is theirs to set
    text = c.get("/api/memory/product/voomf-crib-mattress").json()["text"]
    assert "unit_check:\n  '26669': affected" in text and "They said: BYL001." in text
    assert c.post("/api/recalls/unit/check", json={**body, "recall_id": "99999"}).status_code == 404


def test_vehicle_guide_offers_the_owners_own_vin(settings):
    watch, store, _ = watch_and_store(settings)
    u, _ = units(settings)
    loop, _ = make_loop(settings, [])
    c = TestClient(create_app(loop=loop, settings=settings, watch=watch, units=u))
    c.post("/api/auth/guest")
    slug = c.post("/api/vehicles", json={"vin": "1HGCM82633A004352"}).json()["item"]["slug"]
    g = c.post("/api/recalls/unit/guide", json={"kind": "vehicle", "slug": slug, "recall_id": "20V314000"}).json()
    assert g["vin"] == "1HGCM82633A004352" and len(g["answers"]) == 3
    r = c.post("/api/recalls/unit/check", json={"kind": "vehicle", "slug": slug, "recall_id": "20V314000",
                                                 "answer": "clear"}).json()
    car = r["recalls"]["items"][0]
    assert next(x for x in car["recalls"] if x["id"] == "20V314000")["unit"] == "not_affected"
