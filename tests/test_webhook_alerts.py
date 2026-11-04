import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.api.routes import create_alert_rule, sync_incidents
from app.core.config import Settings
from app.core.database import create_database
from app.core.errors import ServiceError
from app.integrations.n8n.client import Execution
from app.models.incident import AlertEvent, Base, Incident
from app.repositories.alerts import AlertEventRepository, AlertRuleRepository
from app.schemas.domain import AlertRuleCreate, AlertRuleOut
from app.services.alerts import (
    TRIGGER_NEW_INCIDENT,
    AlertService,
    MockAlertDeliveryProvider,
    WebhookAlertDeliveryProvider,
)

PUBLIC_WEBHOOK_URL = "https://8.8.8.8/flowmedic-alerts"


def service_with_webhook(
    tmp_path, handler, *, retries=3, signing_secret="", database_name="webhooks"
):
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / f'{database_name}.db'}",
        alert_webhook_url=PUBLIC_WEBHOOK_URL,
        alert_max_retries=retries,
        alert_webhook_signing_secret=signing_secret,
    )
    engine, factory = create_database(settings.database_url)
    Base.metadata.create_all(engine)
    provider = WebhookAlertDeliveryProvider(
        settings.alert_webhook_url,
        timeout_seconds=settings.alert_webhook_timeout_seconds,
        signing_secret=signing_secret,
        transport=httpx.MockTransport(handler),
    )
    return AlertService(
        settings, factory, providers={"mock": MockAlertDeliveryProvider(), "webhook": provider}
    ), factory


def add_webhook_rule(factory):
    with factory() as session:
        rule = AlertRuleRepository(session).create(
            name="webhook incident",
            trigger_type=TRIGGER_NEW_INCIDENT,
            enabled=True,
            cooldown_seconds=300,
            delivery_provider="webhook",
        )
        session.add(
            Incident(
                id="incident-webhook",
                execution_id="execution-webhook",
                workflow_id="workflow-webhook",
                workflow_name="Webhook workflow",
                failed_node=None,
                error_type="SafeError",
                error_message="sanitized error",
                execution_timestamp=datetime.now(UTC),
                status="open",
                severity="medium",
                diagnosis=None,
                diagnosis_provider=None,
            )
        )
        session.commit()
        return rule


def stored_event(factory):
    with factory() as session:
        return session.scalar(select(AlertEvent))


def test_configured_https_webhook_delivers_only_minimal_payload(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(204)

    service, factory = service_with_webhook(tmp_path, handler)
    add_webhook_rule(factory)

    assert service.on_new_incident("incident-webhook", "workflow-webhook") == 1
    body = json.loads(requests[0].content)
    assert body == {
        "delivery": {"attempt": 1},
        "event": {
            "created_at": body["event"]["created_at"],
            "id": body["event"]["id"],
            "incident_id": "incident-webhook",
            "trigger_type": "new_incident",
            "workflow_id": "workflow-webhook",
        },
        "version": 1,
    }
    assert "sanitized error" not in requests[0].content.decode()
    assert stored_event(factory).status == "delivered"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://8.8.8.8/hook", "HTTPS"),
        ("https://localhost/hook", "hostnames"),
        ("https://127.0.0.1/hook", "globally routable"),
        ("https://10.0.0.5/hook", "globally routable"),
        ("https://169.254.1.1/hook", "globally routable"),
    ],
)
def test_unsafe_webhook_destinations_are_rejected(url, expected):
    with pytest.raises(ValidationError, match=expected):
        Settings(_env_file=None, alert_webhook_url=url)


def test_webhook_timeout_retries_with_bounded_attempts(tmp_path):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.TimeoutException("timeout", request=request)

    service, factory = service_with_webhook(tmp_path, handler, retries=2)
    add_webhook_rule(factory)

    service.on_new_incident("incident-webhook", "workflow-webhook")

    event = stored_event(factory)
    assert calls == 2
    assert event.status == "failed"
    assert event.last_error_summary == "Webhook request timed out"
    with factory() as session:
        assert len(AlertEventRepository(session).deliveries(event.id)) == 2


def test_retryable_5xx_retries_but_4xx_does_not(tmp_path):
    retry_calls = 0

    def retry_handler(request):
        nonlocal retry_calls
        retry_calls += 1
        return httpx.Response(503)

    service, factory = service_with_webhook(tmp_path, retry_handler, retries=3)
    add_webhook_rule(factory)
    service.on_new_incident("incident-webhook", "workflow-webhook")
    assert retry_calls == 3

    client_calls = 0

    def client_handler(request):
        nonlocal client_calls
        client_calls += 1
        return httpx.Response(400)

    service, factory = service_with_webhook(
        tmp_path, client_handler, retries=3, database_name="client-error"
    )
    add_webhook_rule(factory)
    service.on_new_incident("incident-webhook", "workflow-webhook")
    assert client_calls == 1
    assert stored_event(factory).last_error_summary == "Webhook returned a non-retryable response"


def test_redirect_is_not_followed(tmp_path):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"location": "https://8.8.8.8/other"})

    service, factory = service_with_webhook(tmp_path, handler)
    add_webhook_rule(factory)
    service.on_new_incident("incident-webhook", "workflow-webhook")

    assert calls == 1
    assert stored_event(factory).status == "failed"


def test_webhook_signature_and_error_summaries_never_include_secret(tmp_path):
    secret = "test-signing-secret"
    received = []

    def success_handler(request):
        received.append(request)
        return httpx.Response(204)

    service, factory = service_with_webhook(tmp_path, success_handler, signing_secret=secret)
    add_webhook_rule(factory)
    service.on_new_incident("incident-webhook", "workflow-webhook")

    request = received[0]
    timestamp = request.headers["x-flowmedic-timestamp"]
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + request.content, hashlib.sha256
    ).hexdigest()
    assert request.headers["x-flowmedic-signature"] == f"v1={expected}"

    def failure_handler(request):
        return httpx.Response(400)

    service, factory = service_with_webhook(
        tmp_path,
        failure_handler,
        signing_secret=secret,
        database_name="failure",
    )
    add_webhook_rule(factory)
    service.on_new_incident("incident-webhook", "workflow-webhook")
    event = stored_event(factory)
    assert secret not in (event.last_error_summary or "")
    with factory() as session:
        attempt = AlertEventRepository(session).deliveries(event.id)[0]
        assert secret not in (attempt.error_summary or "")


def test_webhook_rule_is_rejected_when_provider_is_not_configured(tmp_path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'api.db'}")
    engine, factory = create_database(settings.database_url)
    Base.metadata.create_all(engine)
    service = AlertService(settings, factory)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(alerts=service, settings=settings))
    )
    with factory() as session, pytest.raises(ServiceError) as error:
        create_alert_rule(
            request,
            AlertRuleCreate(
                name="blocked webhook", trigger_type="new_incident", delivery_provider="webhook"
            ),
            session,
        )
    assert error.value.code == "alert_provider_not_configured"


def test_demo_mode_never_invokes_a_configured_webhook(tmp_path):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(204)

    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'demo.db'}",
        demo_mode=True,
        alert_webhook_url=PUBLIC_WEBHOOK_URL,
    )
    engine, factory = create_database(settings.database_url)
    Base.metadata.create_all(engine)
    provider = WebhookAlertDeliveryProvider(
        PUBLIC_WEBHOOK_URL,
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )
    service = AlertService(settings, factory, providers={"webhook": provider})
    add_webhook_rule(factory)

    assert service.on_new_incident("incident-webhook", "workflow-webhook") == 0
    assert calls == 0


def test_rule_api_exposes_provider_but_never_webhook_secret(tmp_path):
    secret = "api-secret-must-not-leak"
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'safe-api.db'}",
        alert_webhook_url=PUBLIC_WEBHOOK_URL,
        alert_webhook_signing_secret=secret,
    )
    engine, factory = create_database(settings.database_url)
    Base.metadata.create_all(engine)
    provider = WebhookAlertDeliveryProvider(
        PUBLIC_WEBHOOK_URL, timeout_seconds=5, signing_secret=secret
    )
    service = AlertService(settings, factory, providers={"webhook": provider})
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(alerts=service, settings=settings))
    )
    with factory() as session:
        response = create_alert_rule(
            request,
            AlertRuleCreate(
                name="safe webhook", trigger_type="new_incident", delivery_provider="webhook"
            ),
            session,
        )

    assert response.delivery_provider == "webhook"
    assert secret not in AlertRuleOut.model_validate(response).model_dump_json()


def test_webhook_failure_does_not_break_committed_manual_incident_sync(tmp_path):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    service, factory = service_with_webhook(tmp_path, handler, retries=2)
    add_webhook_rule(factory)

    class FakeN8n:
        async def failed_executions(self, limit, cursor):
            return (
                [
                    Execution(
                        id="sync-webhook-failure",
                        workflowId="workflow-sync",
                        status="error",
                        workflowData={"name": "Sync workflow"},
                        data={
                            "resultData": {
                                "error": {"name": "SafeError", "message": "safe failure"}
                            }
                        },
                    )
                ],
                None,
                1,
            )

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(settings=service.settings, n8n=FakeN8n(), alerts=service)
        )
    )
    with factory() as session:
        response = asyncio.run(sync_incidents(request, session))

    assert response.created == 1
    assert calls == 2
    with factory() as session:
        assert (
            session.scalar(select(Incident).where(Incident.execution_id == "sync-webhook-failure"))
            is not None
        )
