"""Budget kill switch: takes Lantern off the public internet when this month's Google Cloud bill reaches KILL_AT_USD.

Cloud Billing sends budget updates to a Pub/Sub topic several times a day; this function (a Cloud Run function on
that topic) removes `allUsers` from the service's `roles/run.invoker` once `costAmount` reaches the limit. Requests
are then refused by Google's front end before any instance starts, so they cost nothing. Budget data runs a few
hours behind, so the bill can pass the limit a little before the switch fires.

Turn the app back on (from PowerShell, with the lantern gcloud config):
    gcloud run services add-iam-policy-binding lantern --region us-central1 --member allUsers --role roles/run.invoker
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.request

import functions_framework

SERVICE = os.environ["SERVICE"]            # projects/<project>/locations/us-central1/services/lantern
KILL_AT_USD = float(os.environ.get("KILL_AT_USD", "5"))
TOKEN_URL = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
RUN_API = "https://run.googleapis.com/v2/"
log = logging.getLogger(__name__)


def _call(method: str, url: str, token: str | None = None, body: dict | None = None) -> dict:
    if not (url == TOKEN_URL or url.startswith(RUN_API)):   # only Google's metadata server and the Run API
        raise ValueError(f"refusing to call {url!r}")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"} if token else \
        {"Metadata-Flavor": "Google"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read() or b"{}")


@functions_framework.cloud_event
def stop(event) -> None:
    update = json.loads(base64.b64decode(event.data["message"]["data"]))
    cost = float(update.get("costAmount", 0))
    if cost < KILL_AT_USD:
        log.info("budget update: $%.2f of $%.2f, nothing to do", cost, KILL_AT_USD)
        return
    token = _call("GET", TOKEN_URL)["access_token"]
    policy = _call("GET", f"{RUN_API}{SERVICE}:getIamPolicy", token)
    bindings = []
    for b in policy.get("bindings", []):
        if b.get("role") == "roles/run.invoker":
            b = {**b, "members": [m for m in b.get("members", []) if m != "allUsers"]}
        if b.get("members"):
            bindings.append(b)
    if bindings == policy.get("bindings", []):
        log.warning("budget $%.2f reached; the app is already off the public internet", cost)
        return
    _call("POST", f"{RUN_API}{SERVICE}:setIamPolicy", token, {"policy": {**policy, "bindings": bindings}})
    log.warning("budget $%.2f reached (limit $%.2f): removed public access to %s", cost, KILL_AT_USD, SERVICE)
