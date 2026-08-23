"""The composition root.

Everything the API needs is built here, once, in the lifespan — and torn
down in the reverse order. Nothing is constructed at import time, so
importing this module has no side effects and `podvinsya migrate` can share
a process with it.

§10: migrations are a separate step. There is no `create_all` here and no
`command.upgrade`; an application started against an unmigrated database
fails its health check and says so.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from podvinsya.api.settings import ApiSettings
from podvinsya.db.engine import create_engine, sessionmaker_for

logger = logging.getLogger(__name__)


def build_app(settings: ApiSettings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url)
        app.state.settings = settings
        app.state.engine = engine
        app.state.sessions = sessionmaker_for(engine)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="Podvinsya", lifespan=lifespan)

    @app.get("/health")
    async def health(request: Request) -> JSONResponse:
        """Database reachability, and nothing else yet.

        §10 wants storage checked too. Object storage arrives with plan 6's
        media; when it does, it becomes a second entry in `checks` below.
        Naming the gap here beats leaving a future reader to notice that a
        green health check proves less than it looks like it does.
        """
        checks = {"database": await _database_reachable(request.app.state.engine)}
        healthy = all(checks.values())
        return JSONResponse(
            {"status": "ok" if healthy else "degraded", "checks": checks},
            status_code=200 if healthy else 503,
        )

    return app


async def _database_reachable(engine: AsyncEngine) -> bool:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        logger.warning("health: the database is unreachable", exc_info=True)
        return False
    return True
