"""Preserve existing data in the reserved operator workspace."""

import time

import sqlalchemy as sa

from alembic import op

revision = "0004_workspace_auth"
down_revision = "0003_alerting_foundation"
branch_labels = None
depends_on = None
LEGACY = "00000000-0000-0000-0000-000000000001"
DEMO = "00000000-0000-0000-0000-000000000002"
OWNED = {
    "incidents": ("execution_id",),
    "execution_history": ("source", "execution_id"),
    "monitoring_checkpoints": ("source", "source_identifier"),
    "alert_rules": ("name",),
    "alert_events": ("event_key",),
    "alert_condition_states": ("condition_type", "scope_key"),
}
NAMES = {
    "incidents": "uq_incidents_workspace",
    "execution_history": "uq_history_workspace",
    "monitoring_checkpoints": "uq_checkpoint_workspace",
    "alert_rules": "uq_rule_workspace",
    "alert_events": "uq_event_workspace",
    "alert_condition_states": "uq_condition_workspace",
}


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        raise RuntimeError("This migration is validated for SQLite only")
    if bind.exec_driver_sql("PRAGMA foreign_keys").scalar():
        raise RuntimeError("Run through the supplied Alembic env with FK checks suspended")
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
    )
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
    )
    for wid, name in [(LEGACY, "Legacy operator"), (DEMO, "Synthetic demo")]:
        bind.execute(
            sa.text("INSERT INTO workspaces (id, name, created_at) VALUES (:id, :name, :now)"),
            {"id": wid, "name": name, "now": int(time.time())},
        )
    op.create_table(
        "workspace_memberships",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), primary_key=True),
        sa.Column("role", sa.String(10), nullable=False),
        sa.CheckConstraint("role IN ('admin', 'viewer')", name="ck_membership_role"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("revoked_at", sa.Integer()),
    )
    for column in ("user_id", "workspace_id", "expires_at"):
        op.create_index(f"ix_sessions_{column}", "sessions", [column])
    op.create_table(
        "workspace_invites",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("consumed_at", sa.Integer()),
        sa.Column("revoked_at", sa.Integer()),
        sa.CheckConstraint("role IN ('admin', 'viewer')", name="ck_invite_role"),
    )
    for column in ("workspace_id", "expires_at"):
        op.create_index(f"ix_workspace_invites_{column}", "workspace_invites", [column])
    op.create_table(
        "auth_throttles",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("window_start", sa.Integer(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
    )
    op.create_index("ix_auth_throttles_window_start", "auth_throttles", ["window_start"])
    op.create_table(
        "operator_state",
        sa.Column("id", sa.String(20), primary_key=True),
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.UniqueConstraint("owner_user_id", name="uq_operator_owner"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id")),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
    )
    for column in ("actor_id", "workspace_id", "created_at"):
        op.create_index(f"ix_audit_events_{column}", "audit_events", [column])
    # Reflect, then remove every form of old global uniqueness (named/unnamed/index).
    metadata = sa.MetaData()
    for table_name, unique_columns in OWNED.items():
        old = sa.Table(table_name, metadata, autoload_with=bind)
        for constraint in list(old.constraints):
            if isinstance(constraint, sa.UniqueConstraint):
                if tuple(c.name for c in constraint.columns) == unique_columns:
                    old.constraints.remove(constraint)
        for index in list(old.indexes):
            if index.unique and tuple(c.name for c in index.columns) == unique_columns:
                old.indexes.remove(index)
        with op.batch_alter_table(table_name, copy_from=old, recreate="always") as batch:
            batch.add_column(
                sa.Column("workspace_id", sa.String(36), nullable=False, server_default=LEGACY)
            )
            batch.create_foreign_key(
                f"fk_{table_name}_workspace", "workspaces", ["workspace_id"], ["id"]
            )
            batch.create_index(f"ix_{table_name}_workspace_id", ["workspace_id"])
            batch.create_unique_constraint(NAMES[table_name], ["workspace_id", *unique_columns])
        # New rows must always specify ownership explicitly; no implicit legacy default.
        with op.batch_alter_table(table_name) as batch:
            batch.alter_column("workspace_id", server_default=None)
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            sa.text(
                f"CREATE TRIGGER audit_events_no_{operation.lower()} BEFORE {operation} "
                "ON audit_events BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END"
            )
        )
    violations = bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError("Migration introduced foreign-key violations")


def downgrade():
    raise RuntimeError(
        "Restore the pre-upgrade backup; auth downgrade is intentionally unsupported"
    )
