"""TrustScore endpoints (provider reputation)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.core.dependencies import (
    CurrentUserDep,
    DbSession,
    PaginationDep,
    client_ip,
    require_permission,
    require_roles,
)
from app.core.responses import paginated, success
from app.services.audit import AuditService
from app.services.provider_portal import AppealReviewService
from app.services.providers import ProviderService

router = APIRouter(prefix="/trustscore", tags=["trustscore"])


@router.get("", summary="Ranked providers with TrustScore")
async def trustscore_table(db: DbSession, pagination: PaginationDep) -> dict:
    items, total = await ProviderService(db).list_page(
        pagination.page, pagination.page_size
    )
    return paginated(items, pagination.page, pagination.page_size, total, "TrustScore")


@router.get("/summary", summary="Badge distribution summary")
async def summary(db: DbSession) -> dict:
    return success(await ProviderService(db).summary(), "TrustScore summary")


class AppealDecision(BaseModel):
    outcome: str  # UPHELD | REJECTED
    outcome_notes: str = Field(..., alias="outcomeNotes", min_length=1, max_length=2000)

    model_config = {"populate_by_name": True}


@router.get("/appeals", summary="Provider TrustScore appeals (staff review)")
async def list_appeals(db: DbSession, status: str | None = Query(None)) -> dict:
    return success(await AppealReviewService(db).list(status), "Appeals")


@router.patch(
    "/appeals/{appeal_id}",
    summary="Resolve a provider appeal",
    dependencies=[Depends(require_permission("provider:manage"))],
)
async def resolve_appeal(
    appeal_id: str,
    payload: AppealDecision,
    db: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> dict:
    data = await AppealReviewService(db).resolve(
        appeal_id, payload.outcome, payload.outcome_notes, actor=user.id
    )
    await AuditService(db).record(
        action="provider.appeal.resolve",
        entity_type="provider_appeal",
        entity_id=appeal_id,
        actor_id=user.id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request),
        changes={"outcome": payload.outcome},
    )
    return success(data, "Appeal resolved")


@router.get("/{code}/history", summary="Score trend, peer percentile and confidence")
async def history(code: str, db: DbSession) -> dict:
    return success(await ProviderService(db).history(code), "TrustScore history")


@router.post(
    "/{code}/recalculate",
    summary="Recompute a provider's TrustScore (admin/analyst)",
    dependencies=[Depends(require_roles("admin", "analyst"))],
)
async def recalculate(code: str, db: DbSession, _: CurrentUserDep) -> dict:
    return success(await ProviderService(db).recalculate(code), "TrustScore recalculated")
