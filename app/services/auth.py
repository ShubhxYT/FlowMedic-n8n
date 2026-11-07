import secrets
import time
from dataclasses import dataclass

from pwdlib import PasswordHash
from sqlalchemy import select, update

from app.core.errors import ServiceError
from app.models.audit import AuditEvent
from app.models.auth import AuthSession, User, WorkspaceMembership
from app.repositories.auth import digest

SESSION_COOKIE = "fm_session"
CSRF_COOKIE = "fm_csrf"
passwords = PasswordHash.recommended()
DUMMY_HASH = passwords.hash(secrets.token_urlsafe(32))


@dataclass(frozen=True)
class AuthContext:
    user_id: str | None
    workspace_id: str
    role: str
    session_id: str | None = None


def audit(session, context, action, outcome="success"):
    session.add(
        AuditEvent(
            actor_id=context.user_id if context else None,
            workspace_id=context.workspace_id if context else None,
            action=action,
            outcome=outcome,
        )
    )


def authenticate(session, token):
    if not token or len(token) > 128:
        raise ServiceError("unauthorized", "Please log in", 401)
    row = session.scalar(select(AuthSession).where(AuthSession.token_hash == digest(token)))
    if row is None or row.revoked_at is not None or row.expires_at <= int(time.time()):
        raise ServiceError("unauthorized", "Please log in", 401)
    user = session.get(User, row.user_id)
    membership = session.get(WorkspaceMembership, (row.user_id, row.workspace_id))
    if user is None or user.disabled or membership is None:
        raise ServiceError("unauthorized", "Please log in", 401)
    return AuthContext(user.id, row.workspace_id, membership.role, row.id), row


def revoke_user_sessions(session, user_id):
    session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=int(time.time()))
    )


def revoke_current(session, request):
    token = request.cookies.get(SESSION_COOKIE, "")
    if token and len(token) <= 128:
        session.execute(
            update(AuthSession)
            .where(AuthSession.token_hash == digest(token), AuthSession.revoked_at.is_(None))
            .values(revoked_at=int(time.time()))
        )


def set_cookie(response, config, name, value, *, httponly, max_age):
    response.set_cookie(
        name,
        value,
        httponly=httponly,
        secure=not config.auth_allow_insecure_cookies,
        samesite="lax",
        path="/",
        max_age=max_age,
    )


def issue_session(session, request, response, user_id, workspace_id):
    config = request.app.state.settings
    revoke_current(session, request)
    # Bound active sessions per user. Rotate the oldest after ten concurrent logins.
    active = session.scalars(
        select(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .order_by(AuthSession.created_at.desc())
    ).all()
    for row in active[9:]:
        row.revoked_at = int(time.time())
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    row = AuthSession(
        token_hash=digest(token),
        csrf_hash=digest(csrf),
        user_id=user_id,
        workspace_id=workspace_id,
        expires_at=int(time.time()) + config.auth_session_seconds,
    )
    session.add(row)
    session.flush()
    set_cookie(
        response, config, SESSION_COOKIE, token, httponly=True, max_age=config.auth_session_seconds
    )
    set_cookie(
        response, config, CSRF_COOKIE, csrf, httponly=False, max_age=config.auth_session_seconds
    )
    return AuthContext(user_id, workspace_id, "admin", row.id)


def check_csrf(request, session_row=None):
    config = request.app.state.settings
    trusted = {value.strip() for value in config.auth_trusted_origins.split(",")}
    origin = request.headers.get("origin", "")
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get("x-csrf-token", "")
    if (
        origin not in trusted
        or not 40 <= len(cookie) <= 128
        or not secrets.compare_digest(cookie, header)
        or (
            session_row is not None
            and not secrets.compare_digest(session_row.csrf_hash, digest(header))
        )
    ):
        raise ServiceError("csrf_rejected", "Refresh the page and try again", 403)
