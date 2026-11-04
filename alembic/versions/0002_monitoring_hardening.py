"""Harden durable monitoring state without rewriting Phase 3 history."""

import sqlalchemy as sa

from alembic import op

revision = "0002_monitoring_hardening"
down_revision = "0001_monitoring_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("monitoring_checkpoints")}
    additions = (
        ("backfill_cursor", sa.Text()),
        ("backfill_completed_at", sa.DateTime(timezone=True)),
        ("last_fresh_poll_at", sa.DateTime(timezone=True)),
        ("last_retention_at", sa.DateTime(timezone=True)),
        ("lease_owner_id", sa.String(length=36)),
        ("lease_acquired_at", sa.DateTime(timezone=True)),
        ("lease_heartbeat_at", sa.DateTime(timezone=True)),
        ("lease_expires_at", sa.DateTime(timezone=True)),
    )
    for name, column_type in additions:
        if name not in columns:
            op.add_column("monitoring_checkpoints", sa.Column(name, column_type, nullable=True))
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("monitoring_checkpoints")}
    if "ix_monitoring_checkpoints_lease_expires_at" not in indexes:
        op.create_index(
            "ix_monitoring_checkpoints_lease_expires_at",
            "monitoring_checkpoints",
            ["lease_expires_at"],
            unique=False,
        )


def downgrade() -> None:
    # SQLite batch migration keeps downgrade available without touching existing Phase 3 data.
    with op.batch_alter_table("monitoring_checkpoints") as batch:
        batch.drop_index("ix_monitoring_checkpoints_lease_expires_at")
        batch.drop_column("lease_expires_at")
        batch.drop_column("lease_heartbeat_at")
        batch.drop_column("lease_acquired_at")
        batch.drop_column("lease_owner_id")
        batch.drop_column("last_retention_at")
        batch.drop_column("last_fresh_poll_at")
        batch.drop_column("backfill_completed_at")
        batch.drop_column("backfill_cursor")
