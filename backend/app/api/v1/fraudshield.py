"""FraudShield scoring endpoints (stateless scoring + claim rescore)."""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.core.dependencies import CurrentUserDep, DbSession, client_ip
from app.core.responses import success
from app.modules.fraudshield import ScoringContext
from app.modules.fraudshield.service import fraudshield_service
from app.services.audit import AuditService

router = APIRouter(prefix="/fraudshield", tags=["fraudshield"])


class ScoreRequest(BaseModel):
    claim_ref: str = Field("AD-HOC", alias="claimRef", max_length=64)
    claimed_amount: float = Field(..., alias="claimedAmount", gt=0)
    member_shortfall: float = Field(0, alias="memberShortfall", ge=0)
    expected_shortfall_min: float = Field(0, alias="expectedShortfallMin", ge=0)
    expected_shortfall_max: float = Field(0, alias="expectedShortfallMax", ge=0)
    provider_trust_score: int = Field(100, alias="providerTrustScore", ge=0, le=100)
    provider_flags_90d: int = Field(0, alias="providerFlags90d", ge=0)
    prescription_after_service: bool = Field(False, alias="prescriptionAfterService")
    has_biometric: bool = Field(True, alias="hasBiometric")
    chronic_drug_no_condition: bool = Field(False, alias="chronicDrugNoCondition")
    syndicate_signal: bool = Field(False, alias="syndicateSignal")

    model_config = {"populate_by_name": True}


@router.post("/score", summary="Score an ad-hoc claim payload (explainable)")
async def score(
    payload: ScoreRequest, db: DbSession, user: CurrentUserDep, request: Request
) -> dict:
    ctx = ScoringContext(
        claim_ref=payload.claim_ref,
        claimed_amount=payload.claimed_amount,
        member_shortfall=payload.member_shortfall,
        expected_shortfall_min=payload.expected_shortfall_min,
        expected_shortfall_max=payload.expected_shortfall_max,
        provider_trust_score=payload.provider_trust_score,
        provider_flags_90d=payload.provider_flags_90d,
        prescription_after_service=payload.prescription_after_service,
        has_biometric=payload.has_biometric,
        chronic_drug_no_condition=payload.chronic_drug_no_condition,
        syndicate_signal=payload.syndicate_signal,
    )
    result = fraudshield_service.score(ctx)
    await AuditService(db).record(
        action="fraudshield.score",
        entity_type="claim",
        entity_id=payload.claim_ref,
        actor_id=user.id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request),
        changes={
            "riskScore": result.risk_score,
            "riskLevel": result.risk_level,
            "decision": result.decision,
            "modelName": result.model_name,
        },
    )
    return success(
        {
            "riskScore": result.risk_score,
            "riskLevel": result.risk_level,
            "decision": result.decision,
            "priority": result.priority,
            "latencyMs": result.latency_ms,
            "modelName": result.model_name,
            "anomalyProbability": result.anomaly_probability,
            "flags": [
                {"code": f.code, "severity": f.severity, "detail": f.detail}
                for f in result.flags
            ],
            "shapContributions": [
                {
                    "feature": s.feature,
                    "contribution": s.contribution,
                    "direction": s.direction,
                }
                for s in result.shap
            ],
            "aiExplanation": result.explanation,
        },
        "Scored",
    )
