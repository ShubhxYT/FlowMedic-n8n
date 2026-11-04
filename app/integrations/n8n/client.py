from datetime import datetime
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import Settings
from app.core.errors import ServiceError


class Execution(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    workflowId: str
    status: str | None = None
    startedAt: datetime | None = None
    stoppedAt: datetime | None = None
    data: dict | None = None
    workflowData: dict | None = None


class ExecutionPage(BaseModel):
    data: list[Execution]
    nextCursor: str | None = None


class N8nWorkflow(BaseModel):
    id: str
    name: str
    active: bool


class N8nWorkflowPage(BaseModel):
    data: list[N8nWorkflow]
    nextCursor: str | None = Field(default=None)


def is_failed(execution: Execution) -> bool:
    if execution.status is not None:
        return execution.status in {"error", "crashed"}
    result = (execution.data or {}).get("resultData")
    return isinstance(result, dict) and isinstance(result.get("error"), dict)


class N8nClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client

    async def _get(self, path, schema, params=None):
        if not self.settings.n8n_base_url or not self.settings.n8n_api_key.get_secret_value():
            raise ServiceError("n8n_not_configured", "Configure N8N_BASE_URL and N8N_API_KEY", 503)
        base = self.settings.n8n_base_url
        if not base.endswith("/api/v1"):
            base += "/api/v1"
        try:
            response = await self.client.get(
                base + path,
                params=params,
                headers={"X-N8N-API-KEY": self.settings.n8n_api_key.get_secret_value()},
                timeout=self.settings.request_timeout,
            )
            if response.status_code in {401, 403}:
                raise ServiceError("n8n_auth_failed", "n8n rejected the configured credentials")
            response.raise_for_status()
            return schema.model_validate(response.json())
        except httpx.TimeoutException:
            raise ServiceError("n8n_timeout", "n8n request timed out", 504) from None
        except httpx.RequestError:
            raise ServiceError("n8n_unreachable", "Unable to reach n8n", 503) from None
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise ServiceError(
                    "n8n_rate_limited", "n8n rate limited the request", 503
                ) from None
            if exc.response.status_code >= 500:
                raise ServiceError(
                    "n8n_server_error", "n8n service is temporarily unavailable", 503
                ) from None
            raise ServiceError("n8n_http_error", "n8n returned an unsuccessful response") from None
        except (ValueError, ValidationError):
            raise ServiceError("n8n_malformed", "n8n returned an invalid response") from None

    async def workflows(self, limit=50, cursor=None):
        return await self._get("/workflows", N8nWorkflowPage, self._params(limit, cursor))

    async def connectivity(self):
        await self.workflows(limit=1)
        return {"connected": True}

    @staticmethod
    def _params(limit, cursor):
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return params

    async def recent_executions(self, limit=50, cursor=None):
        params = self._params(limit, cursor)
        params["includeData"] = "false"
        return await self._get("/executions", ExecutionPage, params)

    async def execution_details(self, execution_id):
        return await self._get(
            "/executions/" + quote(execution_id, safe=""), Execution, {"includeData": "true"}
        )

    async def failed_executions(self, limit=50, cursor=None):
        page = await self.recent_executions(limit, cursor)
        failures = []
        for item in page.data:
            if is_failed(item) or item.status is None:
                detail = await self.execution_details(item.id)
                if is_failed(detail):
                    failures.append(detail)
        return failures, page.nextCursor, len(page.data)
