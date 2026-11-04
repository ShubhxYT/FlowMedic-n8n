import asyncio

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import ServiceError
from app.integrations.n8n.client import Execution, N8nClient, is_failed


def run_client(handler, method="connectivity"):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = N8nClient(
                Settings(
                    _env_file=None, n8n_base_url="https://n8n.example/api/v1", n8n_api_key="key"
                ),
                http,
            )
            return await getattr(client, method)()

    return asyncio.run(run())


@pytest.mark.parametrize(
    "status,expected",
    [
        ("error", True),
        ("crashed", True),
        ("success", False),
        ("running", False),
        ("waiting", False),
        ("canceled", False),
        ("new", False),
    ],
)
def test_failure_detection(status, expected):
    assert is_failed(Execution(id="1", workflowId="w", status=status)) is expected


def test_legacy_failure_requires_error():
    assert not is_failed(Execution(id="1", workflowId="w"))
    assert is_failed(Execution(id="1", workflowId="w", data={"resultData": {"error": {}}}))


@pytest.mark.parametrize(
    "status,body,code",
    [
        (401, {}, "n8n_auth_failed"),
        (403, {}, "n8n_auth_failed"),
        (429, {}, "n8n_rate_limited"),
        (500, {}, "n8n_server_error"),
        (200, {"data": "wrong"}, "n8n_malformed"),
        (200, {"unexpected": []}, "n8n_malformed"),
        (302, {}, "n8n_http_error"),
    ],
)
def test_invalid_responses(status, body, code):
    with pytest.raises(ServiceError) as exc:
        run_client(lambda request: httpx.Response(status, json=body))
    assert exc.value.code == code


@pytest.mark.parametrize(
    "exception,code",
    [
        (httpx.ConnectError, "n8n_unreachable"),
        (httpx.ReadTimeout, "n8n_timeout"),
    ],
)
def test_network_failures(exception, code):
    def handler(request):
        raise exception("private error", request=request)

    with pytest.raises(ServiceError) as exc:
        run_client(handler)
    assert exc.value.code == code
    assert "private" not in str(exc.value)


def test_non_json():
    with pytest.raises(ServiceError, match="invalid response"):
        run_client(lambda r: httpx.Response(200, text="not JSON"))


def test_detail_fetch_rechecks_status_and_preserves_cursor(execution):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/executions"):
            assert request.url.params["includeData"] == "false"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "42", "workflowId": "wf-1"},
                        {"id": "43", "workflowId": "wf-1", "status": "success"},
                    ],
                    "nextCursor": "cursor-2",
                },
            )
        return httpx.Response(200, json=execution)

    failures, cursor, scanned = run_client(handler, "failed_executions")
    assert [item.id for item in failures] == ["42"]
    assert cursor == "cursor-2" and scanned == 2
    assert paths == ["/api/v1/executions", "/api/v1/executions/42"]
