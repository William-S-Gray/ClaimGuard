"""Claims & investigation-queue endpoints."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.core.dependencies import (
    CurrentUserDep,
    DbSession,
    PaginationDep,
    client_ip,
    require_permission,
)
from app.core.responses import paginated, success
from app.schemas.claim import ClaimIngest
from app.services.audit import AuditService
from app.services.claims import ClaimService

router = APIRouter(prefix="/claims", tags=["claims"])


class RejectPayload(BaseModel):
    reason: str = "REJECT_FRAUD"


class NotePayload(BaseModel):
    note: str


class InfoRequestPayload(BaseModel):
    message: str = Field(..., min_length=5, max_length=1000)


class MemberAlertPayload(BaseModel):
    channel: str = "WHATSAPP"  # SMS | WHATSAPP | USSD


class MemberResponsePayload(BaseModel):
    response: str  # CONFIRMED | DISPUTED


@router.get("", summary="Paginated investigation queue", response_model=None)
async def list_claims(
    db: DbSession,
    pagination: PaginationDep,
    search: str | None = Query(None),
    priority: str | None = Query(None),
    status: str | None = Query(None),
    risk_level: str | None = Query(None, alias="riskLevel"),
    investigator: uuid.UUID | None = Query(None, description="Assigned user id"),
    sort: Literal["risk", "amount", "newest"] = Query("risk"),
) -> dict:
    items, total = await ClaimService(db).queue(
        search=search,
        priority=priority,
        status=status,
        risk_level=risk_level,
        investigator=investigator,
        sort=sort,
        page=pagination.page,
        page_size=pagination.page_size,
    )
    return paginated(items, pagination.page, pagination.page_size, total, "Queue loaded")


@router.get("/live-feed", summary="Most recent scored claims")
async def live_feed(db: DbSession, limit: int = Query(5, ge=1, le=50)) -> dict:
    return success(await ClaimService(db).live_feed(limit), "Live feed")


@router.post(
    "/ingest",
    dependencies=[Depends(require_permission("claim:approve"))],
    summary="Ingest and score a new claim (NH263)")
async def ingest(
    payload: ClaimIngest, db: DbSession, user: CurrentUserDep, request: Request
) -> dict:
    data = await ClaimService(db).ingest(payload, actor=user.id)
    await AuditService(db).record(
        action="claim.ingest",
        entity_type="claim",
        entity_id=data["claimRef"],
        actor_id=user.id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request),
    )
    return success(data, "Claim ingested")


@router.get("/{claim_ref}", summary="Full claim detail", response_model=None)
async def get_claim(
    claim_ref: str, db: DbSession, user: CurrentUserDep, request: Request
) -> dict:
    data = await ClaimService(db).get_detail(claim_ref)
    await AuditService(db).record_view(
        entity_type="claim", entity_id=claim_ref, actor_id=user.id, request=request
    )
    return success(data, "Claim detail")


@router.post(
    "/{claim_ref}/approve",
    dependencies=[Depends(require_permission("claim:approve"))],
    summary="Approve a claim")
async def approve(
    claim_ref: str, db: DbSession, user: CurrentUserDep, request: Request
) -> dict:
    data = await ClaimService(db).approve(claim_ref, actor=user.id)
    await AuditService(db).record(
        action="claim.approve",
        entity_type="claim",
        entity_id=claim_ref,
        actor_id=user.id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request),
    )
    return success(data, "Claim approved")


@router.post(
    "/{claim_ref}/reject",
    dependencies=[Depends(require_permission("claim:reject"))],
    summary="Reject a claim")
async def reject(
    claim_ref: str,
    payload: RejectPayload,
    db: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> dict:
    data = await ClaimService(db).reject(claim_ref, payload.reason, actor=user.id)
    await AuditService(db).record(
        action="claim.reject",
        entity_type="claim",
        entity_id=claim_ref,
        actor_id=user.id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request),
        changes={"reason": payload.reason},
    )
    return success(data, "Claim rejected")


@router.post(
    "/{claim_ref}/notes",
    dependencies=[Depends(require_permission("claim:approve"))],
    summary="Attach an agent note")
async def add_note(
    claim_ref: str, payload: NotePayload, db: DbSession, user: CurrentUserDep
) -> dict:
    return success(
        await ClaimService(db).add_note(claim_ref, payload.note, actor=user.id),
        "Note added",
    )


@router.post(
    "/{claim_ref}/request-info",
    dependencies=[Depends(require_permission("claim:approve"))],
    summary="Send the claim back to the provider for more information")
async def request_info(
    claim_ref: str,
    payload: InfoRequestPayload,
    db: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> dict:
    data = await ClaimService(db).request_info(claim_ref, payload.message.strip(), actor=user.id)
    await AuditService(db).record(
        action="claim.request_info",
        entity_type="claim",
        entity_id=claim_ref,
        actor_id=user.id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request),
    )
    return success(data, "Information requested")


@router.post(
    "/{claim_ref}/member-alert",
    dependencies=[Depends(require_permission("claim:approve"))],
    summary="Send a MemberGuard verification alert")
async def member_alert(
    claim_ref: str,
    payload: MemberAlertPayload,
    db: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> dict:
    result = await ClaimService(db).send_member_alert(
        claim_ref, payload.channel, actor=user.id
    )
    await AuditService(db).record(
        action="claim.member_alert",
        entity_type="claim",
        entity_id=claim_ref,
        actor_id=user.id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request),
        changes={
            "channel": result["alert"]["channel"],
            "delivered": result["alert"]["delivered"],
        },
    )
    return success(result, "Alert sent")


@router.post(
    "/{claim_ref}/member-response",
    dependencies=[Depends(require_permission("claim:approve"))],
    summary="Record a member's confirmation reply")
async def member_response(
    claim_ref: str,
    payload: MemberResponsePayload,
    db: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> dict:
    data = await ClaimService(db).record_member_response(claim_ref, payload.response)
    await AuditService(db).record(
        action="claim.member_response",
        entity_type="claim",
        entity_id=claim_ref,
        actor_id=user.id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request),
        changes={"response": data["memberResponse"], "via": "STAFF"},
    )
    return success(data, "Response recorded")


@router.post(
    "/{claim_ref}/rescore",
    dependencies=[Depends(require_permission("claim:approve"))],
    summary="Re-run FraudShield scoring")
async def rescore(claim_ref: str, db: DbSession, user: CurrentUserDep) -> dict:
    return success(await ClaimService(db).rescore(claim_ref, actor=user.id), "Rescored")
