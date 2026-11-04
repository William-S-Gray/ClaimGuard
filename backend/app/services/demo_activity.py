"""Sample claim activity for demo deployments, scored by the real FraudShield engine.

Two entry points share one generator:

- `seed_history` (run by `python -m scripts.seed_activity`) adds a year of claims.
- `ensure_recent_activity` keeps the last week populated, so "claims today" and
  "vs yesterday" stay meaningful on a demo nobody feeds. The API calls it on
  startup and on the first dashboard read of each day. That covers a free-tier
  host that sleeps between visits, where a timed job would never fire.

Every claim goes through the same signal derivation and scoring as live
ingestion, so risk scores, flags, explanations and SHAP values are genuine.
Ingestion's side effects are skipped on purpose: no live-feed events, and no
"claim scored" notification per claim to bury the bell.

Everything here is create-only and deterministic, and refuses to run unless
DEMO_MODE is on.
"""
from __future__ import annotations

import asyncio
import random
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import SessionFactory
from app.core.logging import get_logger
from app.models.claim import Claim, ClaimFlag, ClaimItem, TimelineEvent
from app.models.member import Member
from app.models.provider import Provider
from app.modules.fraudshield.service import fraudshield_service
from app.modules.fraudshield.signals import SignalInputs, derive_input_signals
from app.services.claims import ClaimService

log = get_logger(__name__)

HISTORY_CLAIMS = 140
RECENT_DAYS = 7

# Same namespace as scripts.seed, so ids are stable across runs and environments.
_NS = uuid.UUID("c1a16c4a-0000-4000-8000-000000000001")

# (name, plan, city, conditions)
MEMBERS = [
    ("Nyasha Mutasa", "GOLD", "Harare", ["Type 2 Diabetes"]),
    ("Blessing Ncube", "SILVER", "Bulawayo", []),
    ("Ruvimbo Chigumba", "BRONZE", "Gweru", ["Asthma"]),
    ("Tatenda Mhlanga", "GOLD", "Harare", ["Hypertension", "Type 2 Diabetes"]),
    ("Kudzai Sibanda", "SILVER", "Mutare", []),
    ("Precious Gumbo", "SILVER", "Harare", ["Hypertension"]),
    ("Tinashe Dube", "BRONZE", "Bulawayo", []),
    ("Chiedza Makoni", "GOLD", "Harare", []),
    ("Farai Mpofu", "SILVER", "Gweru", ["Asthma"]),
    ("Rumbidzai Zhou", "BRONZE", "Masvingo", []),
    ("Simbarashe Moyo", "GOLD", "Bulawayo", ["Hypertension"]),
    ("Vimbai Chirwa", "SILVER", "Harare", []),
]
BASE_MEMBER_IDS = ["mem-001", "mem-002", "mem-003"]
BASE_PROVIDER_IDS = ["prov-001", "prov-002", "prov-003", "prov-004", "prov-005", "prov-006"]

# (description, unit price, NAPPI) by provider type. Diabetic drugs trip the
# chronic-condition signal for members without a diabetes registration.
ITEMS = {
    "PHARMACY": [
        ("Amoxicillin 500mg x 21", 9.50, "726010001"),
        ("Paracetamol 500mg x 20", 2.40, "726002001"),
        ("Amlodipine 5mg x 30", 11.00, "726110001"),
        ("Salbutamol inhaler 100mcg", 14.00, "726205001"),
        ("Metformin 500mg x 30", 12.00, "726044001"),
        ("Insulin Human 100U/mL", 19.00, "726125001"),
        ("Glibenclamide 5mg x 30", 18.00, "726098001"),
    ],
    "GP": [
        ("GP consultation", 25.00, None),
        ("Blood pressure review", 15.00, None),
        ("Rapid malaria test", 8.00, None),
    ],
    "HOSPITAL": [
        ("Outpatient consultation", 40.00, None),
        ("Full blood count", 22.00, None),
        ("Chest X-ray", 55.00, None),
        ("Ward admission (per day)", 120.00, None),
    ],
    "SPECIALIST": [
        ("Specialist consultation", 60.00, None),
        ("ECG", 35.00, None),
        ("Ultrasound scan", 75.00, None),
    ],
}

AGENTS = ["Farai Nyathi", "Chipo Marufu", "Rudo Chidziva"]


class DemoModeOff(RuntimeError):
    """Raised instead of writing sample data to a real (non-demo) database."""


def det_id(key: str) -> uuid.UUID:
    return uuid.uuid5(_NS, key)


def _event(at: datetime, event: str, description: str, actor: str, type_: str) -> TimelineEvent:
    return TimelineEvent(
        timestamp=at, event=event, description=description, actor=actor, type=type_
    )


async def _members_and_providers(
    session: AsyncSession,
) -> tuple[list[Member], list[Provider]]:
    """The demo's extra members (created if missing) plus the seeded base cast."""
    members = []
    for i, (name, plan, city, conditions) in enumerate(MEMBERS, start=1):
        mid = det_id(f"activity-member:{i}")
        member = await session.get(Member, mid)
        if member is None:
            benefit = {"GOLD": 2500, "SILVER": 1800, "BRONZE": 1000}[plan]
            member = Member(
                id=mid,
                member_number=f"CIM-07{i:05d}",
                name=name,
                plan=plan,
                city=city,
                annual_benefit=benefit,
                benefit_used=0,
                conditions=conditions,
                phone=f"+263 77 {700 + i:03d} {1000 + i * 37:04d}",
                email=f"{name.lower().replace(' ', '.')}@email.co.zw",
                date_of_birth=f"{1960 + i * 3}-0{1 + i % 9}-1{i % 9}",
            )
            session.add(member)
        members.append(member)
    for mid in BASE_MEMBER_IDS:
        if member := await session.get(Member, det_id(mid)):
            members.append(member)
    providers = [
        p for pid in BASE_PROVIDER_IDS if (p := await session.get(Provider, det_id(pid)))
    ]
    await session.flush()
    return members, providers


def _play_forward(rng: random.Random, claim: Claim, at: datetime, now: datetime) -> None:
    """Advance a backdated claim to where the team would have taken it by now."""
    age = now - at
    agent = rng.choice(AGENTS)

    if claim.decision == "PEND_VERIFY" and age > timedelta(hours=6):
        channel = rng.choices(["USSD", "WHATSAPP", "SMS"], weights=[5, 4, 1])[0]
        sent = at + timedelta(minutes=rng.randrange(5, 90))
        claim.member_notification_sent = True
        claim.member_notification_channel = channel
        claim.timeline.append(
            _event(sent, "Member alert sent", f"{channel} verification alert sent", agent, "agent")
        )
        if age > timedelta(days=4) or rng.random() < 0.3:
            reply = sent + timedelta(hours=rng.randrange(1, 30))
            if rng.random() < 0.75:
                claim.member_response = "CONFIRMED"
                claim.timeline.append(_event(
                    reply, "Member confirmed claim",
                    f"Member confirmed the service via {channel}", "member", "member",
                ))
                if age > timedelta(days=3):
                    claim.decision = "APPROVE"
                    claim.approved_amount = claim.claimed_amount
                    claim.timeline.append(_event(
                        reply + timedelta(hours=2), "Claim approved",
                        f"Approved by {agent}", agent, "agent",
                    ))
            else:
                # Mirrors ClaimService.record_member_response for a dispute.
                claim.member_response = "DISPUTED"
                claim.priority = "CRITICAL"
                claim.flags.append(ClaimFlag(
                    code="MEMBER_DISPUTED", severity="CRITICAL",
                    detail="Member reported they did not receive this service",
                ))
                claim.timeline.append(_event(
                    reply, "Member disputed claim",
                    f"Member disputed the service via {channel}", "member", "member",
                ))
                if age > timedelta(days=5):
                    claim.decision = "REJECT_FRAUD"
                    claim.approved_amount = 0
                    claim.timeline.append(_event(
                        reply + timedelta(days=1), "Claim rejected",
                        f"Rejected (REJECT_FRAUD) by {agent}", agent, "agent",
                    ))

    elif claim.decision == "PEND_INVESTIGATE" and age > timedelta(days=21):
        closed = at + timedelta(days=rng.randrange(5, 20))
        if rng.random() < 0.75:
            claim.decision = "REJECT_FRAUD"
            claim.approved_amount = 0
            claim.timeline.append(_event(
                closed, "Claim rejected", f"Rejected (REJECT_FRAUD) by {agent}", agent, "agent",
            ))
        else:
            claim.decision = "APPROVE"
            claim.approved_amount = claim.claimed_amount
            claim.timeline.append(_event(
                closed, "Claim approved", f"Approved by {agent}", agent, "agent",
            ))

    elif claim.decision == "APPROVE" and claim.approved_amount is None:
        claim.approved_amount = claim.claimed_amount


def _build_claim(
    rng: random.Random,
    *,
    cid: uuid.UUID,
    claim_ref: str,
    nh263_ref: str,
    at: datetime,
    now: datetime,
    members: list[Member],
    providers: list[Provider],
    service: ClaimService,
) -> Claim:
    """One scored claim submitted at `at`, played forward to `now`."""
    member = rng.choice(members)
    provider = rng.choice(providers)
    catalogue = ITEMS.get(provider.type, ITEMS["GP"])
    picks = rng.sample(catalogue, k=min(len(catalogue), rng.randint(1, 3)))
    lines = [(desc, rng.randint(1, 2), price, nappi) for desc, price, nappi in picks]
    amount = round(sum(q * price for _, q, price, _ in lines), 2)
    exp_min = round(amount * 0.10, 2)
    exp_max = round(amount * 0.18, 2)
    # Mostly normal shortfalls; some well above what the provider usually charges.
    ratio = rng.choice([0.12, 0.14, 0.15, 0.16, 0.2, 0.35, 0.5])
    shortfall = round(amount * ratio, 2)
    service_day = (at - timedelta(days=rng.randrange(0, 3))).date()
    late_script = rng.random() < 0.08
    prescription_day = service_day + timedelta(days=3 if late_script else 0)
    has_biometric = rng.random() > (0.25 if amount > 60 else 0.05)
    syndicate = rng.random() < 0.04

    signals = derive_input_signals(SignalInputs(
        item_descriptions=[d for d, *_ in lines],
        member_conditions=member.conditions or [],
        provider_flags_90d=provider.flags_90d,
        provider_trust_score=provider.trust_score,
        service_date=service_day.isoformat(),
        prescription_date=prescription_day.isoformat(),
        has_biometric=has_biometric,
        syndicate_signal=syndicate,
    ))
    claim = Claim(
        id=cid,
        claim_ref=claim_ref,
        nh263_ref=nh263_ref,
        member_id=member.id,
        provider_id=provider.id,
        member=member,
        provider=provider,
        service_date=service_day.isoformat(),
        submitted_at=at,
        claimed_amount=amount,
        member_shortfall=shortfall,
        expected_shortfall_min=exp_min,
        expected_shortfall_max=exp_max,
        sla_deadline=at + timedelta(hours=48),
        **signals,
    )
    claim.items = [
        ClaimItem(description=d, quantity=q, unit_price=p, total=round(q * p, 2), nappi_code=n)
        for d, q, p, n in lines
    ]
    claim.timeline = [
        _event(at, "Claim submitted", "Claim received via NH263 ingestion", "nh263", "system")
    ]

    result = fraudshield_service.score(ClaimService.context_from_claim(claim))
    service._apply_result(claim, result)
    approved = result.decision == "APPROVE"
    claim.auto_approve_at = at + timedelta(hours=72) if approved else None
    claim.timeline.append(_event(
        at + timedelta(milliseconds=result.latency_ms), f"Risk score: {result.risk_score}",
        f"FraudShield decision: {result.decision}", "ClaimGuard AI", "system",
    ))
    _play_forward(rng, claim, at, now)
    return claim


def _require_demo_mode() -> None:
    if not settings.demo_mode:
        raise DemoModeOff("DEMO_MODE is off. Refusing to add sample claims to a real database.")


def _history_time(rng: random.Random, index: int, now: datetime) -> datetime:
    """A few today and yesterday, the rest over the past year, weighted to recent months."""
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if index < 6:
        elapsed = max(int((now - today).total_seconds()), 60)
        return today + timedelta(seconds=rng.randrange(elapsed))
    if index < 11:
        return today - timedelta(seconds=rng.randrange(1, 86_400))
    days = int(365 * rng.random() ** 1.6) + 2  # skewed towards recent
    return today - timedelta(days=days, seconds=rng.randrange(86_400))


async def seed_history(session: AsyncSession, now: datetime) -> int:
    """Add the year of claims (CG-A0001…). Returns how many were created."""
    _require_demo_mode()
    members, providers = await _members_and_providers(session)
    if not providers:
        raise RuntimeError("No providers found. Run `python -m scripts.seed` first.")
    service = ClaimService(session)
    created = 0
    for index in range(HISTORY_CLAIMS):
        cid = det_id(f"activity-claim:{index}")
        if await session.get(Claim, cid):
            continue
        # One generator per claim, so a claim's content depends only on its
        # index and a re-run that skips some claims leaves the rest unchanged.
        rng = random.Random(2000 + index)
        at = _history_time(rng, index, now)
        session.add(_build_claim(
            rng, cid=cid, claim_ref=f"CG-A{index + 1:04d}",
            nh263_ref=f"NH263-{at.year}-{1_000_000 + index:07d}",
            at=at, now=now, members=members, providers=providers, service=service,
        ))
        created += 1
    return created


async def top_up_recent(session: AsyncSession, now: datetime, days: int = RECENT_DAYS) -> int:
    """Give each of the last `days` days (today included) its 4–7 claims, if missing.

    Ids and refs are keyed by date, so a day is filled once however often this
    runs. Today's claims are timed between midnight and now, never later.
    """
    _require_demo_mode()
    members, providers = await _members_and_providers(session)
    if not providers:
        return 0
    service = ClaimService(session)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    created = 0
    for back in range(days - 1, -1, -1):
        start = today - timedelta(days=back)
        day: date = start.date()
        span = 86_400 if back else max(int((now - start).total_seconds()), 60)
        for i in range(random.Random(f"daily:{day}").randint(4, 7)):
            cid = det_id(f"daily-claim:{day}:{i}")
            if await session.get(Claim, cid):
                continue
            rng = random.Random(f"daily:{day}:{i}")
            at = start + timedelta(seconds=rng.randrange(span))
            session.add(_build_claim(
                rng, cid=cid, claim_ref=f"CG-D{day:%y%m%d}{i}",
                nh263_ref=f"NH263-{day:%Y%m%d}-D{i}",
                at=at, now=now, members=members, providers=providers, service=service,
            ))
            created += 1
    return created


_filled_for: date | None = None
_lock = asyncio.Lock()


async def ensure_recent_activity() -> None:
    """Fill the last week once per process per day. Demo mode only; never raises.

    Runs in its own session so a failure here can't touch the caller's
    transaction. Two workers may race on a fresh day: the loser's insert hits
    the primary key and is rolled back, which is the intended outcome.
    """
    global _filled_for
    if not settings.demo_mode:
        return
    now = datetime.now(UTC)
    if _filled_for == now.date():
        return
    async with _lock:
        if _filled_for == now.date():
            return
        try:
            async with SessionFactory() as session:
                created = await top_up_recent(session, now)
                await session.commit()
            _filled_for = now.date()
            if created:
                log.info("demo.activity.topped_up", claims=created)
        except IntegrityError:
            _filled_for = now.date()  # another worker filled it first
        except Exception as exc:  # a demo nicety must never take the API down
            log.warning("demo.activity.failed", error=str(exc))

