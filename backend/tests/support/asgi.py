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

from starlette.types import ASGIApp, Message, Scope


class WebSocketRejected(Exception):
    """The application closed or refused the handshake."""

    def __init__(self, code: int) -> None:
        super().__init__(f"the application refused the connection with code {code}")
        self.code = code


class ASGIWebSocketClient:
    def __init__(
        self,
        app: ASGIApp,
        path: str,
        *,
        cookies: dict[str, str] | None = None,
        query_string: str = "",
    ) -> None:
        self._app = app
        self._path = path
        self._cookies = cookies or {}
        self._query_string = query_string
        self._to_app: asyncio.Queue[Message] = asyncio.Queue()
        self._from_app: asyncio.Queue[Message] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "ASGIWebSocketClient":
        headers: list[tuple[bytes, bytes]] = [(b"host", b"testserver")]
        if self._cookies:
            jar = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
            headers.append((b"cookie", jar.encode()))
        scope: Scope = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "scheme": "ws",
            "path": self._path,
            "raw_path": self._path.encode(),
            "query_string": self._query_string.encode(),
            "root_path": "",
            "headers": headers,
            "client": ("127.0.0.1", 51000),
            "server": ("testserver", 80),
            "subprotocols": [],
            "state": {},
        }
        self._task = asyncio.create_task(self._drive(scope))
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

    async def _drive(self, scope: Scope) -> None:
        """The application call, wrapped in a coroutine.

        `ASGIApp` is typed as returning an `Awaitable`, which
        `asyncio.create_task` will not take; awaiting it inside a coroutine
        of our own is the one-line adaptation.
        """
        await self._app(scope, self._receive, self._send)

    async def _next_from_app(self) -> Message:
        return await self._from_app.get()

    async def _receive(self) -> Message:
        return await self._to_app.get()

    async def _send(self, message: Message) -> None:
        await self._from_app.put(message)
