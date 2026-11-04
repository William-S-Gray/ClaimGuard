"""Claim schemas — mirror the frontend `Claim` contract (camelCase)."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import field_serializer

from app.schemas.common import CamelModel, StrId
from app.schemas.member import MemberOut
from app.schemas.provider import ProviderOut

if TYPE_CHECKING:
    from app.models.claim import Claim


class ClaimItemOut(CamelModel):
    id: StrId
    description: str
    quantity: int
    unit_price: float
    total: float
    icd10_code: str | None = None
    nappi_code: str | None = None


def mask_name(name: str) -> str:
    """'Tendai Moyo' -> 'T. M.' for list views; the audited detail view has the full name."""
    return " ".join(f"{part[0]}." for part in name.split() if part) or "Member"


class MemberRef(CamelModel):
    """Just enough to tell rows apart. No contact details, conditions or birth date."""

    id: StrId
    name: str  # masked
    member_number: str
    plan: str


class ShapContributionOut(CamelModel):
    feature: str
    contribution: float
    direction: str


class TimelineEventOut(CamelModel):
    id: StrId
    timestamp: datetime
    event: str
    description: str | None = None
    actor: str | None = None
    type: str


class ClaimSummary(CamelModel):
    """Lightweight row for list/queue views."""

    id: str
    claim_ref: str
    nh263_ref: str | None = None
    member: MemberRef
    provider: ProviderOut
    service_date: str | None = None
    submitted_at: datetime
    claimed_amount: float
    member_shortfall: float
    risk_score: int
    risk_level: str
    decision: str
    priority: str
    flags: list[str] = []
    latency_ms: int
    ai_explanation: str | None = None
    # Member verification state (no personal data): drives MemberGuard counts
    # and the queue's "Disputed" filter.
    member_notification_sent: bool = False
    member_notification_channel: str | None = None
    member_response: str = "PENDING"


class ClaimOut(ClaimSummary):
    """Full claim detail (each view is audited, so the full member is fine here)."""

    member: MemberOut  # type: ignore[assignment]
    member_id: str
    provider_id: str
    approved_amount: float | None = None
    expected_shortfall: list[float] = []
    items: list[ClaimItemOut] = []
    shap_contributions: list[ShapContributionOut] = []
    auto_approve_at: datetime | None = None
    agent_notes: str | None = None
    sla_deadline: datetime | None = None
    timeline: list[TimelineEventOut] = []

    @field_serializer("expected_shortfall")
    def _ser_shortfall(self, v: list[float]) -> list[float]:
        return v


class ClaimIngestItem(CamelModel):
    description: str
    quantity: int = 1
    unit_price: float
    total: float
    icd10_code: str | None = None
    nappi_code: str | None = None


class ClaimIngest(CamelModel):
    """Incoming claim payload (e.g. from the NH263 feed) to be scored on arrival."""

    claim_ref: str | None = None  # optional; enables idempotent re-delivery
    nh263_ref: str | None = None
    member_number: str
    provider_code: str
    service_date: str | None = None
    claimed_amount: float
    member_shortfall: float
    expected_shortfall_min: float
    expected_shortfall_max: float
    items: list[ClaimIngestItem] = []
    # FraudShield input signals — source-provided; the rest are derived at ingest.
    prescription_date: str | None = None
    has_biometric: bool | None = None
    syndicate_signal: bool | None = None


def claim_to_summary(claim: Claim) -> dict:
    return ClaimSummary(
        id=str(claim.id),
        claim_ref=claim.claim_ref,
        nh263_ref=claim.nh263_ref,
        member=MemberRef(
            id=claim.member.id,
            name=mask_name(claim.member.name),
            member_number=claim.member.member_number,
            plan=claim.member.plan,
        ),
        provider=ProviderOut.model_validate(claim.provider),
        service_date=claim.service_date,
        submitted_at=claim.submitted_at,
        claimed_amount=float(claim.claimed_amount),
        member_shortfall=float(claim.member_shortfall),
        risk_score=claim.risk_score,
        risk_level=claim.risk_level,
        decision=claim.decision,
        priority=claim.priority,
        flags=[f.code for f in claim.flags],
        latency_ms=claim.latency_ms,
        ai_explanation=claim.ai_explanation,
        member_notification_sent=claim.member_notification_sent,
        member_notification_channel=claim.member_notification_channel,
        member_response=claim.member_response,
    ).model_dump(by_alias=True)


def claim_to_detail(claim: Claim) -> dict:
    return ClaimOut(
        id=str(claim.id),
        claim_ref=claim.claim_ref,
        nh263_ref=claim.nh263_ref,
        member=MemberOut.model_validate(claim.member),
        provider=ProviderOut.model_validate(claim.provider),
        service_date=claim.service_date,
        submitted_at=claim.submitted_at,
        claimed_amount=float(claim.claimed_amount),
        member_shortfall=float(claim.member_shortfall),
        risk_score=claim.risk_score,
        risk_level=claim.risk_level,
        decision=claim.decision,
        priority=claim.priority,
        flags=[f.code for f in claim.flags],
        latency_ms=claim.latency_ms,
        member_id=str(claim.member_id),
        provider_id=str(claim.provider_id),
        approved_amount=float(claim.approved_amount)
        if claim.approved_amount is not None
        else None,
        expected_shortfall=[
            float(claim.expected_shortfall_min),
            float(claim.expected_shortfall_max),
        ],
        items=[ClaimItemOut.model_validate(i) for i in claim.items],
        shap_contributions=[
            ShapContributionOut(
                feature=s.feature,
                contribution=float(s.contribution),
                direction=s.direction,
            )
            for s in claim.shap_contributions
        ],
        ai_explanation=claim.ai_explanation,
        auto_approve_at=claim.auto_approve_at,
        member_notification_sent=claim.member_notification_sent,
        member_notification_channel=claim.member_notification_channel,
        member_response=claim.member_response,
        agent_notes=claim.agent_notes,
        sla_deadline=claim.sla_deadline,
        timeline=[TimelineEventOut.model_validate(t) for t in claim.timeline],
    ).model_dump(by_alias=True)
