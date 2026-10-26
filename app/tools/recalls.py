"""U.S. safety recall data: NHTSA (vehicles) and CPSC (household products). Free, no API key.

Endpoints (checked live on 24 Oct 2026):
- NHTSA recalls   api.nhtsa.gov/recalls/recallsByVehicle?make=&model=&modelYear=
                  Make and model must match NHTSA's spelling (see vehicle_models). Dates are DD/MM/YYYY.
                  Matches by model, not by the owner's exact car: whether a specific VIN still needs the
                  fix is only shown on nhtsa.gov/recalls, which has no public API.
- NHTSA models    api.nhtsa.gov/products/vehicle/models?modelYear=&make=&issueType=r
- NHTSA complaints api.nhtsa.gov/complaints/complaintsByVehicle?make=&model=&modelYear=  (checked live 26 Oct 2026)
                  Owners' safety complaints, as summarised by NHTSA staff: unverified, but the database NHTSA uses
                  to open investigations. Dates are MM/DD/YYYY. Components are comma-joined without a space
                  ("POWER TRAIN,ENGINE"); a comma followed by a space is part of one name ("SERVICE BRAKES, HYDRAULIC").
- vPIC VIN decode vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/<vin>?format=json
- NHTSA equipment data.transportation.gov/resource/6axg-epim.json  (NHTSA's recall dataset, Socrata; checked live
                  26 Oct 2026). Child seats (251 recalls) and tires (788) are NHTSA equipment recalls, not in the
                  vehicle API: fetched once a day for the site and matched like household products. Fields include
                  manufacturer, subject, defect_summary, corrective_action and completion_rate (% fixed so far).
- CPSC recalls    www.saferproducts.gov/RestWebServices/Recall?format=json&RecallDateStart=YYYY-MM-DD
                  Unknown parameters are silently ignored and return every recall, so this client only
                  ever sends RecallDateStart and the caller matches the list locally.

Every error raised here is a RecallError with a message that is safe to show to the user.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import httpx

NHTSA_URL = "https://api.nhtsa.gov/"
VPIC_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/"
CPSC_URL = "https://www.saferproducts.gov/RestWebServices/Recall"
NHTSA_DATASET_URL = "https://data.transportation.gov/resource/6axg-epim.json"
EQUIPMENT_TYPES = ("Child Seat", "Tire")
NHTSA_VIN_CHECK = "https://www.nhtsa.gov/recalls"
TIMEOUT_S = 15.0

_VIN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")  # VINs never use I, O or Q
_NAME = re.compile(r"^[\w .&'/+-]{1,40}$")


class RecallError(RuntimeError):
    """A recall lookup failed. The message is safe to show to the user."""


@dataclass(frozen=True)
class Vehicle:
    make: str
    model: str
    year: int
    trim: str = ""


@dataclass(frozen=True)
class VehicleRecall:
    campaign: str             # NHTSA campaign number, e.g. 20V314000
    component: str
    summary: str
    consequence: str
    remedy: str
    reported: str | None      # ISO date
    park_it: bool             # "Do Not Drive": park it until it's fixed
    park_outside: bool        # fire risk: park outside, away from buildings
    over_the_air: bool        # fixed by a software update
    url: str


@dataclass(frozen=True)
class ProductRecall:
    number: str               # CPSC recall number
    date: str                 # ISO date
    title: str
    description: str
    products: list[str]
    models: list[str]
    hazard: str
    remedy: str
    remedy_options: list[str]
    contact: str
    units: str
    sold_at: str
    image: str | None
    url: str
    upcs: list[str] = field(default_factory=list)
    source: str = "cpsc"      # "cpsc", or "nhtsa" for child seats and tires
    completion: str = ""      # NHTSA's share of units fixed so far, e.g. "24.52" (child seats and tires only)


@dataclass(frozen=True)
class Complaint:
    odi: str                  # NHTSA's complaint number
    filed: str | None         # ISO date
    components: list[str]
    summary: str
    crash: bool
    fire: bool
    injuries: int
    deaths: int


def _mdy_to_iso(value: Any) -> str | None:
    try:
        return datetime.strptime(str(value), "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def _dmy_to_iso(value: Any) -> str | None:
    try:
        return datetime.strptime(str(value), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def _names(items: Any, key: str = "Name") -> list[str]:
    return [str(i[key]).strip() for i in items or [] if isinstance(i, dict) and str(i.get(key) or "").strip()]


def _count(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def normalise_vin(text: str) -> str:
    vin = re.sub(r"[\s-]", "", text or "").upper()
    if not _VIN.match(vin):
        raise RecallError("A VIN is 17 letters and digits (never I, O or Q). It's on the dashboard by the "
                          "windscreen, the driver's door frame, and your registration or insurance card.")
    return vin


def _check_vehicle(make: str, model: str, year: Any) -> tuple[str, str, int]:
    make, model = (make or "").strip(), (model or "").strip()
    if not (_NAME.match(make) and _NAME.match(model)):
        raise RecallError("Please give the car's make and model, for example Honda Civic.")
    try:
        year = int(year)
    except (TypeError, ValueError):
        raise RecallError("Please give the car's model year, for example 2018.") from None
    if not 1950 <= year <= date.today().year + 2:
        raise RecallError(f"{year} doesn't look like a model year.")
    return make, model, year


class RecallClient:
    def __init__(self, http: httpx.Client | None = None):
        self._http = http or httpx.Client(timeout=TIMEOUT_S, headers={"User-Agent": "Lantern recall watch"})

    # -- vehicles ---------------------------------------------------------------------

    def vehicle_recalls(self, make: str, model: str, year: Any) -> list[VehicleRecall]:
        """Recalls for a make, model and year, newest first."""
        make, model, year = _check_vehicle(make, model, year)
        data = self._get(NHTSA_URL + "recalls/recallsByVehicle", {"make": make, "model": model, "modelYear": year},
                         "NHTSA")
        recalls = []
        for r in data.get("results") or []:
            if not isinstance(r, dict) or not r.get("NHTSACampaignNumber"):
                continue
            campaign = str(r["NHTSACampaignNumber"])
            recalls.append(VehicleRecall(
                campaign=campaign, component=str(r.get("Component") or ""),
                summary=str(r.get("Summary") or ""), consequence=str(r.get("Consequence") or ""),
                remedy=str(r.get("Remedy") or ""), reported=_dmy_to_iso(r.get("ReportReceivedDate")),
                park_it=bool(r.get("parkIt")), park_outside=bool(r.get("parkOutSide")),
                over_the_air=bool(r.get("overTheAirUpdate")),
                url=f"{NHTSA_VIN_CHECK}?nhtsaId={campaign}",
            ))
        recalls.sort(key=lambda r: r.reported or "", reverse=True)
        return recalls

    def vehicle_complaints(self, make: str, model: str, year: Any) -> list[Complaint]:
        """Owners' safety complaints for a make, model and year, newest first."""
        make, model, year = _check_vehicle(make, model, year)
        data = self._get(NHTSA_URL + "complaints/complaintsByVehicle",
                         {"make": make, "model": model, "modelYear": year}, "NHTSA")
        out = []
        for r in data.get("results") or []:
            if not isinstance(r, dict) or not r.get("odiNumber"):
                continue
            out.append(Complaint(
                odi=str(r["odiNumber"]), filed=_mdy_to_iso(r.get("dateComplaintFiled")),
                components=[c.strip() for c in re.split(r",(?! )", str(r.get("components") or "")) if c.strip()],
                summary=str(r.get("summary") or ""), crash=bool(r.get("crash")), fire=bool(r.get("fire")),
                injuries=_count(r.get("numberOfInjuries")), deaths=_count(r.get("numberOfDeaths")),
            ))
        out.sort(key=lambda c: c.filed or "", reverse=True)
        return out

    def vehicle_models(self, make: str, year: Any) -> list[str]:
        """NHTSA's spelling of every model of a make and year that has recall data."""
        make, _, year = _check_vehicle(make, "x", year)
        data = self._get(NHTSA_URL + "products/vehicle/models",
                         {"modelYear": year, "make": make, "issueType": "r"}, "NHTSA")
        return sorted({str(r["model"]) for r in data.get("results") or [] if isinstance(r, dict) and r.get("model")})

    def decode_vin(self, text: str) -> Vehicle:
        vin = normalise_vin(text)
        data = self._get(VPIC_URL + f"DecodeVinValues/{vin}", {"format": "json"}, "NHTSA's VIN decoder")
        results = data.get("Results") or []
        r = results[0] if results and isinstance(results[0], dict) else {}
        make, model, year = (str(r.get(k) or "").strip() for k in ("Make", "Model", "ModelYear"))
        if not (make and model and year.isdigit()):
            raise RecallError("NHTSA couldn't decode that VIN. Please check it, or add the car by make, model "
                              "and year instead.")
        return Vehicle(make=make.title(), model=model, year=int(year), trim=str(r.get("Trim") or "").strip())

    # -- household products ------------------------------------------------------------

    def product_recalls(self, since: date) -> list[ProductRecall]:
        """Every CPSC recall published since a date, newest first."""
        data = self._get(CPSC_URL, {"format": "json", "RecallDateStart": since.isoformat()}, "the CPSC",
                         want=list)
        recalls = []
        for r in data:
            if not isinstance(r, dict) or not r.get("RecallNumber"):
                continue
            products = r.get("Products") or []
            images = _names(r.get("Images"), "URL")
            recalls.append(ProductRecall(
                number=str(r["RecallNumber"]), date=str(r.get("RecallDate") or "")[:10],
                title=str(r.get("Title") or ""), description=str(r.get("Description") or ""),
                products=_names(products), models=_names(products, "Model"),
                hazard="; ".join(_names(r.get("Hazards"))), remedy="; ".join(_names(r.get("Remedies"))),
                remedy_options=_names(r.get("RemedyOptions"), "Option"),
                contact=str(r.get("ConsumerContact") or ""),
                units="; ".join(_names(products, "NumberOfUnits")),
                sold_at="; ".join(_names(r.get("Retailers"))).replace("Sold At:\n", ""),
                image=images[0] if images else None, url=str(r.get("URL") or ""),
                upcs=_names(r.get("ProductUPCs"), "UPC"),
            ))
        recalls.sort(key=lambda r: r.date, reverse=True)
        return recalls

    def equipment_recalls(self, since: date) -> list[ProductRecall]:
        """NHTSA's child seat and tire recalls since a date, newest first, in the same shape as CPSC recalls."""
        types = ", ".join(f"'{t}'" for t in EQUIPMENT_TYPES)
        data = self._get(NHTSA_DATASET_URL, {
            "$where": f"recall_type in ({types}) AND report_received_date >= '{since.isoformat()}T00:00:00'",
            "$order": "report_received_date DESC", "$limit": 5000}, "NHTSA's recall data", want=list)
        out = []
        for r in data:
            if not isinstance(r, dict) or not r.get("nhtsa_id"):
                continue
            link = r.get("recall_link") if isinstance(r.get("recall_link"), dict) else {}
            maker = str(r.get("manufacturer") or "")
            kind = "child car seat" if r.get("recall_type") == "Child Seat" else "tires"
            out.append(ProductRecall(
                number=str(r["nhtsa_id"]), date=str(r.get("report_received_date") or "")[:10],
                title=str(r.get("subject") or ""), description=str(r.get("defect_summary") or ""),
                products=[f"{maker} {kind}".strip()], models=[], hazard=str(r.get("consequence_summary") or ""),
                remedy=str(r.get("corrective_action") or ""), remedy_options=[],
                contact=str(r.get("corrective_action") or ""), units=str(r.get("potentially_affected") or ""),
                sold_at="", image=None, url=str(link.get("url") or f"{NHTSA_VIN_CHECK}?nhtsaId={r['nhtsa_id']}"),
                source="nhtsa", completion=str(r.get("completion_rate") or ""),
            ))
        return out

    # -- internals ----------------------------------------------------------------------

    def _get(self, url: str, params: dict[str, Any], source: str, want: type = dict) -> Any:
        try:
            r = self._http.get(url, params=params, timeout=TIMEOUT_S)
        except httpx.HTTPError:
            raise RecallError(f"Couldn't reach {source} (network error or timeout). Please try again.") from None
        if r.status_code == 429:
            raise RecallError(f"{source} is limiting requests right now. Please try again in a minute.")
        if r.status_code >= 500:
            raise RecallError(f"{source} is temporarily unavailable. Please try again later.")
        if r.status_code == 400 and want is dict:
            return {}  # NHTSA answers 400 for a make/model it doesn't know: no recalls on record
        if r.status_code != 200:
            raise RecallError(f"{source} request failed (HTTP {r.status_code}).")
        try:
            data = r.json()
        except ValueError:
            data = None
        if not isinstance(data, want):
            raise RecallError(f"{source} returned an unexpected response. Please try again.")
        return data
