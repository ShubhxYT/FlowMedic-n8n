import asyncio
import json

import httpx
import pytest
from sqlalchemy import select

from app.ai.providers import CompatibleAIProvider
from app.core.config import Settings
from app.core.database import create_database
from app.core.errors import ServiceError
from app.core.security import sanitize
from app.integrations.n8n.client import Execution
from app.models.incident import Base, Incident
from app.repositories.monitoring import MonitoringRepository
from app.schemas.domain import Failure
from app.services.monitoring import MonitoringService

ISSUE_MESSAGE = (
    "Upstream rejected request for user@example.com using "
    "sk-live-ABC123SECRETKEY at https://internal.corp.local/api?token=abc123"
)
JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ."
    "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)
HEX_TOKEN = "0123456789abcdef0123456789abcdef01234567"
BASE64_TOKEN_32 = "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"
BASE64_TOKEN_31 = "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p"
# Built at runtime so push-protection scanners do not flag this synthetic fixture.
SLACK_TOKEN = "xoxb-" + "NOTAREALTOKEN1234567890"

DIAGNOSIS = {
    "summary": "Synthetic diagnosis for a sanitization test.",
    "probable_root_cause": "The upstream request was rejected.",
    "affected_component": "Fetch orders",
    "recommended_fix": "Review the failing node manually.",
    "confidence": 0.5,
    "risk_level": "medium",
}


def _capture_prompt(context: Failure) -> str:
    captured: dict[str, bytes] = {}

    def handler(request):
        captured["content"] = request.content
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(DIAGNOSIS)}}]},
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = CompatibleAIProvider(
                Settings(_env_file=None, ai_api_key="ai-secret"), client
            )
            await provider.diagnose(context)

    asyncio.run(run())
    payload = json.loads(captured["content"])
    return payload["messages"][1]["content"]


def test_issue_reproduction_redacts_email_token_and_url():
    assert sanitize(ISSUE_MESSAGE) == (
        "Upstream rejected request for [EMAIL REDACTED] using [TOKEN REDACTED] at [URL REDACTED]"
    )


def test_existing_key_value_redaction_is_independent_of_url_redaction():
    assert sanitize("token=abc123") == "token=[REDACTED]"


def test_jwt_is_redacted_as_a_single_token():
    text = sanitize(f"Provider rejected JWT {JWT} during validation")

    assert text == "Provider rejected JWT [TOKEN REDACTED] during validation"
    assert text.count("[TOKEN REDACTED]") == 1


def test_standalone_40_character_hex_token_is_redacted():
    text = sanitize(f"Digest {HEX_TOKEN} did not match")

    assert text == "Digest [TOKEN REDACTED] did not match"


def test_basic_authorization_credential_is_redacted():
    text = sanitize("Authorization: Basic dXNlcjpwYXNzd29yZA==")

    assert text == "Authorization: [REDACTED]"
    assert "dXNlcjpwYXNzd29yZA==" not in text


@pytest.mark.parametrize(
    "token",
    [
        "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6",
        SLACK_TOKEN,
        "AKIAIOSFODNN7EXAMPLE",
        BASE64_TOKEN_32,
    ],
)
def test_common_bare_token_shapes_are_redacted(token):
    text = sanitize(f"Upstream error included token {token} and was rejected")

    assert token not in text
    assert "[TOKEN REDACTED]" in text
    assert "Upstream error included token" in text
    assert "and was rejected" in text


@pytest.mark.parametrize(
    "message",
    [
        "({token})",
        "[{token}]",
        '"{token}",',
        "prefix:{token};suffix",
    ],
)
def test_tokens_are_redacted_next_to_punctuation(message):
    token = "sk-live-ABC123SECRETKEY"
    text = sanitize(message.replace("{token}", token))

    assert token not in text
    assert "[TOKEN REDACTED]" in text


@pytest.mark.parametrize("prefix", ["cache_", "KEY_", "x-"])
def test_prefixed_tokens_adjacent_to_identifier_characters_are_redacted(prefix):
    token = f"{prefix}sk-live-ABC123SECRETKEY"
    text = sanitize(token)

    assert token not in text
    assert "[TOKEN REDACTED]" in text


@pytest.mark.parametrize("suffix", ["+TAIL", "/TAIL", "=TAIL"])
def test_prefixed_token_tails_are_fully_redacted(suffix):
    token = f"sk-live-ABC123{suffix}"
    text = sanitize(f"value {token} end")

    assert token not in text
    assert text == "value [TOKEN REDACTED] end"


@pytest.mark.parametrize(
    "message",
    [
        "Deployment completed successfully after 3 retries",
        "Error code deadbeef with id 12345",
        "Reference 12345678901234567890123456789012",
        "WorkflowExecutionHistoryRepositoryV2 failed",
        "Request took 2026-11-07T12:34:56.789Z and returned HTTP 200",
        "The quick brown fox jumps over the lazy dog",
        BASE64_TOKEN_31,
    ],
)
def test_ordinary_messages_are_not_redacted(message):
    assert sanitize(message) == message


def test_existing_email_url_and_assignment_redaction_still_works():
    text = sanitize('api_key="abc def" token=xyz person@example.com https://user:pass@host/?x=1')

    for secret in ("abc", "xyz", "person@example.com", "user:pass"):
        assert secret not in text
    assert "api_key=" in text
    assert "[EMAIL REDACTED]" in text
    assert "[URL REDACTED]" in text


def test_sanitize_is_idempotent():
    once = sanitize(ISSUE_MESSAGE)
    assert sanitize(once) == once


def test_monitoring_ingestion_sanitizes_all_incident_text_fields(execution):
    secret = "sk-live-ABC123SECRETKEY"
    execution["workflowData"]["name"] = f"Workflow {secret}"
    execution["data"]["resultData"]["error"]["name"] = f"NodeApiError {secret}"
    execution["data"]["resultData"]["error"]["node"]["name"] = f"Fetch orders {secret}"
    execution["data"]["resultData"]["error"]["message"] = ISSUE_MESSAGE
    detail = Execution.model_validate(execution)

    settings = Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
        n8n_base_url="https://n8n.example",
        n8n_api_key="key",
    )
    engine, factory = create_database(settings.database_url)
    Base.metadata.create_all(engine)
    try:
        service = MonitoringService(settings, factory, n8n=None)
        service._persist_entries([detail])

        with factory() as session:
            incident = session.scalar(select(Incident))
            assert incident is not None
            for value in (
                incident.workflow_name,
                incident.failed_node,
                incident.error_type,
                incident.error_message,
            ):
                assert value is not None
                assert secret not in value
                assert "user@example.com" not in value
            assert "[TOKEN REDACTED]" in incident.error_message
            assert "[EMAIL REDACTED]" in incident.error_message
            assert "[URL REDACTED]" in incident.error_message
    finally:
        engine.dispose()


def test_monitoring_failure_summary_is_sanitized():
    settings = Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
        n8n_base_url="https://n8n.example",
        n8n_api_key="key",
    )
    engine, factory = create_database(settings.database_url)
    Base.metadata.create_all(engine)
    try:
        service = MonitoringService(settings, factory, n8n=None)
        service._record_failure(ServiceError("n8n_error", ISSUE_MESSAGE, 502))

        with factory() as session:
            checkpoint = MonitoringRepository(session).existing_checkpoint("n8n", "default")
            assert checkpoint is not None
            assert checkpoint.last_error_summary is not None
            assert "sk-live-ABC123SECRETKEY" not in checkpoint.last_error_summary
            assert "[TOKEN REDACTED]" in checkpoint.last_error_summary
    finally:
        engine.dispose()


def test_ai_provider_prompt_never_contains_original_secret():
    secret = "sk-live-ABC123SECRETKEY"
    original = f"Upstream rejected {secret} for user@example.com"
    context = Failure(
        workflow_id="wf-1",
        workflow_name="Order import",
        execution_id="42",
        failed_node="Fetch orders",
        error_type="NodeApiError",
        error_message=original,
    )

    content = _capture_prompt(context)

    assert secret not in content
    assert "[TOKEN REDACTED]" in content
    assert "[EMAIL REDACTED]" in content
    assert context.error_message == original


def test_ai_provider_preserves_missing_failed_node():
    context = Failure(
        workflow_id="wf-1",
        workflow_name="Order import",
        execution_id="42",
        failed_node=None,
        error_type="NodeApiError",
        error_message="Request timed out",
    )

    content = _capture_prompt(context)

    assert '"failed_node":null' in content
    assert context.failed_node is None
