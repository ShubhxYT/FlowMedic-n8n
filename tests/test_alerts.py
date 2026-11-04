import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from alembic.config import Config
from sqlalchemy import inspect, select

from alembic import command
from app.api.routes import sync_incidents
from app.core.config import Settings
from app.core.database import create_database
from app.core.migrations import upgrade_database
from app.integrations.n8n.client import Execution
from app.models.incident import AlertEvent, Base, ExecutionHistory, Incident
from app.repositories.alerts import AlertEventRepository, AlertRuleRepository
from app.services.alerts import (
    TRIGGER_MONITORING_DEGRADED,
    TRIGGER_NEW_INCIDENT,
    TRIGGER_WORKFLOW_UNHEALTHY,
    AlertDeliveryProvider,
    AlertService,
    DeliveryPayload,
)


class FailingProvider(AlertDeliveryProvider):
    name = "mock"

    def __init__(self):
        self.calls = 0

    def deliver(self, payload: DeliveryPayload) -> None:
        self.calls += 1
        raise RuntimeError("safe test failure")


def service_with_database(tmp_path, *, provider=None, demo_mode=False, retries=3):
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'alerts.db'}",
        demo_mode=demo_mode,
        alert_max_retries=retries,
    )
    engine, factory = create_database(settings.database_url)
    Base.metadata.create_all(engine)
    return AlertService(settings, factory, provider), factory


def add_rule(factory, *, trigger_type, enabled=True, cooldown_seconds=300):
    with factory() as session:
        rule = AlertRuleRepository(session).create(
            name=f"{trigger_type}-{enabled}-{cooldown_seconds}",
            trigger_type=trigger_type,
            enabled=enabled,
            cooldown_seconds=cooldown_seconds,
            delivery_provider="mock",
        )
        session.commit()
        return rule.id


def add_incident(factory, incident_id="incident-1", workflow_id="workflow-1"):
    now = datetime.now(UTC)
    with factory() as session:
        session.add(
            Incident(
                id=incident_id,
                execution_id=f"execution-{incident_id}",
                workflow_id=workflow_id,
                workflow_name="Test workflow",
                failed_node=None,
                error_type="TestError",
                error_message="safe test error",
                execution_timestamp=now,
                status="open",
                severity="medium",
                diagnosis=None,
                diagnosis_provider=None,
            )
        )
        session.commit()


def events(factory):
    with factory() as session:
        return list(session.scalars(select(AlertEvent)))


def test_disabled_rule_does_not_create_an_alert_event(tmp_path):
    service, factory = service_with_database(tmp_path)
    add_incident(factory)
    add_rule(factory, trigger_type=TRIGGER_NEW_INCIDENT, enabled=False)

    assert service.on_new_incident("incident-1", "workflow-1") == 0
    assert events(factory) == []


def test_new_incident_alert_is_deduplicated_across_service_restarts(tmp_path):
    service, factory = service_with_database(tmp_path)
    add_incident(factory)
    add_rule(factory, trigger_type=TRIGGER_NEW_INCIDENT)

    assert service.on_new_incident("incident-1", "workflow-1") == 1
    restarted, _ = service_with_database(tmp_path)
    assert restarted.on_new_incident("incident-1", "workflow-1") == 0

    stored = events(factory)
    assert len(stored) == 1
    assert stored[0].status == "delivered"
    with factory() as session:
        assert len(AlertEventRepository(session).deliveries(stored[0].id)) == 1


def test_cooldown_uses_persisted_expiry_not_only_a_time_bucket(tmp_path, monkeypatch):
    service, factory = service_with_database(tmp_path)
    add_incident(factory)
    add_rule(factory, trigger_type=TRIGGER_NEW_INCIDENT, cooldown_seconds=60)

    assert service.on_new_incident("incident-1", "workflow-1") == 1
    with factory() as session:
        event = session.scalar(select(AlertEvent))
        event.event_key = "previous-time-bucket"
        session.commit()

    monkeypatch.setattr("app.services.alerts.utc_now", lambda: datetime.now(UTC))
    assert service.on_new_incident("incident-1", "workflow-1") == 0
    assert len(events(factory)) == 1


def test_delivery_failure_uses_a_bounded_persisted_retry_budget(tmp_path):
    provider = FailingProvider()
    service, factory = service_with_database(tmp_path, provider=provider, retries=2)
    add_incident(factory)
    add_rule(factory, trigger_type=TRIGGER_NEW_INCIDENT)

    assert service.on_new_incident("incident-1", "workflow-1") == 1
    event = events(factory)[0]
    assert event.status == "failed"
    assert event.last_error_summary == "Alert delivery provider failed"
    assert provider.calls == 2
    with factory() as session:
        assert len(AlertEventRepository(session).deliveries(event.id)) == 2
    service.deliver(event.id)
    assert provider.calls == 2


def test_conflicting_attempt_reservation_is_retried_without_duplicate_numbers(
    tmp_path, monkeypatch
):
    service, factory = service_with_database(tmp_path)
    rule_id = add_rule(factory, trigger_type=TRIGGER_NEW_INCIDENT)
    now = datetime.now(UTC)
    with factory() as session:
        session.add(
            AlertEvent(
                id="reservation-event",
                rule_id=rule_id,
                trigger_type=TRIGGER_NEW_INCIDENT,
                event_key="reservation-event-key",
                deduplication_key="incident:reservation",
                incident_id=None,
                workflow_id="workflow-1",
                status="pending",
                cooldown_until=now + timedelta(minutes=5),
                last_error_summary=None,
                created_at=now,
                delivered_at=None,
            )
        )
        session.commit()

    with factory() as session:
        AlertEventRepository(session).create_attempt(
            alert_event_id="reservation-event",
            provider="mock",
            attempt_number=1,
            status="pending",
            error_summary=None,
            started_at=now,
            completed_at=None,
        )
        session.commit()

    original_deliveries = AlertEventRepository.deliveries
    stale_read = True

    def stale_deliveries(repository, event_id):
        nonlocal stale_read
        if event_id == "reservation-event" and stale_read:
            stale_read = False
            return []
        return original_deliveries(repository, event_id)

    monkeypatch.setattr(AlertEventRepository, "deliveries", stale_deliveries)
    service.deliver("reservation-event")

    with factory() as session:
        attempts = AlertEventRepository(session).deliveries("reservation-event")
        assert [attempt.attempt_number for attempt in attempts] == [1, 2]
        assert len({attempt.attempt_number for attempt in attempts}) == 2
        assert session.get(AlertEvent, "reservation-event").status == "delivered"


def test_state_transition_rules_only_trigger_when_state_becomes_unhealthy_or_degraded(tmp_path):
    service, factory = service_with_database(tmp_path)
    add_rule(factory, trigger_type=TRIGGER_WORKFLOW_UNHEALTHY)
    add_rule(factory, trigger_type=TRIGGER_MONITORING_DEGRADED)
    add_incident(factory)
    now = datetime.now(UTC)
    with factory() as session:
        session.add(
            ExecutionHistory(
                source="n8n",
                workflow_id="workflow-1",
                workflow_name="Test workflow",
                execution_id="history-1",
                execution_status="error",
                recorded_at=now,
                started_at=now,
                stopped_at=now,
                is_success=False,
                incident_id="incident-1",
            )
        )
        session.commit()

    assert service.evaluate_workflow_health() == 1
    assert service.evaluate_workflow_health() == 0
    assert service.evaluate_monitoring_state("idle") == 0
    assert service.evaluate_monitoring_state("degraded") == 1
    assert service.evaluate_monitoring_state("degraded") == 0
    assert {event.trigger_type for event in events(factory)} == {
        TRIGGER_WORKFLOW_UNHEALTHY,
        TRIGGER_MONITORING_DEGRADED,
    }


def test_demo_mode_isolated_from_alert_creation(tmp_path):
    service, factory = service_with_database(tmp_path, demo_mode=True)
    add_incident(factory)
    add_rule(factory, trigger_type=TRIGGER_NEW_INCIDENT)

    assert service.on_new_incident("incident-1", "workflow-1") == 0
    assert events(factory) == []


def test_alert_api_exposes_only_safe_rule_event_and_delivery_state(client):
    created = client.post(
        "/api/v1/alerts/rules",
        json={"name": "Incident alert", "trigger_type": "new_incident", "enabled": True},
    )
    assert created.status_code == 201
    assert created.json()["delivery_provider"] == "mock"
    assert client.get("/api/v1/alerts/rules").json()["data"][0]["enabled"] is True
    updated = client.patch(f"/api/v1/alerts/rules/{created.json()['id']}", json={"enabled": False})
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False
    assert client.get("/api/v1/alerts/events").json() == {"data": []}


def test_manual_incident_sync_succeeds_when_alert_evaluation_fails(tmp_path):
    class FailingAlerts:
        def on_new_incident(self, incident_id, workflow_id):
            raise RuntimeError("alert provider unavailable")

    class FakeN8n:
        async def failed_executions(self, limit, cursor):
            return (
                [
                    Execution(
                        id="failing-sync-execution",
                        workflowId="workflow-1",
                        status="error",
                        workflowData={"name": "Manual sync workflow"},
                        data={
                            "resultData": {
                                "error": {
                                    "name": "TestError",
                                    "message": "safe test failure",
                                }
                            }
                        },
                    )
                ],
                None,
                1,
            )

    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'manual-sync.db'}",
    )
    engine, factory = create_database(settings.database_url)
    Base.metadata.create_all(engine)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=settings,
                n8n=FakeN8n(),
                alerts=FailingAlerts(),
            )
        )
    )
    with factory() as session:
        response = asyncio.run(sync_incidents(request, session))

    assert response.created == 1
    with factory() as session:
        assert len(list(session.scalars(select(Incident)))) == 1


def upgrade_to_revision(database_url, revision):
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, revision)


def test_alerting_migration_preserves_pre_phase_four_incident_data(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'alerting.db'}"
    upgrade_to_revision(database_url, "0002_monitoring_hardening")
    engine, factory = create_database(database_url)
    original = {
        "id": "pre-phase-four-incident",
        "execution_id": "pre-phase-four-execution",
        "workflow_id": "pre-phase-four-workflow",
        "workflow_name": "Pre Phase Four Workflow",
        "failed_node": "Safe node",
        "error_type": "PrePhaseFourError",
        "error_message": "safe preserved error",
        "status": "open",
        "severity": "high",
    }
    with factory() as session:
        session.add(
            Incident(
                **original,
                execution_timestamp=datetime(2026, 9, 1, tzinfo=UTC),
                diagnosis=None,
                diagnosis_provider=None,
            )
        )
        session.commit()

    upgrade_database(database_url)
    tables = set(inspect(engine).get_table_names())
    assert {
        "alert_rules",
        "alert_events",
        "alert_delivery_attempts",
        "alert_condition_states",
    } <= tables
    with factory() as session:
        preserved = session.get(Incident, original["id"])
        assert preserved is not None
        assert {
            "id": preserved.id,
            "execution_id": preserved.execution_id,
            "workflow_id": preserved.workflow_id,
            "workflow_name": preserved.workflow_name,
            "failed_node": preserved.failed_node,
            "error_type": preserved.error_type,
            "error_message": preserved.error_message,
            "status": preserved.status,
            "severity": preserved.severity,
        } == original
