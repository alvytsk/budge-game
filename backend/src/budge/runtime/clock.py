"""The system clock: the only place production code learns the time."""

import asyncio
from datetime import UTC, datetime


class SystemClock:
    """Aware UTC, always. The codec refuses a naive datetime, and every
    instant that reaches an event comes from here."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep_until(self, when: datetime) -> None:
        delay = (when - self.now()).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
