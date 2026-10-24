from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from app.tools.recalls import RecallClient, RecallError, normalise_vin

# Shapes copied from live responses on 24 Oct 2026 (trimmed).
NHTSA_CIVIC = {"Count": 2, "Message": "Results returned successfully", "results": [
    {"Manufacturer": "Honda (American Honda Motor Co.)", "NHTSACampaignNumber": "18V421000", "parkIt": False,
     "parkOutSide": False, "overTheAirUpdate": False, "ReportReceivedDate": "20/06/2018",
     "Component": "EQUIPMENT:OTHER:LABELS", "Summary": "Labels may have random characters.",
     "Consequence": "Owner may not know a recall applies.", "Remedy": "Dealers will replace the label.",
     "ModelYear": "2018", "Make": "HONDA", "Model": "CIVIC"},
    {"Manufacturer": "Honda (American Honda Motor Co.)", "NHTSACampaignNumber": "20V314000", "parkIt": True,
     "parkOutSide": False, "overTheAirUpdate": False, "ReportReceivedDate": "28/05/2020",
     "Component": "FUEL SYSTEM, GASOLINE:DELIVERY:FUEL PUMP", "Summary": "The fuel pump may fail.",
     "Consequence": "The engine can stall while driving.", "Remedy": "Dealers will replace the fuel pump, free of charge.",
     "ModelYear": "2018", "Make": "HONDA", "Model": "CIVIC"},
]}
CPSC = [{
    "RecallID": 10904, "RecallNumber": "26669", "RecallDate": "2026-08-06T00:00:00", "Title": "Crib Mattresses Recalled",
    "Description": "Voomf-branded crib mattresses.", "URL": "https://www.cpsc.gov/Recalls/2026/x",
    "ConsumerContact": "Voomf by email.", "Products": [{"Name": "Voomf Crib Mattresses", "Model": "BYL001",
                                                         "NumberOfUnits": "About 2,401"}],
    "Images": [{"URL": "https://www.cpsc.gov/s3fs-public/voomf-1.jpg", "Caption": "x"}],
    "Hazards": [{"Name": "Entrapment hazard."}], "Remedies": [{"Name": "Stop using it and contact Voomf."}],
    "RemedyOptions": [{"Option": "Refund"}], "Retailers": [{"Name": "Sold At:\nAmazon.com"}], "ProductUPCs": [],
}]


def client(handler):
    seen = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)
    return RecallClient(http=httpx.Client(transport=httpx.MockTransport(wrapped))), seen


def reply(body, status=200):
    return lambda request: httpx.Response(status, text=json.dumps(body))


def test_vehicle_recalls_parse_flags_dates_and_sort_newest_first():
    c, seen = client(reply(NHTSA_CIVIC))
    recalls = c.vehicle_recalls("honda", "civic", "2018")
    assert [r.campaign for r in recalls] == ["20V314000", "18V421000"]
    fuel = recalls[0]
    assert fuel.park_it and fuel.reported == "2020-05-28" and "free of charge" in fuel.remedy
    assert fuel.url == "https://www.nhtsa.gov/recalls?nhtsaId=20V314000"
    assert dict(seen[0].url.params) == {"make": "honda", "model": "civic", "modelYear": "2018"}


def test_unknown_model_is_no_recalls_not_an_error():
    c, _ = client(reply({"Count": 0, "results": []}, status=400))   # what NHTSA really sends
    assert c.vehicle_recalls("Honda", "Notacar", 2018) == []


@pytest.mark.parametrize("make,model,year", [("", "Civic", 2018), ("Honda", "Civic", "soon"),
                                             ("Honda", "Civic", 1890), ("Honda", "<script>", 2018)])
def test_bad_vehicle_input_is_refused_before_any_call(make, model, year):
    c, seen = client(reply(NHTSA_CIVIC))
    with pytest.raises(RecallError):
        c.vehicle_recalls(make, model, year)
    assert seen == []


@pytest.mark.parametrize("status,message", [(429, "limiting"), (503, "unavailable"), (403, "HTTP 403")])
def test_http_errors_become_readable_messages(status, message):
    c, _ = client(reply({}, status=status))
    with pytest.raises(RecallError, match=message):
        c.vehicle_recalls("Honda", "Civic", 2018)


def test_network_failure_is_a_readable_error():
    def boom(request):
        raise httpx.ConnectError("down")
    c, _ = client(boom)
    with pytest.raises(RecallError, match="Couldn't reach NHTSA"):
        c.vehicle_recalls("Honda", "Civic", 2018)


def test_decode_vin():
    c, seen = client(reply({"Results": [{"Make": "HONDA", "Model": "Accord", "ModelYear": "2003",
                                         "Trim": "EX-V6", "ErrorCode": "0"}]}))
    v = c.decode_vin("1hgcm82633a004352")
    assert (v.make, v.model, v.year, v.trim) == ("Honda", "Accord", 2003, "EX-V6")
    assert seen[0].url.path.endswith("/DecodeVinValues/1HGCM82633A004352")


def test_undecodable_vin_and_malformed_vin():
    c, _ = client(reply({"Results": [{"Make": "", "Model": "", "ModelYear": "", "ErrorCode": "7"}]}))
    with pytest.raises(RecallError, match="couldn't decode"):
        c.decode_vin("1HGCM82633A004352")
    for bad in ("123", "1HGCM82633A00435O", "../../etc/passwd/x"):
        with pytest.raises(RecallError, match="17 letters"):
            normalise_vin(bad)


def test_product_recalls_parse_and_only_send_the_date_filter():
    c, seen = client(reply(CPSC))
    [r] = c.product_recalls(date(2026, 8, 1))
    assert (r.number, r.date, r.models, r.remedy_options) == ("26669", "2026-08-06", ["BYL001"], ["Refund"])
    assert r.products == ["Voomf Crib Mattresses"] and r.units == "About 2,401" and r.sold_at == "Amazon.com"
    assert r.image.endswith("voomf-1.jpg") and r.hazard == "Entrapment hazard."
    # CPSC returns *everything* for a misspelt parameter, so nothing else is ever sent
    assert dict(seen[0].url.params) == {"format": "json", "RecallDateStart": "2026-08-01"}


def test_unexpected_cpsc_body_is_an_error():
    c, _ = client(reply({"error": "x"}))
    with pytest.raises(RecallError, match="unexpected"):
        c.product_recalls(date(2026, 8, 1))


def test_vehicle_models_are_deduplicated():
    c, _ = client(reply({"results": [{"model": "CIVIC"}, {"model": "CIVIC SI"}, {"model": "CIVIC"}]}))
    assert c.vehicle_models("Honda", 2018) == ["CIVIC", "CIVIC SI"]
