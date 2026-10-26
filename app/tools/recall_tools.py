"""The agent's recall tool: save the things the user mentions, then report what may affect them.

One tool on purpose: the planner runs it before the model answers, so "we have a 2018 Civic" gets the
car's recalls in the same reply. There's no tool to mark recalls fixed or to delete anything: those are
the user's decisions, made in the app (a web page can't talk the model into marking a recall fixed).
"""

from __future__ import annotations

from typing import Any

from app.memory.store import MemoryStore, MemoryStoreError
from app.recall_watch import RecallWatch
from app.tools.recalls import RecallError
from app.tools.registry import Tool, ToolContext

MAX_ADD = 5
MAX_RECALLS_PER_ITEM = 4


def _brief(r: dict[str, Any]) -> dict[str, Any]:
    keep = {k: r[k] for k in ("id", "title", "date", "url", "status") if r.get(k)}
    keep.update({"what": r["summary"][:300], "risk": r["consequence"][:200], "fix": r["remedy"][:250]})
    if r["severity"] == "park_it":
        keep["urgent"] = "DO NOT DRIVE until repaired (official NHTSA flag)"
    elif r["severity"] == "park_outside":
        keep["urgent"] = "PARK OUTSIDE, away from buildings, until repaired (fire risk, official NHTSA flag)"
    elif r["severity"] == "stop_using":
        keep["urgent"] = "STOP USING this product now (the official CPSC notice says so)"
    if r.get("plain"):
        keep["plain_summary"] = f"{r['plain']['what']} {r['plain']['todo']}"
    if r.get("new"):
        keep["new"] = True
    if "confirmed" in r and not r["confirmed"]:
        keep["match"] = "possible match, not double-checked"
    return keep


def save_things(watch: RecallWatch, user_id: str, things: list[Any], limit: int = MAX_ADD) -> tuple[list[str], list[str]]:
    """Save cars and products (as read from chat, the "what do you own?" box or receipts). Returns saved names and
    problems."""
    saved, problems = [], []
    for thing in (things or [])[:limit]:
        if not isinstance(thing, dict):
            continue
        try:
            if thing.get("type") == "vehicle":
                item = watch.save_vehicle(user_id, str(thing.get("make") or ""), str(thing.get("model") or ""),
                                          thing.get("year"), str(thing.get("vin") or ""), owner=str(thing.get("owner") or ""))
            else:
                item = watch.save_product(user_id, str(thing.get("product") or ""),
                                          str(thing.get("brand") or ""), str(thing.get("model") or ""),
                                          owner=str(thing.get("owner") or ""))
            name = item.meta.get("name", item.slug)
            owner = item.meta.get("owner")
            saved.append(f"{owner}'s {name}" if owner and not name.startswith(f"{owner}'s") else name)
        except (RecallError, MemoryStoreError, ValueError) as e:
            problems.append(str(e))
    return saved, problems


def make_recall_tools(store: MemoryStore, watch: RecallWatch) -> list[Tool]:
    def recall_check(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        saved, problems = save_things(watch, ctx.user_id, args.get("add") or [])
        if str(args.get("location") or "").strip():
            store.upsert(ctx.user_id, "profile", None, {"location": str(args["location"]).strip()[:80]})
        report = watch.check(ctx.user_id)
        items = []
        for it in report["items"]:
            open_recalls = [r for r in it["recalls"] if r["status"] == "open"]
            items.append({"name": it.get("label") or it["name"], "kind": it["kind"], "open_recalls": len(open_recalls),
                          "recalls": [_brief(r) for r in open_recalls[:MAX_RECALLS_PER_ITEM]],
                          **({"more": len(open_recalls) - MAX_RECALLS_PER_ITEM}
                             if len(open_recalls) > MAX_RECALLS_PER_ITEM else {}),
                          **({"note": it["note"]} if it["note"] else {})})
        return {
            "saved": saved, "problems": problems, "items": items,
            "open_total": report["open"], "urgent_total": report["urgent"], "lookup_note": report["note"],
            "how_to_read_this": ("Vehicle recalls are listed for the make, model and year, not this exact car: "
                                 "whether this car was already repaired is shown only by the official VIN check "
                                 f"at {report['vin_check_url']}. Car recall repairs are free at any authorized "
                                 "dealer for the brand. The user marks recalls fixed in the app."),
        }

    return [Tool(
        name="recall_check",
        description=("Save cars and household products the user owns, then check official U.S. recall data "
                     "(NHTSA for vehicles, CPSC for products) for everything they own. Returns open recalls."),
        parameters={
            "type": "object",
            "properties": {
                "add": {"type": "array", "description": "Things the user just said they own.", "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["vehicle", "product"]},
                        "make": {"type": "string"}, "model": {"type": "string"}, "year": {"type": "integer"},
                        "vin": {"type": "string"}, "brand": {"type": "string"},
                        "product": {"type": "string", "description": "Kind of product, e.g. 'crib', 'space heater'."},
                    },
                    "required": ["type"]}},
                "location": {"type": "string", "description": "City or ZIP code, if the user gave one."},
            },
        },
        handler=recall_check,
        status="Checking official recall lists…",
        timeout_s=45.0,
    )]
