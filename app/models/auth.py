import time
import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.incident import Base

LEGACY_WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"
DEMO_WORKSPACE_ID = "00000000-0000-0000-0000-000000000002"


def identifier():
    return str(uuid.uuid4())


def now():
    return int(time.time())


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[int] = mapped_column(Integer, default=now)


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[int] = mapped_column(Integer, default=now)


class WorkspaceMembership(Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (CheckConstraint("role IN ('admin', 'viewer')", name="ck_membership_role"),)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(10))


class AuthSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    created_at: Mapped[int] = mapped_column(Integer, default=now)
    expires_at: Mapped[int] = mapped_column(Integer, index=True)
    revoked_at: Mapped[int | None] = mapped_column(Integer)


class WorkspaceInvite(Base):
    __tablename__ = "workspace_invites"
    __table_args__ = (CheckConstraint("role IN ('admin', 'viewer')", name="ck_invite_role"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(10))
    expires_at: Mapped[int] = mapped_column(Integer, index=True)
    consumed_at: Mapped[int | None] = mapped_column(Integer)
    revoked_at: Mapped[int | None] = mapped_column(Integer)


class AuthThrottle(Base):
    __tablename__ = "auth_throttles"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[int] = mapped_column(Integer, index=True)
    count: Mapped[int] = mapped_column(Integer)


class OperatorState(Base):
    __tablename__ = "operator_state"
    __table_args__ = (UniqueConstraint("owner_user_id", name="uq_operator_owner"),)
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
