from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.auth.accounts import AccountStore, AuthError
from app.auth.steam_openid import STEAM_OPENID_LOGIN, login_url, verify
from app.main import create_app
from tests.test_agent_loop import make_loop
from tests.test_steam_import import FakeSteam

SID = "76561198000000001"
RETURN_TO = "http://testserver/auth/steam/callback?state=abc"


def steam_params(return_to=RETURN_TO, sid=SID, **over):
    claimed = f"https://steamcommunity.com/openid/id/{sid}"
    p = {"openid.ns": "http://specs.openid.net/auth/2.0", "openid.mode": "id_res",
         "openid.op_endpoint": STEAM_OPENID_LOGIN, "openid.claimed_id": claimed, "openid.identity": claimed,
         "openid.return_to": return_to, "openid.response_nonce": "2026-10-23T12:00:00Zabc",
         "openid.assoc_handle": "1234567890", "openid.signed": "signed,op_endpoint,claimed_id,identity,return_to",
         "openid.sig": "c2lnbmF0dXJl"}
    p.update(over)
    return p


def steam_http(valid=True, status=200, fail=False, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            raise httpx.ConnectError("down")
        if seen is not None:
            seen.append(dict(parse_qs(request.content.decode())))
        body = f"ns:http://specs.openid.net/auth/2.0\nis_valid:{'true' if valid else 'false'}\n"
        return httpx.Response(status, text=body)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_login_url_points_to_steam_with_our_return_address():
    q = parse_qs(urlparse(login_url(RETURN_TO, "http://testserver")).query)
    assert urlparse(login_url(RETURN_TO, "x")).netloc == "steamcommunity.com"
    assert q["openid.mode"] == ["checkid_setup"] and q["openid.return_to"] == [RETURN_TO]
    assert q["openid.realm"] == ["http://testserver"]


def test_verify_accepts_a_genuine_response_and_asks_steam_to_confirm():
    seen = []
    assert verify(steam_params(), RETURN_TO, steam_http(seen=seen)) == SID
    assert seen[0]["openid.mode"] == ["check_authentication"]
    assert "state" not in seen[0]  # only openid.* fields go back to Steam


@pytest.mark.parametrize("params,http,message", [
    (steam_params(**{"openid.mode": "cancel"}), steam_http(), "cancelled"),
    (steam_params(**{"openid.op_endpoint": "https://evil.example/openid/login"}), steam_http(), "didn't come from Steam"),
    (steam_params(return_to="http://evil.example/auth/steam/callback?state=abc"), steam_http(), "different page"),
    (steam_params(sid="12345"), steam_http(), "unexpected identity"),
    (steam_params(**{"openid.identity": "https://steamcommunity.com/openid/id/76561190000000000"}), steam_http(),
     "unexpected identity"),
    (steam_params(), steam_http(valid=False), "didn't confirm"),       # forged: Steam says no
    (steam_params(), steam_http(status=500), "didn't confirm"),
    (steam_params(), steam_http(fail=True), "Couldn't reach Steam"),
])
def test_verify_rejects_forged_or_broken_responses(params, http, message):
    with pytest.raises(AuthError, match=message):
        verify(params, RETURN_TO, http)


def test_steam_accounts_cannot_use_passwords_and_prefix_is_reserved(settings):
    store = AccountStore(settings.data_dir)
    account, created = store.get_or_create_steam(SID, "Shreyas")
    assert (account.user_id, account.display_name, created) == (f"steam-{SID}", "Shreyas", True)
    assert store.get_or_create_steam(SID)[1] is False
    with pytest.raises(AuthError):
        store.authenticate(f"steam-{SID}", "anything-at-all")
    with pytest.raises(AuthError, match="reserved"):
        store.register(f"steam-{SID}", "password123")
    with pytest.raises(AuthError):
        store.get_or_create_steam("../etc")


def app_with(settings, http, steam=None):
    loop, _ = make_loop(settings, [])
    return create_app(loop=loop, settings=settings, steam=steam, openid_http=http)


def start(client):
    r = client.get("/auth/steam/start", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(STEAM_OPENID_LOGIN)
    return_to = parse_qs(urlparse(r.headers["location"]).query)["openid.return_to"][0]
    return return_to, parse_qs(urlparse(return_to).query)["state"][0]


def test_full_sign_in_creates_account_imports_library_and_signs_in(settings):
    steam = FakeSteam()
    steam.player_name = lambda sid: "Shreyas"
    client = TestClient(app_with(settings, steam_http(), steam))
    return_to, state = start(client)
    r = client.get("/auth/steam/callback", params={"state": state, **steam_params(return_to=return_to)},
                   follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    me = client.get("/api/me").json()
    assert (me["user_id"], me["display_name"], me["kind"]) == (f"steam-{SID}", "Shreyas", "user")
    assert {g["title"] for g in client.get("/api/profile").json()["games"]} == {"Warframe", "The Witcher 3: Wild Hunt"}


def test_second_sign_in_does_not_reimport(settings):
    steam = FakeSteam()
    calls = []
    steam.owned_games = lambda sid: calls.append(sid) or FakeSteam().games
    client = TestClient(app_with(settings, steam_http(), steam))
    for _ in range(2):
        return_to, state = start(client)
        client.get("/auth/steam/callback", params={"state": state, **steam_params(return_to=return_to)})
    assert len(calls) == 1


def test_callback_without_matching_state_cookie_is_refused(settings):
    attacker = TestClient(app_with(settings, steam_http()))
    return_to, state = start(attacker)
    victim = TestClient(app_with(settings, steam_http()))  # different browser: no state cookie
    r = victim.get("/auth/steam/callback", params={"state": state, **steam_params(return_to=return_to)},
                   follow_redirects=False)
    assert r.status_code == 303 and "auth_error=" in r.headers["location"]
    assert victim.get("/api/me").status_code == 401


def test_forged_callback_is_refused(settings):
    client = TestClient(app_with(settings, steam_http(valid=False)))
    return_to, state = start(client)
    r = client.get("/auth/steam/callback", params={"state": state, **steam_params(return_to=return_to)},
                   follow_redirects=False)
    assert "auth_error=" in r.headers["location"] and "confirm" in r.headers["location"]
    assert client.get("/api/me").status_code == 401


def test_public_base_url_is_used_for_the_return_address(settings):
    from dataclasses import replace
    client = TestClient(app_with(replace(settings, public_base_url="https://lantern.example"), steam_http()))
    return_to, _ = start(client)
    assert return_to.startswith("https://lantern.example/auth/steam/callback?state=")
