from datetime import UTC

from app.core.security import sanitize
from app.integrations.n8n.client import Execution, is_failed
from app.schemas.domain import DiagnosisEvidence, Failure

MAX_ITEM_COUNT = 100_000
MAX_BRANCH_COUNT = 1_000
MAX_RUN_COUNT = 1_000


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _safe_int(value: object, *, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            value = int(text, 10)
        except ValueError:
            return None
    if not isinstance(value, int):
        return None
    if value < minimum or value > maximum:
        return None
    return value


def _safe_text(value: object, secrets=()) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return sanitize(value, secrets)


def _item_shape(items: list) -> str:
    if not items:
        return "empty"
    shapes = set()
    for item in items:
        payload = item.get("json") if isinstance(item, dict) and "json" in item else item
        if isinstance(payload, dict):
            shapes.add("object")
        elif isinstance(payload, list):
            shapes.add("array")
        else:
            shapes.add("scalar")
    if len(shapes) == 1:
        return shapes.pop()
    return "mixed"


def _item_metadata(run_data: object, node_name: str | None) -> dict:
    """Derive bounded item metadata from runData without retaining any item content.

    Only counts and the shape of the JSON payload are kept. The item bodies are read
    transiently to classify their type and are never persisted or transmitted.
    """

    if not isinstance(run_data, dict) or not node_name:
        return {}
    runs = run_data.get(node_name)
    if not isinstance(runs, list):
        return {}
    runs = [run for run in runs[:MAX_RUN_COUNT] if isinstance(run, dict)]
    if not runs:
        return {}

    main: list | None = None
    for run in runs:
        data = run.get("data")
        if isinstance(data, dict) and isinstance(data.get("main"), list):
            main = data["main"]
            break

    metadata: dict = {
        "node_run_count": min(len(runs), MAX_RUN_COUNT),
    }
    if main is None:
        return metadata

    metadata["output_branch_count"] = min(len(main), MAX_BRANCH_COUNT)
    items: list = []
    for branch in main[:MAX_BRANCH_COUNT]:
        if not isinstance(branch, list):
            continue
        for item in branch:
            if len(items) >= MAX_ITEM_COUNT:
                break
            items.append(item)
        if len(items) >= MAX_ITEM_COUNT:
            break

    metadata["item_count"] = len(items)
    metadata["item_shape"] = _item_shape(items)
    return metadata


def extract_evidence(execution: Execution, secrets=()) -> DiagnosisEvidence:
    result = _mapping(execution.data).get("resultData")
    result = _mapping(result)
    error = _mapping(result.get("error"))
    node = _mapping(error.get("node"))
    node_name = node.get("name") if isinstance(node.get("name"), str) else None
    last_node = result.get("lastNodeExecuted")
    node_name = node_name or (last_node if isinstance(last_node, str) else None)

    metadata = _item_metadata(result.get("runData"), node_name)
    return DiagnosisEvidence(
        error_description=_safe_text(error.get("description"), secrets),
        http_code=_safe_int(error.get("httpCode"), minimum=100, maximum=599),
        line_number=_safe_int(error.get("lineNumber"), minimum=1, maximum=MAX_ITEM_COUNT),
        node_type=_safe_text(node.get("type"), secrets),
        **metadata,
    )


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
        evidence=extract_evidence(execution, secrets),
    )
