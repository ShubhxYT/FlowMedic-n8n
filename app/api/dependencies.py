from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, Request

from app.core.errors import ServiceError
from app.models.auth import DEMO_WORKSPACE_ID
from app.services.auth import SESSION_COOKIE, AuthContext, authenticate, check_csrf


def identity_session(request: Request):
    with request.app.state.session_factory() as session:
        yield session


IdentityDB = Annotated[object, Depends(identity_session)]


def current_context(request: Request, session: IdentityDB):
    config = request.app.state.settings
    if config.public_demo_readonly:
        context = AuthContext(None, DEMO_WORKSPACE_ID, "viewer")
    else:
        context, row = authenticate(session, request.cookies.get(SESSION_COOKIE))
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            check_csrf(request, row)
    request.state.auth = context
    return context


Context = Annotated[AuthContext, Depends(current_context)]


def admin_context(context: Context):
    if context.role != "admin":
        raise ServiceError("forbidden", "Workspace admin permission is required", 403)
    return context


Admin = Annotated[AuthContext, Depends(admin_context)]


@dataclass(frozen=True)
class WorkspaceContext:
    """Integration surface for feature routers built on the authentication module."""

    workspace_id: str
    user_id: str
    role: Literal["admin", "viewer"]


def get_workspace_context(context: Context) -> WorkspaceContext:
    """Return the verified session/workspace selection as an immutable value."""
    role = context.role if context.role in {"admin", "viewer"} else "viewer"
    return WorkspaceContext(
        workspace_id=context.workspace_id,
        user_id=context.user_id or "",
        role=role,
    )


def require_admin(
    context: Annotated[WorkspaceContext, Depends(get_workspace_context)],
) -> WorkspaceContext:
    """Reject viewers; mutations already passed CSRF/origin checks in current_context."""
    if context.role != "admin":
        raise ServiceError("forbidden", "Workspace admin permission is required", 403)
    return context
