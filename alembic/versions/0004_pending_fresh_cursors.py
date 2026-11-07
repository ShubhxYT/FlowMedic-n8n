"""Persist pending fresh-page cursors for monitoring overflow recovery."""

import sqlalchemy as sa

from alembic import op

revision = "0004_pending_fresh_cursors"
down_revision = "0003_alerting_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "monitoring_checkpoints",
        sa.Column("pending_cursors", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("monitoring_checkpoints", "pending_cursors")
