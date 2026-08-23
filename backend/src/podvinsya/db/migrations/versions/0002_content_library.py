"""content library

Revision ID: 0002
Revises: 0001

§5.3's two ordinary tables. Not event-sourced: the library outlives every
match, and the link to the log is one-way — `AttackDeclared` records the
image identifiers it drew, so a later edit here cannot reach back into a
duel that has already been played.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("is_secret", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        # §5.3's locking invariant, not bookkeeping: selection takes
        # FOR SHARE on this row, so an edit that changes an image must move
        # this column or it slips past the lock.
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "images",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("media_sha256", sa.Text(), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "media_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_images_media_sha256_is_a_digest",
        ),
        sa.CheckConstraint("position >= 0", name="ck_images_position_non_negative"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_images_category_id"), "images", ["category_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_images_category_id"), table_name="images")
    op.drop_table("images")
    op.drop_table("categories")
