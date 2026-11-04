from datetime import UTC

from app.core.security import sanitize
from app.integrations.n8n.client import Execution, is_failed
from app.schemas.domain import Failure


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
        error_type=sanitize(error.get("name") or "UnknownError", secrets),
        error_message=sanitize(error.get("message") or "Insufficient failure information", secrets),
        execution_timestamp=(
            execution.startedAt.replace(tzinfo=UTC)
            if execution.startedAt and execution.startedAt.tzinfo is None
            else execution.startedAt.astimezone(UTC)
            if execution.startedAt
            else None
        ),
        severity="high" if execution.status == "crashed" else "medium",
    )
