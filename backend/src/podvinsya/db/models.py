"""ORM models: the event log and the read model of §5.2.

`matches` and `match_players` are a projection, not an authority — they
carry only what the admin list needs, plus `last_seq` for the optimistic
append guard. Board and settings are deliberately absent: they live in the
`MatchCreated` event, and a copy here would invite someone to trust it.

The log table is `MatchEventRow`, not `MatchEvent`: the domain already has
an `Event` union of fifteen dataclasses, and a module importing both under
one name produces a type error far from its cause.

Nothing here declares update or delete machinery for `match_events`. The
log is append-only.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from podvinsya.db.base import Base
from podvinsya.domain.state import MatchStatus

_STATUSES = ", ".join(f"'{status.value}'" for status in MatchStatus)


class Match(Base):
    """One match. `status` mirrors the domain's `MatchStatus`, and the check
    constraint is built from that enum rather than a list retyped here, so a
    new status cannot silently diverge from what the database will accept.

    TEXT plus a check constraint rather than a PostgreSQL ENUM: adding a
    value to a PG enum has historically been restricted inside a
    transaction, and this set is small and stable enough that the constraint
    costs nothing.
    """

    __tablename__ = "matches"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    status: Mapped[str] = mapped_column(Text)
    winner_id: Mapped[UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # No default: creation always sets this to 1 explicitly, in the same
    # transaction that writes the genesis event at seq=1. A default of 0
    # would describe a row state that is never actually persisted.
    last_seq: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUSES})", name="ck_matches_status_valid"),
    )


class MatchPlayer(Base):
    """A player of one match. No seat column: turn order lives in
    `MatchStarted`, and §5.2 wants only "players" for the admin list."""

    __tablename__ = "match_players"

    match_id: Mapped[UUID] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), primary_key=True
    )
    player_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    colour: Mapped[str] = mapped_column(Text)
    eliminated: Mapped[bool] = mapped_column(Boolean, default=False)


class MatchEventRow(Base):
    """The append-only log. PK `(match_id, seq)`; never updated or deleted.

    `(match_id, operation_id)` is an index, not a unique constraint: one
    command emits up to four events sharing its `operation_id`, and §6.3's
    reconciliation counts those rows and compares their ordered types.
    """

    __tablename__ = "match_events"

    match_id: Mapped[UUID] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    operation_id: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text)
    schema_version: Mapped[int] = mapped_column(SmallInteger)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_match_events_match_id_operation_id", "match_id", "operation_id"),
    )
