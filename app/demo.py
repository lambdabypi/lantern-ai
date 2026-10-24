"""A made-up demo household, so judges see Lantern working in one click (#2).

Real makes and models with real, current recall data, but no real people and no VINs. It shows each kind of
result: a "Do Not Drive" car (2006 Ford Ranger, Takata airbag inflators), a "park outside" car (2019 Hyundai
Tucson, fire risk), a recalled product (a crib mattress) and an item with nothing to report. The Ranger's older,
non-urgent recalls start marked fixed, as an owner who has kept up with servicing would have them.
"""

from __future__ import annotations

from app.memory.store import MemoryStore, MemoryStoreError
from app.recall_watch import RecallWatch
from app.tools.recalls import RecallError

VEHICLES = [("Ford", "Ranger", 2006), ("Hyundai", "Tucson", 2019)]
PRODUCTS = [("Voomf", "crib mattress"), ("Lasko", "ceramic space heater")]
LOCATION = "Boston, MA"
FIXED_BEFORE = "2024-01-01"   # demo owner already had older, non-urgent recalls done


def load_demo_household(store: MemoryStore, watch: RecallWatch, user_id: str) -> dict:
    if store.list(user_id, "vehicle") or store.list(user_id, "product"):
        raise MemoryStoreError("The demo household only loads into an empty account. Start a new guest session to try it.")
    store.upsert(user_id, "profile", None, {"location": LOCATION}, source="user")
    notes = []
    for make, model, year in VEHICLES:
        try:
            item = watch.save_vehicle(user_id, make, model, year, note="Demo household (made-up data).", source="user")
        except RecallError as e:
            notes.append(f"{year} {make} {model}: {e}")
            continue
        try:
            recalls = watch.vehicle_recalls(make, item.meta.get("nhtsa_model") or model, year)
        except RecallError:
            continue
        done = {r["campaign"]: "fixed" for r in recalls
                if not (r["park_it"] or r["park_outside"]) and (r["reported"] or "") < FIXED_BEFORE}
        if done:
            store.upsert(user_id, "vehicle", item.slug, {"recall_status": done}, source="user")
    for brand, product in PRODUCTS:
        watch.save_product(user_id, product, brand, note="Demo household (made-up data).", source="user")
    report = watch.check(user_id)
    if notes:
        report["note"] = " ".join(filter(None, [report.get("note"), *notes]))
    return report
