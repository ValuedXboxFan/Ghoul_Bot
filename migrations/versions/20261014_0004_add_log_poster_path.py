"""Store the selected provider poster on each log.

Revision ID: 20261014_0004
Revises: 20261014_0003
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261014_0004"
down_revision: str | None = "20261014_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add per-log poster selection and preserve existing TMDB artwork."""
    op.add_column("media_logs", sa.Column("poster_path", sa.String(length=255), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE media_logs AS logs
            SET poster_path = items.poster_path
            FROM media_items AS items
            WHERE logs.media_item_id = items.id
              AND items.provider = 'tmdb'
            """
        )
    )


def downgrade() -> None:
    """Remove per-log poster selection."""
    op.drop_column("media_logs", "poster_path")
