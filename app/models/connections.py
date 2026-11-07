import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.incident import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class ConnectionColumns:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120))
    api_key_ciphertext: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(default=False, server_default="0")
    deleted: Mapped[bool] = mapped_column(default=False, server_default="0")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    import_tag: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class N8nInstance(ConnectionColumns, Base):
    __tablename__ = "n8n_instances"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_n8n_workspace_id"),
        UniqueConstraint("workspace_id", "import_tag", name="uq_n8n_workspace_import"),
        CheckConstraint("revision >= 1", name="ck_n8n_revision"),
        Index("ix_n8n_workspace_state", "workspace_id", "deleted", "enabled"),
    )
    base_url: Mapped[str] = mapped_column(String(2048))


class AIProvider(ConnectionColumns, Base):
    __tablename__ = "ai_providers"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_ai_workspace_id"),
        UniqueConstraint("workspace_id", "import_tag", name="uq_ai_workspace_import"),
        CheckConstraint("revision >= 1", name="ck_ai_revision"),
        CheckConstraint("kind IN ('openai-compatible','mock')", name="ck_ai_kind"),
        Index("ix_ai_workspace_state", "workspace_id", "deleted", "enabled"),
    )
    base_url: Mapped[str | None] = mapped_column(String(2048))
    kind: Mapped[str] = mapped_column(String(30))
    model: Mapped[str | None] = mapped_column(String(200))


class WorkspaceConnectionState(Base):
    __tablename__ = "workspace_connection_state"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "default_ai_provider_id"],
            ["ai_providers.workspace_id", "ai_providers.id"],
            name="fk_workspace_default_ai",
        ),
        CheckConstraint("revision >= 1", name="ck_connection_state_revision"),
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    default_ai_provider_id: Mapped[str | None] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class ConnectionKeyCheck(Base):
    __tablename__ = "connection_key_checks"
    id: Mapped[str] = mapped_column(String(30), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text)


class ConnectionTestBucket(Base):
    __tablename__ = "connection_test_buckets"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    minute_start: Mapped[int] = mapped_column(Integer, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
