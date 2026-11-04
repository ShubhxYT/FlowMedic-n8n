import asyncio

import httpx
import pytest
from pydantic import ValidationError

from app.ai.providers import CompatibleAIProvider, MockDiagnosisProvider
from app.core.config import Settings
from app.core.database import create_database
from app.core.errors import ServiceError
from app.core.security import sanitize
from app.integrations.n8n.client import Execution
from app.models.incident import Base
from app.repositories.incidents import IncidentRepository
from app.schemas.domain import Diagnosis
from app.services.incidents import normalize


@pytest.mark.parametrize(
    "values",
    [
        {"n8n_base_url": "ftp://host"},
        {"n8n_base_url": "https://user:password@host"},
        {"n8n_base_url": "https://host?token=x"},
        {"n8n_api_key": "key"},
        {"request_timeout": 0},
    ],
)
def test_invalid_configuration(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


@pytest.mark.parametrize(
    "values",
    [
        {"demo_mode": True, "n8n_base_url": "https://n8n.example", "n8n_api_key": "key"},
        {"demo_mode": True, "ai_api_key": "key"},
    ],
)
def test_demo_mode_rejects_live_provider_credentials(values):
    with pytest.raises(ValidationError, match="DEMO_MODE"):
        Settings(_env_file=None, **values)


def test_normalization_and_persistence(execution):
    failure = normalize(Execution.model_validate(execution))
    assert failure.failed_node == "Fetch orders"
    assert "private-value" not in failure.model_dump_json()
    engine, factory = create_database("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with factory() as session:
        incident, created = IncidentRepository(session).create_once(failure)
        assert created
        session.commit()
        incident_id = incident.id
    with factory() as session:
        assert IncidentRepository(session).get(incident_id).execution_id == "42"
        _, created = IncidentRepository(session).create_once(failure)
        assert not created
    engine.dispose()


def test_incomplete_crash_and_success_rejection():
    failure = normalize(Execution(id="1", workflowId="w", status="crashed"))
    assert failure.severity == "high"
    assert failure.execution_timestamp is None
    assert failure.failed_node is None
    diagnosis = asyncio.run(MockDiagnosisProvider().diagnose(failure))
    assert "Insufficient" in diagnosis.probable_root_cause
    assert diagnosis.confidence == 0.1
    with pytest.raises(ValueError):
        normalize(Execution(id="2", workflowId="w", status="success"))


def test_mock_is_deterministic(execution):
    context = normalize(Execution.model_validate(execution))
    provider = MockDiagnosisProvider()
    assert asyncio.run(provider.diagnose(context)) == asyncio.run(provider.diagnose(context))


def test_timestamp_normalizes_offset(execution):
    execution["startedAt"] = "2026-09-09T11:00:00+03:00"
    failure = normalize(Execution.model_validate(execution))
    assert failure.execution_timestamp.isoformat() == "2026-09-09T08:00:00+00:00"


@pytest.mark.parametrize(
    "field,value",
    [("confidence", 1.1), ("confidence", -0.1), ("risk_level", "certain"), ("summary", "")],
)
def test_diagnosis_validation(execution, field, value):
    context = normalize(Execution.model_validate(execution))
    data = asyncio.run(MockDiagnosisProvider().diagnose(context)).model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(data)


def test_sanitization():
    text = sanitize('api_key="abc def" token=xyz person@example.com https://user:pass@host/?x=1')
    for secret in ("abc", "xyz", "person@example.com", "user:pass"):
        assert secret not in text
    assert sanitize("Known secret-123", ("secret-123",)) == "Known [REDACTED]"


@pytest.mark.parametrize("valid", [True, False])
def test_real_provider_structured_response_and_safe_input(execution, valid):
    context = normalize(Execution.model_validate(execution))
    diagnosis = asyncio.run(MockDiagnosisProvider().diagnose(context))

    def handler(request):
        assert b"sensitive customer" not in request.content
        assert b"private-value" not in request.content
        assert b"json_schema" in request.content
        assert request.headers["Authorization"] == "Bearer ai-secret"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": diagnosis.model_dump_json()
                            if valid
                            else '{"confidence": 9}',
                        }
                    }
                ]
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = CompatibleAIProvider(
                Settings(_env_file=None, ai_api_key="ai-secret"), client
            )
            return await provider.diagnose(context)

    if valid:
        assert asyncio.run(run()) == diagnosis
    else:
        with pytest.raises(ServiceError, match="invalid output"):
            asyncio.run(run())
