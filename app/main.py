import secrets
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app import models  # noqa: F401
from app.ai.providers import MockDiagnosisProvider
from app.api.auth import router as auth_router
from app.api.routes import router
from app.api.settings import router as settings_router
from app.core.config import Settings
from app.core.database import create_database
from app.core.encryption import ConnectionCipher
from app.core.errors import ServiceError
from app.core.migrations import upgrade_database
from app.core.security import BodySizeLimitMiddleware
from app.core.workspaces import WorkspaceFactory, ensure_reserved_workspaces
from app.demo import DEMO_INSTANCE_ID, demo_failure, ensure_demo_instance, seed_demo_history
from app.integrations.n8n.client import N8nClient
from app.integrations.outbound import OutboundHTTP, OutboundPolicy
from app.models.auth import DEMO_WORKSPACE_ID
from app.models.incident import Base
from app.repositories.connections import verify_keyring_startup
from app.repositories.incidents import IncidentRepository
from app.schemas.domain import ErrorResponse
from app.services.alerts import (
    AlertService,
    MockAlertDeliveryProvider,
    WebhookAlertDeliveryProvider,
)
from app.services.connection_registry import (
    ConnectionRegistry,
    read_enabled_snapshots,
    revision_guard,
)
from app.services.connections import ConnectionService
from app.services.monitoring import MonitoringService


def error_response(code, message, status_code):
    return JSONResponse(
        {"error": {"code": code, "message": message}},
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


def create_app(
    settings: Settings | None = None,
    *,
    n8n_transport=None,
    ai_transport=None,
    webhook_transport=None,
    outbound_transport=None,
):
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        if ":memory:" in config.database_url:
            engine, factory = create_database(config.database_url)
            Base.metadata.create_all(engine)
        else:
            upgrade_database(config.database_url)
            engine, factory = create_database(config.database_url)
        ensure_reserved_workspaces(factory)
        cipher = None
        keys = config.connection_encryption_keys.get_secret_value()
        if not config.demo_mode and keys:
            cipher = ConnectionCipher(keys, config.connection_active_key_id)
        verify_keyring_startup(factory, cipher, demo_mode=config.demo_mode)
        app.state.connection_cipher = cipher
        app.state.session_factory = factory
        providers = {"mock": MockAlertDeliveryProvider()}
        if config.alert_webhook_url and not config.demo_mode:
            providers["webhook"] = WebhookAlertDeliveryProvider(
                config.alert_webhook_url,
                timeout_seconds=config.alert_webhook_timeout_seconds,
                signing_secret=config.alert_webhook_signing_secret.get_secret_value(),
                transport=webhook_transport,
            )
        app.state.alert_providers = providers
        registry_holder: dict[str, ConnectionRegistry] = {}
        try:
            limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
            async with (
                httpx.AsyncClient(
                    transport=n8n_transport,
                    follow_redirects=False,
                    trust_env=False,
                    limits=limits,
                ) as n8n_http,
                httpx.AsyncClient(
                    transport=ai_transport,
                    follow_redirects=False,
                    trust_env=False,
                    limits=limits,
                ) as ai_http,
                httpx.AsyncClient(
                    transport=outbound_transport,
                    follow_redirects=False,
                    trust_env=False,
                    limits=limits,
                ) as outbound_http,
            ):
                policy = OutboundPolicy(config)
                outbound = OutboundHTTP(outbound_http, policy)
                n8n_outbound = OutboundHTTP(n8n_http, policy)
                app.state.outbound = outbound
                app.state.ai_outbound = OutboundHTTP(ai_http, policy)
                app.state.connections = ConnectionService(
                    factory,
                    cipher,
                    outbound,
                    notify_changed=lambda: registry_holder["registry"].notify_changed(),
                )

                def read_enabled():
                    if cipher is None:
                        return []
                    return read_enabled_snapshots(factory, cipher)

                def build_worker(snapshot, slots):
                    workspace_factory = WorkspaceFactory(factory, snapshot.workspace_id)
                    alerts = AlertService(
                        config,
                        workspace_factory,
                        snapshot.workspace_id,
                        snapshot.id,
                        providers=providers,
                    )
                    client = N8nClient(
                        snapshot,
                        n8n_outbound,
                        guard=revision_guard(factory, snapshot),
                    )
                    return MonitoringService(
                        config,
                        workspace_factory,
                        client,
                        snapshot,
                        alerts=alerts,
                        poll_slots=slots,
                    )

                registry = ConnectionRegistry(
                    read_enabled,
                    build_worker,
                    reconcile_seconds=config.connection_reconcile_seconds,
                    concurrency=config.connection_poll_concurrency,
                )
                registry_holder["registry"] = registry
                app.state.connections.drain = registry.drain
                app.state.registry = registry

                if config.demo_mode:
                    with WorkspaceFactory(factory, DEMO_WORKSPACE_ID)() as session:
                        ensure_demo_instance(session)
                        failure = demo_failure()
                        incident, _ = IncidentRepository(
                            session, DEMO_WORKSPACE_ID, DEMO_INSTANCE_ID
                        ).create_once(failure)
                        if incident.diagnosis is None:
                            incident.diagnosis = (
                                await MockDiagnosisProvider().diagnose(failure)
                            ).model_dump()
                            incident.diagnosis_provider = "mock"
                            incident.status = "diagnosed"
                        session.commit()
                        app.state.demo_incident_id = incident.id
                        seed_demo_history(session, incident.id)
                        session.commit()
                else:
                    await registry.start()
                try:
                    yield
                finally:
                    await registry.stop()
                    for provider in providers.values():
                        close = getattr(provider, "close", None)
                        if callable(close):
                            close()
        finally:
            engine.dispose()

    app = FastAPI(
        title="FlowMedic AI",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if config.api_docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if config.api_docs_enabled else None,
    )
    app.state.settings = config

    @app.middleware("http")
    async def readonly_public_demo(request: Request, call_next):
        if config.public_demo_readonly and request.method not in {"GET", "HEAD", "OPTIONS"}:
            return error_response("demo_readonly", "Public demo is read-only", 403)
        return await call_next(request)

    @app.middleware("http")
    async def authentication(request: Request, call_next):
        key = config.flowmedic_api_key.get_secret_value()
        request.state.service_authenticated = False
        if key and request.url.path != "/health" and request.method != "OPTIONS":
            expected = ("Bearer " + key).encode()
            supplied = request.headers.get("Authorization", "").encode()
            if not secrets.compare_digest(supplied, expected):
                return error_response("unauthorized", "A valid bearer token is required", 401)
            request.state.service_authenticated = True
        response = await call_next(request)
        if request.method in {"POST", "PATCH", "DELETE"} and response.status_code >= 400:
            context = getattr(request.state, "auth", None)
            if context is not None:
                from app.services.auth import audit

                with app.state.session_factory() as session:
                    audit(session, context, "request.mutation", "denied")
                    session.commit()
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return response

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return error_response(exc.code, exc.message, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return error_response("validation_error", "Invalid request parameters", 422)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response("http_error", "Request could not be completed", exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return error_response("internal_error", "An internal error occurred", 500)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/")
    def index() -> dict[str, str | None]:
        return {
            "service": "FlowMedic AI",
            "docs": "/docs" if config.api_docs_enabled else None,
            "demo": "/api/v1/demo" if config.demo_mode else None,
        }

    app.include_router(auth_router)
    app.include_router(settings_router)
    app.include_router(
        router,
        responses={
            status: {"model": ErrorResponse} for status in (401, 404, 422, 500, 502, 503, 504)
        },
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            origin.strip() for origin in config.cors_allow_origins.split(",") if origin.strip()
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
    )
    app.add_middleware(BodySizeLimitMiddleware)
    return app


app = create_app()
