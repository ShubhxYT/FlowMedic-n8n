import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.integrations.n8n.client import N8nClient
from app.main import create_app


def test_cursor_forwarded():
    def handler(request):
        assert request.url.params["cursor"] == "a+b/c=="
        assert request.url.params["limit"] == "7"
        return httpx.Response(200, json={"data": [], "nextCursor": None})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            n8n = N8nClient(
                Settings(_env_file=None, n8n_base_url="https://example.com", n8n_api_key="key"),
                client,
            )
            page = await n8n.recent_executions(7, "a+b/c==")
            assert page.data == [] and page.nextCursor is None

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["timeout", "malformed", "http_error"])
def test_ai_failure_does_not_mark_incident_diagnosed(execution, failure):
    def n8n_handler(request):
        if request.url.path.endswith("/executions"):
            return httpx.Response(200, json={"data": [execution]})
        return httpx.Response(200, json=execution)

    def ai_handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("secret stack", request=request)
        if failure == "http_error":
            return httpx.Response(401, text="secret stack")
        return httpx.Response(200, json={"choices": []})

    settings = Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
        n8n_base_url="https://example.com",
        n8n_api_key="key",
        ai_api_key="ai-key",
    )
    app = create_app(
        settings,
        n8n_transport=httpx.MockTransport(n8n_handler),
        ai_transport=httpx.MockTransport(ai_handler),
    )
    with TestClient(app) as client:
        assert client.post("/api/v1/incidents/sync").status_code == 200
        incident_id = client.get("/api/v1/incidents").json()["data"][0]["id"]
        path = "/api/v1/incidents/" + incident_id
        response = client.post(path + "/diagnose")
        assert response.status_code == (504 if failure == "timeout" else 502)
        assert "secret" not in response.text
        incident = client.get(path).json()
        assert incident["status"] == "open" and incident["diagnosis"] is None
