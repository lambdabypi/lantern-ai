"""Sign in through Steam (OpenID 2.0). Steam is the identity provider; we never see a password.

Verification (all must pass, otherwise AuthError):
1. openid.mode is id_res (anything else means the player cancelled).
2. openid.op_endpoint is Steam's login endpoint.
3. openid.return_to is exactly the callback URL we sent (including our one-time state).
4. openid.claimed_id (and identity) look like a Steam claimed ID with a 64-bit SteamID.
5. Steam confirms the whole response server-to-server: check_authentication -> is_valid:true.
   Without this step anyone could forge a sign-in by editing the URL.
Docs: steamcommunity.com/dev and partner.steamgames.com/doc/features/auth
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urlencode

import httpx

from app.auth.accounts import AuthError

STEAM_OPENID_LOGIN = "https://steamcommunity.com/openid/login"
OPENID_NS = "http://specs.openid.net/auth/2.0"
_IDENTIFIER_SELECT = "http://specs.openid.net/auth/2.0/identifier_select"
# The two official pages disagree on http vs https, so accept both.
_CLAIMED_ID = re.compile(r"^https?://steamcommunity\.com/openid/id/(7656\d{13})$")
TIMEOUT_S = 10.0


def login_url(return_to: str, realm: str) -> str:
    """Where to send the player to sign in on Steam's own site."""
    return STEAM_OPENID_LOGIN + "?" + urlencode({
        "openid.ns": OPENID_NS,
        "openid.mode": "checkid_setup",
        "openid.return_to": return_to,
        "openid.realm": realm,
        "openid.identity": _IDENTIFIER_SELECT,
        "openid.claimed_id": _IDENTIFIER_SELECT,
    })


def verify(params: Mapping[str, str], expected_return_to: str, http: httpx.Client) -> str:
    """Check Steam's response and return the player's SteamID64. Raises AuthError otherwise."""
    if params.get("openid.mode") != "id_res":
        raise AuthError("Steam sign-in was cancelled.", status=400)
    if params.get("openid.op_endpoint") != STEAM_OPENID_LOGIN:
        raise AuthError("That sign-in didn't come from Steam.", status=400)
    if params.get("openid.return_to") != expected_return_to:
        raise AuthError("That Steam sign-in was meant for a different page. Please try again.", status=400)
    claimed = params.get("openid.claimed_id", "")
    match = _CLAIMED_ID.fullmatch(claimed)
    if not match or params.get("openid.identity") != claimed:
        raise AuthError("Steam returned an unexpected identity.", status=400)

    check = {k: v for k, v in params.items() if k.startswith("openid.")}
    check["openid.mode"] = "check_authentication"
    try:
        resp = http.post(STEAM_OPENID_LOGIN, data=check, timeout=TIMEOUT_S)
    except httpx.HTTPError:
        raise AuthError("Couldn't reach Steam to confirm the sign-in. Please try again.", status=502) from None
    lines = {line.strip() for line in resp.text.splitlines()}
    if resp.status_code != 200 or "is_valid:true" not in lines:
        raise AuthError("Steam didn't confirm that sign-in.", status=401)
    return match.group(1)
