import asyncio
import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect

from app.ai.providers import CompatibleAIProvider, MockDiagnosisProvider
from app.core.config import Settings
from app.core.database import create_database
from app.core.migrations import upgrade_database
from app.integrations.n8n.client import Execution
from app.main import create_app
from app.models.incident import Base, Incident
from app.repositories.incidents import IncidentRepository
from app.schemas.domain import Diagnosis, IncidentOut
from app.services.incidents import normalize

RICH_EXECUTION = {
    "id": "exec-100",
    "workflowId": "wf-orders",
    "status": "error",
    "startedAt": "2026-11-07T09:30:00Z",
    "workflowData": {
        "name": "Order import",
        "credentials": {"apiKey": "workflow-credential-should-not-leak"},
        "nodes": [
            {
                "name": "Stop and fail",
                "parameters": {
                    "message": "node-parameter-secret",
                    "url": "https://internal.example/private",
                },
            }
        ],
    },
    "data": {
        "resultData": {
            "lastNodeExecuted": "Stop and fail",
            "error": {
                "name": "NodeApiError",
                "message": "Authorization failed",
                "description": (
                    "Upstream rejected request api_key=description-secret-value "
                    "for user@example.com at https://internal.example/private"
                ),
                "httpCode": "403",
                "lineNumber": "17",
                "node": {
                    "name": "Stop and fail",
                    "type": "n8n-nodes-base.stopAndError",
                    "parameters": {
                        "message": "node-parameter-secret",
                        "url": "https://internal.example/private",
                    },
                },
                "stack": "private stack trace",
            },
            "runData": {
                "Stop and fail": [
                    {
                        "data": {
                            "main": [
                                [{"json": {"order_id": 1, "customer": "raw-input-item"}}],
                                [{"json": {"order_id": 2}}],
                            ]
                        }
                    },
                    {"data": {"main": [[{"json": {"order_id": 3}}]]}},
                ]
            },
        }
    },
}

DIAGNOSIS_PAYLOAD = {
    "summary": "Upstream authorization failed at the Stop and fail node.",
    "probable_root_cause": "The upstream credential was rejected with HTTP 403.",
    "affected_component": "Stop and fail",
    "recommended_fix": "Verify the upstream credential's scopes in a safe environment.",
    "confidence": 0.7,
    "confidence_basis": (
        "Observed NodeApiError with HTTP 403 and a Stop and Error node type; "
        "no raw request body or credential scope metadata was supplied."
    ),
    "risk_level": "high",
    "hypotheses": [
        {
            "rank": 1,
            "explanation": "The upstream service rejected the configured credential.",
            "supporting_evidence": ["NodeApiError", "HTTP status 403"],
            "contradictory_evidence": "No credential scope metadata was supplied.",
            "verification_step": (
                "Re-run the request with a known-good credential and compare the HTTP status."
            ),
        }
    ],
    "recommendations": [
        {
            "action": "Check the upstream credential's scopes and expiry.",
            "verification": "A successful request or a non-403 status confirms the hypothesis.",
        }
    ],
}


def rich_execution() -> dict:
    return copy.deepcopy(RICH_EXECUTION)


def test_normalize_extracts_and_sanitizes_evidence():
    failure = normalize(Execution.model_validate(rich_execution()))

    evidence = failure.evidence
    assert evidence is not None
    assert evidence.http_code == 403
    assert evidence.line_number == 17
    assert evidence.node_type == "n8n-nodes-base.stopAndError"
    assert evidence.item_count == 2
    assert evidence.output_branch_count == 2
    assert evidence.node_run_count == 2
    assert evidence.item_shape == "object"
    assert evidence.error_description is not None
    assert "description-secret-value" not in evidence.error_description
    assert "user@example.com" not in evidence.error_description
    assert "internal.example" not in evidence.error_description
    assert "[REDACTED]" in evidence.error_description

    dumped = failure.model_dump_json()
    for forbidden in (
        "raw-input-item",
        "node-parameter-secret",
        "private stack trace",
        "workflow-credential-should-not-leak",
        "runData",
    ):
        assert forbidden not in dumped


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        {"resultData": None},
        {"resultData": {"error": "not-a-dict"}},
        {"resultData": {"error": {"node": "not-a-dict", "httpCode": object()}}},
        {"resultData": {"error": {"httpCode": "not-a-number", "lineNumber": -2}}},
        {"resultData": {"runData": {"Stop and fail": "not-a-list"}}},
        {"resultData": {"runData": {"Stop and fail": [{"data": {"main": "not-a-list"}}]}}},
        {"resultData": {"runData": {"Stop and fail": [{"data": {"main": ["bad-branch"]}}]}}},
        {"resultData": {"lastNodeExecuted": {"unexpected": True}}},
        {"resultData": {"lastNodeExecuted": ["unexpected-list"]}},
        {"resultData": {"error": {"node": {"name": {"unexpected": True}}}}},
    ],
)
def test_malformed_execution_structures_do_not_crash(data):
    failure = normalize(Execution(id="exec-bad", workflowId="wf", status="error", data=data))

    assert failure.error_type
    assert failure.error_message
    if failure.evidence is not None:
        assert failure.evidence.http_code is None
        assert failure.evidence.line_number is None


def test_evidence_is_persisted_to_sqlite_already_sanitized():
    failure = normalize(Execution.model_validate(rich_execution()))
    engine, factory = create_database("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with factory() as session:
            incident, created = IncidentRepository(session).create_once(failure)
            assert created
            session.commit()
            incident_id = incident.id

        with factory() as session:
            stored = session.get(Incident, incident_id)
            assert stored is not None
            assert stored.evidence == failure.evidence.model_dump()
            persisted = json.dumps(stored.evidence)
            for forbidden in (
                "description-secret-value",
                "user@example.com",
                "internal.example",
                "raw-input-item",
                "node-parameter-secret",
                "private stack trace",
                "workflow-credential-should-not-leak",
                "runData",
            ):
                assert forbidden not in persisted
            assert stored.evidence["http_code"] == 403
            assert stored.evidence["node_type"] == "n8n-nodes-base.stopAndError"
    finally:
        engine.dispose()


def test_provider_prompt_contains_evidence_and_excludes_forbidden_payloads():
    failure = normalize(Execution.model_validate(rich_execution()))
    captured: dict[str, bytes] = {}

    def handler(request):
        captured["content"] = request.content
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(DIAGNOSIS_PAYLOAD)}}]}
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = CompatibleAIProvider(
                Settings(
                    _env_file=None,
                    ai_api_key="ai-secret",
                    n8n_base_url="https://n8n.example",
                    n8n_api_key="test-n8n-key",
                ),
                client,
            )
            return await provider.diagnose(failure)

    diagnosis = asyncio.run(run())
    assert diagnosis.hypotheses[0].rank == 1

    content = captured["content"]
    request_payload = json.loads(content)
    prompt = json.loads(request_payload["messages"][1]["content"])

    assert prompt["evidence"]["http_code"] == 403
    assert prompt["evidence"]["line_number"] == 17
    assert prompt["evidence"]["node_type"] == "n8n-nodes-base.stopAndError"
    assert prompt["evidence"]["item_count"] == 2
    assert prompt["evidence"]["output_branch_count"] == 2
    assert prompt["evidence"]["node_run_count"] == 2
    assert prompt["evidence"]["item_shape"] == "object"
    assert "[REDACTED]" in prompt["evidence"]["error_description"]
    assert prompt["failure"]["error_message"] == "Authorization failed"

    for forbidden in (
        b"description-secret-value",
        b"user@example.com",
        b"internal.example",
        b"raw-input-item",
        b"node-parameter-secret",
        b"private stack trace",
        b"workflow-credential-should-not-leak",
        b"test-n8n-key",
        b"ai-secret",
        b"runData",
        b"parameters",
    ):
        assert forbidden not in content


@pytest.mark.parametrize("field", ["confidence_basis", "hypotheses", "recommendations"])
def test_diagnosis_rejects_missing_required_field(field):
    data = copy.deepcopy(DIAGNOSIS_PAYLOAD)
    data.pop(field)

    with pytest.raises(ValidationError):
        Diagnosis.model_validate(data)


def test_diagnosis_rejects_empty_hypotheses_and_recommendations():
    data = copy.deepcopy(DIAGNOSIS_PAYLOAD)
    data["hypotheses"] = []
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(data)

    data = copy.deepcopy(DIAGNOSIS_PAYLOAD)
    data["recommendations"] = []
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(data)


def test_hypothesis_requires_supporting_evidence_and_verification():
    data = copy.deepcopy(DIAGNOSIS_PAYLOAD)
    data["hypotheses"][0]["supporting_evidence"] = []
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(data)

    data = copy.deepcopy(DIAGNOSIS_PAYLOAD)
    data["hypotheses"][0]["verification_step"] = ""
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(data)


def test_recommendation_requires_action_and_verification():
    data = copy.deepcopy(DIAGNOSIS_PAYLOAD)
    data["recommendations"][0]["action"] = ""
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(data)

    data = copy.deepcopy(DIAGNOSIS_PAYLOAD)
    data["recommendations"][0]["verification"] = ""
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(data)


def test_hypotheses_are_sorted_by_rank_and_duplicate_ranks_rejected():
    second = copy.deepcopy(DIAGNOSIS_PAYLOAD["hypotheses"][0])
    second["rank"] = 2
    second["explanation"] = "A secondary explanation with weaker support."
    data = copy.deepcopy(DIAGNOSIS_PAYLOAD)
    data["hypotheses"] = [second, DIAGNOSIS_PAYLOAD["hypotheses"][0]]

    diagnosis = Diagnosis.model_validate(data)
    assert [hypothesis.rank for hypothesis in diagnosis.hypotheses] == [1, 2]

    duplicate = copy.deepcopy(second)
    duplicate["rank"] = 1
    data["hypotheses"] = [DIAGNOSIS_PAYLOAD["hypotheses"][0], duplicate]
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(data)


def test_mock_provider_uses_evidence_and_returns_actionable_diagnosis():
    failure = normalize(Execution.model_validate(rich_execution()))
    diagnosis = asyncio.run(MockDiagnosisProvider().diagnose(failure))

    assert diagnosis.confidence_basis
    assert "403" in diagnosis.confidence_basis or "NodeApiError" in diagnosis.confidence_basis
    assert diagnosis.hypotheses
    assert diagnosis.hypotheses[0].rank == 1
    assert diagnosis.hypotheses[0].supporting_evidence
    assert diagnosis.hypotheses[0].verification_step
    assert diagnosis.recommendations
    assert diagnosis.recommendations[0].action
    assert diagnosis.recommendations[0].verification
    assert diagnosis.recommended_fix


def test_legacy_persisted_diagnosis_remains_readable():
    legacy = {
        "summary": "Legacy summary.",
        "probable_root_cause": "Legacy root cause.",
        "affected_component": "Fetch orders",
        "recommended_fix": "Review the failing node.",
        "confidence": 0.5,
        "risk_level": "medium",
    }
    incident = IncidentOut.model_validate(
        {
            "id": "incident-legacy",
            "workflow_id": "wf-1",
            "workflow_name": "Order import",
            "execution_id": "exec-1",
            "failed_node": "Fetch orders",
            "error_type": "NodeApiError",
            "error_message": "401 Unauthorized",
            "execution_timestamp": None,
            "severity": "medium",
            "status": "diagnosed",
            "diagnosis_provider": "mock",
            "diagnosis": legacy,
        }
    )

    assert incident.diagnosis is not None
    assert incident.diagnosis.confidence_basis
    assert incident.diagnosis.hypotheses[0].rank == 1
    assert incident.diagnosis.recommendations[0].action == "Review the failing node."


def test_migration_adds_incident_evidence_column(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'evidence.db'}"
    upgrade_database(database_url)

    engine = create_engine(database_url)
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("incidents")}
    finally:
        engine.dispose()
    assert "evidence" in columns


def test_end_to_end_sync_and_diagnose_uses_sanitized_evidence():
    captured: dict[str, bytes] = {}

    def n8n_handler(request):
        if request.url.path.endswith("/executions"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "exec-100",
                            "workflowId": "wf-orders",
                            "status": "error",
                            "startedAt": "2026-11-07T09:30:00Z",
                        }
                    ],
                    "nextCursor": None,
                },
            )
        if request.url.path.endswith("/executions/exec-100"):
            return httpx.Response(200, json=rich_execution())
        return httpx.Response(404)

    def ai_handler(request):
        captured["content"] = request.content
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(DIAGNOSIS_PAYLOAD)}}]}
        )

    settings = Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
        n8n_base_url="https://n8n.example",
        n8n_api_key="test-n8n-key",
        ai_api_key="ai-secret",
    )
    with TestClient(
        create_app(
            settings,
            n8n_transport=httpx.MockTransport(n8n_handler),
            ai_transport=httpx.MockTransport(ai_handler),
        )
    ) as client:
        assert client.post("/api/v1/incidents/sync").json()["created"] == 1
        incident = client.get("/api/v1/incidents").json()["data"][0]
        assert incident["evidence"]["http_code"] == 403
        assert incident["evidence"]["line_number"] == 17
        assert incident["evidence"]["node_type"] == "n8n-nodes-base.stopAndError"
        assert incident["evidence"]["item_count"] == 2
        result = client.post(f"/api/v1/incidents/{incident['id']}/diagnose")

    assert result.status_code == 200
    diagnosis = result.json()["diagnosis"]
    assert diagnosis["confidence_basis"] == DIAGNOSIS_PAYLOAD["confidence_basis"]
    assert diagnosis["hypotheses"][0]["rank"] == 1
    assert diagnosis["hypotheses"][0]["supporting_evidence"]
    assert diagnosis["recommendations"][0]["verification"]

    content = captured["content"]
    prompt = json.loads(json.loads(content)["messages"][1]["content"])
    assert prompt["evidence"]["http_code"] == 403
    assert prompt["evidence"]["node_type"] == "n8n-nodes-base.stopAndError"
    assert prompt["evidence"]["item_count"] == 2
    for forbidden in (
        b"description-secret-value",
        b"user@example.com",
        b"raw-input-item",
        b"node-parameter-secret",
        b"private stack trace",
        b"workflow-credential-should-not-leak",
        b"test-n8n-key",
        b"runData",
        b"parameters",
    ):
        assert forbidden not in content
