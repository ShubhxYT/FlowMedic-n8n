"""Continue with Google: OpenID Connect authorization-code flow with PKCE.

Only the ``openid email profile`` scopes are requested. The ID token is received directly from
Google's token endpoint over TLS, so its claims are validated (issuer, audience, expiry, nonce,
verified email) without a separate signature check (OpenID Connect Core 3.1.3.7).
"""

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.auth import throttle
from app.api.dependencies import IdentityDB
from app.core.errors import ServiceError
from app.models.auth import User, Workspace, WorkspaceMembership
from app.repositories.auth import AuthRepository
from app.schemas.auth import normalize_email
from app.services.auth import audit, check_csrf, issue_session, passwords, set_cookie

router = APIRouter(prefix="/api/v1/auth/google", tags=["accounts"])

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
SCOPES = "openid email profile"
OAUTH_COOKIE = "fm_oauth"
OAUTH_COOKIE_SECONDS = 600


class GoogleCallback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=2048)
    state: str = Field(min_length=20, max_length=128)


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _config(request):
    config = request.app.state.settings
    if not config.google_enabled:
        raise ServiceError("google_disabled", "Google sign-in is not configured", 404)
    return config


def _clear(response, config):
    response.delete_cookie(
        OAUTH_COOKIE,
        path="/",
        secure=not config.auth_allow_insecure_cookies,
        httponly=True,
        samesite="lax",
    )


@router.post("/start")
def start(request: Request, response: Response, session: IdentityDB):
    check_csrf(request)
    throttle(request, session)
    session.commit()
    config = _config(request)
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    set_cookie(
        response,
        config,
        OAUTH_COOKIE,
        f"{state}.{nonce}.{verifier}",
        httponly=True,
        max_age=OAUTH_COOKIE_SECONDS,
    )
    query = urlencode(
        {
            "client_id": config.google_client_id,
            "redirect_uri": config.google_callback_uri,
            "response_type": "code",
            "scope": SCOPES,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
    )
    return {"url": f"{AUTHORIZE_URL}?{query}"}


def _claims(id_token):
    try:
        payload = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError) as exc:
        raise ServiceError("google_failed", "Google sign-in failed", 401) from exc


def _exchange(request, config, code, verifier):
    try:
        with httpx.Client(
            transport=request.app.state.google_transport,
            timeout=config.request_timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            result = client.post(
                TOKEN_URL,
                data={
                    "code": code,
                    "client_id": config.google_client_id,
                    "client_secret": config.google_client_secret.get_secret_value(),
                    "redirect_uri": config.google_callback_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": verifier,
                },
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        raise ServiceError("google_unavailable", "Google sign-in is unavailable", 502) from exc
    if result.status_code != 200:
        raise ServiceError("google_failed", "Google sign-in failed", 401)
    token = result.json().get("id_token") if result.content else None
    if not isinstance(token, str):
        raise ServiceError("google_failed", "Google sign-in failed", 401)
    return _claims(token)


@router.post("/callback")
def callback(payload: GoogleCallback, request: Request, response: Response, session: IdentityDB):
    try:
        return _callback(payload, request, response, session)
    except ServiceError as exc:
        # The one-time flow cookie is cleared on failure too, so it cannot be replayed.
        failure = JSONResponse(
            {"error": {"code": exc.code, "message": exc.message}},
            status_code=exc.status_code,
            headers={"Cache-Control": "no-store"},
        )
        _clear(failure, request.app.state.settings)
        return failure


def _callback(payload, request, response, session):
    check_csrf(request)
    throttle(request, session)
    config = _config(request)
    stored = request.cookies.get(OAUTH_COOKIE, "")
    _clear(response, config)
    parts = stored.split(".") if len(stored) <= 400 else []
    if len(parts) != 3 or not secrets.compare_digest(parts[0], payload.state):
        session.commit()
        raise ServiceError("google_state", "Google sign-in expired. Please try again.", 400)
    _, nonce, verifier = parts
    claims = _exchange(request, config, payload.code, verifier)
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    try:
        email = normalize_email(str(claims.get("email", "")))
    except ValueError:
        email = ""
    if (
        claims.get("iss") not in ISSUERS
        or config.google_client_id not in audiences
        or not isinstance(claims.get("exp"), int | float)
        or claims["exp"] <= time.time()
        or not secrets.compare_digest(str(claims.get("nonce", "")), nonce)
        or claims.get("email_verified") not in (True, "true")
        or not email
    ):
        audit(session, None, "account.google", "denied")
        session.commit()
        raise ServiceError("google_failed", "Google sign-in failed", 401)
    repo = AuthRepository(session)
    user = repo.user_by_email(email)
    action = "account.google_login"
    try:
        if user is None:
            if not config.auth_registration_enabled:
                raise ServiceError(
                    "registration_disabled", "Ask an operator to enable registration", 403
                )
            # Google-only accounts get an unusable random password.
            user = User(email=email, password_hash=passwords.hash(secrets.token_urlsafe(48)))
            session.add(user)
            session.flush()
            workspace = Workspace(name="Personal workspace")
            session.add(workspace)
            session.flush()
            session.add(
                WorkspaceMembership(user_id=user.id, workspace_id=workspace.id, role="admin")
            )
            action = "account.google_signup"
        if user.disabled:
            raise ServiceError("login_failed", "This account is disabled", 401)
        membership = session.scalar(
            select(WorkspaceMembership)
            .where(WorkspaceMembership.user_id == user.id)
            .order_by(WorkspaceMembership.workspace_id)
        )
        if membership is None:
            raise ServiceError("login_failed", "This account has no workspace", 401)
        context = issue_session(session, request, response, user.id, membership.workspace_id)
        audit(session, context, action)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ServiceError("signup_failed", "Unable to create this account", 409) from exc
    return {"ok": True}
