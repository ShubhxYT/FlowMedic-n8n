from datetime import UTC, datetime, timedelta

import pytest
from auth_support import ORIGIN, PASSWORD, login
from connection_support import provision
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import aliased
from test_auth import invite_token, signup

from app.commands.bootstrap_owner import bootstrap
from app.core.config import Settings
from app.core.workspaces import WorkspaceFactory
from app.main import create_app
from app.models.auth import DEMO_WORKSPACE_ID, LEGACY_WORKSPACE_ID, Workspace
from app.models.incident import (
    AlertConditionState,
    AlertDeliveryAttempt,
    AlertEvent,
    AlertRule,
    ExecutionHistory,
    Incident,
    MonitoringCheckpoint,
)
from app.repositories.alerts import AlertEventRepository
from app.repositories.incidents import IncidentRepository
from app.repositories.monitoring import MonitoringRepository
from app.schemas.domain import Failure
from app.services.alerts import AlertService


@pytest.fixture
def two_users():
    settings = Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
        auth_registration_enabled=True,
        auth_allow_insecure_cookies=True,
        auth_trusted_origins=ORIGIN,
    )
    app = create_app(settings)
    with TestClient(app) as owner:
        bootstrap(app.state.session_factory, "owner@example.com", PASSWORD)
        login(owner)
        other = TestClient(app)
        person = signup(other, "person@example.com")
        try:
            yield owner, other, person
        finally:
            other.close()


def seed(factory, wid):
    now = datetime.now(UTC)
    instance = provision(factory, workspace_id=wid)
    with WorkspaceFactory(factory, wid)() as db:
        incident, _ = IncidentRepository(db, wid, instance.id).create_once(
            Failure(
                execution_id="shared-execution",
                workflow_id="shared-workflow",
                workflow_name="Private",
                failed_node=None,
                error_type="Error",
                error_message="Workspace-only",
                severity="medium",
            )
        )
        rule = AlertRule(
            name="same-rule",
            trigger_type="new_incident",
            enabled=True,
            cooldown_seconds=300,
            delivery_provider="mock",
            created_at=now,
            updated_at=now,
        )
        db.add(rule)
        db.flush()
        event = AlertEvent(
            rule_id=rule.id,
            trigger_type="new_incident",
            instance_id=instance.id,
            event_key="same-key",
            deduplication_key="same-key",
            incident_id=incident.id,
            workflow_id="shared-workflow",
            status="pending",
            cooldown_until=now + timedelta(minutes=5),
            created_at=now,
        )
        db.add(event)
        db.flush()
        db.add(
            AlertDeliveryAttempt(
                alert_event_id=event.id,
                provider="mock",
                attempt_number=1,
                status="pending",
                started_at=now,
            )
        )
        db.add(
            ExecutionHistory(
                instance_id=instance.id,
                source="n8n",
                workflow_id="shared-workflow",
                workflow_name="Private",
                execution_id="shared-execution",
                execution_status="error",
                recorded_at=now,
                is_success=False,
                incident_id=incident.id,
            )
        )
        db.add(
            AlertConditionState(
                instance_id=instance.id,
                condition_type="unhealthy",
                scope_key="shared-workflow",
                current_state="unhealthy",
                updated_at=now,
            )
        )
        checkpoint = MonitoringRepository(db, wid, instance.id).checkpoint("n8n")
        checkpoint.lease_owner_id = wid
        db.commit()
        return incident.id, rule.id, event.id


def test_two_workspace_api_matrix_and_background_scope(two_users):
    owner, other, person = two_users
    factory = owner.app.state.session_factory
    ids = seed(factory, LEGACY_WORKSPACE_ID)
    # New account cannot inherit any environment integration or records.
    for path in ["incidents", "alerts/rules", "alerts/events"]:
        response = other.get(f"/api/v1/{path}")
        assert response.status_code == 200, response.text
        assert response.json()["data"] == []
    assert other.get("/api/v1/metrics/overview").json()["recent_failure_count"] == 0
    assert other.get("/api/v1/monitoring/status").json() == {"items": []}
    assert other.get("/api/v1/system/status").json()["n8n_configured"] is False
    assert other.get("/api/v1/n8n/status").status_code == 422
    assert other.post("/api/v1/monitoring/sync").status_code == 422
    for path in [f"incidents/{ids[0]}", f"alerts/events/{ids[2]}/deliveries"]:
        assert other.get(f"/api/v1/{path}").status_code == 404
    assert other.post(f"/api/v1/incidents/{ids[0]}/diagnose").status_code == 404
    assert other.patch(f"/api/v1/alerts/rules/{ids[1]}", json={"enabled": False}).status_code == 404
    second_ids = seed(factory, person["workspace"]["id"])
    assert other.get("/api/v1/incidents").json()["data"][0]["id"] == second_ids[0]
    assert owner.get("/api/v1/incidents").json()["data"][0]["id"] == ids[0]
    assert other.get("/api/v1/metrics/overview").json()["recent_failure_count"] == 1
    # Aliases, aggregate selects, ORM bulk updates/deletes, identity-map lookups and attempts.
    with WorkspaceFactory(factory, person["workspace"]["id"])() as db:
        alias = aliased(Incident)
        assert db.scalar(select(func.count()).select_from(alias)) == 1
        assert db.get(Incident, ids[0]) is None
        assert AlertEventRepository(db).deliveries(ids[2]) == []
        assert db.execute(update(MonitoringCheckpoint).values(lease_owner_id="other")).rowcount == 1
        assert db.execute(delete(AlertConditionState)).rowcount == 1
        db.commit()
        with pytest.raises(RuntimeError):
            db.execute(text("SELECT * FROM incidents"))
    with WorkspaceFactory(factory, LEGACY_WORKSPACE_ID)() as db:
        assert (
            MonitoringRepository(db, LEGACY_WORKSPACE_ID, provision(factory).id)
            .existing_checkpoint("n8n")
            .lease_owner_id
            == LEGACY_WORKSPACE_ID
        )
        assert db.scalar(select(func.count()).select_from(AlertConditionState)) == 1
    with factory() as db, pytest.raises(RuntimeError):
        IncidentRepository(db, LEGACY_WORKSPACE_ID)
    with pytest.raises(RuntimeError):
        AlertService(owner.app.state.settings, factory, LEGACY_WORKSPACE_ID, provision(factory).id)
    with WorkspaceFactory(factory, person["workspace"]["id"])() as db:
        db.add(
            ExecutionHistory(
                source="n8n",
                workflow_id="w",
                workflow_name="w",
                execution_id="invalid",
                recorded_at=datetime.now(UTC),
                incident_id=ids[0],
            )
        )
        with pytest.raises(ValueError):
            db.flush()


def test_viewer_permissions_workspace_switching_and_removal(two_users):
    owner, other, person = two_users
    incident_id, rule_id, event_id = seed(owner.app.state.session_factory, LEGACY_WORKSPACE_ID)
    token = invite_token(owner)
    assert other.post("/api/v1/auth/invites/accept", json={"token": token}).status_code == 200
    other.headers["X-CSRF-Token"] = other.cookies["fm_csrf"]
    assert other.get(f"/api/v1/incidents/{incident_id}").status_code == 200
    assert other.get(f"/api/v1/alerts/events/{event_id}/deliveries").status_code == 200
    for path in ["incidents/sync", "monitoring/sync", f"incidents/{incident_id}/diagnose"]:
        assert other.post(f"/api/v1/{path}").status_code == 403
    assert (
        other.post(
            "/api/v1/alerts/rules", json={"name": "blocked", "trigger_type": "new_incident"}
        ).status_code
        == 403
    )
    assert (
        other.patch(f"/api/v1/alerts/rules/{rule_id}", json={"enabled": False}).status_code == 403
    )
    assert other.post("/api/v1/auth/invites", json={"role": "admin"}).status_code == 403
    assert (
        other.post("/api/v1/auth/workspace", json={"workspace_id": DEMO_WORKSPACE_ID}).status_code
        == 404
    )
    old = other.cookies["fm_session"]
    assert (
        other.post(
            "/api/v1/auth/workspace", json={"workspace_id": person["workspace"]["id"]}
        ).status_code
        == 200
    )
    assert other.cookies["fm_session"] != old
    other.headers["X-CSRF-Token"] = other.cookies["fm_csrf"]
    assert other.get("/api/v1/incidents").json()["data"] == []
    assert (
        other.post("/api/v1/auth/workspace", json={"workspace_id": LEGACY_WORKSPACE_ID}).status_code
        == 200
    )
    assert owner.delete(f"/api/v1/auth/members/{person['user']['id']}").status_code == 200
    assert other.get("/api/v1/incidents").status_code == 401


def test_public_demo_cannot_read_legacy_records():
    settings = Settings(
        _env_file=None, database_url="sqlite:///:memory:", demo_mode=True, public_demo_readonly=True
    )
    with TestClient(create_app(settings)) as c:
        seed(c.app.state.session_factory, LEGACY_WORKSPACE_ID)
        rows = c.get("/api/v1/incidents").json()["data"]
        assert len(rows) == 1 and rows[0]["execution_id"].startswith("demo-")
        assert c.post("/api/v1/monitoring/sync").status_code == 403
        with c.app.state.session_factory() as db:
            assert db.get(Workspace, LEGACY_WORKSPACE_ID) is not None
