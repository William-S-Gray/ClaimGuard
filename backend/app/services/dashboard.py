"""Dashboard & analytics service — live KPIs computed from claim data."""
from __future__ import annotations

import calendar
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.claim import Claim
from app.models.investigation import Investigation
from app.repositories.claim import ClaimRepository
from app.schemas.dashboard import DashboardMetrics, SavingsDataPoint, USSDStats


class DashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.claims = ClaimRepository(session)

    async def metrics(self) -> dict:
        today = self.claims.start_of_today()
        yesterday = today - timedelta(days=1)
        claims_today = await self.claims.count_since(today)
        claims_yesterday = await self.claims.count_since(yesterday) - claims_today
        flagged_today = await self.claims.count_flagged_since(today)
        pending = await self.claims.count_by_decision("PEND_INVESTIGATE")
        # Scoped to today like the card it sits under. It used to count every
        # approval ever, so "0 claims today" sat over "2 auto-approved".
        auto_approved = await self.claims.count_by_decision_since("APPROVE", today)
        # A dispute is recorded as the member's reply (and a flag), never as a
        # decision, so counting decision == "MEMBER_DISPUTED" always gave 0.
        disputed = await self.claims.count_member_alerts(response="DISPUTED")
        avg_latency = await self.claims.avg_latency()
        rating_avg, ratings = await self.claims.rating_stats()
        saved = await self.claims.sum_estimated_saved()

        total = max(claims_today, 1)
        detection_rate = round((flagged_today / total) * 100, 1)

        return DashboardMetrics(
            claims_today=claims_today,
            claims_yesterday=claims_yesterday,
            flagged_today=flagged_today,
            estimated_saved=round(saved, 2),
            member_alerts=disputed,
            member_rating_avg=rating_avg,
            member_ratings=ratings,
            pending_investigation=pending,
            auto_approved_today=auto_approved,
            avg_latency_ms=int(avg_latency),
            detection_rate=detection_rate,
        ).model_dump(by_alias=True)

    async def investigator(self) -> dict:
        """Investigator KPIs. Ratios are None until there is data behind them."""
        pending = await self.claims.count_by_decision("PEND_INVESTIGATE")

        closed = (
            await self.session.execute(
                select(
                    Investigation.created_at,
                    Investigation.resolved_at,
                    Investigation.resolution,
                )
                .where(
                    Investigation.resolved_at.is_not(None),
                    Investigation.deleted_at.is_(None),
                )
            )
        ).all()
        hours = [(r.resolved_at - r.created_at).total_seconds() / 3600 for r in closed]
        # Accuracy: of cases closed with a verdict, how many flags were real fraud.
        fraud = sum(r.resolution in ("CONFIRMED_FRAUD", "RECOVERED") for r in closed)
        false_pos = sum(r.resolution == "FALSE_POSITIVE" for r in closed)

        by_level = dict(
            (
                await self.session.execute(
                    select(Claim.risk_level, func.count())
                    .where(Claim.deleted_at.is_(None))
                    .group_by(Claim.risk_level)
                )
            ).all()
        )
        return {
            "pendingClaims": pending,
            "avgReviewHours": round(sum(hours) / len(hours), 1) if hours else None,
            "casesResolved": len(closed),
            "detectionAccuracy": (
                round(100 * fraud / (fraud + false_pos), 1) if fraud + false_pos else None
            ),
            "riskDistribution": {
                level: int(by_level.get(level, 0))
                for level in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
            },
        }

    async def savings_series(self) -> list[dict]:
        """Loss avoided per calendar month over the last 12 months, oldest first.

        Built from the claims themselves, so the running total ends at the
        same figure the Estimated Saved card shows for that window. It used to
        be a synthetic curve with a $4,000/month floor, which put $26k on the
        chart beside $268 on the card.
        """
        now = datetime.now(UTC)
        months: list[tuple[int, int]] = []
        year, month = now.year, now.month
        for _ in range(12):
            months.append((year, month))
            year, month = (year, month - 1) if month > 1 else (year - 1, 12)
        months.reverse()

        first_year, first_month = months[0]
        since = datetime(first_year, first_month, 1, tzinfo=UTC)
        by_month: dict[tuple[int, int], float] = {m: 0.0 for m in months}
        for submitted_at, amount in await self.claims.saved_amounts_since(since):
            at = submitted_at if submitted_at.tzinfo else submitted_at.replace(tzinfo=UTC)
            key = (at.year, at.month)
            if key in by_month:
                by_month[key] += amount

        cumulative = 0.0
        out: list[dict] = []
        for y, m in months:
            cumulative += by_month[(y, m)]
            out.append(
                SavingsDataPoint(
                    month=calendar.month_abbr[m],
                    savings=round(by_month[(y, m)], 2),
                    cumulative=round(cumulative, 2),
                ).model_dump(by_alias=True)
            )
        return out

    async def ussd_stats(self) -> dict:
        """Counts of real USSD verification alerts; previously padded with constants."""
        sent = await self.claims.count_member_alerts(channel="USSD")
        confirmed = await self.claims.count_member_alerts(channel="USSD", response="CONFIRMED")
        disputed = await self.claims.count_member_alerts(channel="USSD", response="DISPUTED")
        return USSDStats(
            alerts_sent=sent,
            confirmed=confirmed,
            disputed=disputed,
            awaiting_reply=sent - confirmed - disputed,
            code=settings.ussd_code,
        ).model_dump(by_alias=True)
