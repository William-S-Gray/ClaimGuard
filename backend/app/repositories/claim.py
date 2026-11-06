"""Claim repository with queue filtering, search, and aggregate helpers."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, and_, func, or_, select

from app.models.claim import Claim
from app.models.investigation import Investigation
from app.models.member import Member
from app.models.provider import Provider
from app.repositories.base import BaseRepository

# Queue sort orders; ties fall back to newest first.
QUEUE_SORTS = {
    "risk": Claim.risk_score.desc(),
    "amount": Claim.claimed_amount.desc(),
    "newest": Claim.submitted_at.desc(),
}

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
        risk_level: str | None = None,
        investigator: str | None = None,
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
                        func.lower(Member.member_number).like(like),
                        func.lower(Provider.name).like(like),
                        func.lower(Provider.code).like(like),
                    )
                )
            )
        if priority and priority != "ALL":
            stmt = stmt.where(Claim.priority == priority)
        if status == "MEMBER_DISPUTED":
            # A dispute is the member's reply, never a decision value.
            stmt = stmt.where(Claim.member_response == "DISPUTED")
        elif status and status != "ALL":
            stmt = stmt.where(Claim.decision == status)
        if risk_level and risk_level != "ALL":
            stmt = stmt.where(Claim.risk_level == risk_level)
        if investigator:
            # Claims with a case assigned to this user.
            stmt = stmt.where(
                Claim.id.in_(
                    select(Investigation.claim_id).where(
                        Investigation.assigned_to == investigator,
                        Investigation.deleted_at.is_(None),
                    )
                )
            )
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

    async def awaiting_rating_for_phone(self, phone: str) -> Claim | None:
        """The latest claim this member confirmed in the past week but hasn't rated."""
        stmt = (
            self._base_query()
            .join(Member, Claim.member_id == Member.id)
            .where(
                func.replace(Member.phone, " ", "") == phone,
                Claim.member_response == "CONFIRMED",
                Claim.member_rating.is_(None),
                Claim.updated_at >= datetime.now(UTC) - timedelta(days=7),
            )
            .order_by(Claim.updated_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def rating_stats(self, provider_id: uuid.UUID | None = None) -> tuple[float | None, int]:
        """(average 1-5 member rating, number of ratings), optionally for one provider."""
        stmt = select(func.avg(Claim.member_rating), func.count(Claim.member_rating)).where(
            Claim.deleted_at.is_(None), Claim.member_rating.is_not(None)
        )
        if provider_id:
            stmt = stmt.where(Claim.provider_id == provider_id)
        avg, count = (await self.session.execute(stmt)).one()
        return (round(float(avg), 1) if avg is not None else None), int(count)

    async def query_queue(
        self,
        *,
        search: str | None = None,
        priority: str | None = None,
        status: str | None = None,
        risk_level: str | None = None,
        investigator: str | None = None,
        sort: str = "risk",
        offset: int = 0,
        limit: int = 10,
    ) -> tuple[list[Claim], int]:
        base = self._filtered_query(
            search=search,
            priority=priority,
            status=status,
            risk_level=risk_level,
            investigator=investigator,
        )

        count_stmt = select(func.count()).select_from(base.subquery())
        total = int((await self.session.execute(count_stmt)).scalar_one())

        stmt = base.order_by(QUEUE_SORTS.get(sort, QUEUE_SORTS["risk"]), Claim.submitted_at.desc())
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

    async def member_history(self, claim: Claim, days: int = 7) -> list[Claim]:
        """The member's other claims that matter for scoring this one.

        Everything on the same service date (any provider), plus anything submitted
        in the `days` before this claim. Duplicate, splitting and frequency checks
        all run over this one list.
        """
        since = (claim.submitted_at or datetime.now(UTC)) - timedelta(days=days)
        window = [Claim.submitted_at >= since]
        if claim.service_date:
            window.append(Claim.service_date == claim.service_date)
        related = and_(Claim.member_id == claim.member_id, or_(*window))
        if claim.nh263_ref:  # the same upstream claim re-sent under a new ref
            related = or_(related, Claim.nh263_ref == claim.nh263_ref)
        stmt = self._base_query().where(Claim.id != claim.id, related)
        return list((await self.session.execute(stmt)).scalars().unique().all())

    async def similar(self, claim: Claim, limit: int = 5) -> list[Claim]:
        """The same member's other claims at the same provider, newest first."""
        stmt = (
            self._base_query()
            .where(
                Claim.member_id == claim.member_id,
                Claim.provider_id == claim.provider_id,
                Claim.id != claim.id,
            )
            .order_by(Claim.submitted_at.desc())
            .limit(limit)
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
