"""Provider / TrustScore application service."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.websocket import WSEventType, publish
from app.models.provider import TrustScoreSnapshot
from app.modules.trustscore import trustscore_service
from app.modules.trustscore.service import confidence, inputs_from_claims
from app.repositories.claim import ClaimRepository
from app.repositories.provider import ProviderRepository
from app.schemas.claim import claim_to_summary
from app.schemas.provider import ProviderOut, TrustScorePoint, TrustScoreSummary
from app.services.notifications import NotificationService


class ProviderService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ProviderRepository(session)
        self.claims = ClaimRepository(session)

    async def list_page(self, page: int, page_size: int) -> tuple[list[dict], int]:
        offset = (max(page, 1) - 1) * page_size
        rows, total = await self.repo.list_ranked(offset=offset, limit=page_size)
        return [ProviderOut.model_validate(p).model_dump(by_alias=True) for p in rows], total

    async def get_by_code(self, code: str) -> dict:
        provider = await self.repo.get_by_code(code)
        if not provider:
            raise NotFoundError(f"Provider {code} not found")
        return ProviderOut.model_validate(provider).model_dump(by_alias=True)

    async def claims_for(self, code: str) -> list[dict]:
        provider = await self.repo.get_by_code(code)
        if not provider:
            raise NotFoundError(f"Provider {code} not found")
        rows = await self.claims.list_by_provider(provider.id)
        return [claim_to_summary(c) for c in rows]

    async def summary(self) -> dict:
        providers = await self.repo.all_ranked()
        buckets = {"VERIFIED": 0, "STANDARD": 0, "CAUTION": 0, "REVIEW": 0, "WATCHLIST": 0}
        for p in providers:
            buckets[p.badge] = buckets.get(p.badge, 0) + 1
        return TrustScoreSummary(
            verified=buckets["VERIFIED"],
            standard=buckets["STANDARD"],
            caution=buckets["CAUTION"],
            review=buckets["REVIEW"],
            watchlist=buckets["WATCHLIST"],
            total=len(providers),
        ).model_dump(by_alias=True)

    async def recalculate(self, code: str, reason: str = "manual") -> dict:
        """Recompute a provider's TrustScore from its claims and snapshot it."""
        provider = await self.repo.get_by_code(code)
        if not provider:
            raise NotFoundError(f"Provider {code} not found")
        old_score = provider.trust_score
        # ponytail: loads every claim per recalc; aggregate in SQL past ~10k/provider.
        claims = await self.claims.list_by_provider(provider.id)
        inputs = inputs_from_claims(claims, datetime.now(UTC))
        score, badge = trustscore_service.recompute(inputs)

        provider.total_claims = inputs.total_claims
        provider.dispute_rate = inputs.dispute_rate
        provider.shortfall_index = inputs.shortfall_index
        provider.flags_90d = inputs.flags_90d
        if claims:
            provider.average_claim_value = round(
                sum(float(c.claimed_amount) for c in claims) / len(claims), 2
            )
        provider.trust_score = score
        provider.badge = badge
        self.session.add(
            TrustScoreSnapshot(
                provider_id=provider.id, score=score, badge=badge, reason=reason[:255]
            )
        )
        await self.session.flush()

        if score < old_score:  # only a drop is worth an alert
            trust_payload = {
                "provider": provider.name,
                "code": provider.code,
                "oldScore": old_score,
                "newScore": score,
            }
            publish(WSEventType.TRUSTSCORE_UPDATED, trust_payload)
            await NotificationService(self.session).create_from_event(
                "trustscore_updated", trust_payload
            )
        return ProviderOut.model_validate(provider).model_dump(by_alias=True)

    async def history(self, code: str, limit: int = 30) -> dict:
        """Score trend, peer percentile and confidence for one provider."""
        provider = await self.repo.get_by_code(code)
        if not provider:
            raise NotFoundError(f"Provider {code} not found")
        snapshots = (
            await self.session.execute(
                select(TrustScoreSnapshot)
                .where(TrustScoreSnapshot.provider_id == provider.id)
                .order_by(TrustScoreSnapshot.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        history = [
            TrustScorePoint.model_validate(s).model_dump(by_alias=True)
            for s in reversed(snapshots)  # oldest first, ready to chart
        ]
        peers = [p.trust_score for p in await self.repo.all_ranked() if p.id != provider.id]
        percentile = (
            round(100 * sum(s < provider.trust_score for s in peers) / len(peers))
            if peers
            else 100
        )
        return {
            "code": provider.code,
            "trustScore": provider.trust_score,
            "badge": provider.badge,
            "percentile": percentile,  # share of peers scoring lower
            "confidence": confidence(provider.total_claims),
            "change": history[-1]["score"] - history[0]["score"] if history else 0,
            "history": history,
        }
