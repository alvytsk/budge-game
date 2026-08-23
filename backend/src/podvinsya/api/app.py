"""The composition root.

Everything the API needs is built here, once, in the lifespan — and torn
down in the reverse order. Nothing is constructed at import time, so
importing this module has no side effects and `podvinsya migrate` can share
a process with it.

§10: migrations are a separate step. There is no `create_all` here and no
`command.upgrade`; an application started against an unmigrated database
fails its health check and says so.

Two of the graph's leaves are plan 6's and do not exist yet, and both are
wired to explicitly-named unavailable implementations rather than left as
`None`: `UnavailableCategories` refuses a draw the way §6.3 routes as an
ordinary rejection, and `UnavailableContent` names nothing, which ruling 3
already made a state both projections handle. A `None` in either place
would be an `AttributeError` inside a writer task instead.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from random import Random

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from podvinsya.api.content import (
    CachingContentDirectory,
    UnavailableCategories,
    UnavailableContent,
)
from podvinsya.api.hub import MatchHub
from podvinsya.api.services import CommandGateway, MatchLifecycle, Services
from podvinsya.api.settings import ApiSettings
from podvinsya.db.engine import create_engine, sessionmaker_for
from podvinsya.db.repository import MatchRepository
from podvinsya.db.store import UnitOfWork
from podvinsya.runtime.clock import SystemClock
from podvinsya.runtime.manager import MatchManager
from podvinsya.runtime.materialiser import Materialiser

logger = logging.getLogger(__name__)


def build_app(settings: ApiSettings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url)
        sessions = sessionmaker_for(engine)
        clock = SystemClock()
        repository = MatchRepository(sessions)
        uow = UnitOfWork(sessions)
        hub = MatchHub(capacity=settings.frame_queue_capacity)
        bank = UnavailableCategories()

        def materialiser_factory() -> Materialiser:
            return Materialiser(clock, repository, bank, Random())

        manager = MatchManager(repository, uow, materialiser_factory, hub, clock)
        lifecycle = MatchLifecycle(repository, manager, clock, sessions)

        app.state.settings = settings
        app.state.engine = engine
        app.state.sessions = sessions
        app.state.clock = clock
        app.state.hub = hub
        app.state.services = Services(
            lifecycle=lifecycle,
            gateway=CommandGateway(lifecycle, manager),
            manager=manager,
            directory=CachingContentDirectory(UnavailableContent()),
            clock=clock,
        )
        try:
            yield
        finally:
            # The manager first, and the engine second. `shutdown` resolves
            # every origin still waiting on a command as it goes, and doing
            # that against a disposed pool would turn a clean stop into a
            # quarantine for every match that happened to be mid-command.
            await manager.shutdown()
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
