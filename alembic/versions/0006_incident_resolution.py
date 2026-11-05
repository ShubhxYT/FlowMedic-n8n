"""Add incident resolution timestamp."""

import sqlalchemy as sa

from alembic import op

revision = "0006_incident_resolution"
down_revision = "0005_gui_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("incidents")}
    if "resolved_at" not in columns:
        op.add_column("incidents", sa.Column("resolved_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    with op.batch_alter_table("incidents") as batch:
        batch.drop_column("resolved_at")
