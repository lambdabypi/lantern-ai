"""Book the free fix (#25): find who does the repair and draft the message. The user sends it themselves.

Car recall repairs are paid for by the maker only at an authorized dealer for the brand (any one, new or used
car), so for cars we look for nearby authorized dealers with one Tavily search and add a Google Maps search
link. For products, the CPSC notice names the company to contact. Nothing is ever sent from here: the draft
opens in the user's own email, text or WhatsApp app (the "ask before acting" rule).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from app.tools.web_search import SearchError, WebSearch

MAX_DEALERS = 5

# A ZIP code's first three digits name its state (USPS prefix ranges). A bare "07306" means nothing to a web
# search: it returned Phoenix dealers first; "ZIP code 07306, New Jersey" returned 8 of 8 in New Jersey. Kept
# offline so no extra service sees anyone's ZIP. Military and territory prefixes are left out.
_ZIP_STATES = [
    (5, 5, "New York"), (10, 27, "Massachusetts"), (28, 29, "Rhode Island"), (30, 38, "New Hampshire"),
    (39, 49, "Maine"), (50, 59, "Vermont"), (55, 55, "Massachusetts"), (60, 69, "Connecticut"), (70, 89, "New Jersey"),
    (100, 149, "New York"), (150, 196, "Pennsylvania"), (197, 199, "Delaware"), (200, 205, "District of Columbia"),
    (201, 201, "Virginia"), (206, 219, "Maryland"), (220, 246, "Virginia"), (247, 268, "West Virginia"),
    (270, 289, "North Carolina"), (290, 299, "South Carolina"), (300, 319, "Georgia"), (320, 349, "Florida"),
    (350, 369, "Alabama"), (370, 385, "Tennessee"), (386, 397, "Mississippi"), (398, 399, "Georgia"),
    (400, 427, "Kentucky"), (430, 459, "Ohio"), (460, 479, "Indiana"), (480, 499, "Michigan"), (500, 528, "Iowa"),
    (530, 549, "Wisconsin"), (550, 567, "Minnesota"), (569, 569, "District of Columbia"), (570, 577, "South Dakota"),
    (580, 588, "North Dakota"), (590, 599, "Montana"), (600, 629, "Illinois"), (630, 658, "Missouri"),
    (660, 679, "Kansas"), (680, 693, "Nebraska"), (700, 714, "Louisiana"), (716, 729, "Arkansas"),
    (730, 749, "Oklahoma"), (733, 733, "Texas"), (750, 799, "Texas"), (800, 816, "Colorado"), (820, 831, "Wyoming"),
    (832, 838, "Idaho"), (840, 847, "Utah"), (850, 865, "Arizona"), (870, 884, "New Mexico"), (885, 885, "Texas"),
    (889, 898, "Nevada"), (900, 961, "California"), (967, 968, "Hawaii"), (970, 979, "Oregon"),
    (980, 994, "Washington"), (995, 999, "Alaska"),
]
_ZIP = re.compile(r"^\s*(\d{5})(?:-\d{4})?\s*$")


def search_place(location: str) -> str:
    """'07306' -> 'ZIP code 07306, New Jersey'; anything else (a city, 'Boston, MA') is used as typed."""
    m = _ZIP.match(location)
    if not m:
        return location
    prefix = int(m.group(1)[:3])
    # later, narrower ranges (e.g. 201 Virginia inside 200-205) win over the wide ones before them
    state = next((s for lo, hi, s in reversed(_ZIP_STATES) if lo <= prefix <= hi), "")
    return f"ZIP code {m.group(1)}, {state}" if state else f"ZIP code {m.group(1)}"


def maps_link(make: str, location: str) -> str:
    return "https://www.google.com/maps/search/?api=1&query=" + quote(f"{make} dealer service near {location}")


def find_dealers(search: WebSearch, make: str, location: str) -> dict[str, Any]:
    """Nearby authorized dealers for a make. Costs 1 Tavily credit (basic search; repeat searches are cached)."""
    out: dict[str, Any] = {"maps_url": maps_link(make, location), "dealers": [], "note": None}
    try:
        results = search.search(f"authorized {make} dealer service department near {search_place(location)}",
                                max_results=8)
    except SearchError as e:
        out["note"] = f"Couldn't search for dealers right now ({e}). The map link still works."
        return out
    seen = set()
    for r in results:
        url, title = str(r.get("url") or ""), str(r.get("title") or "").strip()
        if not url.startswith("https://") or url in seen or not title:
            continue
        if re.search(r"\bused\b", title, re.I) and not re.search(r"service|parts|recall", title, re.I):
            continue  # used-car sales pages aren't where the free repair is done
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
        lines = [f"- {_label(r)} (NHTSA recall {r['id']})" for r in recalls]
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
            "[Your name and phone number]",
        ])
        return {"subject": f"Safety recall repair for my {name}", "body": body}
    lines = [f"- {_label(r)} (CPSC recall {r['id']})" for r in recalls]
    body = "\n".join([
        "Hello,",
        "",
        f"I have a {name} that appears to be covered by your recall:",
        *lines,
        "",
        "Please tell me how to get the remedy (refund, repair or replacement) and what you need from me.",
        "",
        "Thank you,",
        "[Your name and phone number]",
    ])
    return {"subject": f"Recall remedy for my {name}", "body": body}


def _label(recall: dict[str, Any]) -> str:
    """The plain-English title when Nemotron wrote one, else the official one."""
    return (recall.get("plain") or {}).get("title") or recall["title"]
