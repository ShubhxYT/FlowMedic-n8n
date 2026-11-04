import copy

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def execution():
    return {
        "id": "42",
        "workflowId": "wf-1",
        "status": "error",
        "startedAt": "2026-09-09T08:00:00Z",
        "workflowData": {"name": "Order import", "credentials": {"token": "private-value"}},
        "data": {
            "resultData": {
                "lastNodeExecuted": "Fetch orders",
                "error": {
                    "name": "NodeApiError",
                    "message": "401 Unauthorized api_key=private-value",
                    "node": {"name": "Fetch orders"},
                    "stack": "private stack",
                },
                "runData": {"Fetch orders": [{"data": "sensitive customer payload"}]},
            }
        },
    }


@pytest.fixture
def client(execution):
    def handler(request):
        assert request.headers["X-N8N-API-KEY"] == "test-n8n-key"
        if request.url.path.endswith("/workflows"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "wf-1",
                            "name": "Order import",
                            "active": True,
                            "nodes": ["private"],
                        },
                    ],
                    "nextCursor": "workflow-next",
                },
            )
        if request.url.path.endswith("/executions"):
            summary = {k: v for k, v in execution.items() if k not in {"data", "workflowData"}}
            return httpx.Response(200, json={"data": [summary], "nextCursor": "next-page"})
        if request.url.path.endswith("/executions/42"):
            return httpx.Response(200, json=copy.deepcopy(execution))
        return httpx.Response(404)

    settings = Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
        n8n_base_url="https://n8n.example",
        n8n_api_key="test-n8n-key",
    )
    with TestClient(create_app(settings, n8n_transport=httpx.MockTransport(handler))) as result:
        yield result
