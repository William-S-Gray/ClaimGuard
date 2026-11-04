"""Provider portal endpoints. Every route is scoped to the caller's own provider."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.core.dependencies import DbSession, ProviderUserDep, client_ip, get_provider_user
from app.core.responses import success
from app.services.audit import AuditService
from app.services.claims import ClaimService
from app.services.provider_portal import ProviderPortalService, portal_claim

router = APIRouter(
    prefix="/portal/provider",
    tags=["provider-portal"],
    dependencies=[Depends(get_provider_user)],
)


class AppealPayload(BaseModel):
    reason: str = Field(..., min_length=10, max_length=2000)


class InfoPayload(BaseModel):
    response: str = Field(..., min_length=5, max_length=2000)


@router.get("/dashboard", summary="Own TrustScore, breakdown, peers and trend")
async def dashboard(db: DbSession, user: ProviderUserDep) -> dict:
    return success(await ProviderPortalService(db).dashboard(user.provider_id), "Dashboard")


@router.get("/claims", summary="Own recent claims (members masked)")
async def claims(
    db: DbSession, user: ProviderUserDep, days: int = Query(30, ge=1, le=365)
) -> dict:
    return success(
        await ProviderPortalService(db).recent_claims(user.provider_id, days), "Claims"
    )


@router.post("/claims/{claim_ref}/info", summary="Answer an information request")
async def supply_info(
    claim_ref: str, payload: InfoPayload, db: DbSession, user: ProviderUserDep, request: Request
) -> dict:
    claim = await ClaimService(db).supply_info(
        claim_ref, user.provider_id, payload.response.strip(), actor=user.id
    )
    await AuditService(db).record(
        action="claim.supply_info",
        entity_type="claim",
        entity_id=claim_ref,
        actor_id=user.id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request),
    )
    return success(portal_claim(claim), "Information sent")


@router.get("/report.csv", summary="Download own claims as CSV")
async def report(
    db: DbSession, user: ProviderUserDep, days: int = Query(90, ge=1, le=365)
) -> Response:
    body = await ProviderPortalService(db).report_csv(user.provider_id, days)
    return Response(
        body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="claims-{days}d.csv"'},
    )


@router.get("/appeals", summary="Own TrustScore appeals and outcomes")
async def appeals(db: DbSession, user: ProviderUserDep) -> dict:
    return success(await ProviderPortalService(db).appeals(user.provider_id), "Appeals")


@router.post("/appeals", summary="Appeal (dispute) the current TrustScore")
async def submit_appeal(
    payload: AppealPayload, db: DbSession, user: ProviderUserDep, request: Request
) -> dict:
    data = await ProviderPortalService(db).submit_appeal(
        user.provider_id, user.id, payload.reason
    )
    await AuditService(db).record(
        action="provider.appeal.submit",
        entity_type="provider_appeal",
        entity_id=data["id"],
        actor_id=user.id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request),
        changes={"scoreAtSubmission": data["scoreAtSubmission"]},
    )
    return success(data, "Appeal submitted")
