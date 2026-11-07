"""Append-only audit helper for feature modules built on the authentication schema."""

from app.models.audit import AuditEvent

OUTCOMES = {"success", "failure", "denied"}


def record_event(
    session,
    *,
    workspace_id,
    actor_id,
    action,
    target_id=None,
    outcome,
):
    """Append a metadata-only event in the caller's transaction.

    Accepts IDs and fixed strings only. The target ID is appended to the fixed
    action prefix so the append-only schema needs no extra column.
    """
    if outcome not in OUTCOMES:
        raise ValueError("Invalid audit outcome")
    if not isinstance(action, str) or not action:
        raise ValueError("Invalid audit action")
    label = f"{action}:{target_id}" if target_id else action
    if len(label) > 100:
        raise ValueError("Audit action label is too long")
    session.add(
        AuditEvent(
            actor_id=actor_id or None,
            workspace_id=workspace_id,
            action=label,
            outcome=outcome,
        )
    )
