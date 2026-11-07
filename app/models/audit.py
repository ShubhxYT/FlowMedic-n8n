from sqlalchemy import DDL, ForeignKey, Integer, String, event
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import identifier, now
from app.models.incident import Base


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"), index=True)
    action: Mapped[str] = mapped_column(String(100))
    outcome: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[int] = mapped_column(Integer, default=now, index=True)


for operation in ("UPDATE", "DELETE"):
    event.listen(
        AuditEvent.__table__,
        "after_create",
        DDL(
            f"CREATE TRIGGER audit_events_no_{operation.lower()} BEFORE {operation} "
            "ON audit_events BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END"
        ).execute_if(dialect="sqlite"),
    )
