from datetime import UTC, datetime
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

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


def is_failed(
    execution: Execution, canceled_is_failure: bool = False, waiting_threshold_hours: int = 24
) -> bool:
    """
    Improved status classification for Issue #16:
    - Treats unknown status with error payload as failed.
    - Handles explicit error/crashed states.
    - Adds age-based stuck detection for waiting/running executions.
    - Supports configurable canceled status.
    """
    status = execution.status
    result = (execution.data or {}).get("resultData", {})
    has_error_payload = isinstance(result, dict) and isinstance(result.get("error"), dict)

    # 1. If status is None, check error payload
    if status is None:
        return has_error_payload

    status_lower = str(status).lower()

    # 2. Explicit error or crashed states
    if status_lower in {"error", "crashed"}:
        return True

    # 3. Unknown/other statuses with an error payload count as failure
    if has_error_payload and status_lower != "success":
        return True

    # 4. Canceled execution handling (configurable)
    if status_lower == "canceled":
        return canceled_is_failure

    # 5. Age-based stuck detection for waiting or running executions
    if status_lower in {"waiting", "running"}:
        target_time = execution.startedAt
        if target_time:
            try:
                # Ensure timezone-aware comparison
                if target_time.tzinfo is None:
                    target_time = target_time.replace(tzinfo=UTC)

                age_hours = (datetime.now(UTC) - target_time).total_seconds() / 3600
                if age_hours > waiting_threshold_hours:
                    return True  # Surfaces as a stuck problem / failure
            except Exception:
                pass

    return False


class N8nClient:
    def __init__(self, snapshot, outbound, guard=None):
        self.snapshot = snapshot
        self.outbound = outbound
        self.guard = guard

    async def _get(self, path, schema, params=None):
        base = self.snapshot.base_url
        key = self.snapshot.api_key.get_secret_value()
        if not base or not key:
            raise ServiceError("n8n_not_configured", "This n8n connection is not configured", 503)
        if not base.endswith("/api/v1"):
            base += "/api/v1"
        try:
            response = await self.outbound.get(
                base,
                path,
                params=params,
                headers={"X-N8N-API-KEY": key},
                guard=self.guard,
            )
            if response.status_code in {401, 403}:
                raise ServiceError("n8n_auth_failed", "n8n rejected the configured credentials")
            response.raise_for_status()
            return schema.model_validate(response.json())
        except ServiceError as exc:
            if exc.code == "upstream_timeout":
                raise ServiceError("n8n_timeout", "n8n request timed out", 504) from None
            if exc.code == "upstream_unreachable":
                raise ServiceError("n8n_unreachable", "Unable to reach n8n", 503) from None
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise ServiceError(
                    "n8n_rate_limited", "n8n rate limited the request", 503
                ) from None
            if exc.response.status_code >= 500:
                raise ServiceError(
                    "n8n_server_error", "n8n service is temporarily unavailable", 503
                ) from None
            raise ServiceError(
                "n8n_http_error",
                "n8n returned an unsuccessful response",
                exc.response.status_code,
            ) from None
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

    async def recent_executions(self, limit=50, cursor=None, status=None):
        params = self._params(limit, cursor)
        params["includeData"] = "false"
        if status:
            params["status"] = status  # Server-side ?status= filter support added
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
