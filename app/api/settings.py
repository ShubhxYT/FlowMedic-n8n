from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request

from app.api.dependencies import WorkspaceContext, get_workspace_context, require_admin
from app.core.errors import ServiceError
from app.schemas.settings import (
    CandidateTest,
    ConnectionCreate,
    ConnectionOut,
    ConnectionPage,
    ConnectionPatch,
    ConnectionTestResult,
    SavedTest,
)

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])
Member = Annotated[WorkspaceContext, Depends(get_workspace_context)]
Admin = Annotated[WorkspaceContext, Depends(require_admin)]


def deletion_revision(if_match: str | None) -> int:
    if if_match is None:
        raise ServiceError("revision_required", "A connection revision is required", 428)
    try:
        value = int(if_match.strip('"'))
        if value < 1:
            raise ValueError
        return value
    except ValueError:
        raise ServiceError("invalid_revision", "Invalid connection revision", 422) from None


@router.get("/n8n-instances", response_model=ConnectionPage)
def list_n8n(request: Request, context: Member):
    return request.app.state.connections.list(context, "n8n")


@router.post("/n8n-instances/test", response_model=ConnectionTestResult)
async def test_n8n_candidate(request: Request, context: Admin, payload: CandidateTest):
    return await request.app.state.connections.test_candidate(context, "n8n", payload)


@router.post("/n8n-instances", response_model=ConnectionOut, status_code=201)
async def create_n8n(request: Request, context: Admin, payload: ConnectionCreate):
    return await request.app.state.connections.create(context, "n8n", payload)


@router.patch("/n8n-instances/{connection_id}", response_model=ConnectionOut)
async def patch_n8n(
    connection_id: UUID, request: Request, context: Admin, payload: ConnectionPatch
):
    return await request.app.state.connections.patch(context, "n8n", str(connection_id), payload)


@router.delete("/n8n-instances/{connection_id}")
async def delete_n8n(
    connection_id: UUID,
    request: Request,
    context: Admin,
    if_match: Annotated[str | None, Header()] = None,
):
    await request.app.state.connections.delete(
        context, "n8n", str(connection_id), deletion_revision(if_match)
    )
    return {"deleted": True}


@router.post("/n8n-instances/{connection_id}/test", response_model=ConnectionTestResult)
async def test_n8n_saved(
    connection_id: UUID, request: Request, context: Admin, payload: SavedTest
):
    return await request.app.state.connections.test_saved(
        context, "n8n", str(connection_id), payload
    )


@router.get("/ai-providers", response_model=ConnectionPage)
def list_ai(request: Request, context: Member):
    return request.app.state.connections.list(context, "ai")


@router.post("/ai-providers/test", response_model=ConnectionTestResult)
async def test_ai_candidate(request: Request, context: Admin, payload: CandidateTest):
    return await request.app.state.connections.test_candidate(context, "ai", payload)


@router.post("/ai-providers", response_model=ConnectionOut, status_code=201)
async def create_ai(request: Request, context: Admin, payload: ConnectionCreate):
    return await request.app.state.connections.create(context, "ai", payload)


@router.patch("/ai-providers/{connection_id}", response_model=ConnectionOut)
async def patch_ai(
    connection_id: UUID, request: Request, context: Admin, payload: ConnectionPatch
):
    return await request.app.state.connections.patch(context, "ai", str(connection_id), payload)


@router.delete("/ai-providers/{connection_id}")
async def delete_ai(
    connection_id: UUID,
    request: Request,
    context: Admin,
    if_match: Annotated[str | None, Header()] = None,
):
    await request.app.state.connections.delete(
        context, "ai", str(connection_id), deletion_revision(if_match)
    )
    return {"deleted": True}


@router.post("/ai-providers/{connection_id}/test", response_model=ConnectionTestResult)
async def test_ai_saved(
    connection_id: UUID, request: Request, context: Admin, payload: SavedTest
):
    return await request.app.state.connections.test_saved(
        context, "ai", str(connection_id), payload
    )
