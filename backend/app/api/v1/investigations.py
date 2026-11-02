"""Investigation workflow endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import CurrentUserDep, DbSession, PaginationDep, require_permission
from app.core.responses import paginated, success
from app.repositories.user import UserRepository
from app.schemas.investigation import (
    CommentCreate,
    InvestigationCreate,
    InvestigationUpdate,
)
from app.services.investigations import InvestigationService

router = APIRouter(prefix="/investigations", tags=["investigations"])


@router.get("", summary="Paginated investigation cases")
async def list_cases(
    db: DbSession,
    pagination: PaginationDep,
    _: CurrentUserDep,
    status: str | None = Query(None),
) -> dict:
    items, total = await InvestigationService(db).list_page(
        status=status, page=pagination.page, page_size=pagination.page_size
    )
    return paginated(items, pagination.page, pagination.page_size, total, "Investigations")


@router.post(
    "",
    dependencies=[Depends(require_permission("investigation:manage"))],
    summary="Open an investigation for a claim")
async def open_case(
    payload: InvestigationCreate, db: DbSession, user: CurrentUserDep
) -> dict:
    data = await InvestigationService(db).open_case(
        claim_ref=payload.claim_id,
        priority=payload.priority,
        assigned_to=payload.assigned_to,
        actor=user.id,
    )
    return success(data, "Investigation opened")


@router.get("/{investigation_id}", summary="Investigation detail")
async def get_case(investigation_id: str, db: DbSession, _: CurrentUserDep) -> dict:
    return success(await InvestigationService(db).get(investigation_id), "Investigation")


@router.patch(
    "/{investigation_id}",
    dependencies=[Depends(require_permission("investigation:manage"))],
    summary="Update an investigation")
async def update_case(
    investigation_id: str,
    payload: InvestigationUpdate,
    db: DbSession,
    user: CurrentUserDep,
) -> dict:
    data = await InvestigationService(db).update(
        investigation_id, actor=user.id, **payload.model_dump(exclude_none=True)
    )
    return success(data, "Investigation updated")


@router.post(
    "/{investigation_id}/comments",
    dependencies=[Depends(require_permission("investigation:manage"))],
    summary="Add a case comment")
async def add_comment(
    investigation_id: str,
    payload: CommentCreate,
    db: DbSession,
    user: CurrentUserDep,
) -> dict:
    db_user = await UserRepository(db).get(user.id)
    author_name = db_user.full_name if db_user else "Agent"
    data = await InvestigationService(db).add_comment(
        investigation_id, body=payload.body, author_id=user.id, author_name=author_name
    )
    return success(data, "Comment added")
