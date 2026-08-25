"""`Caller` over `urllib`, so the demo needs no HTTP dependency.

`httpx` is dev-only and `requests` is not a dependency at all; `urllib` is
six lines more and keeps the runtime image exactly as it is.

Blocking calls behind `asyncio.to_thread`, for the reason `S3MediaStore`
does the same: the walk is a few dozen sequential requests run by hand
between shows, so a thread per in-flight call costs nothing this deployment
can feel, and the loop is never blocked.
"""

import asyncio
import json as jsonlib
import urllib.error
import urllib.request
from typing import Any

from budge.api.principal import SESSION_COOKIE


class UrllibCaller:
    def __init__(self, base_url: str, session_cookie: str) -> None:
        self._base = base_url.rstrip("/")
        self._cookie = f"{SESSION_COOKIE}={session_cookie}"

    def _blocking_call(
        self,
        method: str,
        path: str,
        payload: bytes | None,
        content_type: str | None,
    ) -> tuple[int, object]:
        request = urllib.request.Request(f"{self._base}{path}", data=payload, method=method)
        request.add_header("Cookie", self._cookie)
        if content_type is not None:
            request.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                return response.status, jsonlib.loads(raw) if raw else None
        except urllib.error.HTTPError as refused:
            # A 409 carrying a `reason` is an ordinary answer (§6.3), not an
            # exception — the walk decides what to do with it.
            raw = refused.read()
            body: Any = jsonlib.loads(raw) if raw else None
            return refused.code, body

    async def call(
        self,
        method: str,
        path: str,
        *,
        json: object | None = None,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> tuple[int, object]:
        payload = body
        kind = content_type
        if json is not None:
            payload = jsonlib.dumps(json).encode("utf-8")
            kind = "application/json"
        return await asyncio.to_thread(self._blocking_call, method, path, payload, kind)
