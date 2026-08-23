"""Fixtures for the API suite.

The database-backed modules reuse `tests/db`'s engine and schema fixtures,
the same way `tests/runtime`'s conftest does, and carry both
`pytest.mark.integration` and `pytest.mark.asyncio(loop_scope="session")`.
Modules that touch no database — the projection, the hub, the security
primitives — carry neither and run in the fast lane.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI

from db.conftest import clean_db, engine, migrated_schema, sessions
from media.conftest import clean_bucket, s3_bucket
from budge.api.security import hash_password
from budge.api.settings import ApiSettings
from support.db import (
    DATABASE_URL,
    S3_ACCESS_KEY,
    S3_BUCKET,
    S3_ENDPOINT,
    S3_SECRET_KEY,
)

__all__ = [
    "clean_db",
    "engine",
    "migrated_schema",
    "sessions",
    "clean_bucket",
    "s3_bucket",
    "api_settings",
    "running_app",
]

TEST_SECRET = "test-secret-key-not-used-anywhere-real"
TEST_PASSWORD = "correct horse battery staple"


@pytest.fixture
def api_settings() -> ApiSettings:
    """A settings object built explicitly rather than from the environment.

    `ApiSettings()` would read `PODVINSYA_*` and make every test depend on
    the shell it ran in. The password hash is computed in the fixture, not
    hardcoded, because `hash_password` salts randomly.
    """
    return ApiSettings(
        database_url=DATABASE_URL,
        secret_key=TEST_SECRET,
        host_password=hash_password(TEST_PASSWORD),
        s3_endpoint=S3_ENDPOINT,
        s3_access_key=S3_ACCESS_KEY,
        s3_secret_key=S3_SECRET_KEY,
        s3_bucket=S3_BUCKET,
    )


@asynccontextmanager
async def running_app(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Run the app's lifespan and yield a client bound to it.

    `httpx.ASGITransport` never sends the `lifespan` scope, so without this
    the engine on `app.state` would never exist and every test would fail
    on an attribute that the production server always has.
    """
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
