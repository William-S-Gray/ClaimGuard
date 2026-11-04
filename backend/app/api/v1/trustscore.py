"""TrustScore endpoints (provider reputation)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.dependencies import CurrentUserDep, DbSession, PaginationDep, require_roles
from app.core.responses import paginated, success
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
