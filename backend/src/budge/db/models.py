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

from budge.db.base import Base
from budge.domain.state import MatchStatus

_STATUSES = ", ".join(f"'{status.value}'" for status in MatchStatus)


class Match(Base):
    """One match. `status` mirrors the domain's `MatchStatus`; the check
    constraint here is built from that enum rather than a list retyped by
    hand, so this model's copy cannot itself diverge from `MatchStatus`.

    That does not, by itself, keep the *migration's* copy of the constraint
    in sync: `alembic check` compares check constraints by name only, not by
    body, so a `MatchStatus` member added here without updating the
    hardcoded `status IN (...)` text in the migration passes `alembic
    check` silently. What actually catches that drift is the behavioural
    test `test_the_status_check_admits_exactly_the_domain_statuses` in
    `tests/db/test_schema.py`, which inserts every domain status and one
    that is not.

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


class Category(Base):
    """§5.3's `categories`, an ordinary table and deliberately not
    event-sourced: the library outlives every match, and a log of its edits
    would be a second history nobody replays.

    `version` is not bookkeeping. §5.3 makes it a locking invariant:
    selection takes `FOR SHARE` on this row, and an edit path that changed
    an image without touching this row would slip past that lock. The bump
    lives in exactly one place — `LibraryCatalogue._bump` — and
    `test_no_write_outside_the_catalogue` is what keeps it there.

    There is no delete. §5.3: «контент удаляется только мягко, флагом
    `is_active`» — the observed behaviour of an operator who turned a theme
    off after three editions rather than throwing it away.
    """

    __tablename__ = "categories"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Image(Base):
    """§5.3's `images`.

    `answer_text` is the thing §7.1 forbids the stage screen ever to
    receive, which is why it lives here and reaches the frame layer only
    through `ContentDirectory` — a port whose stage-side caller passes
    `images=frozenset()` and therefore never asks for one.

    `media_sha256` is content-addressable by definition (§7.6: «медиа
    контент-адресуемо по sha256»); the store behind it arrives with the
    media plan. The check constraint below is the part that is true either
    way, and it is in the schema rather than only in Pydantic because that
    plan will write here too.
    """

    __tablename__ = "images"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    category_id: Mapped[UUID] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), index=True
    )
    media_sha256: Mapped[str] = mapped_column(Text)
    answer_text: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        CheckConstraint(
            "media_sha256 ~ '^[0-9a-f]{64}$'", name="ck_images_media_sha256_is_a_digest"
        ),
        CheckConstraint("position >= 0", name="ck_images_position_non_negative"),
    )
