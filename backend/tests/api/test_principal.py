"""Who the server thinks is calling, and what it takes to convince it.

Every test here goes through a real app and a real cookie jar, because the
property under test is about the *transport*: §7.4 is satisfied by there
being no code path from a body to a principal, and a unit test that handed
`require_host` a hand-built request would not exercise that path at all.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends, FastAPI

from api.conftest import TEST_SECRET, running_app
from budge.api.app import build_app
from budge.api.principal import SESSION_COOKIE, HostPrincipal, require_host, stage_principal_for
from budge.api.security import mint_session, mint_stage_token
from budge.api.settings import ApiSettings
from budge.domain.ids import MatchId
from uuid import uuid4

pytestmark = pytest.mark.integration


def app_with_a_guarded_route(settings: ApiSettings) -> FastAPI:
    """A real app plus one route that does nothing but require the host.

    Mounted in the test rather than in production code: the property is
    `require_host`'s, and giving it a route of its own keeps this module
    passing whatever Task 9 later does to the real ones.
    """
    app = build_app(settings)

    @app.get("/only-the-host")
    async def guarded(principal: HostPrincipal = Depends(require_host)) -> dict[str, bool]:
        return {"ok": True}

    return app


def cookie_header(token: str) -> dict[str, str]:
    """The session cookie as it actually travels.

    Set as a header rather than through httpx's per-request `cookies=`,
    which is deprecated — and a header is closer to what a browser sends
    anyway.
    """
    return {"cookie": f"{SESSION_COOKIE}={token}"}


async def test_a_request_with_no_cookie_is_refused(api_settings: ApiSettings) -> None:
    async with running_app(app_with_a_guarded_route(api_settings)) as client:
        assert (await client.get("/only-the-host")).status_code == 401


async def test_a_request_with_a_valid_cookie_is_admitted(api_settings: ApiSettings) -> None:
    token = mint_session(TEST_SECRET, issued_at=datetime.now(UTC))
    async with running_app(app_with_a_guarded_route(api_settings)) as client:
        response = await client.get("/only-the-host", headers=cookie_header(token))
    assert response.status_code == 200


async def test_a_request_with_a_forged_cookie_is_refused(api_settings: ApiSettings) -> None:
    """Kills on: trusting the cookie's presence rather than its signature —
    the one-line mistake that turns the whole session scheme into a flag
    any client can set."""
    forged = mint_session("a-key-this-server-does-not-have", issued_at=datetime.now(UTC))
    async with running_app(app_with_a_guarded_route(api_settings)) as client:
        response = await client.get("/only-the-host", headers=cookie_header(forged))
    assert response.status_code == 401


async def test_a_request_with_an_expired_cookie_is_refused(api_settings: ApiSettings) -> None:
    """Kills on: reading the cookie without its TTL, which would make every
    session this server ever issued valid forever."""
    stale = mint_session(
        TEST_SECRET,
        issued_at=datetime.now(UTC) - timedelta(hours=api_settings.session_ttl_hours + 1),
    )
    async with running_app(app_with_a_guarded_route(api_settings)) as client:
        response = await client.get("/only-the-host", headers=cookie_header(stale))
    assert response.status_code == 401


async def test_a_stage_token_does_not_open_a_host_route(api_settings: ApiSettings) -> None:
    """§7.5: the two tokens decide which of two projections to build, so
    one must never read as the other. Kills on: dropping the subject prefix
    from either payload."""
    stage = mint_stage_token(TEST_SECRET, MatchId(uuid4()))
    async with running_app(app_with_a_guarded_route(api_settings)) as client:
        response = await client.get("/only-the-host", headers=cookie_header(stage))
    assert response.status_code == 401


async def test_a_body_cannot_make_anyone_the_host(api_settings: ApiSettings) -> None:
    """§7.4, as a route-level property: there is no payload that grants a
    principal, because nothing on the path from a request to a principal
    reads one.

    Kills on: any future `require_host` that fell back to a header or a
    field when the cookie was missing."""
    async with running_app(app_with_a_guarded_route(api_settings)) as client:
        response = await client.request(
            "GET", "/only-the-host", json={"principal": "host", "role": "host"}
        )
    assert response.status_code == 401


def test_a_stage_token_names_the_match_it_was_minted_for(api_settings: ApiSettings) -> None:
    """Ruling 7: derived, not stored — and bound to exactly one match."""
    match_id = MatchId(uuid4())
    principal = stage_principal_for(api_settings, mint_stage_token(TEST_SECRET, match_id))
    assert principal is not None
    assert principal.match_id == match_id


def test_an_unreadable_stage_token_names_nothing(api_settings: ApiSettings) -> None:
    """Kills on: returning a principal for an unverified token, which would
    let anyone watch any match by guessing a UUID."""
    assert stage_principal_for(api_settings, "not-a-token") is None
    session = mint_session(TEST_SECRET, issued_at=datetime.now(UTC))
    assert stage_principal_for(api_settings, session) is None


def test_a_host_principal_carries_no_identity() -> None:
    """Deliberately empty (see `principal.py`): a name here would be a
    second source of identity that a later route could start comparing a
    payload against — the shape §7.4 exists to forbid."""
    assert not getattr(HostPrincipal(), "__dict__", {})
    assert HostPrincipal() == HostPrincipal()
