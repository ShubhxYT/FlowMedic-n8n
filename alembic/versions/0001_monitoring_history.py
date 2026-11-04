"""Create durable FlowMedic storage and preserve pre-migration SQLite incidents."""

import sqlalchemy as sa

from alembic import op

revision = "0001_monitoring_history"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("incidents"):
        op.create_table(
            "incidents",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("execution_id", sa.String(length=255), nullable=False),
            sa.Column("workflow_id", sa.String(length=255), nullable=False),
            sa.Column("workflow_name", sa.String(length=1000), nullable=False),
            sa.Column("failed_node", sa.String(length=1000), nullable=True),
            sa.Column("error_type", sa.String(length=1000), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=False),
            sa.Column("execution_timestamp", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("severity", sa.String(length=20), nullable=False),
            sa.Column("diagnosis", sa.JSON(), nullable=True),
            sa.Column("diagnosis_provider", sa.String(length=50), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("execution_id"),
        )
        op.create_index("ix_incidents_execution_id", "incidents", ["execution_id"], unique=False)
    if not inspector.has_table("monitoring_checkpoints"):
        op.create_table(
            "monitoring_checkpoints",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("source", sa.String(length=50), nullable=False),
            sa.Column("source_identifier", sa.String(length=255), nullable=False),
            sa.Column("cursor_value", sa.Text(), nullable=True),
            sa.Column("last_execution_id", sa.String(length=255), nullable=True),
            sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_successful_sync_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error_summary", sa.String(length=500), nullable=True),
            sa.Column("consecutive_failure_count", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("source", "source_identifier"),
        )
        op.create_index(
            "ix_monitoring_checkpoints_source", "monitoring_checkpoints", ["source"], unique=False
        )
    if not inspector.has_table("execution_history"):
        op.create_table(
            "execution_history",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("source", sa.String(length=50), nullable=False),
            sa.Column("workflow_id", sa.String(length=255), nullable=False),
            sa.Column("workflow_name", sa.String(length=1000), nullable=False),
            sa.Column("execution_id", sa.String(length=255), nullable=False),
            sa.Column("execution_status", sa.String(length=100), nullable=True),
            sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("is_success", sa.Boolean(), nullable=True),
            sa.Column("incident_id", sa.String(length=36), nullable=True),
            sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("source", "execution_id"),
        )
        op.create_index(
            "ix_execution_history_source", "execution_history", ["source"], unique=False
        )
        op.create_index(
            "ix_execution_history_workflow_id", "execution_history", ["workflow_id"], unique=False
        )
        op.create_index(
            "ix_execution_history_recorded_at", "execution_history", ["recorded_at"], unique=False
        )
        op.create_index(
            "ix_execution_history_incident_id", "execution_history", ["incident_id"], unique=False
        )


def downgrade() -> None:
    op.drop_table("execution_history")
    op.drop_table("monitoring_checkpoints")
