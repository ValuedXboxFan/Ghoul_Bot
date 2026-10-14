"""Create member media logs and manual artwork storage.

Revision ID: 20261014_0003
Revises: 20261014_0002
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261014_0003"
down_revision: str | None = "20261014_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add artwork fields and member-owned logging records."""
    op.create_check_constraint(
        "ck_media_items_type",
        "media_items",
        "media_type IN ('movie', 'tv_show', 'book', 'comic', 'podcast', "
        "'music', 'video_game', 'other')",
    )
    op.add_column("media_items", sa.Column("artwork_data", sa.LargeBinary(), nullable=True))
    op.add_column(
        "media_items", sa.Column("artwork_content_type", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "media_items", sa.Column("artwork_filename", sa.String(length=255), nullable=True)
    )
    op.create_table(
        "media_logs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("media_item_id", sa.BigInteger(), nullable=False),
        sa.Column("discord_user_id", sa.BigInteger(), nullable=False),
        sa.Column("review", sa.Text(), nullable=True),
        sa.Column("rating", sa.Numeric(precision=2, scale=1), nullable=True),
        sa.Column("activity_date", sa.Date(), nullable=True),
        sa.Column("forum_thread_id", sa.BigInteger(), nullable=True),
        sa.Column("forum_message_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "rating IS NULL OR rating IN (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0)",
            name="ck_media_logs_rating_range",
        ),
        sa.ForeignKeyConstraint(["media_item_id"], ["media_items.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("forum_thread_id"),
    )
    op.create_index(
        op.f("ix_media_logs_discord_user_id"),
        "media_logs",
        ["discord_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_media_logs_media_item_id"),
        "media_logs",
        ["media_item_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove member logging records and manual artwork fields."""
    op.drop_index(op.f("ix_media_logs_media_item_id"), table_name="media_logs")
    op.drop_index(op.f("ix_media_logs_discord_user_id"), table_name="media_logs")
    op.drop_table("media_logs")
    op.drop_column("media_items", "artwork_filename")
    op.drop_column("media_items", "artwork_content_type")
    op.drop_column("media_items", "artwork_data")
    op.drop_constraint("ck_media_items_type", "media_items", type_="check")
