import secrets
import time

from fastapi import APIRouter, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import Admin, Context, IdentityDB
from app.core.errors import ServiceError
from app.models.auth import (
    DEMO_WORKSPACE_ID,
    AuthSession,
    User,
    Workspace,
    WorkspaceInvite,
    WorkspaceMembership,
)
from app.repositories.auth import AuthRepository, digest
from app.schemas.auth import (
    Credentials,
    InviteAccept,
    InviteCreate,
    MemberUpdate,
    WorkspaceSelection,
)
from app.services.auth import (
    CSRF_COOKIE,
    DUMMY_HASH,
    SESSION_COOKIE,
    audit,
    check_csrf,
    issue_session,
    passwords,
    revoke_current,
    set_cookie,
)

router = APIRouter(prefix="/api/v1/auth", tags=["accounts"])


def throttle(request, session, email=""):
    # The proxy forwards a trusted address only after its independent service token is checked.
    # Direct anonymous callers cannot influence this header without knowing that token.
    peer = request.client.host if request.client else "unknown"
    if getattr(request.state, "service_authenticated", False):
        peer = request.headers.get("x-flowmedic-client-ip", peer)[:100]
    keys = [(f"ip:{peer}", 60)]
    if email:
        keys.append((f"account:{email}", 10))
    AuthRepository(session).throttle(keys)


@router.get("/csrf")
def csrf(request: Request, response: Response):
    token = request.cookies.get(CSRF_COOKIE)
    if not token or not 40 <= len(token) <= 128:
        token = secrets.token_urlsafe(32)
    set_cookie(
        response, request.app.state.settings, CSRF_COOKIE, token, httponly=False, max_age=43200
    )
    return {"registration_enabled": request.app.state.settings.auth_registration_enabled}


@router.post("/signup", status_code=201)
def signup(payload: Credentials, request: Request, response: Response, session: IdentityDB):
    check_csrf(request)
    throttle(request, session, payload.email)
    if not request.app.state.settings.auth_registration_enabled:
        raise ServiceError("registration_disabled", "Ask an operator to enable registration", 403)
    try:
        user = User(
            email=payload.email, password_hash=passwords.hash(payload.password.get_secret_value())
        )
        session.add(user)
        session.flush()
        workspace = Workspace(name="Personal workspace")
        session.add(workspace)
        session.flush()
        session.add(WorkspaceMembership(user_id=user.id, workspace_id=workspace.id, role="admin"))
        context = issue_session(session, request, response, user.id, workspace.id)
        audit(session, context, "account.signup")
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ServiceError("signup_failed", "Unable to create this account", 409) from exc
    return {"ok": True}


@router.post("/login")
def login(payload: Credentials, request: Request, response: Response, session: IdentityDB):
    check_csrf(request)
    throttle(request, session, payload.email)
    user = AuthRepository(session).user_by_email(payload.email)
    valid = passwords.verify(
        payload.password.get_secret_value(), user.password_hash if user else DUMMY_HASH
    )
    membership = None
    if user and valid and not user.disabled:
        membership = session.scalar(
            select(WorkspaceMembership)
            .where(WorkspaceMembership.user_id == user.id)
            .order_by(WorkspaceMembership.workspace_id)
        )
    if membership is None:
        audit(session, None, "account.login", "denied")
        session.commit()
        raise ServiceError("login_failed", "Email or password is incorrect", 401)
    context = issue_session(session, request, response, user.id, membership.workspace_id)
    audit(session, context, "account.login")
    session.commit()
    return {"ok": True}


@router.get("/me")
def me(context: Context, session: IdentityDB):
    if context.user_id is None:
        return {
            "user": {"id": None, "email": "Synthetic demo"},
            "workspace": {"id": DEMO_WORKSPACE_ID, "name": "Synthetic demo", "role": "viewer"},
            "workspaces": [],
            "demo": True,
        }
    user = session.get(User, context.user_id)
    rows = session.execute(
        select(Workspace, WorkspaceMembership.role)
        .join(WorkspaceMembership, Workspace.id == WorkspaceMembership.workspace_id)
        .where(WorkspaceMembership.user_id == user.id)
    ).all()
    workspaces = [{"id": w.id, "name": w.name, "role": role} for w, role in rows]
    current = next(w for w in workspaces if w["id"] == context.workspace_id)
    return {
        "user": {"id": user.id, "email": user.email},
        "workspace": current,
        "workspaces": workspaces,
        "demo": False,
    }


@router.post("/logout")
def logout(request: Request, response: Response, context: Context, session: IdentityDB):
    revoke_current(session, request)
    audit(session, context, "account.logout")
    session.commit()
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(
            name,
            path="/",
            secure=not request.app.state.settings.auth_allow_insecure_cookies,
            httponly=name == SESSION_COOKIE,
            samesite="lax",
        )
    return {"ok": True}


@router.post("/workspace")
def select_workspace(
    payload: WorkspaceSelection,
    request: Request,
    response: Response,
    context: Context,
    session: IdentityDB,
):
    membership = session.get(WorkspaceMembership, (context.user_id, payload.workspace_id))
    if membership is None:
        raise ServiceError("workspace_not_found", "Workspace was not found", 404)
    selected = issue_session(session, request, response, context.user_id, payload.workspace_id)
    audit(session, selected, "workspace.select")
    session.commit()
    return {"ok": True}


@router.post("/invites", status_code=201)
def invite(payload: InviteCreate, request: Request, context: Admin, session: IdentityDB):
    throttle(request, session)
    token = secrets.token_urlsafe(32)
    row = WorkspaceInvite(
        token_hash=digest(token),
        workspace_id=context.workspace_id,
        created_by=context.user_id,
        role=payload.role,
        expires_at=int(time.time()) + request.app.state.settings.auth_invite_seconds,
    )
    session.add(row)
    audit(session, context, "invite.create")
    session.commit()
    # Fragment keeps this bearer secret out of proxy access logs and Referer headers.
    origin = request.headers["origin"]
    return {"id": row.id, "url": f"{origin}/invite#token={token}", "expires_at": row.expires_at}


@router.delete("/invites/{invite_id}")
def revoke_invite(invite_id: str, context: Admin, session: IdentityDB):
    row = session.scalar(
        select(WorkspaceInvite).where(
            WorkspaceInvite.id == invite_id, WorkspaceInvite.workspace_id == context.workspace_id
        )
    )
    if row is None:
        raise ServiceError("invite_not_found", "Invitation was not found", 404)
    row.revoked_at = int(time.time())
    audit(session, context, "invite.revoke")
    session.commit()
    return {"ok": True}


@router.post("/invites/accept")
def accept_invite(
    payload: InviteAccept,
    request: Request,
    response: Response,
    context: Context,
    session: IdentityDB,
):
    throttle(request, session)
    now = int(time.time())
    # Atomic UPDATE prevents two requests consuming the same invitation.
    row = session.execute(
        update(WorkspaceInvite)
        .where(
            WorkspaceInvite.token_hash == digest(payload.token.get_secret_value()),
            WorkspaceInvite.expires_at > now,
            WorkspaceInvite.consumed_at.is_(None),
            WorkspaceInvite.revoked_at.is_(None),
        )
        .values(consumed_at=now)
        .returning(WorkspaceInvite)
    ).scalar_one_or_none()
    if row is None:
        raise ServiceError("invite_invalid", "Invitation is invalid or expired", 404)
    membership = session.get(WorkspaceMembership, (context.user_id, row.workspace_id))
    if membership is None:
        session.add(
            WorkspaceMembership(
                user_id=context.user_id, workspace_id=row.workspace_id, role=row.role
            )
        )
    # Existing memberships retain their current privilege; accepting never changes role.
    selected = issue_session(session, request, response, context.user_id, row.workspace_id)
    audit(session, selected, "invite.accept")
    session.commit()
    return {"ok": True}


@router.get("/members")
def members(context: Admin, session: IdentityDB):
    rows = session.execute(
        select(User, WorkspaceMembership.role)
        .join(WorkspaceMembership, User.id == WorkspaceMembership.user_id)
        .where(WorkspaceMembership.workspace_id == context.workspace_id)
    ).all()
    return {"data": [{"id": user.id, "email": user.email, "role": role} for user, role in rows]}


def edit_membership(session, context, user_id, role):
    # Acquire SQLite's write reservation before checking last-admin invariants.
    session.execute(
        update(Workspace).where(Workspace.id == context.workspace_id).values(name=Workspace.name)
    )
    row = session.get(WorkspaceMembership, (user_id, context.workspace_id))
    if row is None:
        raise ServiceError("member_not_found", "Member was not found", 404)
    admins = session.scalar(
        select(func.count())
        .select_from(WorkspaceMembership)
        .where(
            WorkspaceMembership.workspace_id == context.workspace_id,
            WorkspaceMembership.role == "admin",
        )
    )
    if row.role == "admin" and role != "admin" and admins <= 1:
        raise ServiceError("last_admin", "Keep at least one workspace admin", 409)
    if role is None:
        session.delete(row)
        session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.workspace_id == context.workspace_id)
            .values(revoked_at=int(time.time()))
        )
    else:
        row.role = role
    audit(session, context, "membership.remove" if role is None else "membership.change")
    session.commit()


@router.patch("/members/{user_id}")
def update_member(user_id: str, payload: MemberUpdate, context: Admin, session: IdentityDB):
    edit_membership(session, context, user_id, payload.role)
    return {"ok": True}


@router.delete("/members/{user_id}")
def remove_member(user_id: str, context: Admin, session: IdentityDB):
    edit_membership(session, context, user_id, None)
    return {"ok": True}
