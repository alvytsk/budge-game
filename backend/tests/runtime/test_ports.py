"""The concrete classes satisfy the protocols — checked by mypy, not by
`isinstance`.

`runtime_checkable` would only verify that the method *names* exist; it says
nothing about their signatures, which is exactly where a port drifts from
its implementation. These assignments are the real assertion, and they fail
at type-check time rather than in a test run.
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.db.repository import MatchRepository
from podvinsya.db.store import UnitOfWork
from podvinsya.runtime.clock import SystemClock
from podvinsya.services.ports import (
    Broadcaster,
    CategoryBank,
    Clock,
    MatchRepositoryPort,
    UnitOfWorkPort,
)
from support.fakes import FakeCategoryBank, FakeClock, RecordingBroadcaster

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def test_the_persistence_classes_satisfy_their_ports(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """If `append`'s or `load`'s signature ever drifts from the port, this
    stops type-checking — which is the whole reason the port exists."""
    uow: UnitOfWorkPort = UnitOfWork(sessions)
    repository: MatchRepositoryPort = MatchRepository(sessions)
    assert uow is not None and repository is not None


def test_the_clock_and_the_fakes_satisfy_their_ports() -> None:
    system: Clock = SystemClock()
    fake: Clock = FakeClock(NOW)
    broadcaster: Broadcaster = RecordingBroadcaster()
    bank: CategoryBank = FakeCategoryBank()
    assert (system, fake, broadcaster, bank) is not None
