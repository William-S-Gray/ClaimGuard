"""Dashboard & analytics service — live KPIs computed from claim data."""
from __future__ import annotations

import calendar
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

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
        saved = await self.claims.sum_estimated_saved()

        total = max(claims_today, 1)
        detection_rate = round((flagged_today / total) * 100, 1)

        return DashboardMetrics(
            claims_today=claims_today,
            claims_yesterday=claims_yesterday,
            flagged_today=flagged_today,
            estimated_saved=round(saved, 2),
            member_alerts=disputed,
            pending_investigation=pending,
            auto_approved_today=auto_approved,
            avg_latency_ms=int(avg_latency),
            detection_rate=detection_rate,
        ).model_dump(by_alias=True)

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
        ).model_dump(by_alias=True)
