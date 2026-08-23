"""The composition root, exercised through its own lifespan.

Both tests are integration-marked: the point of a health check is what it
says about a real database, and a health check tested against a fake would
prove only that the fake was reachable.
"""

import pytest

from api.conftest import running_app
from pathlib import Path

import budge.api.app
from budge.api.app import build_app
from budge.api.settings import ApiSettings
from support.media import InMemoryMediaStore

pytestmark = pytest.mark.integration

# Port 1 is reserved and nothing listens on it, so a connection attempt is
# refused immediately rather than hanging until a TCP timeout.
UNREACHABLE_URL = "postgresql+asyncpg://podvinsya:podvinsya@127.0.0.1:1/podvinsya_test"


async def test_health_reports_both_checks(
    s3_bucket: None, api_settings: ApiSettings
) -> None:
    """§10: «Healthcheck проверяет доступность БД и хранилища.»

    Kills on: the storage entry never being added — `all()` over a
    one-key dict reports `ok` whatever the object store is doing, and the
    node would advertise itself as healthy with no pictures on it."""
    async with running_app(build_app(api_settings)) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"database": True, "storage": True},
    }


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
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"]["database"] is False


async def test_health_is_degraded_when_only_the_store_is_down(
    api_settings: ApiSettings,
) -> None:
    """The check that makes the second probe worth having.

    Kills on: reporting `ok` while storage is false — which is exactly what
    an `all()` over a dict that never gained its second key does, silently,
    on a node where every picture is a 503."""
    app = build_app(api_settings)
    async with running_app(app) as client:
        app.state.media = InMemoryMediaStore(fail=True)
        response = await client.get("/health")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "checks": {"database": True, "storage": False},
    }


async def test_both_probes_run_even_when_the_first_fails(
    api_settings: ApiSettings,
) -> None:
    """Kills on: short-circuiting on the first failure — an operator
    restarting a node needs to know whether it is one outage or two, and a
    report naming only the database would send them to the wrong machine."""
    settings = api_settings.model_copy(update={"database_url": UNREACHABLE_URL})
    app = build_app(settings)
    async with running_app(app) as client:
        app.state.media = InMemoryMediaStore(fail=True)
        response = await client.get("/health")

    assert response.json()["checks"] == {"database": False, "storage": False}


def test_the_health_docstring_no_longer_promises_a_missing_probe() -> None:
    """Plan 4 wrote «object storage arrives with plan 6's media» into
    `health`'s docstring as a deliberate note about a gap. The gap is
    closed.

    Kills on: leaving the paragraph in place — a comment describing a gap
    that no longer exists is a comment that lies, and the next reader
    trusts it over the code."""
    source = Path(budge.api.app.__file__).read_text(encoding="utf-8")

    assert "arrives with plan 6" not in source
    assert "nothing else yet" not in source
    # And the promise it replaced is actually kept.
    assert "_storage_reachable" in source
