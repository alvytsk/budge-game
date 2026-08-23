"""initial

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "matches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("winner_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_seq", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "status IN ('setup', 'running', 'finished')", name="ck_matches_status_valid"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "match_events",
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("match_id", "seq"),
    )
    op.create_index(
        "ix_match_events_match_id_operation_id",
        "match_events",
        ["match_id", "operation_id"],
        unique=False,
    )
    op.create_table(
        "match_players",
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("colour", sa.Text(), nullable=False),
        sa.Column("eliminated", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("match_id", "player_id"),
    )


def downgrade() -> None:
    op.drop_table("match_players")
    op.drop_index("ix_match_events_match_id_operation_id", table_name="match_events")
    op.drop_table("match_events")
    op.drop_table("matches")
