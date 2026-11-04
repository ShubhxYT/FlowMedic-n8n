"""Add durable, local-first alerting foundation."""

import sqlalchemy as sa

from alembic import op

revision = "0003_alerting_foundation"
down_revision = "0002_monitoring_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("trigger_type", sa.String(length=50), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False),
        sa.Column("delivery_provider", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_alert_rules_trigger_type", "alert_rules", ["trigger_type"], unique=False)
    op.create_table(
        "alert_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("rule_id", sa.String(length=36), nullable=False),
        sa.Column("trigger_type", sa.String(length=50), nullable=False),
        sa.Column("event_key", sa.String(length=64), nullable=False),
        sa.Column("deduplication_key", sa.String(length=255), nullable=False),
        sa.Column("incident_id", sa.String(length=36), nullable=True),
        sa.Column("workflow_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error_summary", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"]),
        sa.ForeignKeyConstraint(["rule_id"], ["alert_rules.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_key"),
    )
    for name, columns in (
        ("ix_alert_events_rule_id", ["rule_id"]),
        ("ix_alert_events_trigger_type", ["trigger_type"]),
        ("ix_alert_events_deduplication_key", ["deduplication_key"]),
        ("ix_alert_events_incident_id", ["incident_id"]),
        ("ix_alert_events_workflow_id", ["workflow_id"]),
        ("ix_alert_events_status", ["status"]),
        ("ix_alert_events_created_at", ["created_at"]),
    ):
        op.create_index(name, "alert_events", columns, unique=False)
    op.create_table(
        "alert_delivery_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("alert_event_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_summary", sa.String(length=500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["alert_event_id"], ["alert_events.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alert_event_id", "attempt_number"),
    )
    op.create_index(
        "ix_alert_delivery_attempts_alert_event_id",
        "alert_delivery_attempts",
        ["alert_event_id"],
        unique=False,
    )
    op.create_table(
        "alert_condition_states",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("condition_type", sa.String(length=50), nullable=False),
        sa.Column("scope_key", sa.String(length=255), nullable=False),
        sa.Column("current_state", sa.String(length=50), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("condition_type", "scope_key"),
    )
    op.create_index(
        "ix_alert_condition_states_condition_type",
        "alert_condition_states",
        ["condition_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("alert_condition_states")
    op.drop_table("alert_delivery_attempts")
    op.drop_table("alert_events")
    op.drop_table("alert_rules")
