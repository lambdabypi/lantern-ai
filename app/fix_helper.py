"""Book the free fix (#25): find who does the repair and draft the message. The user sends it themselves.

Car recall repairs are paid for by the maker only at an authorized dealer for the brand (any one, new or used
car), so for cars we look for nearby authorized dealers with one Tavily search and add a Google Maps search
link. For products, the CPSC notice names the company to contact. Nothing is ever sent from here: the draft
opens in the user's own email, text or WhatsApp app (the "ask before acting" rule).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from app.tools.web_search import SearchError, WebSearch

MAX_DEALERS = 5


def maps_link(make: str, location: str) -> str:
    return "https://www.google.com/maps/search/?api=1&query=" + quote(f"{make} dealer service near {location}")


def find_dealers(search: WebSearch, make: str, location: str) -> dict[str, Any]:
    """Nearby authorized dealers for a make. Costs 1 Tavily credit (basic search; repeat searches are cached)."""
    out: dict[str, Any] = {"maps_url": maps_link(make, location), "dealers": [], "note": None}
    try:
        results = search.search(f"authorized {make} dealer service department near {location}", max_results=8)
    except SearchError as e:
        out["note"] = f"Couldn't search for dealers right now ({e}). The map link still works."
        return out
    seen = set()
    for r in results:
        url, title = str(r.get("url") or ""), str(r.get("title") or "").strip()
        if not url.startswith("https://") or url in seen or not title:
            continue
        seen.add(url)
        out["dealers"].append({"title": title[:120], "url": url, "snippet": str(r.get("content") or "")[:200]})
        if len(out["dealers"]) >= MAX_DEALERS:
            break
    if not out["dealers"]:
        out["note"] = "No dealer pages found. Try the map link, or a nearby city."
    return out


def draft_message(kind: str, meta: dict[str, Any], recalls: list[dict[str, Any]]) -> dict[str, str]:
    """A ready-to-send message asking for the free repair (car) or the remedy (product)."""
    name = meta.get("name", "my item")
    if kind == "vehicle":
        vin = f" (VIN {meta['vin']})" if meta.get("vin") else ""
        lines = [f"- NHTSA recall {r['id']}: {r['title']}" for r in recalls]
        urgent = any(r.get("severity") == "park_it" for r in recalls)
        body = "\n".join([
            "Hello,",
            "",
            f"I'd like to book the free safety recall repair{'s' if len(recalls) > 1 else ''} for my {name}{vin}. "
            f"Open recall{'s' if len(recalls) > 1 else ''}:",
            *lines,
            "",
            ("NHTSA says this vehicle should not be driven until it's repaired. Can you arrange a tow, or a mobile "
             "repair? " if urgent else "")
            + "Could you confirm the parts are in stock before my visit, and let me know your earliest appointment?",
            "",
            "Thank you,",
        ])
        return {"subject": f"Safety recall repair for my {name}", "body": body}
    lines = [f"- CPSC recall {r['id']}: {r['title']}" for r in recalls]
    body = "\n".join([
        "Hello,",
        "",
        f"I have a {name} that appears to be covered by your recall:",
        *lines,
        "",
        "Please tell me how to get the remedy (refund, repair or replacement) and what you need from me.",
        "",
        "Thank you,",
    ])
    return {"subject": f"Recall remedy for my {name}", "body": body}
