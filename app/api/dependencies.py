from typing import Annotated

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
