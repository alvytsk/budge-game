"""Resolution is exactly-once, idempotent, and never raises. Every one of
those three has cost somebody a production incident, so each gets a test."""

import pytest

from podvinsya.domain.actions import PauseDuel
from podvinsya.domain.errors import RejectionReason
from podvinsya.runtime.origins import (
    Accepted,
    Failed,
    FutureOrigin,
    NoOp,
    QueuedCommand,
    Rejected,
    SystemOrigin,
)
from podvinsya.services.ports import RuntimeCode


async def test_a_future_origin_hands_its_caller_the_events() -> None:
    origin = FutureOrigin()
    origin.resolve_ok(())
    assert await origin.result() == Accepted(())


async def test_each_resolution_maps_to_its_own_outcome() -> None:
    ok, noop, rejected, failed = (FutureOrigin() for _ in range(4))
    ok.resolve_ok(())
    noop.resolve_noop()
    rejected.resolve_rejected(RejectionReason.DUEL_PAUSED)
    failed.resolve_failed(RuntimeCode.QUARANTINED, "the match is quarantined")

    assert await ok.result() == Accepted(())
    assert await noop.result() == NoOp()
    assert await rejected.result() == Rejected(RejectionReason.DUEL_PAUSED)
    assert (await failed.result()) == Failed(RuntimeCode.QUARANTINED, "the match is quarantined")


async def test_a_second_resolution_is_ignored_rather_than_raising() -> None:
    """The loop resolves on exactly one path, but a bug that resolved twice
    must not take the match down after its commit already landed."""
    origin = FutureOrigin()
    origin.resolve_ok(())
    origin.resolve_failed(RuntimeCode.INTERNAL, "should never be seen")
    assert await origin.result() == Accepted(())


async def test_resolving_an_abandoned_caller_does_not_raise() -> None:
    """A REST client can disconnect while its command is queued. Setting a
    result on its cancelled future raises InvalidStateError — *after* the
    commit. If that escaped, a delivery failure on a dead request would
    quarantine a match whose state is durable and correct."""
    origin = FutureOrigin()
    origin.abandon()
    origin.resolve_ok(())  # must not raise


async def test_a_system_origin_absorbs_every_outcome() -> None:
    """Nobody is waiting on a deadline expiry or a recovery pause, but the
    loop still resolves unconditionally — a nullable origin would put a
    branch on every resolution path, and the forgotten one would hang."""
    origin = SystemOrigin("deadline")
    origin.resolve_ok(())
    origin.resolve_noop()
    origin.resolve_rejected(RejectionReason.NO_DUEL)
    origin.resolve_failed(RuntimeCode.INTERNAL, "boom")


async def test_a_system_origin_logs_a_rejection_it_should_never_get(
    caplog: pytest.LogCaptureFixture
) -> None:
    """A server-issued command being rejected means the server's own model
    of the match was wrong. Silence there is how that stays undiscovered."""
    with caplog.at_level("WARNING"):
        SystemOrigin("watchdog").resolve_rejected(RejectionReason.NO_DUEL)
    assert "watchdog" in caplog.text
    assert RejectionReason.NO_DUEL.value in caplog.text


def test_the_server_mints_the_operation_id() -> None:
    """§5.1: always, for every command without exception. A client-supplied
    value is untrusted input — repeating someone else's would make the
    reconciliation of an ambiguous commit conclude the wrong batch landed."""
    first = QueuedCommand.issue(PauseDuel(), SystemOrigin("test"))
    second = QueuedCommand.issue(PauseDuel(), SystemOrigin("test"))
    assert first.operation_id != second.operation_id
    assert first.operation_id


def test_the_envelope_carries_the_command_and_its_origin_unchanged() -> None:
    command = PauseDuel()
    origin = SystemOrigin("test")
    queued = QueuedCommand.issue(command, origin)
    assert queued.command is command
    assert queued.origin is origin
