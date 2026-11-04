"""Fill the demo with a year of claim activity, scored by the real FraudShield engine.

The base seed holds five hand-written claims from one week, which leaves the
dashboard nearly empty. This adds a dozen more members and ~140 claims spread
over the last twelve months (busier recently, a few today and yesterday), then
plays each one forward the way the team would have: low-risk claims
auto-approve, verification alerts go out and members reply, and older
investigations end in an approval or a fraud rejection.

Every claim goes through the same signal derivation and scoring as live
ingestion, so risk scores, flags, explanations and SHAP values are genuine.
It skips ingestion's side effects on purpose: no live-feed events, and no
"claim scored" notification per claim to bury the bell.

Create-only and deterministic, like scripts.seed: re-running skips what exists.
Refuses to run unless DEMO_MODE is on, so a real deployment stays clean.

    python -m scripts.seed_activity
"""
from __future__ import annotations

import asyncio
import random
import sys
from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.core.database import SessionFactory, engine
from app.models.claim import Claim, ClaimFlag, ClaimItem, TimelineEvent
from app.models.member import Member
from app.models.provider import Provider
from app.modules.fraudshield.service import fraudshield_service
from app.modules.fraudshield.signals import SignalInputs, derive_input_signals
from app.services.claims import ClaimService
from scripts.seed import det_id
from scripts.seed_data import PROVIDERS

CLAIM_COUNT = 140

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


def _when(rng: random.Random, index: int, now: datetime) -> datetime:
    """Submission time for claim `index`: a few today and yesterday, the rest
    over the past year, weighted towards recent months."""
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if index < 6:
        elapsed = max(int((now - today).total_seconds()), 60)
        return today + timedelta(seconds=rng.randrange(elapsed))
    if index < 11:
        return today - timedelta(seconds=rng.randrange(1, 86_400))
    days = int(365 * rng.random() ** 1.6) + 2  # skewed towards recent
    return today - timedelta(days=days, seconds=rng.randrange(86_400))


def _event(at: datetime, event: str, description: str, actor: str, type_: str) -> TimelineEvent:
    return TimelineEvent(
        timestamp=at, event=event, description=description, actor=actor, type=type_
    )


async def _seed_members(session) -> list[Member]:
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
    await session.flush()
    return members


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


async def main() -> None:
    if not settings.demo_mode:
        sys.exit(
            "seed_activity: DEMO_MODE is off. Refusing to add sample claims to a real database."
        )

    now = datetime.now(UTC)
    created = 0
    async with SessionFactory() as session:
        members = await _seed_members(session)
        providers = []
        for p in PROVIDERS:
            if provider := await session.get(Provider, det_id(p["id"])):
                providers.append(provider)
        if not providers:
            sys.exit("seed_activity: no providers found. Run `python -m scripts.seed` first.")

        service = ClaimService(session)
        for index in range(CLAIM_COUNT):
            cid = det_id(f"activity-claim:{index}")
            if await session.get(Claim, cid):
                continue
            # One generator per claim, so a claim's content depends only on its
            # index and a re-run that skips some claims leaves the rest unchanged.
            rng = random.Random(2000 + index)
            at = _when(rng, index, now)
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

            claim_ref = f"CG-A{index + 1:04d}"
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
                nh263_ref=f"NH263-{at.year}-{1_000_000 + index:07d}",
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
                ClaimItem(
                    description=d, quantity=q, unit_price=p, total=round(q * p, 2), nappi_code=n
                )
                for d, q, p, n in lines
            ]
            claim.timeline = [_event(at, "Claim submitted", "Claim received via NH263 ingestion",
                                     "nh263", "system")]

            result = fraudshield_service.score(ClaimService.context_from_claim(claim))
            service._apply_result(claim, result)
            approved = result.decision == "APPROVE"
            claim.auto_approve_at = at + timedelta(hours=72) if approved else None
            claim.timeline.append(_event(
                at + timedelta(milliseconds=result.latency_ms), f"Risk score: {result.risk_score}",
                f"FraudShield decision: {result.decision}", "ClaimGuard AI", "system",
            ))
            _play_forward(rng, claim, at, now)
            session.add(claim)
            created += 1

        await session.commit()
    await engine.dispose()
    print(f"seed_activity: {created} claims added ({CLAIM_COUNT - created} already present).")


if __name__ == "__main__":
    asyncio.run(main())
