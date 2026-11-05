from datetime import UTC, datetime, timedelta

import pytest

from app.models.incident import ExecutionHistory, Incident
from app.services.health import HealthCounts, build_workflow_health, classify_health


def first_incident_id(client):
    assert client.post("/api/v1/incidents/sync").status_code == 200
    return client.get("/api/v1/incidents").json()["data"][0]["id"]


def test_incident_can_be_resolved_and_stays_in_history(client):
    incident_id = first_incident_id(client)
    response = client.patch(f"/api/v1/incidents/{incident_id}", json={"status": "resolved"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "resolved"
    assert body["resolved_at"] is not None
    listed = client.get("/api/v1/incidents").json()["data"]
    assert [item["id"] for item in listed] == [incident_id]
    assert listed[0]["status"] == "resolved"


def test_resolving_twice_keeps_original_timestamp(client):
    incident_id = first_incident_id(client)
    url = f"/api/v1/incidents/{incident_id}"
    first = client.patch(url, json={"status": "resolved"}).json()
    second = client.patch(url, json={"status": "resolved"}).json()
    assert first["resolved_at"] == second["resolved_at"]
    assert first["resolved_at"].endswith("Z")
    listed = client.get(url).json()
    assert listed["resolved_at"] == first["resolved_at"]


@pytest.mark.parametrize("payload", [{"status": "open"}, {"status": "nonsense"}, {}, {"x": 1}])
def test_invalid_update_payloads_are_rejected(client, payload):
    incident_id = first_incident_id(client)
    assert client.patch(f"/api/v1/incidents/{incident_id}", json=payload).status_code == 422


def test_resolving_unknown_incident_is_404(client):
    response = client.patch(
        "/api/v1/incidents/00000000-0000-0000-0000-000000000000", json={"status": "resolved"}
    )
    assert response.status_code == 404


def test_diagnosing_a_resolved_incident_does_not_reopen_it(client):
    incident_id = first_incident_id(client)
    client.patch(f"/api/v1/incidents/{incident_id}", json={"status": "resolved"})
    result = client.post(f"/api/v1/incidents/{incident_id}/diagnose")
    assert result.status_code == 200
    assert result.json()["status"] == "resolved"
    assert result.json()["diagnosis"] is not None


NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)


def history(index, success, incident_id=None):
    return ExecutionHistory(
        source="n8n",
        workflow_id="wf",
        workflow_name="Flow",
        execution_id=str(index),
        execution_status="success" if success else "error",
        recorded_at=NOW + timedelta(minutes=index),
        started_at=NOW + timedelta(minutes=index),
        is_success=success,
        incident_id=incident_id,
    )


def incident(incident_id, status):
    return Incident(
        id=incident_id,
        execution_id=f"exec-{incident_id}",
        workflow_id="wf",
        workflow_name="Flow",
        error_type="Error",
        error_message="boom",
        severity="medium",
        status=status,
    )


def test_resolving_incidents_returns_workflow_to_healthy():
    rows = [history(1, True), history(2, False, "a"), history(3, False, "b")]
    unresolved = build_workflow_health(rows, [incident("a", "open"), incident("b", "open")])[0]
    assert unresolved.health == "unhealthy"
    assert unresolved.open_incident_count == 2

    one_left = build_workflow_health(rows, [incident("a", "resolved"), incident("b", "open")])[0]
    assert one_left.health == "degraded"
    assert one_left.open_incident_count == 1

    cleared = build_workflow_health(
        rows, [incident("a", "resolved"), incident("b", "resolved")]
    )[0]
    assert cleared.health == "healthy"
    assert cleared.open_incident_count == 0
    # The failures stay visible in the execution history counts.
    assert cleared.recent_failure_count == 2


def test_new_failure_after_resolution_makes_workflow_unhealthy_again():
    rows = [
        history(1, True),
        history(2, False, "a"),
        history(3, False, "b"),
        history(4, False, "c"),
        history(5, False, "d"),
    ]
    incidents = [
        incident("a", "resolved"),
        incident("b", "resolved"),
        incident("c", "open"),
        incident("d", "open"),
    ]
    assert build_workflow_health(rows, incidents)[0].health == "unhealthy"


def test_classify_health_ignores_resolved_failures():
    assert classify_health(HealthCounts(3, 1, 2, 0, resolved_failure_count=2)) == "healthy"
    assert classify_health(HealthCounts(3, 1, 2, 0, resolved_failure_count=1)) == "degraded"
    assert classify_health(HealthCounts(3, 1, 2, 0)) == "unhealthy"
