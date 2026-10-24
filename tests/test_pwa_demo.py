from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_agent_loop import make_loop
from tests.test_recall_chat import watch_and_store


def client(settings, replies=()):
    watch, store, _ = watch_and_store(settings, replies)
    loop, _ = make_loop(settings, [])
    return TestClient(create_app(loop=loop, settings=settings, watch=watch)), store


def test_installable_app_files_are_served(settings):
    c, _ = client(settings)
    manifest = c.get("/manifest.webmanifest")
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    assert json.loads(manifest.content)["display"] == "standalone"
    sw = c.get("/sw.js")
    assert sw.headers["content-type"].startswith("application/javascript") and "showNotification" in sw.text
    assert sw.headers["service-worker-allowed"] == "/"
    icon = c.get("/icons/icon-192.png")
    assert icon.status_code == 200 and icon.content.startswith(b"\x89PNG")
    assert c.get("/icons/../main.py").status_code == 404 and c.get("/icons/secret.png").status_code == 404
    font = c.get("/fonts/geist-latin-wght-normal.woff2")
    assert font.status_code == 200 and font.content.startswith(b"wOF2") and font.headers["content-type"] == "font/woff2"
    assert "Open Font License" in c.get("/fonts/OFL-geist.txt").text
    assert c.get("/fonts/other.woff2").status_code == 404
    page = c.get("/").text
    assert 'rel="manifest"' in page and "apple-touch-icon" in page


def test_demo_household_loads_once_with_older_recalls_fixed(settings):
    verdict = json.dumps({"matches": [{"recall": "26669", "match": True, "why": "same brand"}]})
    c, store = client(settings, [verdict])
    uid = c.post("/api/auth/guest").json()["user_id"]
    report = c.post("/api/demo").json()
    names = [i["name"] for i in report["items"]]
    assert names[:2] == ["2006 Ford Ranger", "2019 Hyundai Tucson"] and "Voomf crib mattress" in names
    ranger = report["items"][0]["recalls"]
    assert {r["id"]: r["status"] for r in ranger} == {"20V314000": "open", "18V421000": "fixed"}   # urgent stays open
    assert store.get(uid, "profile").meta["location"] == "Boston, MA"
    assert all("made-up" in v.body for v in store.list(uid, "vehicle"))
    again = c.post("/api/demo")
    assert again.status_code == 422 and "empty account" in again.json()["detail"]
