"""Provider portal: one provider's own TrustScore, claims and appeals.

Every method takes the caller's provider id (from their token) and only ever
queries that provider. Members appear as initials; fraud scores and model
explanations stay internal so the scored party can't tune claims against them.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError, NotFoundError
from app.models.claim import Claim
from app.models.investigation import Investigation
from app.models.provider import Provider, ProviderAppeal, TrustScoreSnapshot
from app.modules.trustscore.service import confidence
from app.repositories.claim import ClaimRepository
from app.repositories.provider import ProviderRepository
from app.schemas.claim import mask_name
from app.services.claims import latest_info_request
from app.services.providers import ProviderService

APPEAL_OUTCOMES = {"UPHELD", "REJECTED"}
# Closed cases where the claim turned out fine: they count for the provider.
_LEGITIMATE = {"FALSE_POSITIVE", "NO_ACTION"}


def claim_status(claim: Claim) -> str:
    if claim.decision == "INFO_NEEDED":  # needs the provider's action: show first
        return "INFO_NEEDED"
    if claim.member_response == "DISPUTED":
        return "DISPUTED"
    if claim.decision == "APPROVE":
        return "APPROVED"
    if claim.decision.startswith("REJECT"):
        return "REJECTED"
    return "UNDER_REVIEW"


def portal_claim(c: Claim) -> dict:
    """A claim as its provider sees it: member initials, no scores or model output."""
    status = claim_status(c)
    dispute = next((f.detail for f in c.flags if f.code == "MEMBER_DISPUTED"), None)
    request = latest_info_request(c) if status == "INFO_NEEDED" else None
    return {
        "claimRef": c.claim_ref,
        "submittedAt": c.submitted_at,
        "serviceDate": c.service_date,
        "amount": float(c.claimed_amount),
        "service": c.items[0].description if c.items else None,
        "member": mask_name(c.member.name),
        "status": status,
        "disputeReason": dispute if c.member_response == "DISPUTED" else None,
        "infoRequest": request.description if request else None,
    }


def appeal_out(a: ProviderAppeal) -> dict:
    return {
        "id": str(a.id),
        "status": a.status,
        "reason": a.reason,
        "scoreAtSubmission": a.score_at_submission,
        "outcomeNotes": a.outcome_notes,
        "submittedAt": a.created_at,
        "resolvedAt": a.resolved_at,
    }


class ProviderPortalService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _provider(self, provider_id: str) -> Provider:
        provider = await ProviderRepository(self.session).get(uuid.UUID(provider_id))
        if not provider:
            raise NotFoundError("Provider not found")
        return provider

    async def _claims_since(self, provider_id: uuid.UUID, days: int) -> list[Claim]:
        since = datetime.now(UTC) - timedelta(days=days)
        stmt = (
            select(Claim)
            .where(
                Claim.provider_id == provider_id,
                Claim.deleted_at.is_(None),
                # Anything waiting on the provider shows however old it is.
                or_(Claim.submitted_at >= since, Claim.decision == "INFO_NEEDED"),
            )
            .order_by(Claim.submitted_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().unique().all())

    async def dashboard(self, provider_id: str) -> dict:
        provider = await self._provider(provider_id)
        trend = await ProviderService(self.session).history(provider.code)

        # Change vs. a month ago: the latest snapshot at least 30 days old.
        month_ago = datetime.now(UTC) - timedelta(days=30)
        old = (
            await self.session.execute(
                select(TrustScoreSnapshot.score)
                .where(
                    TrustScoreSnapshot.provider_id == provider.id,
                    TrustScoreSnapshot.created_at <= month_ago,
                )
                .order_by(TrustScoreSnapshot.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        change = provider.trust_score - old if old is not None else None

        resolutions = (
            await self.session.execute(
                select(Investigation.resolution)
                .join(Claim, Investigation.claim_id == Claim.id)
                .where(
                    Claim.provider_id == provider.id,
                    Investigation.resolved_at.is_not(None),
                    Investigation.deleted_at.is_(None),
                )
            )
        ).scalars().all()
        legit = sum(r in _LEGITIMATE for r in resolutions)
        rating_avg, ratings = await ClaimRepository(self.session).rating_stats(provider.id)

        others = [
            p for p in await ProviderRepository(self.session).all_ranked()
            if p.id != provider.id
        ]
        regional = [p for p in others if p.city == provider.city]
        peers, region = (regional, provider.city) if regional else (others, "all regions")
        scores = [p.trust_score for p in peers]

        return {
            "provider": {
                "code": provider.code,
                "name": provider.name,
                "type": provider.type,
                "city": provider.city,
            },
            "trustScore": provider.trust_score,
            "badge": provider.badge,
            "changeFromLastMonth": change,  # None until a month of history exists
            "breakdown": {
                "claimAccuracy": round(100 - float(provider.dispute_rate), 1),
                "investigationSuccess": (
                    round(100 * legit / len(resolutions), 1) if resolutions else None
                ),
                "investigationsClosed": len(resolutions),
                "memberSatisfaction": rating_avg,  # 1-5, None until rated
                "memberRatings": ratings,
                "shortfallIndex": float(provider.shortfall_index),
                "flags90d": provider.flags_90d,
                "totalClaims": provider.total_claims,
                "confidence": confidence(provider.total_claims),
            },
            "peers": {
                "region": region,
                "count": len(scores),
                "average": round(sum(scores) / len(scores), 1) if scores else None,
                # Share of peers in the region scoring lower than this provider.
                "percentile": (
                    round(100 * sum(s < provider.trust_score for s in scores) / len(scores))
                    if scores
                    else None
                ),
            },
            "history": trend["history"],
        }

    async def recent_claims(self, provider_id: str, days: int = 30) -> list[dict]:
        provider = await self._provider(provider_id)
        return [portal_claim(c) for c in await self._claims_since(provider.id, days)]

    async def report_csv(self, provider_id: str, days: int) -> str:
        rows = await self.recent_claims(provider_id, days)
        buf = io.StringIO()
        fields = [
            "claimRef", "submittedAt", "serviceDate", "amount",
            "service", "member", "status", "disputeReason", "infoRequest",
        ]
        writer = csv.DictWriter(buf, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        return buf.getvalue()

    # ── Appeals ───────────────────────────────────────────────────────────────
    async def appeals(self, provider_id: str) -> list[dict]:
        rows = (
            await self.session.execute(
                select(ProviderAppeal)
                .where(
                    ProviderAppeal.provider_id == uuid.UUID(provider_id),
                    ProviderAppeal.deleted_at.is_(None),
                )
                .order_by(ProviderAppeal.created_at.desc())
            )
        ).scalars().all()
        return [appeal_out(a) for a in rows]

    async def submit_appeal(self, provider_id: str, user_id: str, reason: str) -> dict:
        provider = await self._provider(provider_id)
        open_ = (
            await self.session.execute(
                select(ProviderAppeal.id).where(
                    ProviderAppeal.provider_id == provider.id,
                    ProviderAppeal.status == "OPEN",
                    ProviderAppeal.deleted_at.is_(None),
                )
            )
        ).first()
        if open_:
            raise BusinessRuleError("You already have an open appeal")
        appeal = ProviderAppeal(
            provider_id=provider.id,
            submitted_by=uuid.UUID(user_id),
            score_at_submission=provider.trust_score,
            reason=reason,
            status="OPEN",
        )
        self.session.add(appeal)
        await self.session.flush()
        return appeal_out(appeal)


class AppealReviewService:
    """Staff side of the appeals workflow."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(self, status: str | None) -> list[dict]:
        stmt = (
            select(ProviderAppeal, Provider.code, Provider.name)
            .join(Provider, ProviderAppeal.provider_id == Provider.id)
            .where(ProviderAppeal.deleted_at.is_(None))
            .order_by(ProviderAppeal.created_at.desc())
        )
        if status:
            stmt = stmt.where(ProviderAppeal.status == status)
        return [
            {**appeal_out(a), "providerCode": code, "providerName": name}
            for a, code, name in (await self.session.execute(stmt)).all()
        ]

    async def resolve(self, appeal_id: str, outcome: str, notes: str, actor: str) -> dict:
        if outcome not in APPEAL_OUTCOMES:
            raise BusinessRuleError(f"Outcome must be one of {sorted(APPEAL_OUTCOMES)}")
        try:
            appeal = await self.session.get(ProviderAppeal, uuid.UUID(appeal_id))
        except ValueError:
            appeal = None
        if not appeal or appeal.deleted_at is not None:
            raise NotFoundError("Appeal not found")
        if appeal.status != "OPEN":
            raise BusinessRuleError("Appeal is already resolved")
        appeal.status = outcome
        appeal.outcome_notes = notes
        appeal.resolved_at = datetime.now(UTC)
        appeal.updated_by = actor
        await self.session.flush()
        return appeal_out(appeal)
