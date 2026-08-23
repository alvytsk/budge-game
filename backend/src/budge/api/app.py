"""The composition root.

Everything the API needs is built here, once, in the lifespan — and torn
down in the reverse order. Nothing is constructed at import time, so
importing this module has no side effects and `budge migrate` can share
a process with it.

§10: migrations are a separate step. There is no `create_all` here and no
`command.upgrade`; an application started against an unmigrated database
fails its health check and says so.

Every leaf is now real. Both content leaves are the library (§5.3), and
the object store behind `images.media_sha256` is the S3-compatible one §10
asks for.

Both content leaves are the real library (§5.3). `DatabaseCategoryBank`
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

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from random import Random

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from budge.api.content import CachingContentDirectory
from budge.api.hub import MatchHub
from budge.api.routes import host_ws, library, matches, media, session, stage_ws
from budge.api.services import CommandGateway, MatchLifecycle, Services
from budge.api.settings import ApiSettings
from budge.db.engine import create_engine, sessionmaker_for
from budge.db.repository import MatchRepository
from budge.db.store import UnitOfWork
from budge.library.bank import DatabaseCategoryBank
from budge.library.catalogue import LibraryCatalogue
from budge.library.directory import DatabaseContentDirectory
from budge.media.s3 import S3MediaStore
from budge.services.ports import MediaStore
from budge.runtime.clock import SystemClock
from budge.runtime.manager import MatchManager
from budge.runtime.materialiser import Materialiser

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
        app.state.media = S3MediaStore(
            endpoint=settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            bucket=settings.s3_bucket,
            region=settings.s3_region,
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
    app.include_router(session.router)
    app.include_router(matches.router)
    app.include_router(library.router)
    app.include_router(media.router)
    app.include_router(host_ws.router)
    app.include_router(stage_ws.router)

    @app.get("/health")
    async def health(request: Request) -> JSONResponse:
        """§10: «Healthcheck проверяет доступность БД и хранилища.»

        Both probes run, always, even when the first already failed — a
        check that short-circuited would report one outage and hide the
        other, and an operator restarting a node needs to know whether it
        is one thing or two.
        """
        database, storage = await asyncio.gather(
            _database_reachable(request.app.state.engine),
            _storage_reachable(request.app.state.media),
        )
        checks = {"database": database, "storage": storage}
        healthy = all(checks.values())
        return JSONResponse(
            {"status": "ok" if healthy else "degraded", "checks": checks},
            status_code=200 if healthy else 503,
        )

    return app


async def _storage_reachable(store: MediaStore) -> bool:
    """`S3MediaStore.healthy` already promises never to raise; the guard
    here is for any other implementation that is wired in later — §10 wants
    a signal a load balancer can read, and a degraded node answering with a
    stack trace would be a 500 instead."""
    try:
        return await store.healthy()
    except Exception:
        logger.warning("health: the object store is unreachable", exc_info=True)
        return False


async def _database_reachable(engine: AsyncEngine) -> bool:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        logger.warning("health: the database is unreachable", exc_info=True)
        return False
    return True
