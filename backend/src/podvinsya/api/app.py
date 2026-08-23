"""The composition root.

Everything the API needs is built here, once, in the lifespan — and torn
down in the reverse order. Nothing is constructed at import time, so
importing this module has no side effects and `podvinsya migrate` can share
a process with it.

§10: migrations are a separate step. There is no `create_all` here and no
`command.upgrade`; an application started against an unmigrated database
fails its health check and says so.

Both content leaves are now the real library (§5.3). `DatabaseCategoryBank`
draws under the `FOR SHARE` §5.3 requires, and `DatabaseContentDirectory`
names categories and answers images. An empty library behaves exactly as
the unavailable implementations they replaced did — `ContentExhausted`,
which §6.3 routes as an ordinary rejection — so a server started against a
database nobody has stocked refuses a deal cleanly rather than quarantining
the match.

`UnavailableCategories` and `UnavailableContent` remain in `api/content.py`
as the null implementations the tests use to describe that state
deliberately.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from random import Random

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from podvinsya.api.content import CachingContentDirectory
from podvinsya.api.hub import MatchHub
from podvinsya.api.routes import host_ws, library, matches, session, stage_ws
from podvinsya.api.services import CommandGateway, MatchLifecycle, Services
from podvinsya.api.settings import ApiSettings
from podvinsya.db.engine import create_engine, sessionmaker_for
from podvinsya.db.repository import MatchRepository
from podvinsya.db.store import UnitOfWork
from podvinsya.library.bank import DatabaseCategoryBank
from podvinsya.library.catalogue import LibraryCatalogue
from podvinsya.library.directory import DatabaseContentDirectory
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
        bank = DatabaseCategoryBank(sessions)

        def materialiser_factory() -> Materialiser:
            return Materialiser(clock, repository, bank, Random())

        manager = MatchManager(repository, uow, materialiser_factory, hub, clock)
        lifecycle = MatchLifecycle(repository, manager, clock, sessions)

        app.state.settings = settings
        app.state.engine = engine
        app.state.sessions = sessions
        app.state.clock = clock
        app.state.hub = hub
        services = Services(
            lifecycle=lifecycle,
            gateway=CommandGateway(lifecycle, manager),
            manager=manager,
            # The cache stays: §8 makes the library permanent, so a name
            # read once cannot become wrong. It never caches a miss, which
            # is what keeps it correct against a library the operator is
            # still filling in during setup.
            directory=CachingContentDirectory(DatabaseContentDirectory(sessions)),
            clock=clock,
        )
        app.state.services = services
        # Ruling 12: the stage's route is handed this and never `services`,
        # which carries a `.gateway` — one attribute away from a command.
        app.state.read_only = services.read_only()
        app.state.catalogue = LibraryCatalogue(sessions)
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
    app.include_router(session.router)
    app.include_router(matches.router)
    app.include_router(library.router)
    app.include_router(host_ws.router)
    app.include_router(stage_ws.router)

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
