"""Idempotent database seeder for ClaimGuard 360° demo data.

Run with:  python -m scripts.seed
Deterministic UUIDs (uuid5) keep relationships stable across re-runs.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionFactory, engine
from app.core.logging import configure_logging, get_logger
from app.core.security import hash_password
from app.models import Base
from app.models.audit import AuditLog
from app.models.claim import Claim, ClaimFlag, ClaimItem, ShapContribution, TimelineEvent
from app.models.member import Member
from app.models.notification import Notification
from app.models.provider import Provider
from app.models.user import Permission, Role, User
from scripts import seed_data as sd

log = get_logger("seed")

# Fixed namespace so seed ids map to stable UUIDs across runs/environments.
_NS = uuid.UUID("c1a16c4a-0000-4000-8000-000000000001")


def det_id(key: str) -> uuid.UUID:
    return uuid.uuid5(_NS, key)


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def seed_rbac(session) -> None:
    perms: dict[str, Permission] = {}
    for code, desc in sd.PERMISSIONS.items():
        existing = (
            await session.execute(select(Permission).where(Permission.code == code))
        ).scalar_one_or_none()
        if not existing:
            existing = Permission(id=det_id(f"perm:{code}"), code=code, description=desc)
            session.add(existing)
        perms[code] = existing
    await session.flush()

    for name, desc in sd.ROLES.items():
        role = (
            await session.execute(select(Role).where(Role.name == name))
        ).scalar_one_or_none()
        if not role:
            role = Role(id=det_id(f"role:{name}"), name=name, description=desc)
            session.add(role)
        role.permissions = [perms[c] for c in sd.ROLE_PERMISSIONS[name]]
    await session.flush()
    log.info("seed.rbac", roles=len(sd.ROLES), permissions=len(sd.PERMISSIONS))


async def seed_admin(session) -> None:
    email = settings.first_admin_email.lower()
    existing = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing:
        return
    admin_role = (
        await session.execute(select(Role).where(Role.name == "admin"))
    ).scalar_one()
    user = User(
        id=det_id(f"user:{email}"),
        email=email,
        full_name="ClaimGuard Admin",
        hashed_password=hash_password(settings.first_admin_password),
        is_active=True,
        is_superuser=True,
    )
    user.roles = [admin_role]
    session.add(user)
    await session.flush()
    log.info("seed.admin", email=email)


TEAM = [
    ("Rudo Chidziva", "rudo.chidziva@claimguard.co.zw", "analyst"),
    ("Farai Nyathi", "farai.nyathi@claimguard.co.zw", "agent"),
    ("Chipo Marufu", "chipo.marufu@claimguard.co.zw", "agent"),
    ("Tapiwa Sithole", "tapiwa.sithole@claimguard.co.zw", "auditor"),
]


async def seed_team(session) -> None:
    """Additional agents/analysts so cases can be assigned to real people."""
    for full_name, email, role_name in TEAM:
        email = email.lower()
        if (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none():
            continue
        role = (
            await session.execute(select(Role).where(Role.name == role_name))
        ).scalar_one()
        user = User(
            id=det_id(f"user:{email}"),
            email=email,
            full_name=full_name,
            hashed_password=hash_password(settings.first_admin_password),
            is_active=True,
        )
        user.roles = [role]
        session.add(user)
    await session.flush()
    log.info("seed.team", count=len(TEAM))


async def seed_members(session) -> None:
    for m in sd.MEMBERS:
        mid = det_id(m["id"])
        if await session.get(Member, mid):
            continue
        session.add(
            Member(
                id=mid,
                member_number=m["member_number"],
                name=m["name"],
                plan=m["plan"],
                city=m["city"],
                annual_benefit=m["annual_benefit"],
                benefit_used=m["benefit_used"],
                conditions=m["conditions"],
                phone=m["phone"],
                email=m["email"],
                date_of_birth=m["date_of_birth"],
            )
        )
    await session.flush()
    log.info("seed.members", count=len(sd.MEMBERS))


async def seed_providers(session) -> None:
    for p in sd.PROVIDERS:
        pid = det_id(p["id"])
        if await session.get(Provider, pid):
            continue
        session.add(
            Provider(
                id=pid,
                code=p["code"],
                name=p["name"],
                type=p["type"],
                city=p["city"],
                trust_score=p["trust_score"],
                badge=p["badge"],
                shortfall_index=p["shortfall_index"],
                dispute_rate=p["dispute_rate"],
                flags_90d=p["flags_90d"],
                total_claims=p["total_claims"],
                average_claim_value=p["average_claim_value"],
                phone=p["phone"],
                address=p["address"],
                registration_date=p["registration_date"],
                last_audit_date=p["last_audit_date"],
            )
        )
    await session.flush()
    log.info("seed.providers", count=len(sd.PROVIDERS))


async def seed_provider_users(session) -> None:
    """Demo provider-portal accounts, each linked to exactly one provider."""
    role = (await session.execute(select(Role).where(Role.name == "provider"))).scalar_one()
    for full_name, email, provider_seed_id in sd.PROVIDER_USERS:
        if (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none():
            continue
        user = User(
            id=det_id(f"user:{email}"),
            email=email,
            full_name=full_name,
            hashed_password=hash_password(settings.first_admin_password),
            is_active=True,
            provider_id=det_id(provider_seed_id),
        )
        user.roles = [role]
        session.add(user)
    await session.flush()
    log.info("seed.provider_users", count=len(sd.PROVIDER_USERS))


async def seed_claims(session) -> None:
    for c in sd.CLAIMS:
        cid = det_id(c["id"])
        if await session.get(Claim, cid):
            continue
        submitted = parse_dt(c["submitted_at"]) or datetime.now(UTC)
        claim = Claim(
            id=cid,
            claim_ref=c["claim_ref"],
            nh263_ref=c["nh263_ref"],
            member_id=det_id(c["member_id"]),
            provider_id=det_id(c["provider_id"]),
            service_date=c["service_date"],
            submitted_at=submitted,
            claimed_amount=c["claimed_amount"],
            approved_amount=c.get("approved_amount"),
            member_shortfall=c["member_shortfall"],
            expected_shortfall_min=c["expected_shortfall_min"],
            expected_shortfall_max=c["expected_shortfall_max"],
            risk_score=c["risk_score"],
            risk_level=c["risk_level"],
            decision=c["decision"],
            priority=c["priority"],
            ai_explanation=c["ai_explanation"],
            latency_ms=c["latency_ms"],
            auto_approve_at=parse_dt(c.get("auto_approve_at")),
            sla_deadline=parse_dt(c.get("sla_deadline")),
            member_notification_sent=c.get("member_notification_sent", False),
            member_notification_channel=c.get("member_notification_channel"),
            member_response=c.get("member_response", "PENDING"),
            member_rating=c.get("member_rating"),
        )
        claim.items = [
            ClaimItem(
                description=desc, quantity=qty, unit_price=up, total=tot,
                icd10_code=icd, nappi_code=nappi,
            )
            for (desc, qty, up, tot, icd, nappi) in c["items"]
        ]
        claim.flags = [
            ClaimFlag(code=code, severity=sev) for (code, sev) in c["flags"]
        ]
        # Derive the persisted FraudShield input signals from the flags so a
        # later rescore reproduces the same inputs (keeps seed data DRY).
        flag_codes = {code for (code, _sev) in c["flags"]}
        claim.prescription_after_service = "PRESCRIPTION_DATE_AFTER_SERVICE" in flag_codes
        claim.has_biometric = "HIGH_VALUE_NO_BIOMETRIC" not in flag_codes
        claim.chronic_drug_no_condition = "CHRONIC_DRUG_NO_CONDITION_REGISTERED" in flag_codes
        claim.syndicate_signal = "POTENTIAL_FRAUD_SYNDICATE_DETECTED" in flag_codes
        claim.shap_contributions = [
            ShapContribution(feature=f, contribution=val, direction=d)
            for (f, val, d) in c["shap"]
        ]
        claim.timeline = [
            TimelineEvent(
                timestamp=submitted,
                event="Claim submitted",
                description="Claim received via NH263 webhook",
                actor="NH263 System",
                type="system",
            ),
            TimelineEvent(
                timestamp=submitted,
                event=f"Risk score: {c['risk_score']}",
                description=f"FraudShield decision: {c['decision']}",
                actor="ClaimGuard AI",
                type="system",
            ),
        ]
        if req := c.get("info_request"):
            # Waiting on the provider: SLA paused, time left kept (as ClaimService does).
            claim.timeline.append(
                TimelineEvent(
                    timestamp=parse_dt(req["at"]),
                    event="Information requested",
                    description=req["message"],
                    actor=req["actor"],
                    type="agent",
                    event_metadata={
                        "previousDecision": req["previous_decision"],
                        "slaRemainingSeconds": req["sla_left_hours"] * 3600,
                    },
                )
            )
        session.add(claim)
    await session.flush()
    log.info("seed.claims", count=len(sd.CLAIMS))


async def seed_notifications(session) -> None:
    seeds = [
        {
            "key": "notif:1",
            "title": "⚠️ Moderate Risk Claim",
            "message": "Claim CG-00441 has been held for review (67 risk score).",
            "type": "warning",
            "link": "/queue/CG-00441",
            "read": False,
        },
        {
            "key": "notif:2",
            "title": "✓ Member Confirmed",
            "message": "Claim CG-00291 was confirmed by member via USSD.",
            "type": "info",
            "link": "/queue/CG-00291",
            "read": False,
        },
        {
            "key": "notif:3",
            "title": "📉 Provider Alert",
            "message": "City Pharmacy Bulawayo TrustScore caution warning.",
            "type": "alert",
            "link": "/trustscore/PROV-BYO-00441",
            "read": True,
        },
    ]
    for s in seeds:
        nid = det_id(s["key"])
        if await session.get(Notification, nid):
            continue
        session.add(
            Notification(
                id=nid,
                user_id=None,  # broadcast to all users
                title=s["title"],
                message=s["message"],
                type=s["type"],
                link=s["link"],
                read=s["read"],
            )
        )
    await session.flush()
    log.info("seed.notifications", count=len(seeds))


async def seed_audit(session) -> None:
    """A representative slice of the who-did-what-when trail so the Audit page is
    populated on first load. Real usage appends to this organically."""
    admin_email = settings.first_admin_email.lower()
    people = {
        "admin": (det_id(f"user:{admin_email}"), "ClaimGuard Admin", admin_email),
        "analyst": (
            det_id("user:rudo.chidziva@claimguard.co.zw"),
            "Rudo Chidziva",
            "rudo.chidziva@claimguard.co.zw",
        ),
        "agent": (
            det_id("user:farai.nyathi@claimguard.co.zw"),
            "Farai Nyathi",
            "farai.nyathi@claimguard.co.zw",
        ),
        "auditor": (
            det_id("user:tapiwa.sithole@claimguard.co.zw"),
            "Tapiwa Sithole",
            "tapiwa.sithole@claimguard.co.zw",
        ),
    }
    now = datetime.now(UTC)
    # (key, who, action, entity_type, entity_id, minutes_ago, changes)
    events = [
        ("aud:1", "admin", "auth.login", "user", None, 240, {}),
        ("aud:2", "agent", "claim.view", "claim", "CG-00291", 232, {}),
        ("aud:3", "agent", "claim.reject", "claim", "CG-00291", 228,
         {"reason": "REJECT_FRAUD"}),
        ("aud:4", "analyst", "auth.login", "user", None, 180, {}),
        ("aud:5", "analyst", "claim.view", "claim", "CG-00441", 176, {}),
        ("aud:6", "analyst", "claim.approve", "claim", "CG-00441", 174, {}),
        ("aud:7", "auditor", "auth.login", "user", None, 90, {}),
        ("aud:8", "auditor", "claim.view", "claim", "CG-00112", 86, {}),
        ("aud:9", "admin", "user.create", "user", None, 45,
         {"email": "tapiwa.sithole@claimguard.co.zw", "roles": ["auditor"]}),
        ("aud:10", "agent", "claim.view", "claim", "CG-00088", 12, {}),
    ]
    for key, who, action, etype, eid, mins, changes in events:
        aid = det_id(key)
        if await session.get(AuditLog, aid):
            continue
        actor_id, _name, actor_email = people[who]
        # Login rows historically carried only the email (no actor_id), mirroring
        # the auth flow; everything else records the acting user's id.
        session.add(
            AuditLog(
                id=aid,
                actor_id=None if action == "auth.login" else actor_id,
                actor_email=actor_email if action == "auth.login" else None,
                action=action,
                entity_type=etype,
                entity_id=eid,
                ip_address="102.32.44.10",
                changes=changes,
                created_at=now - timedelta(minutes=mins),
            )
        )
    await session.flush()
    log.info("seed.audit", count=len(events))


async def main(create_tables: bool = False) -> None:
    configure_logging()
    log.info("seed.start", db=settings.database_url.split("@")[-1], demo=settings.demo_mode)
    if create_tables:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    async with SessionFactory() as session:
        # Always provision RBAC + the first admin (from FIRST_ADMIN_* settings).
        await seed_rbac(session)
        await seed_admin(session)
        # Demo fixtures (team, members, providers, claims, notifications) are only
        # loaded in demo mode — a real production DB stays clean of fake data.
        if settings.demo_mode:
            await seed_team(session)
            await seed_members(session)
            await seed_providers(session)
            await seed_provider_users(session)
            await seed_claims(session)
            await seed_notifications(session)
            await seed_audit(session)
        await session.commit()
    await engine.dispose()
    log.info("seed.done", demo=settings.demo_mode)


if __name__ == "__main__":
    import sys

    asyncio.run(main(create_tables="--create-tables" in sys.argv))
