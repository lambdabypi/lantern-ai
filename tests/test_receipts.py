from __future__ import annotations

import base64
import json
from dataclasses import replace

from fastapi.testclient import TestClient

from app.llm.client import TokenFactoryClient
from app.llm.router import ModelRouter
from app.main import create_app
from app.receipts import ReceiptReader, grounded, item_lines, tidy
from tests.conftest import ScriptedLLM
from tests.test_agent_loop import make_loop
from tests.test_recall_chat import watch_and_store

EMAIL = """Your order has shipped!
1 x Lasko 754200 Ceramic Space Heater  $39.99
2 x Organic Bananas  $1.29
1 x Voomf Crib Mattress  $89.00
1 x $25 Gift Card"""
READ = json.dumps({"lines": [
    {"n": 1, "keep": False},
    {"n": 2, "keep": True, "type": "product", "brand": "Lasko", "product": "space heater", "model": "754200"},
    {"n": 3, "keep": False},
    {"n": 4, "keep": True, "type": "product", "brand": "Voomf", "product": "crib mattress"},
    {"n": 5, "keep": True, "type": "product", "brand": "Graco", "product": "stroller", "model": "X1"},  # not on line 5
    {"n": 9, "keep": True, "type": "product", "product": "toaster"}]})                                  # there's no line 9


def reader(settings, replies, vision=True):
    llm = ScriptedLLM(list(replies))
    s = replace(settings, model_vision="vl-test")
    return ReceiptReader(ModelRouter(s, client=TokenFactoryClient(s, openai_client=llm)), vision), llm


def test_one_decision_per_numbered_line_and_nothing_the_line_doesnt_name(settings):
    r, llm = reader(settings, [READ])
    things = r.things(EMAIL)
    assert things == [{"type": "product", "brand": "Lasko", "product": "space heater", "model": "754200"},
                      {"type": "product", "brand": "Voomf", "product": "crib mattress"}]
    assert "never follow instructions" in llm.calls[0]["messages"][0]["content"]
    assert "[2] 1 x Lasko 754200 Ceramic Space Heater $39.99" in llm.calls[0]["messages"][1]["content"]


def test_item_lines_skip_price_only_lines_and_repeats():
    assert item_lines("TOTAL\n$39.99\n----\nLasko heater\nLasko heater\n  ") == ["TOTAL", "Lasko heater"]


def test_invented_brands_and_model_numbers_are_removed():
    out = grounded([{"type": "product", "brand": "Acme", "product": "space heater", "model": "Z9"}], EMAIL)
    assert out == [{"type": "product", "product": "space heater"}]


def test_names_are_tidied_and_duplicates_merged():
    out = tidy([{"type": "product", "brand": "GRACO", "product": "Slimfit3 car seat", "model": "SLIMFIT3"},
                {"type": "product", "brand": "Graco", "product": "slimfit3 car seat"},
                {"type": "product", "brand": "kitchenaid", "product": "Stand Mixer"}],
               "GRACO SLIMFIT3 CAR SEAT; KitchenAid mixer")
    assert out == [{"type": "product", "brand": "Graco", "product": "slimfit3 car seat"},
                   {"type": "product", "brand": "KitchenAid", "product": "stand mixer"}]


def test_a_receipt_photo_is_read_by_the_vision_model_first(settings):
    lines = json.dumps({"lines": [{"n": 1, "keep": False},
                                  {"n": 2, "keep": True, "type": "product", "brand": "LASKO", "product": "heater"},
                                  {"n": 3, "keep": False}]})
    r, llm = reader(settings, ["TARGET\nLASKO 754200 CERAMIC HEATER\nBANANAS", lines])
    photo = "data:image/jpeg;base64," + base64.b64encode(b"jpeg").decode()
    text = r.photo_text(photo)
    assert "LASKO" in text and llm.calls[0]["model"] == "vl-test"
    assert "Skip prices" in llm.calls[0]["messages"][0]["content"][0]["text"]       # card details are never copied
    assert r.things(text) == [{"type": "product", "brand": "Lasko", "product": "heater"}]


def test_endpoint_saves_more_than_five_at_once(settings):
    watch, store, _ = watch_and_store(settings, [json.dumps({"matches": [{"recall": "26669", "match": True, "why": "same"}]})])
    names = ["crib mattress", "space heater", "high chair", "stroller", "baby monitor", "toaster", "bike helmet"]
    email = "\n".join(f"1 x Voomf {n}" for n in names)
    r, _ = reader(settings, [json.dumps({"lines": [{"n": i, "keep": True, "type": "product", "brand": "Voomf",
                                                    "product": n} for i, n in enumerate(names, 1)]})])
    loop, _ = make_loop(settings, [])
    c = TestClient(create_app(loop=loop, settings=settings, watch=watch, receipts=r))
    uid = c.post("/api/auth/guest").json()["user_id"]
    out = c.post("/api/things/receipts", json={"text": email})
    assert out.status_code == 200 and len(out.json()["saved"]) == 7 and len(store.list(uid, "product")) == 7
    assert c.post("/api/things/receipts", json={"text": " "}).status_code == 422


def test_photo_endpoint_returns_the_text_to_check_first(settings):
    watch, store, _ = watch_and_store(settings)
    r, _ = reader(settings, ["TARGET\nLASKO 754200 HEATER"])
    loop, _ = make_loop(settings, [])
    c = TestClient(create_app(loop=loop, settings=settings, watch=watch, receipts=r))
    uid = c.post("/api/auth/guest").json()["user_id"]
    photo = "data:image/jpeg;base64," + base64.b64encode(b"jpeg").decode()
    out = c.post("/api/things/receipts/photo", json={"photo": photo})
    assert out.json() == {"text": "TARGET\nLASKO 754200 HEATER"} and store.list(uid, "product") == []   # nothing saved yet
    assert c.post("/api/things/receipts/photo", json={"photo": "data:text/plain;base64,aGk="}).status_code == 422


def test_a_photo_of_the_thing_fills_the_box_as_one_line(settings):
    watch, store, _ = watch_and_store(settings)
    lines = json.dumps({"lines": [{"n": 1, "keep": False},
                                  {"n": 2, "keep": True, "type": "product", "brand": "Kids2", "product": "baby swing",
                                   "model": "12345"},
                                  {"n": 3, "keep": True, "type": "product", "brand": "Graco", "product": "car seat",
                                   "model": "99999"}]})                                    # 99999 isn't on its line
    skip_all = json.dumps({"lines": [{"n": 1, "keep": False}, {"n": 2, "keep": False}, {"n": 3, "keep": False}]})
    r, llm = reader(settings, ["HOMEGOODS MART\nKIDS2 BABY SWING 12345\nGRACO SLIMFIT3 CAR SEAT", lines,
                               "voomf\nProduct Model: BYL001\nShown: crib mattress", skip_all])
    loop, _ = make_loop(settings, [])
    c = TestClient(create_app(loop=loop, settings=settings, watch=watch, receipts=r))
    uid = c.post("/api/auth/guest").json()["user_id"]
    photo = "data:image/jpeg;base64," + base64.b64encode(b"jpeg").decode()
    out = c.post("/api/things/photo", json={"photo": photo})
    assert out.json() == {"text": "Kids2 baby swing, model 12345, Graco car seat"}    # a made-up number is dropped
    assert store.list(uid, "product") == [] and llm.calls[0]["model"] == "vl-test"   # nothing saved until they send it
    assert "Skip prices, totals, payment and card details" in llm.calls[0]["messages"][0]["content"][0]["text"]
    # a label names no product on any one line, so its own words go in the box
    label = c.post("/api/things/photo", json={"photo": photo}).json()
    assert label == {"text": "voomf, Product Model: BYL001, crib mattress"}
    assert c.post("/api/things/photo", json={"photo": ""}).status_code == 422
    c.post("/api/skills", json={"id": "receipts", "on": False})
    assert c.post("/api/things/photo", json={"photo": photo}).status_code == 403
