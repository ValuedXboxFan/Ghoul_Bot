"""Assign every media log to an immutable Ghoulstober season.

Revision ID: 20261020_0005
Revises: 20261014_0004
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261020_0005"
down_revision: str | None = "20261014_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add and backfill the season year for existing logs."""
    op.add_column("media_logs", sa.Column("season_year", sa.Integer(), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE media_logs
            SET season_year = EXTRACT(
                YEAR FROM COALESCE(
                    activity_date::timestamp,
                    created_at AT TIME ZONE 'America/Chicago'
                )
            )::integer
            """
        )
    )
    op.alter_column("media_logs", "season_year", nullable=False)
    op.create_index("ix_media_logs_season_year", "media_logs", ["season_year"])


def downgrade() -> None:
    """Remove explicit season ownership from logs."""
    op.drop_index("ix_media_logs_season_year", table_name="media_logs")
    op.drop_column("media_logs", "season_year")
