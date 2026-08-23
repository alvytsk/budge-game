"""§7.5's login, over the real transport.

The cookie's flags are asserted on the `Set-Cookie` header rather than on
the call that produced it: a browser reads the header, and a test that
checked the arguments would pass against a header that never carried them.
"""

from datetime import UTC, datetime, timedelta

import pytest

from api.conftest import TEST_PASSWORD, running_app
from budge.api.app import build_app
from budge.api.principal import SESSION_COOKIE
from budge.api.security import read_session
from budge.api.settings import ApiSettings

pytestmark = pytest.mark.integration


async def test_the_right_password_sets_a_session_cookie(api_settings: ApiSettings) -> None:
    async with running_app(build_app(api_settings)) as client:
        response = await client.post("/api/session", json={"password": TEST_PASSWORD})

    assert response.status_code == 204
    token = response.cookies[SESSION_COOKIE]
    assert read_session(
        api_settings.secret_key,
        token,
        now=datetime.now(UTC),
        ttl=timedelta(hours=api_settings.session_ttl_hours),
    )


async def test_a_wrong_password_sets_nothing(api_settings: ApiSettings) -> None:
    """Kills on: setting the cookie before verifying, which would make the
    password field decorative."""
    async with running_app(build_app(api_settings)) as client:
        response = await client.post("/api/session", json={"password": "not it"})

    assert response.status_code == 401
    assert SESSION_COOKIE not in response.cookies


async def test_the_session_cookie_is_http_only_and_lax(api_settings: ApiSettings) -> None:
    """Kills on: dropping `httponly`. §9.3 puts both surfaces in one Vite
    application, so anything in that bundle runs on the console's own
    origin — a readable session cookie would be one XSS away from an
    operator's whole match."""
    async with running_app(build_app(api_settings)) as client:
        response = await client.post("/api/session", json={"password": TEST_PASSWORD})

    header = response.headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=lax" in header


async def test_logging_out_clears_the_cookie_without_a_valid_session(
    api_settings: ApiSettings,
) -> None:
    """Deliberately unauthenticated: an operator whose session has expired
    must still be able to leave it. Kills on: putting `require_host` on the
    DELETE, which makes the one state you cannot exit the one you most want
    to."""
    async with running_app(build_app(api_settings)) as client:
        response = await client.delete("/api/session")

    assert response.status_code == 204
    assert 'budge_session=""' in response.headers["set-cookie"] or "Max-Age=0" in (
        response.headers["set-cookie"]
    )


async def test_the_login_body_carries_nothing_but_a_password(
    api_settings: ApiSettings,
) -> None:
    """§7.4: the client never says who it is. Kills on: dropping
    `extra="forbid"`, which would let a body carry a `role` that looks
    accepted from the client's side and is silently ignored."""
    async with running_app(build_app(api_settings)) as client:
        response = await client.post(
            "/api/session", json={"password": TEST_PASSWORD, "role": "host"}
        )

    assert response.status_code == 422
