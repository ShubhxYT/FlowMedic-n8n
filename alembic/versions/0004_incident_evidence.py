"""Persist sanitized execution evidence on incidents."""

import sqlalchemy as sa

from alembic import op

revision = "0004_incident_evidence"
down_revision = "0003_alerting_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("evidence", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("incidents", "evidence")
