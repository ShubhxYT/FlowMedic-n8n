"""Encrypted connections, per-instance identity, and legacy environment backfill."""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision = "0005_gui_connections"
down_revision = "0004_workspace_auth"
branch_labels = None
depends_on = None

LEGACY_WORKSPACE = "00000000-0000-0000-0000-000000000001"
IMPORT_TAG = "legacy-env-v1"
OWNED = (
    "incidents",
    "execution_history",
    "monitoring_checkpoints",
    "alert_events",
    "alert_condition_states",
)
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}


def legacy_id(workspace_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"flowmedic:{workspace_id}:legacy:n8n"))


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        raise RuntimeError("This migration is validated for SQLite only")
    if bind.exec_driver_sql("PRAGMA foreign_keys").scalar():
        raise RuntimeError("Run through the supplied Alembic env with FK checks suspended")

    op.create_table(
        "n8n_instances",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("base_url", sa.String(2048), nullable=False),
        sa.Column("api_key_ciphertext", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("import_tag", sa.String(50)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_n8n_workspace_id"),
        sa.UniqueConstraint("workspace_id", "import_tag", name="uq_n8n_workspace_import"),
        sa.CheckConstraint("revision >= 1", name="ck_n8n_revision"),
    )
    op.create_index(
        "ix_n8n_workspace_state", "n8n_instances", ["workspace_id", "deleted", "enabled"]
    )
    op.create_table(
        "ai_providers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("base_url", sa.String(2048)),
        sa.Column("api_key_ciphertext", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("import_tag", sa.String(50)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("model", sa.String(200)),
        sa.UniqueConstraint("workspace_id", "id", name="uq_ai_workspace_id"),
        sa.UniqueConstraint("workspace_id", "import_tag", name="uq_ai_workspace_import"),
        sa.CheckConstraint("revision >= 1", name="ck_ai_revision"),
        sa.CheckConstraint("kind IN ('openai-compatible','mock')", name="ck_ai_kind"),
    )
    op.create_index("ix_ai_workspace_state", "ai_providers", ["workspace_id", "deleted", "enabled"])
    op.create_table(
        "workspace_connection_state",
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), primary_key=True),
        sa.Column("default_ai_provider_id", sa.String(36)),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "default_ai_provider_id"],
            ["ai_providers.workspace_id", "ai_providers.id"],
            name="fk_workspace_default_ai",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_connection_state_revision"),
    )
    op.create_table(
        "connection_key_checks",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("ciphertext", sa.Text(), nullable=False),
    )
    op.create_table(
        "connection_test_buckets",
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), primary_key=True),
        sa.Column("actor_id", sa.String(36), primary_key=True),
        sa.Column("minute_start", sa.Integer(), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
    )

    for table in OWNED:
        op.add_column(table, sa.Column("instance_id", sa.String(36), nullable=True))
    op.add_column("alert_rules", sa.Column("instance_id", sa.String(36), nullable=True))

    now = datetime.now(UTC)
    workspaces = set()
    for table in OWNED:
        for row in bind.exec_driver_sql(
            f"SELECT DISTINCT workspace_id FROM {table} WHERE workspace_id IS NOT NULL"
        ):
            workspaces.add(row[0])
    workspaces.add(LEGACY_WORKSPACE)
    for workspace_id in sorted(workspaces):
        instance_id = legacy_id(workspace_id)
        bind.execute(
            sa.text(
                "INSERT INTO n8n_instances (id, workspace_id, name, base_url, "
                "api_key_ciphertext, enabled, deleted, revision, import_tag, created_at, "
                "updated_at) VALUES (:id, :workspace_id, 'Legacy n8n', "
                "'https://legacy.invalid', NULL, 0, 0, 1, :tag, :now, :now)"
            ),
            {
                "id": instance_id,
                "workspace_id": workspace_id,
                "tag": IMPORT_TAG,
                "now": now,
            },
        )
        for table in OWNED:
            bind.execute(
                sa.text(f"UPDATE {table} SET instance_id = :instance_id WHERE workspace_id = :wid"),
                {"instance_id": instance_id, "wid": workspace_id},
            )
    bind.exec_driver_sql(
        "UPDATE monitoring_checkpoints SET lease_owner_id = NULL, lease_acquired_at = NULL, "
        "lease_heartbeat_at = NULL, lease_expires_at = NULL"
    )

    with op.batch_alter_table(
        "incidents", recreate="always", naming_convention=NAMING_CONVENTION
    ) as batch:
        batch.alter_column("instance_id", nullable=False)
        batch.drop_constraint("uq_incidents_workspace", type_="unique")
        batch.create_unique_constraint(
            "uq_incidents_instance_execution", ["workspace_id", "instance_id", "execution_id"]
        )
        batch.create_foreign_key(
            "fk_incidents_instance",
            "n8n_instances",
            ["workspace_id", "instance_id"],
            ["workspace_id", "id"],
        )
        batch.create_index(
            "ix_incidents_instance_workflow", ["workspace_id", "instance_id", "workflow_id"]
        )
    with op.batch_alter_table(
        "execution_history", recreate="always", naming_convention=NAMING_CONVENTION
    ) as batch:
        batch.alter_column("instance_id", nullable=False)
        batch.drop_constraint("uq_history_workspace", type_="unique")
        batch.create_unique_constraint(
            "uq_history_instance",
            ["workspace_id", "instance_id", "source", "execution_id"],
        )
        batch.create_foreign_key(
            "fk_history_instance",
            "n8n_instances",
            ["workspace_id", "instance_id"],
            ["workspace_id", "id"],
        )
        batch.create_index(
            "ix_history_instance_workflow",
            ["workspace_id", "instance_id", "workflow_id", "recorded_at"],
        )
    with op.batch_alter_table(
        "monitoring_checkpoints", recreate="always", naming_convention=NAMING_CONVENTION
    ) as batch:
        batch.alter_column("instance_id", nullable=False)
        batch.drop_constraint("uq_checkpoint_workspace", type_="unique")
        batch.create_unique_constraint(
            "uq_checkpoint_instance", ["workspace_id", "instance_id", "source"]
        )
        batch.create_foreign_key(
            "fk_checkpoints_instance",
            "n8n_instances",
            ["workspace_id", "instance_id"],
            ["workspace_id", "id"],
        )
    with op.batch_alter_table(
        "alert_rules", recreate="always", naming_convention=NAMING_CONVENTION
    ) as batch:
        batch.create_foreign_key(
            "fk_rules_instance",
            "n8n_instances",
            ["workspace_id", "instance_id"],
            ["workspace_id", "id"],
        )
    with op.batch_alter_table(
        "alert_events", recreate="always", naming_convention=NAMING_CONVENTION
    ) as batch:
        batch.alter_column("instance_id", nullable=False)
        batch.create_foreign_key(
            "fk_events_instance",
            "n8n_instances",
            ["workspace_id", "instance_id"],
            ["workspace_id", "id"],
        )
        batch.create_index(
            "ix_events_instance_workflow",
            ["workspace_id", "instance_id", "workflow_id", "created_at"],
        )
    with op.batch_alter_table(
        "alert_condition_states", recreate="always", naming_convention=NAMING_CONVENTION
    ) as batch:
        batch.alter_column("instance_id", nullable=False)
        batch.drop_constraint("uq_condition_workspace", type_="unique")
        batch.create_unique_constraint(
            "uq_condition_instance",
            ["workspace_id", "instance_id", "condition_type", "scope_key"],
        )
        batch.create_foreign_key(
            "fk_conditions_instance",
            "n8n_instances",
            ["workspace_id", "instance_id"],
            ["workspace_id", "id"],
        )

    if bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("Migration introduced foreign-key violations")


def downgrade():
    raise RuntimeError(
        "Restore the pre-upgrade backup; reverting would merge data from distinct servers"
    )
