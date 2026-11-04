import secrets
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.ai.providers import CompatibleAIProvider, MockDiagnosisProvider
from app.api.routes import router
from app.core.config import Settings
from app.core.database import create_database
from app.core.errors import ServiceError
from app.core.migrations import upgrade_database
from app.demo import demo_failure, seed_demo_history
from app.integrations.n8n.client import N8nClient
from app.models.incident import Base
from app.repositories.incidents import IncidentRepository
from app.schemas.domain import ErrorResponse
from app.services.alerts import (
    AlertService,
    MockAlertDeliveryProvider,
    WebhookAlertDeliveryProvider,
)
from app.services.monitoring import MonitoringService


def error_response(code, message, status_code):
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status_code)


def create_app(
    settings: Settings | None = None,
    *,
    n8n_transport=None,
    ai_transport=None,
    webhook_transport=None,
):
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        if ":memory:" in config.database_url:
            # Isolated test databases do not share connections with Alembic's migration engine.
            engine, factory = create_database(config.database_url)
            Base.metadata.create_all(engine)
        else:
            upgrade_database(config.database_url)
            engine, factory = create_database(config.database_url)
        app.state.session_factory = factory
        app.state.monitoring = None
        providers = {"mock": MockAlertDeliveryProvider()}
        if config.alert_webhook_url and not config.demo_mode:
            providers["webhook"] = WebhookAlertDeliveryProvider(
                config.alert_webhook_url,
                timeout_seconds=config.alert_webhook_timeout_seconds,
                signing_secret=config.alert_webhook_signing_secret.get_secret_value(),
                transport=webhook_transport,
            )
        app.state.alerts = AlertService(config, factory, providers=providers)
        try:
            async with (
                httpx.AsyncClient(transport=n8n_transport, follow_redirects=False) as n8n_http,
                httpx.AsyncClient(transport=ai_transport, follow_redirects=False) as ai_http,
            ):
                app.state.n8n = N8nClient(config, n8n_http)
                app.state.provider = (
                    CompatibleAIProvider(config, ai_http)
                    if config.ai_api_key.get_secret_value() and not config.demo_mode
                    else MockDiagnosisProvider()
                )
                if config.demo_mode:
                    with factory() as session:
                        failure = demo_failure()
                        incident, _ = IncidentRepository(session).create_once(failure)
                        if incident.diagnosis is None:
                            incident.diagnosis = (
                                await app.state.provider.diagnose(failure)
                            ).model_dump()
                            incident.diagnosis_provider = app.state.provider.name
                            incident.status = "diagnosed"
                        session.commit()
                        app.state.demo_incident_id = incident.id
                        seed_demo_history(session, incident.id)
                        session.commit()
                app.state.monitoring = MonitoringService(
                    config,
                    factory,
                    app.state.n8n,
                    alerts=app.state.alerts,
                )
                await app.state.monitoring.start()
                yield
        finally:
            if app.state.monitoring is not None:
                await app.state.monitoring.stop()
            app.state.alerts.close()
            engine.dispose()

    app = FastAPI(title="FlowMedic AI", version="0.1.0", lifespan=lifespan)
    app.state.settings = config

    @app.middleware("http")
    async def readonly_public_demo(request: Request, call_next):
        # Visitors can browse synthetic data but cannot mutate demo state.
        if config.public_demo_readonly and request.method not in {"GET", "HEAD", "OPTIONS"}:
            return error_response("demo_readonly", "Public demo is read-only", 403)
        return await call_next(request)

    @app.middleware("http")
    async def authentication(request: Request, call_next):
        key = config.flowmedic_api_key.get_secret_value()
        if key and request.url.path != "/health":
            expected = ("Bearer " + key).encode()
            supplied = request.headers.get("Authorization", "").encode()
            if not secrets.compare_digest(supplied, expected):
                return error_response("unauthorized", "A valid bearer token is required", 401)
        return await call_next(request)

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return error_response(exc.code, exc.message, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Validation details can echo sensitive user input; expose a fixed message.
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
            "docs": "/docs",
            "demo": "/api/v1/demo" if config.demo_mode else None,
        }

    app.include_router(
        router,
        responses={
            status: {"model": ErrorResponse} for status in (401, 404, 422, 500, 502, 503, 504)
        },
    )
    # Handle browser preflight before bearer checks and add CORS to safe errors.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            origin.strip() for origin in config.cors_allow_origins.split(",") if origin.strip()
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    return app


app = create_app()
