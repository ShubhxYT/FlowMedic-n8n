from datetime import UTC

from app.core.security import sanitize
from app.integrations.n8n.client import Execution, is_failed
from app.schemas.domain import Failure

# Connection-level failures mean the workflow could not reach its dependency at all.
HIGH_SEVERITY_MARKERS = (
    "enotfound",
    "econnrefused",
    "econnaborted",
    "econnreset",
    "etimedout",
    "timeout",
    "timed out",
    "dns",
    "connection cannot be established",
    "connection was aborted",
    "socket hang up",
)
HIGH_SEVERITY_STATUS_CODES = {401, 403, 408, 429}


def _http_code(error: dict) -> int | None:
    value = error.get("httpCode")
    try:
        return int(value)  # n8n reports this as a string for NodeApiError
    except (TypeError, ValueError):
        return None


def _severity(execution: Execution, error: dict) -> str:
    """Classify severity from the failure itself, not only the execution status.

    A crashed execution is always high. Authentication failures, rate limits and
    upstream 5xx responses are high. Connection/DNS failures are high because the
    workflow could not reach its dependency at all. Everything else is medium.
    """
    if execution.status == "crashed":
        return "high"
    code = _http_code(error)
    if code is not None and (code in HIGH_SEVERITY_STATUS_CODES or code >= 500):
        return "high"
    text = " ".join(
        str(error.get(field) or "") for field in ("description", "message", "name")
    ).lower()
    if any(marker in text for marker in HIGH_SEVERITY_MARKERS):
        return "high"
    return "medium"


def normalize(execution: Execution, secrets=()) -> Failure:
    if not is_failed(execution):
        raise ValueError("Only failed executions can become incidents")
    result = (execution.data or {}).get("resultData")
    result = result if isinstance(result, dict) else {}
    error = result.get("error")
    error = error if isinstance(error, dict) else {}
    node = error.get("node")
    node = node.get("name") if isinstance(node, dict) else None
    node = node or result.get("lastNodeExecuted")
    # Never retain runData, node inputs/outputs, credentials, or the source error object.
    return Failure(
        execution_id=execution.id,
        workflow_id=execution.workflowId,
        workflow_name=sanitize((execution.workflowData or {}).get("name") or "Unknown", secrets),
        failed_node=sanitize(node, secrets) if isinstance(node, str) else None,
        # n8n reports the error class in `description` for Code-node failures and
        # in `name` for node-level errors; prefer whichever is present.
        error_type=sanitize(
            error.get("name") or error.get("description") or "UnknownError", secrets
        ),
        error_message=sanitize(error.get("message") or "Insufficient failure information", secrets),
        execution_timestamp=(
            execution.startedAt.replace(tzinfo=UTC)
            if execution.startedAt and execution.startedAt.tzinfo is None
            else execution.startedAt.astimezone(UTC)
            if execution.startedAt
            else None
        ),
        severity=_severity(execution, error),
    )
