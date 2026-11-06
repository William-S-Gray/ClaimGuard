"""Claim lifecycle sweep: act on deadlines that nothing else watches.

Claims carry an SLA deadline and member alerts wait for a reply, but no request
ever arrives to say "time's up". This sweep runs on a timer inside the API
process (Render's plan has no Celery worker or beat) and:

- escalates open claims whose SLA has passed (flag + priority + timeline);
- lapses verification alerts the member never answered (flag + timeline), so a
  person follows up instead of the claim waiting forever;
- releases model approvals whose payment hold has run out (nothing stopped them).

Every step is idempotent (it skips claims already flagged), so running it on
several API workers at once, or twice in a row, changes nothing extra.
"""
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import SessionFactory
from app.core.logging import get_logger
from app.core.redis import redis_client
from app.core.websocket import WSEventType, publish
from app.models.claim import Claim, ClaimFlag, TimelineEvent
from app.repositories.investigation import InvestigationRepository
from app.services.claims import CASE_TERMINAL, MEMBER_NO_REPLY, SLA_BREACHED, raise_priority
from app.services.notifications import NotificationService

log = get_logger(__name__)

_OPEN = ("PEND_VERIFY", "PEND_INVESTIGATE")  # INFO_NEEDED has its SLA paused
_BATCH = 500  # per step per run; the next run picks up the rest


@dataclass(slots=True)
class SweepResult:
    sla_breached: int = 0
    alerts_lapsed: int = 0
    payments_released: int = 0


def _flag(claim: Claim, code: str, severity: str, detail: str, event: str, now: datetime) -> None:
    claim.flags.append(ClaimFlag(claim_id=claim.id, code=code, severity=severity, detail=detail))
    claim.timeline.append(TimelineEvent(
        claim_id=claim.id, timestamp=now, event=event, description=detail,
        actor="ClaimGuard", type="system",
    ))


async def _breached_slas(session: AsyncSession, now: datetime) -> list[Claim]:
    stmt = (
        select(Claim)
        .where(
            Claim.deleted_at.is_(None),
            Claim.decision.in_(_OPEN),
            Claim.sla_deadline.is_not(None),
            Claim.sla_deadline < now,
        )
        .order_by(Claim.sla_deadline)
        .limit(_BATCH)
    )
    rows = (await session.execute(stmt)).scalars().unique().all()
    return [c for c in rows if not any(f.code == SLA_BREACHED for f in c.flags)]


async def _lapsed_alerts(session: AsyncSession, now: datetime) -> list[Claim]:
    sent_at = func.coalesce(Claim.member_alert_sent_at, Claim.updated_at)
    cutoff = now - timedelta(days=settings.member_alert_ttl_days)
    stmt = (
        select(Claim)
        .where(
            Claim.deleted_at.is_(None),
            Claim.member_notification_sent.is_(True),
            Claim.member_response == "PENDING",
            sent_at < cutoff,
        )
        .limit(_BATCH)
    )
    rows = (await session.execute(stmt)).scalars().unique().all()
    return [c for c in rows if not any(f.code == MEMBER_NO_REPLY for f in c.flags)]


async def _holds_ended(session: AsyncSession, now: datetime) -> list[Claim]:
    stmt = (
        select(Claim)
        .where(
            Claim.deleted_at.is_(None),
            Claim.decision == "APPROVE",
            Claim.auto_approve_at.is_not(None),
            Claim.auto_approve_at <= now,
        )
        .order_by(Claim.auto_approve_at)
        .limit(_BATCH)
    )
    return list((await session.execute(stmt)).scalars().unique().all())


async def _release(session: AsyncSession, claim: Claim, now: datetime) -> bool:
    """Pay a held approval, unless something should have stopped it.

    Disputes and new cases withdraw the hold as they happen; this is the belt to
    those braces, for a hold that predates them.
    """
    inv = await InvestigationRepository(session).get_by_claim(claim.id)
    if claim.member_response == "DISPUTED" or (inv and inv.status not in CASE_TERMINAL):
        claim.decision = "PEND_INVESTIGATE"
        claim.auto_approve_at = None
        claim.approved_amount = None
        claim.timeline.append(TimelineEvent(
            claim_id=claim.id, timestamp=now, event="Payment stopped",
            description="Hold ended under a dispute or open case: sent for review",
            actor="ClaimGuard", type="system",
        ))
        return False
    unpaid = claim.approved_amount is None
    claim.approved_amount = claim.claimed_amount
    claim.auto_approve_at = None
    if unpaid:
        claim.timeline.append(TimelineEvent(
            claim_id=claim.id, timestamp=now, event="Payment released",
            description="Payment hold ended with no dispute: approval is final",
            actor="ClaimGuard", type="system",
        ))
    return True


async def sweep(session: AsyncSession, now: datetime | None = None) -> SweepResult:
    """One pass over both deadlines. The caller commits."""
    now = now or datetime.now(UTC)
    result = SweepResult()

    for claim in await _breached_slas(session, now):
        claim.priority = raise_priority(claim.priority, "HIGH")
        _flag(claim, SLA_BREACHED, "HIGH",
              "Review deadline passed with no decision", "SLA breached", now)
        result.sla_breached += 1

    days = settings.member_alert_ttl_days
    for claim in await _lapsed_alerts(session, now):
        _flag(claim, MEMBER_NO_REPLY, "MEDIUM",
              f"Member did not answer the verification alert within {days} days; "
              "follow up by phone", "Member did not reply", now)
        result.alerts_lapsed += 1

    for claim in await _holds_ended(session, now):
        if await _release(session, claim, now):
            result.payments_released += 1

    # One summary each, however many claims: the first sweep after a long sleep
    # can catch dozens at once, and nobody wants dozens of bell notifications.
    notes = NotificationService(session)
    if result.sla_breached:
        n = result.sla_breached
        await notes.create(
            user_id=None, type_="warning", link="/queue",
            title="SLA breached",
            message=f"{n} open claim{'s' if n != 1 else ''} passed the review deadline.",
        )
    if result.alerts_lapsed:
        n = result.alerts_lapsed
        await notes.create(
            user_id=None, type_="warning", link="/memberguard",
            title="Member alerts unanswered",
            message=f"{n} member{'s' if n != 1 else ''} did not reply in {days} days.",
        )
    await session.flush()
    if result.sla_breached or result.alerts_lapsed or result.payments_released:
        publish(WSEventType.QUEUE_UPDATED, {"reason": "sweep"})
        log.info("claim_sweep.done", sla_breached=result.sla_breached,
                 alerts_lapsed=result.alerts_lapsed,
                 payments_released=result.payments_released)
    return result


_LOCK_KEY = "claim_sweep:lock"
_LOCK_SECONDS = 300  # frees itself if a worker dies mid-sweep


async def run_sweep_once() -> SweepResult | None:
    """Sweep unless another worker already is (None = skipped).

    The per-claim flag checks keep a sweep idempotent, but two workers sweeping at
    the same instant could both see a claim as unflagged. INCR is atomic in Redis
    and in the in-memory fallback, so whoever bumps it to 1 holds the lock.
    """
    try:
        holder = await redis_client.client.incr(_LOCK_KEY) == 1
        if holder:
            await redis_client.client.expire(_LOCK_KEY, _LOCK_SECONDS)
    except Exception:  # noqa: BLE001 - no lock store: single worker, just sweep
        holder = True
    if not holder:
        return None
    try:
        async with SessionFactory() as session:
            result = await sweep(session)
            await session.commit()
            return result
    finally:
        with contextlib.suppress(Exception):
            await redis_client.client.delete(_LOCK_KEY)


async def sweep_forever() -> None:
    """The API's background loop. Never raises: a bad run is logged and retried."""
    interval = settings.claim_sweep_minutes * 60
    while True:
        try:
            await run_sweep_once()
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            log.warning("claim_sweep.failed", error=str(exc))
        await asyncio.sleep(interval)


def start_sweeper() -> asyncio.Task | None:
    if settings.claim_sweep_minutes <= 0:
        return None
    return asyncio.create_task(sweep_forever())


async def stop_sweeper(task: asyncio.Task | None) -> None:
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
