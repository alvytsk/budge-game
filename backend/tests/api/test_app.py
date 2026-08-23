"""The composition root, exercised through its own lifespan.

Both tests are integration-marked: the point of a health check is what it
says about a real database, and a health check tested against a fake would
prove only that the fake was reachable.
"""

import pytest

from api.conftest import running_app
from podvinsya.api.app import build_app
from podvinsya.api.settings import ApiSettings

pytestmark = pytest.mark.integration

# Port 1 is reserved and nothing listens on it, so a connection attempt is
# refused immediately rather than hanging until a TCP timeout.
UNREACHABLE_URL = "postgresql+asyncpg://podvinsya:podvinsya@127.0.0.1:1/podvinsya_test"


async def test_health_reports_ok_against_a_reachable_database(
    api_settings: ApiSettings,
) -> None:
    """A GET /health against the live test database returns 200 and
    {"status": "ok", "checks": {"database": true}}."""
    async with running_app(build_app(api_settings)) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": True}}


async def test_health_reports_degraded_when_the_database_is_unreachable(
    api_settings: ApiSettings,
) -> None:
    """Built against a URL pointing at a closed port, /health returns 503
    and reports database: false rather than raising.

    Kills on: removing the try/except in `_database_reachable`, which turns
    a degraded node into a 500 with a stack trace instead of a health
    signal a load balancer can read.
    """
    settings = api_settings.model_copy(update={"database_url": UNREACHABLE_URL})

    async with running_app(build_app(settings)) as client:
        response = await client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "checks": {"database": False}}
