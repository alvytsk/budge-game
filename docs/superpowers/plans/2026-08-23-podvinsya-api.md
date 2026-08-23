# API и проекции «Подвинься» — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the running match on the wire — one authenticated operator sending commands over a WebSocket, one read-only stage screen watching the same match through a projection that structurally cannot carry a correct answer, and a REST surface for assembling a match and loading it the first time.

**Architecture:** `api/` is the outermost ring and the only place that knows about HTTP. It owns three things and nothing else. A **hub** takes the runtime's synchronous `publish` and turns it into one bounded queue per subscriber; a **projection** turns an immutable `MatchState` into one of exactly two Pydantic frames, per subscriber, in that subscriber's own writer task where it is allowed to await content; and a **principal** is derived from the transport — a signed cookie for the host, a signed match token in the URL for the stage — never from anything the client puts in a payload. The two WebSocket endpoints live in two modules, and the stage's module never imports the command gateway: §7.5's «отправить команду он не может конструктивно» is a property of the import graph, not of a runtime check.

**Tech Stack:** Python 3.12, FastAPI over Starlette, Pydantic v2 (the frame models are plan 5's codegen input), the plan-3 runtime, the plan-2 persistence layer, pytest + pytest-asyncio + httpx.

**Spec:** `docs/superpowers/specs/2026-08-22-podvinsya-design.md` — §7 is this plan's mandate. §11's «Проекции» and «Противодавление» rows say what its tests owe, §6.1's `Broadcaster` contract bounds the hub, and §5.2's read model is what the match list finally makes reachable.

**Branch:** `feature/api`, cut from `feature/runtime` (commit `3881b07`), which is cut from `feature/persistence` (`043bd42`), which is not yet merged to `main`. Nothing under `src/podvinsya/domain/`, `src/podvinsya/db/` or `src/podvinsya/runtime/` is modified by this plan; `src/podvinsya/services/ports.py` gains one protocol and nothing else.

## Global Constraints

- Python `>=3.12`. `mypy --strict` clean over `src/podvinsya` and `tests`; `ruff check` clean with `select = ["E4", "E7", "E9", "F", "E501"]` and `line-length = 100`.
- Dependency direction is one-way: `api → services → domain`, with `runtime` and `db` as service-layer implementations. Nothing under `domain/`, `db/` or `runtime/` imports anything from `api/`.
- «Клиент **никогда не передаёт, кто он**. Принципал выводится из аутентифицированной сессии.» (§7.4) No request body, query parameter or WebSocket frame in this plan carries an actor, a role, a seat or a player identity *as the sender*. A test walks the generated JSON Schema of every inbound model to keep it that way.
- «Экран сцены не получает никогда: правильные ответы к картинкам; название категории любой группы с `revealed = False`. Данных просто нет в отправленном кадре. Прятать ответ на клиенте недопустимо.» (§7.1)
- «Каждое сообщение несёт состояние целиком.» (§7.2) A gap in `seq` costs narrative, never correctness: no client ever asks for a resync, and no endpoint offers one.
- «Сервер не шлёт тики.» (§7.3) Every frame carries `remaining_ms` for both players, `answering`, `anchor`, `paused`, an absolute `deadline_at` and the server's own `now`. Nothing in this plan sends a periodic timer message.
- «`publish` синхронный … проецирует и `put_nowait`, ничего больше.» (§6.1) The hub's `publish` does no `await`, no I/O, and lets no exception escape — including from a subscriber whose socket has already gone.
- «Отправить команду он не может конструктивно.» (§7.5) The stage transport has no path to `submit`, enforced by module boundaries and asserted by a test that reads the import graph.
- «`operation_id` всегда генерируется сервером.» (§5.1) A client correlation id may be echoed back in an acknowledgement and is used for nothing else.
- «Медиа контент-адресуемо по sha256; в сообщениях ездят идентификаторы, а не URL.» (§7.6) No frame in this plan contains a URL.
- «Миграции применяются отдельным шагом до старта приложения, а не при импорте.» (§10) No `create_all`, no implicit migration in the lifespan.
- The event log stays append-only, and every write goes through the plan-3 runtime. `api/` never opens a transaction to write.
- Code, identifiers and comments in English. User-facing Russian copy belongs to the front-end plans; the only Russian this plan may emit is a category label, and it does not emit one — the stage frame omits the field, and the word «Секрет» is the front-end's to render.
- **Every test states what would kill it.** Load-bearing tests — anything proving a leak cannot happen, an unauthenticated caller is refused, or the loop is not blocked — are written out in full below. The remaining tests are specified by name and docstring, and every task report must name, for each such test, the single change to the code under test that would make it fail. A test whose killing mutation cannot be named is hollow and gets fixed before the task is reported.

## Rulings made while writing this plan

1. **The stage's projection never *asks* for what it may not show.** `project_stage` collects the category ids of revealed groups only and passes `images=frozenset()` — it never requests a single image answer. The leak is therefore impossible one layer earlier than the frame: there is nothing in the function's local scope to leak. The whole-tree frame test §11 demands stays, as the backstop; the fake directory additionally records what it was asked for, and a second test asserts no unrevealed category id was ever in the request. *Cost if wrong: none — this is strictly stronger than filtering on the way out.*
2. **`ContentDirectory` is declared here and implemented in plan 6.** Same shape as plan 3's `CategoryBank`: the projection genuinely needs category names and image answers today, so the protocol lives in `services/ports.py`, the tests drive a fake, and plan 6 supplies the database-backed implementation. *Cost if wrong: plan 6 reshapes one signature.*
3. **A category the directory cannot name is `hidden` to the stage and `null` to the host.** Until plan 6 exists, no category has a name. The stage's category union has exactly two variants — `named` and `hidden` — and `hidden` covers both «секрет, ещё не раскрыт» and «имени нет». That keeps the stage leak-proof by construction and puts the content defect where an operator can see it. *Cost if wrong: a revealed group with a missing name reads as a secret on the big screen — visible, not silent.*
4. **The API re-validates nothing the domain validates.** Board size, player count, divisibility, turn order, adjacency: `decide` owns all of it, raises `Rejected`, and the API maps `Rejected` to `409` with the reason's string value. A Pydantic constraint duplicating `validate_board` would be a second source of truth that drifts. *Cost if wrong: an invalid board round-trips to the database layer before being refused — one wasted request, no wrong state.*
5. **Setup goes over REST, live judging goes over WebSocket.** §7.4 assigns «сборка партии» to REST and the live match to WS, and this plan follows that line exactly: `CreateMatch`/`AddPlayer`/`AssignSecret`/`DealBoard`/`StartMatch` are REST; `DeclareAttack`/`StartDuel`/`JudgeCorrect`/`JudgePass`/`PauseDuel`/`ResumeDuel`/`UndoLastJudgement` are WS. Both funnel into the same `CommandGateway` and the same outcome mapping. *Cost if wrong: the console opens its socket one step earlier than it needs to.*
6. **Authentication uses the standard library and nothing else.** `hashlib.scrypt` for the operator's password, `hmac` + SHA-256 for the session cookie and the stage token. One operator, one password, an isolated network (§1.1, §7.5): adding argon2 or `itsdangerous` would buy nothing and add an upgrade cadence to a machine that lives in a meeting room. *Cost if wrong: swapping in a library KDF later touches two functions in one module.*
7. **The stage token is derived, not stored.** `HMAC(secret_key, "stage." + match_id)`. No column, no migration, no lookup — and the token is bound to exactly one match, so a link to yesterday's match cannot watch today's. It does not expire and can be revoked only by rotating `secret_key`. §7.5 itself sets the bar here: the token exists «чтобы сервер знал, какую из двух проекций строить», not to withstand an adversary. *Cost if wrong: revocation needs a `stage_token` column and a migration in a later plan.*
8. **The session cookie is stateless.** A signed `host.<issued_at>` payload, verified against `session_ttl_hours` with one minute of forward skew allowed. A server-side session table would log the operator out on every restart — and §4.4 makes a restart a *normal* event mid-show, one the host is expected to recover from by pressing resume, not by logging in again. *Cost if wrong: rotating `secret_key` logs the operator out, which is the intended behaviour anyway.*
9. **Projection happens in the subscriber's writer task, not in `publish`.** §6.1 forbids the loop to await, and the projection must await content. So `publish` enqueues the immutable `(base_seq, state, events)` triple — the cheapest possible non-blocking act — and each subscriber's own task projects the frames it actually sends. A frame dropped under backpressure is never projected at all. *Cost if wrong: two subscribers of the same kind project the same state twice; at three subscribers that is not worth a shared future.*
10. **Backpressure drops the oldest frame, never the newest.** The queue is bounded; on overflow the oldest waiting frame is discarded and the new one takes its place. This is safe *only* because of §7.2 — every message carries the whole state — and it is the reason §7.2 exists. The drop is counted and logged; it is never an error. *Cost if wrong: a stage screen that stalled for minutes misses intermediate narration, which is exactly what §7.2 says is acceptable.*
11. **`ApiSettings` extends `Settings` rather than replacing it.** `podvinsya migrate` must keep working without a `PODVINSYA_SECRET_KEY`, and `db/migrations/env.py` constructs `Settings()` directly. So the API's required fields live on a subclass that only `build_app` constructs. *Cost if wrong: the migrate step demands a secret it does not use.*
12. **The two WebSocket endpoints live in two modules.** `api/routes/stage_ws.py` imports the hub, the lifecycle and the projection. It does not import `CommandGateway`, `MatchManager` or any domain `Command`, and a test asserts that by reading the module's imports. This is what makes §7.5's «конструктивно» checkable instead of aspirational. *Cost if wrong: two small modules where one would do.*
13. **Frames carry the last batch's event type names and, when present, the resolution.** §9.1's third beat — the capture animation — needs to know that a resolution just happened and which cells moved, and §7.2 forbids the client from reconstructing that by diffing against a frame it may never have received. `last_event_types` (the frozen wire names from plan 2's registry) plus an optional `resolution` block covers it without duplicating the log on the wire. Neither carries content, so neither can leak. *Cost if wrong: the stage animates a merge it cannot describe, which §9.1 calls unacceptable.*
14. **`GET /api/matches` reads the §5.2 projection tables.** Nothing has read them since plan 2 built them. A list endpoint that folded every log instead would make the read model dead code with tests. *Cost if wrong: the list shows a stale row if the projection ever falls behind its own transaction — it cannot, it is written in that transaction.*
15. **The health check covers the database only.** §10 asks for database *and* storage; object storage arrives with plan 6's media. The endpoint is written so adding the second probe is one entry in a list, and the gap is named in its docstring rather than left to be discovered. *Cost if wrong: a green health check on a node whose storage is down — impossible today, since nothing stores anything yet.*

## File Structure

```
backend/pyproject.toml                            modify  fastapi, uvicorn, httpx
backend/src/podvinsya/config.py                   unchanged
backend/src/podvinsya/cli.py                      modify  hash-password, serve
backend/src/podvinsya/services/ports.py           modify  + ContentDirectory, ContentDescription
backend/src/podvinsya/api/__init__.py             create
backend/src/podvinsya/api/settings.py             create  ApiSettings
backend/src/podvinsya/api/security.py             create  scrypt, hmac, session, stage token
backend/src/podvinsya/api/principal.py            create  HostPrincipal, StagePrincipal, dependencies
backend/src/podvinsya/api/content.py              create  CachingContentDirectory, UnavailableContent
backend/src/podvinsya/api/schemas/__init__.py     create
backend/src/podvinsya/api/schemas/frames.py       create  StageFrame, HostFrame and their parts
backend/src/podvinsya/api/schemas/commands.py     create  inbound command union, ack
backend/src/podvinsya/api/schemas/rest.py         create  REST request and response bodies
backend/src/podvinsya/api/projection.py           create  project_stage, project_host
backend/src/podvinsya/api/hub.py                  create  Update, Subscriber, MatchHub
backend/src/podvinsya/api/outcomes.py             create  CommandOutcome -> HTTP / ack
backend/src/podvinsya/api/services.py             create  MatchLifecycle, CommandGateway, Services
backend/src/podvinsya/api/app.py                  create  build_app, lifespan, health
backend/src/podvinsya/api/routes/__init__.py      create
backend/src/podvinsya/api/routes/session.py       create  POST/DELETE /api/session
backend/src/podvinsya/api/routes/matches.py       create  assembly, snapshot, list
backend/src/podvinsya/api/routes/host_ws.py       create  the operator's socket
backend/src/podvinsya/api/routes/stage_ws.py      create  the read-only socket
backend/tests/support/asgi.py                     create  in-loop ASGI WebSocket client
backend/tests/support/content.py                  create  RecordingContentDirectory
backend/tests/api/                                create  one module per task
```

---

### Task 1: The application skeleton and an ASGI client that runs in the test's own loop

Nothing is exposed yet. This task adds the HTTP dependencies, the settings type the whole plan hangs off, a `build_app` with a lifespan that owns the engine, a health check, and — the part every later task depends on — a WebSocket client that speaks ASGI directly instead of through a background thread, so a test can drive a fake clock and a socket in one event loop.

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/src/podvinsya/api/__init__.py`
- Create: `backend/src/podvinsya/api/settings.py`
- Create: `backend/src/podvinsya/api/app.py`
- Create: `backend/tests/support/asgi.py`
- Create: `backend/tests/api/__init__.py`
- Create: `backend/tests/api/conftest.py`
- Test: `backend/tests/api/test_app.py`

**Interfaces:**
- Produces: `ApiSettings`, `build_app(settings: ApiSettings) -> FastAPI`, `app.state.settings`, `app.state.engine`, `app.state.sessions`; `support.asgi.ASGIWebSocketClient`.
- Consumes: `podvinsya.config.Settings`, `podvinsya.db.engine.create_engine`, `sessionmaker_for`.

- [ ] **Step 1: Add the dependencies**

In `backend/pyproject.toml`, add to `[project] dependencies`:

```toml
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
```

and to `[project.optional-dependencies] dev`:

```toml
    "httpx>=0.28",
```

Then reinstall: `pip install -e '.[dev]'` from `backend/`.

- [ ] **Step 2: Write `api/settings.py`**

```python
"""Configuration the API needs on top of what the migrate step needs.

A subclass, not extra fields on `Settings`: `db/migrations/env.py` builds
`Settings()` directly, and `podvinsya migrate` must not start demanding a
signing key it never uses. Only `build_app` constructs this type.
"""

from podvinsya.config import Settings


class ApiSettings(Settings):
    # No defaults, for the same reason `database_url` has none: an unset
    # signing key that quietly became a constant would make every session
    # cookie in every deployment forgeable by anyone who read the source.
    secret_key: str
    host_password: str

    session_ttl_hours: int = 12
    # Per subscriber. Small on purpose: §7.2 makes every frame complete, so
    # a slow reader losing intermediate frames costs narration, not state,
    # and a deep queue would only delay the moment it catches up.
    frame_queue_capacity: int = 32
```

- [ ] **Step 3: Write `api/app.py`**

```python
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
```

- [ ] **Step 4: Write `tests/support/asgi.py`**

The whole runtime suite drives a `FakeClock` from the test's own event loop. Starlette's `TestClient` runs the application on a second loop in a background thread, which would make every timing-sensitive WebSocket test in this plan either flaky or impossible. Speaking ASGI directly keeps one loop.

```python
"""A WebSocket client that talks ASGI directly, in the caller's own loop.

Starlette's `TestClient` runs the app on its own loop in a background
thread. That is fine for a request/response test and wrong for every test
in this plan that also drives a `FakeClock`: two loops means the clock the
test advances is not the clock the application reads. This client is the
alternative — it calls `app(scope, receive, send)` as a task on the test's
loop and hands the two message queues back.
"""

import asyncio
import json
from types import TracebackType
from typing import Any

from starlette.types import ASGIApp


class WebSocketRejected(Exception):
    """The application closed or refused the handshake."""

    def __init__(self, code: int) -> None:
        super().__init__(f"the application refused the connection with code {code}")
        self.code = code


class ASGIWebSocketClient:
    def __init__(self, app: ASGIApp, path: str, *, cookies: dict[str, str] | None = None) -> None:
        self._app = app
        self._path = path
        self._cookies = cookies or {}
        self._to_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._from_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "ASGIWebSocketClient":
        headers: list[tuple[bytes, bytes]] = [(b"host", b"testserver")]
        if self._cookies:
            jar = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
            headers.append((b"cookie", jar.encode()))
        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "scheme": "ws",
            "path": self._path,
            "raw_path": self._path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "client": ("127.0.0.1", 51000),
            "server": ("testserver", 80),
            "subprotocols": [],
            "state": {},
        }
        self._task = asyncio.create_task(self._app(scope, self._receive, self._send))
        await self._to_app.put({"type": "websocket.connect"})
        first = await self._next_from_app()
        if first["type"] != "websocket.accept":
            raise WebSocketRejected(int(first.get("code", 1000)))
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()

    async def send_json(self, payload: dict[str, Any]) -> None:
        await self._to_app.put({"type": "websocket.receive", "text": json.dumps(payload)})

    async def receive_json(self, *, timeout: float = 5.0) -> dict[str, Any]:
        message = await asyncio.wait_for(self._next_from_app(), timeout=timeout)
        if message["type"] == "websocket.close":
            raise WebSocketRejected(int(message.get("code", 1000)))
        loaded = json.loads(message["text"])
        assert isinstance(loaded, dict)
        return loaded

    async def expect_close(self, *, timeout: float = 5.0) -> int:
        """Wait for the application to close, and return the close code.

        Used by the tests that prove the stage transport refuses commands:
        the assertion is that the socket goes away, so a test that merely
        saw no reply would pass against an endpoint that silently ignored
        the frame and stayed open.
        """
        while True:
            message = await asyncio.wait_for(self._next_from_app(), timeout=timeout)
            if message["type"] == "websocket.close":
                return int(message.get("code", 1000))

    async def _next_from_app(self) -> dict[str, Any]:
        return await self._from_app.get()

    async def _receive(self) -> dict[str, Any]:
        return await self._to_app.get()

    async def _send(self, message: dict[str, Any]) -> None:
        await self._from_app.put(message)
```

- [ ] **Step 5: Write `tests/api/conftest.py`**

```python
"""Fixtures for the API suite.

The database-backed modules reuse `tests/db`'s engine and schema fixtures,
the same way `tests/runtime`'s conftest does, and carry both
`pytest.mark.integration` and `pytest.mark.asyncio(loop_scope="session")`.
Modules that touch no database — the projection, the hub, the security
primitives — carry neither and run in the fast lane.
"""

import pytest

from db.conftest import clean_db, engine, migrated_schema, sessions
from podvinsya.api.settings import ApiSettings
from support.db import DATABASE_URL

__all__ = ["clean_db", "engine", "migrated_schema", "sessions", "api_settings"]

TEST_SECRET = "test-secret-key-not-used-anywhere-real"
TEST_PASSWORD = "correct horse battery staple"


@pytest.fixture
def api_settings() -> ApiSettings:
    """A settings object built explicitly rather than from the environment.

    `ApiSettings()` would read `PODVINSYA_*` and make every test depend on
    the shell it ran in. The password hash is computed in the fixture, not
    hardcoded, because `hash_password` salts randomly.
    """
    from podvinsya.api.security import hash_password

    return ApiSettings(
        database_url=DATABASE_URL,
        secret_key=TEST_SECRET,
        host_password=hash_password(TEST_PASSWORD),
    )
```

Note for the implementer: `hash_password` arrives in Task 2. Until then, write this fixture with a literal placeholder string for `host_password` and replace it in Task 2 — do not import a function that does not exist yet.

- [ ] **Step 6: Write `tests/api/test_app.py`**

Two tests, both integration-marked:

```python
async def test_health_reports_ok_against_a_reachable_database(...) -> None:
    """A GET /health against the live test database returns 200 and
    {"status": "ok", "checks": {"database": true}}."""


async def test_health_reports_degraded_when_the_database_is_unreachable(...) -> None:
    """Built against a URL pointing at a closed port, /health returns 503
    and reports database: false rather than raising.

    Kills on: removing the try/except in `_database_reachable`, which turns
    a degraded node into a 500 with a stack trace instead of a health
    signal a load balancer can read.
    """
```

Both drive the app through `httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")` inside `async with LifespanManager`-equivalent — FastAPI's lifespan runs when the ASGI `lifespan` scope is exercised, which `httpx.ASGITransport` does not do. Run the lifespan explicitly with a small helper in `tests/api/conftest.py`:

```python
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
```

- [ ] **Step 7: Run the suite**

`pytest -q` from `backend/`. Expected: the existing 399 pass, plus the two new ones. Then `mypy` and `ruff check`.

- [ ] **Step 8: Commit**

```bash
git add backend/pyproject.toml backend/src/podvinsya/api backend/tests/api backend/tests/support/asgi.py
git commit -m "Serve an app skeleton, and a WebSocket client that shares the test's loop"
```

---

### Task 2: The password, the session cookie and the stage token

Three primitives, no HTTP. `hashlib.scrypt` for the operator's password; HMAC-SHA256 for the two signed tokens. This task also gives the operator a way to produce a password hash, because a deployment step that says «put a scrypt hash here» without a command that emits one is a step nobody can follow.

**Files:**
- Create: `backend/src/podvinsya/api/security.py`
- Modify: `backend/src/podvinsya/cli.py`
- Modify: `backend/tests/api/conftest.py` (replace the placeholder from Task 1)
- Test: `backend/tests/api/test_security.py`
- Test: `backend/tests/test_cli.py` (create if absent)

**Interfaces:**
- Produces: `hash_password(password) -> str`, `verify_password(password, encoded) -> bool`, `mint_session(secret, *, issued_at) -> str`, `read_session(secret, token, *, now, ttl) -> bool`, `mint_stage_token(secret, match_id) -> str`, `read_stage_token(secret, token) -> MatchId | None`.
- Consumes: `podvinsya.domain.ids.MatchId`.

- [ ] **Step 1: Write the failing tests**

```python
"""The three primitives §7.5 needs, and the properties that make them safe.

No HTTP here: these are pure functions over strings and datetimes, so the
tests are pure too, and every fixed instant below is written out rather
than read from a clock.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from podvinsya.api.security import (
    hash_password,
    mint_session,
    mint_stage_token,
    read_session,
    read_stage_token,
    verify_password,
)
from podvinsya.domain.ids import MatchId

SECRET = "a-secret-that-is-only-a-test-secret"
NOON = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
TTL = timedelta(hours=12)


def test_a_password_verifies_against_its_own_hash() -> None:
    assert verify_password("hunter2", hash_password("hunter2"))


def test_a_wrong_password_does_not_verify() -> None:
    assert not verify_password("hunter3", hash_password("hunter2"))


def test_two_hashes_of_one_password_differ() -> None:
    """Salting, stated as a property. Without a per-hash random salt, two
    deployments choosing the same password would carry the same string, and
    a hash leaked from one would be recognisable in the other."""
    assert hash_password("hunter2") != hash_password("hunter2")


@pytest.mark.parametrize(
    "encoded",
    [
        "",
        "not-even-close",
        "scrypt$16384$8$1$onlyfourfields",
        "bcrypt$16384$8$1$c2FsdA$a2V5",
        "scrypt$notanumber$8$1$c2FsdA$a2V5",
    ],
)
def test_a_malformed_hash_verifies_nothing(encoded: str) -> None:
    """Kills on: letting the ValueError out of `verify_password`. A
    misconfigured PODVINSYA_HOST_PASSWORD must refuse every login, not turn
    the login endpoint into a 500 that reveals the parse failure."""
    assert not verify_password("hunter2", encoded)


def test_a_fresh_session_reads_back() -> None:
    assert read_session(SECRET, mint_session(SECRET, issued_at=NOON), now=NOON, ttl=TTL)


def test_a_session_past_its_ttl_is_refused() -> None:
    token = mint_session(SECRET, issued_at=NOON)
    assert not read_session(SECRET, token, now=NOON + TTL + timedelta(seconds=1), ttl=TTL)


def test_a_session_from_the_future_is_refused() -> None:
    """One minute of forward skew is allowed and no more. A cookie stamped
    hours ahead is either a forgery or a clock so wrong that nothing else
    in this system works either."""
    token = mint_session(SECRET, issued_at=NOON + timedelta(hours=2))
    assert not read_session(SECRET, token, now=NOON, ttl=TTL)


def test_a_session_signed_with_another_key_is_refused() -> None:
    token = mint_session("some-other-key", issued_at=NOON)
    assert not read_session(SECRET, token, now=NOON, ttl=TTL)


def test_a_tampered_session_payload_is_refused() -> None:
    """Kills on: comparing only the payload, or trusting it before the MAC.
    The timestamp is moved forward while the signature stays; a verifier
    that parsed first and checked second would accept it."""
    token = mint_session(SECRET, issued_at=NOON - timedelta(days=30))
    payload, _, signature = token.rpartition(".")
    subject, _, _issued = payload.partition(".")
    forged = f"{subject}.{int(NOON.timestamp())}.{signature}"
    assert not read_session(SECRET, forged, now=NOON, ttl=TTL)


def test_a_stage_token_names_its_own_match() -> None:
    match_id = MatchId(uuid4())
    assert read_stage_token(SECRET, mint_stage_token(SECRET, match_id)) == match_id


def test_a_stage_token_for_one_match_does_not_open_another() -> None:
    """The token is bound to the match, so last week's link cannot watch
    tonight's game. Kills on: signing a constant instead of the match id."""
    first, second = MatchId(uuid4()), MatchId(uuid4())
    assert read_stage_token(SECRET, mint_stage_token(SECRET, first)) != second


def test_a_stage_token_with_a_swapped_match_id_is_refused() -> None:
    token = mint_stage_token(SECRET, MatchId(uuid4()))
    _, _, signature = token.rpartition(".")
    assert read_stage_token(SECRET, f"stage.{uuid4()}.{signature}") is None


def test_a_session_cookie_is_not_a_stage_token() -> None:
    """Kills on: signing both with the same payload shape. Without the
    subject prefix, a host cookie would be a valid stage token and the
    reverse, and §7.5's two-projection decision would be forgeable."""
    assert read_stage_token(SECRET, mint_session(SECRET, issued_at=NOON)) is None
    assert not read_session(SECRET, mint_stage_token(SECRET, MatchId(uuid4())), now=NOON, ttl=TTL)
```

- [ ] **Step 2: Run them and watch them fail**

`pytest tests/api/test_security.py -q`. Expected: collection error, `podvinsya.api.security` does not exist.

- [ ] **Step 3: Write `api/security.py`**

```python
"""Password hashing and signed tokens, on the standard library alone.

§7.5 asks for one operator, one password, a session cookie, and a per-match
link for the screen. That is a small enough surface that `hashlib.scrypt`
and `hmac` cover it exactly, and adding a KDF library would put an upgrade
cadence on a machine that lives in a meeting room (§1.1).

Every comparison here goes through `hmac.compare_digest`. Timing is a weak
channel on an isolated network, but the constant-time call is one character
longer than the variable-time one, so there is no trade to make.
"""

import base64
import binascii
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from podvinsya.domain.ids import MatchId

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_KEY_BYTES = 32

_HOST_SUBJECT = "host"
_STAGE_SUBJECT = "stage"

# A cookie stamped slightly ahead of the server is a clock skew; one stamped
# far ahead is a forgery or a broken deployment. Neither should be honoured
# beyond the width of ordinary NTP drift.
_FORWARD_SKEW = timedelta(minutes=1)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    """Encode as `scrypt$n$r$p$salt$key`, all base64url, no padding.

    The parameters travel with the hash so raising them later does not
    invalidate hashes produced before the change.
    """
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_KEY_BYTES
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(key)}"


def verify_password(password: str, encoded: str) -> bool:
    """False for a wrong password and false for a malformed hash.

    A malformed hash is a misconfiguration, and the right behaviour is to
    refuse every login — not to raise out of the login endpoint, where the
    parse failure would reach the client as a 500 that says more about the
    deployment than an attacker should learn.
    """
    try:
        scheme, n, r, p, salt, key = encoded.split("$")
        if scheme != "scrypt":
            return False
        expected = _unb64(key)
        actual = hashlib.scrypt(
            password.encode(),
            salt=_unb64(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError, binascii.Error):
        return False
    return hmac.compare_digest(actual, expected)


def _sign(secret: str, payload: str) -> str:
    mac = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return f"{payload}.{_b64(mac)}"


def _unsign(secret: str, token: str) -> str | None:
    payload, dot, signature = token.rpartition(".")
    if not dot:
        return None
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    try:
        given = _unb64(signature)
    except (ValueError, binascii.Error):
        return None
    return payload if hmac.compare_digest(expected, given) else None


def mint_session(secret: str, *, issued_at: datetime) -> str:
    return _sign(secret, f"{_HOST_SUBJECT}.{int(issued_at.timestamp())}")


def read_session(secret: str, token: str, *, now: datetime, ttl: timedelta) -> bool:
    """True only for an unexpired cookie this server signed for the host.

    The subject prefix is what keeps a stage token from reading as a session
    and the reverse: both are signed with the same key, so without it the
    two would be interchangeable and §7.5's whole distinction — which of the
    two projections to build — would be something a client could choose.
    """
    payload = _unsign(secret, token)
    if payload is None:
        return False
    subject, dot, issued = payload.partition(".")
    if not dot or subject != _HOST_SUBJECT:
        return False
    try:
        issued_at = datetime.fromtimestamp(int(issued), tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return False
    return now - ttl <= issued_at <= now + _FORWARD_SKEW


def mint_stage_token(secret: str, match_id: MatchId) -> str:
    """A derived token, not a stored one (see the plan's ruling 7): no
    column, no lookup, and bound to exactly one match."""
    return _sign(secret, f"{_STAGE_SUBJECT}.{match_id}")


def read_stage_token(secret: str, token: str) -> MatchId | None:
    payload = _unsign(secret, token)
    if payload is None:
        return None
    subject, dot, raw_id = payload.partition(".")
    if not dot or subject != _STAGE_SUBJECT:
        return None
    try:
        return MatchId(UUID(raw_id))
    except ValueError:
        return None
```

- [ ] **Step 4: Run the tests**

`pytest tests/api/test_security.py -q`. Expected: all pass.

- [ ] **Step 5: Add `podvinsya hash-password`**

In `cli.py`, add a subcommand. It reads the password from stdin rather than argv so it does not land in shell history:

```python
    subcommands.add_parser(
        "hash-password",
        help="read a password on stdin and print the value for PODVINSYA_HOST_PASSWORD",
    )
```

and in `main`:

```python
    if args.command == "hash-password":
        from podvinsya.api.security import hash_password

        # stdin, not argv: a password on a command line lands in shell
        # history and in `ps` output for every user on the machine.
        print(hash_password(sys.stdin.readline().rstrip("\n")))
        return 0
```

- [ ] **Step 6: Test the subcommand**

```python
def test_hash_password_prints_a_hash_that_verifies(monkeypatch, capsys) -> None:
    """Kills on: printing the password, or hashing a line that still has
    its trailing newline — either would make the printed value fail to
    verify against what the operator typed."""
    monkeypatch.setattr("sys.stdin", io.StringIO("hunter2\n"))
    assert main(["hash-password"]) == 0
    assert verify_password("hunter2", capsys.readouterr().out.strip())
```

- [ ] **Step 7: Replace the Task 1 placeholder in `tests/api/conftest.py`** with the real `hash_password(TEST_PASSWORD)` call, and run the whole suite, `mypy`, `ruff check`.

- [ ] **Step 8: Commit**

```bash
git add backend/src/podvinsya/api/security.py backend/src/podvinsya/cli.py backend/tests
git commit -m "Hash one operator's password, and sign the two things a client may hold"
```

---
