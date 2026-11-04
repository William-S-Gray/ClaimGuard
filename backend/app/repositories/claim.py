"""Claim repository with queue filtering, search, and aggregate helpers."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, func, or_, select

from app.models.claim import Claim
from app.models.member import Member
from app.models.provider import Provider
from app.repositories.base import BaseRepository

# Decisions whose claimed amount counts as loss avoided: rejected, or held for investigation.
SAVED_DECISIONS = ("REJECT_FRAUD", "REJECT_ERROR", "PEND_INVESTIGATE")


class ClaimRepository(BaseRepository[Claim]):
    model = Claim

    def _filtered_query(
        self,
        *,
        search: str | None = None,
        priority: str | None = None,
        status: str | None = None,
    ) -> Select:
        stmt = self._base_query()
        if search:
            like = f"%{search.lower()}%"
            stmt = (
                stmt.join(Member, Claim.member_id == Member.id)
                .join(Provider, Claim.provider_id == Provider.id)
                .where(
                    or_(
                        func.lower(Claim.claim_ref).like(like),
                        func.lower(Member.name).like(like),
                        func.lower(Provider.name).like(like),
                    )
                )
            )
        if priority and priority != "ALL":
            stmt = stmt.where(Claim.priority == priority)
        if status and status != "ALL":
            stmt = stmt.where(Claim.decision == status)
        return stmt

    async def get_by_ref(self, claim_ref: str) -> Claim | None:
        stmt = self._base_query().where(Claim.claim_ref == claim_ref)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def pending_alert_for_phone(self, phone: str) -> Claim | None:
        """The most recently alerted claim still awaiting this member's reply.

        `phone` is E.164 without spaces; stored numbers may contain spaces.
        """
        # ponytail: a reply always answers the latest open alert; if members get
        # several alerts at once, put the claim ref in the message and match on it.
        stmt = (
            self._base_query()
            .join(Member, Claim.member_id == Member.id)
            .where(
                func.replace(Member.phone, " ", "") == phone,
                Claim.member_notification_sent.is_(True),
                Claim.member_response == "PENDING",
            )
            .order_by(Claim.updated_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def query_queue(
        self,
        *,
        search: str | None = None,
        priority: str | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 10,
    ) -> tuple[list[Claim], int]:
        base = self._filtered_query(search=search, priority=priority, status=status)

        count_stmt = select(func.count()).select_from(base.subquery())
        total = int((await self.session.execute(count_stmt)).scalar_one())

        stmt = base.order_by(Claim.risk_score.desc(), Claim.submitted_at.desc())
        stmt = stmt.offset(offset).limit(limit)
        rows = list((await self.session.execute(stmt)).scalars().unique().all())
        return rows, total

    async def list_by_provider(self, provider_id: uuid.UUID | str) -> list[Claim]:
        stmt = (
            self._base_query()
            .where(Claim.provider_id == provider_id)
            .order_by(Claim.submitted_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().unique().all())

    async def list_by_member(self, member_id: uuid.UUID | str) -> list[Claim]:
        stmt = (
            self._base_query()
            .where(Claim.member_id == member_id)
            .order_by(Claim.submitted_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().unique().all())

    async def recent(self, limit: int = 5) -> list[Claim]:
        stmt = self._base_query().order_by(Claim.submitted_at.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().unique().all())

    # ── Aggregates for the dashboard ──────────────────────────────────────────
    async def _count_where(self, *conditions) -> int:
        stmt = select(func.count()).select_from(Claim).where(
            Claim.deleted_at.is_(None), *conditions
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def count_since(self, since: datetime) -> int:
        return await self._count_where(Claim.submitted_at >= since)

    async def count_flagged_since(self, since: datetime) -> int:
        return await self._count_where(
            Claim.submitted_at >= since, Claim.risk_score >= 50
        )

    async def count_by_decision(self, decision: str) -> int:
        return await self._count_where(Claim.decision == decision)

    async def count_by_decision_since(self, decision: str, since: datetime) -> int:
        return await self._count_where(
            Claim.decision == decision, Claim.submitted_at >= since
        )

    async def count_member_alerts(
        self, channel: str | None = None, response: str | None = None
    ) -> int:
        """Claims whose member was sent a verification alert, optionally by channel/reply."""
        conditions = [Claim.member_notification_sent.is_(True)]
        if channel:
            conditions.append(Claim.member_notification_channel == channel)
        if response:
            conditions.append(Claim.member_response == response)
        return await self._count_where(*conditions)

    async def saved_amounts_since(self, since: datetime) -> list[tuple[datetime, float]]:
        """(submitted_at, claimed_amount) for every claim counted as loss avoided."""
        stmt = select(Claim.submitted_at, Claim.claimed_amount).where(
            Claim.deleted_at.is_(None),
            Claim.decision.in_(SAVED_DECISIONS),
            Claim.submitted_at >= since,
        )
        rows = (await self.session.execute(stmt)).all()
        return [(at, float(amount)) for at, amount in rows]

    async def avg_latency(self) -> float:
        stmt = select(func.avg(Claim.latency_ms)).where(Claim.deleted_at.is_(None))
        val = (await self.session.execute(stmt)).scalar_one_or_none()
        return float(val or 0)

    async def sum_estimated_saved(self) -> float:
        """Sum of claimed amounts on rejected/investigated claims (loss avoided)."""
        stmt = select(func.coalesce(func.sum(Claim.claimed_amount), 0)).where(
            Claim.deleted_at.is_(None),
            Claim.decision.in_(SAVED_DECISIONS),
        )
        return float((await self.session.execute(stmt)).scalar_one())

    @staticmethod
    def start_of_today() -> datetime:
        now = datetime.now(UTC)
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    def days_ago(days: int) -> datetime:
        return datetime.now(UTC) - timedelta(days=days)
