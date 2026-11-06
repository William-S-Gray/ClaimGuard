"""Claims application service — queue, detail, lifecycle actions, scoring."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import BusinessRuleError, NotFoundError
from app.core.logging import get_logger
from app.core.websocket import WSEventType, publish
from app.models.claim import Claim, ClaimFlag, ClaimItem, ShapContribution, TimelineEvent
from app.modules.fraudshield import ScoringContext, ScoringResult
from app.modules.fraudshield.rule_engine import HIGH_VALUE_THRESHOLD
from app.modules.fraudshield.service import fraudshield_service
from app.modules.fraudshield.signals import SignalInputs, derive_input_signals
from app.repositories.claim import ClaimRepository
from app.repositories.investigation import InvestigationRepository
from app.repositories.member import MemberRepository
from app.repositories.provider import ProviderRepository
from app.schemas.claim import ClaimIngest, claim_to_detail, claim_to_summary
from app.services.notifications import CHANNELS, NotificationService
from app.services.providers import ProviderService

# Hours until an un-actioned claim auto-approves / breaches SLA.
_AUTO_APPROVE_HOURS = 72
_SLA_HOURS = 48
_HISTORY_DAYS = 7  # how far back duplicate/splitting/frequency checks look

_REJECT_REASONS = {"REJECT_FRAUD", "REJECT_ERROR"}

log = get_logger(__name__)

# MemberGuard: channels a member can be alerted on (in fallback order), and the
# responses they can give. If every channel fails the alert still shows in-app.
_MEMBER_CHANNELS = ("WHATSAPP", "SMS", "USSD")
_MEMBER_RESPONSES = {"CONFIRMED", "DISPUTED"}

# Decided claims are final: they can't be re-decided, rescored, or sent back to
# the provider for more information.
_FINAL_DECISIONS = {"APPROVE", "REJECT_FRAUD", "REJECT_ERROR"}
INFO_REQUESTED = "Information requested"

# Investigation statuses that close a case (shared with the investigation service).
CASE_TERMINAL = {"RESOLVED", "CLOSED"}

# Approving over a member's dispute needs a written reason at least this long.
_OVERRIDE_REASON_MIN = 10
MEMBER_DISPUTED = "MEMBER_DISPUTED"
SLA_BREACHED = "SLA_BREACHED"
MEMBER_NO_REPLY = "MEMBER_NO_REPLY"
PERSISTENT_FLAGS = {MEMBER_DISPUTED, "RECOVERY_REQUIRED", SLA_BREACHED, MEMBER_NO_REPLY}

_PRIORITY_ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


def raise_priority(current: str | None, at_least: str) -> str:
    """The higher of two priorities (never lowers one)."""
    rank = {p: i for i, p in enumerate(_PRIORITY_ORDER)}
    return max(current or "LOW", at_least, key=lambda p: rank.get(p, 0))


def compose_rating_prompt(claim: Claim) -> str:
    """Asked once a member confirms a claim. Short enough for one SMS or USSD screen."""
    return (
        f"Thank you. How would you rate your care at {claim.provider.name}? "
        "Answer 1 (poor) to 5 (excellent)."
    )


def latest_info_request(claim: Claim) -> TimelineEvent | None:
    return next((t for t in reversed(claim.timeline) if t.event == INFO_REQUESTED), None)


SMS_LIMIT = 160  # one SMS segment; longer texts split and cost more


def compose_sms_alert(claim: Claim) -> str:
    """Single-segment SMS. The provider name is trimmed to fit, never the reply hint."""
    amount = f"US${float(claim.claimed_amount):,.2f}"
    head = f"ClaimGuard: {amount} claimed at "
    tail = f" on {claim.service_date or 'recently'}. Was this you? Reply YES or NO"
    room = SMS_LIMIT - len(head) - len(tail)
    provider = claim.provider.name
    if len(provider) > room:
        provider = provider[: room - 1] + "…"
    return head + provider + tail


def compose_ussd_menu(claim: Claim) -> str:
    """The menu a member sees after dialling settings.ussd_code."""
    amount = f"US${float(claim.claimed_amount):,.2f}"
    return f"ClaimGuard Alert\nClaim: {amount} at {claim.provider.name}\nCorrect?\n1=Yes 2=No"


def compose_member_alert(claim: Claim, channel: str = "WHATSAPP") -> str:
    """The verification message a member receives for a flagged claim."""
    if channel == "SMS":
        return compose_sms_alert(claim)
    if channel == "USSD":
        return compose_ussd_menu(claim)
    amount = f"US${float(claim.claimed_amount):,.2f}"
    provider = claim.provider.name
    service = claim.service_date or "recently"
    return (
        f"CIMAS ClaimGuard: a claim for {amount} at {provider} (service date "
        f"{service}) was submitted in your name. Did you receive this service? "
        f"Reply YES if correct, NO if not — your reply protects your benefits."
    )


class ClaimService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ClaimRepository(session)

    # ── Reads ─────────────────────────────────────────────────────────────────
    async def queue(
        self,
        *,
        search: str | None,
        priority: str | None,
        status: str | None,
        page: int,
        page_size: int,
        risk_level: str | None = None,
        investigator: str | None = None,
        sort: str = "risk",
    ) -> tuple[list[dict], int]:
        offset = (max(page, 1) - 1) * page_size
        rows, total = await self.repo.query_queue(
            search=search,
            priority=priority,
            status=status,
            risk_level=risk_level,
            investigator=investigator,
            sort=sort,
            offset=offset,
            limit=page_size,
        )
        return [claim_to_summary(c) for c in rows], total

    async def live_feed(self, limit: int = 5) -> list[dict]:
        rows = await self.repo.recent(limit=limit)
        return [claim_to_summary(c) for c in rows]

    async def get_detail(self, claim_ref: str) -> dict:
        claim = await self.repo.get_by_ref(claim_ref)
        if not claim:
            raise NotFoundError(f"Claim {claim_ref} not found")
        detail = claim_to_detail(claim)
        detail["similarClaims"] = [claim_to_summary(c) for c in await self.repo.similar(claim)]
        return detail

    # ── Lifecycle actions ──────────────────────────────────────────────────────
    async def _add_timeline(
        self, claim: Claim, event: str, description: str, actor: str, type_: str = "agent"
    ) -> None:
        claim.timeline.append(
            TimelineEvent(
                claim_id=claim.id,
                timestamp=datetime.now(UTC),
                event=event,
                description=description,
                actor=actor,
                type=type_,
            )
        )

    @staticmethod
    def _ensure_undecided(claim: Claim) -> None:
        if claim.decision in _FINAL_DECISIONS:
            raise BusinessRuleError(
                f"Claim is already decided ({claim.decision}); a decision is final"
            )

    async def _has_open_case(self, claim: Claim) -> bool:
        inv = await InvestigationRepository(self.session).get_by_claim(claim.id)
        return inv is not None and inv.status not in CASE_TERMINAL

    async def approve(self, claim_ref: str, actor: str, reason: str | None = None) -> dict:
        claim = await self.repo.get_by_ref(claim_ref)
        if not claim:
            raise NotFoundError(f"Claim {claim_ref} not found")
        self._ensure_undecided(claim)
        if claim.decision == "INFO_NEEDED":
            raise BusinessRuleError(
                "Waiting on the provider: wait for their answer or reject the claim"
            )
        if await self._has_open_case(claim):
            raise BusinessRuleError("Close the open investigation before approving")
        reason = (reason or "").strip()
        if claim.member_response == "DISPUTED" and len(reason) < _OVERRIDE_REASON_MIN:
            raise BusinessRuleError(
                "The member disputed this claim. Give a reason to approve it anyway"
            )

        claim.decision = "APPROVE"
        claim.approved_amount = claim.claimed_amount
        claim.updated_by = actor
        detail = f"Approved by {actor}"
        if claim.member_response == "DISPUTED":
            detail += f", overriding the member's dispute: {reason}"
        elif reason:
            detail += f": {reason}"
        await self._add_timeline(claim, "Claim approved", detail, actor)
        await self.session.flush()

        publish(WSEventType.QUEUE_UPDATED, {"claimRef": claim_ref, "decision": "APPROVE"})
        publish(WSEventType.DASHBOARD_UPDATED, {"reason": "claim_approved"})
        return claim_to_detail(claim)

    async def reject(
        self, claim_ref: str, reason: str, actor: str, note: str | None = None
    ) -> dict:
        if reason not in _REJECT_REASONS:
            raise BusinessRuleError(f"Invalid rejection reason: {reason}")
        claim = await self.repo.get_by_ref(claim_ref)
        if not claim:
            raise NotFoundError(f"Claim {claim_ref} not found")
        self._ensure_undecided(claim)

        claim.decision = reason
        claim.approved_amount = 0
        claim.updated_by = actor
        await self._add_timeline(
            claim,
            "Claim rejected",
            f"Rejected ({reason}) by {actor}" + (f": {note}" if note else ""),
            actor,
        )
        await self.session.flush()

        publish(WSEventType.QUEUE_UPDATED, {"claimRef": claim_ref, "decision": reason})
        publish(WSEventType.DASHBOARD_UPDATED, {"reason": "claim_rejected"})
        return claim_to_detail(claim)

    async def add_note(self, claim_ref: str, note: str, actor: str) -> dict:
        claim = await self.repo.get_by_ref(claim_ref)
        if not claim:
            raise NotFoundError(f"Claim {claim_ref} not found")
        claim.agent_notes = note
        await self._add_timeline(claim, "Note added", note, actor)
        await self.session.flush()
        return claim_to_detail(claim)

    # ── Info needed: send a claim back to the provider ─────────────────────────
    async def request_info(self, claim_ref: str, message: str, actor: str) -> dict:
        claim = await self.repo.get_by_ref(claim_ref)
        if not claim:
            raise NotFoundError(f"Claim {claim_ref} not found")
        if claim.decision in _FINAL_DECISIONS:
            raise BusinessRuleError("Claim is already decided")
        if claim.decision == "INFO_NEEDED":
            raise BusinessRuleError("Already waiting on the provider")

        previous = claim.decision
        # Stop the SLA clock while the provider has the ball; keep what was left.
        remaining = None
        if claim.sla_deadline:
            deadline = claim.sla_deadline.replace(tzinfo=claim.sla_deadline.tzinfo or UTC)
            remaining = max(0, int((deadline - datetime.now(UTC)).total_seconds()))
        claim.sla_deadline = None
        claim.decision = "INFO_NEEDED"
        claim.updated_by = actor
        await self._add_timeline(claim, INFO_REQUESTED, message, actor)
        # Remember where the claim was so the provider's answer puts it back.
        claim.timeline[-1].event_metadata = {
            "previousDecision": previous,
            "slaRemainingSeconds": remaining,
        }
        await self.session.flush()
        publish(WSEventType.QUEUE_UPDATED, {"claimRef": claim_ref, "decision": "INFO_NEEDED"})
        return claim_to_detail(claim)

    async def supply_info(
        self, claim_ref: str, provider_id: str, response: str, actor: str
    ) -> Claim:
        """The provider answers an info request; the claim goes back to review."""
        claim = await self.repo.get_by_ref(claim_ref)
        # Another provider's claim looks the same as a missing one.
        if not claim or str(claim.provider_id) != provider_id:
            raise NotFoundError(f"Claim {claim_ref} not found")
        if claim.decision != "INFO_NEEDED":
            raise BusinessRuleError("No information has been requested for this claim")

        meta = (latest_info_request(claim) or TimelineEvent()).event_metadata or {}
        claim.decision = meta.get("previousDecision") or "PEND_VERIFY"
        remaining = meta.get("slaRemainingSeconds")
        if remaining is not None:
            claim.sla_deadline = datetime.now(UTC) + timedelta(seconds=remaining)
        claim.updated_by = actor
        await self._add_timeline(
            claim, "Provider supplied information", response, actor, type_="provider"
        )
        await NotificationService(self.session).create(
            user_id=None,
            title="Provider replied",
            message=f"{claim.provider.name} answered the information request on {claim_ref}.",
            type_="info",
            link=f"/queue/{claim_ref}",
        )
        await self.session.flush()
        publish(WSEventType.QUEUE_UPDATED, {"claimRef": claim_ref, "decision": claim.decision})
        return claim

    async def record_member_rating(self, claim_ref: str, rating: int) -> dict:
        if not 1 <= rating <= 5:
            raise BusinessRuleError("Rating must be 1 to 5")
        claim = await self.repo.get_by_ref(claim_ref)
        if not claim:
            raise NotFoundError(f"Claim {claim_ref} not found")
        if claim.member_response != "CONFIRMED":
            raise BusinessRuleError("Only a confirmed claim can be rated")
        claim.member_rating = rating
        await self._add_timeline(
            claim, "Member rated care", f"{rating}/5 for {claim.provider.name}",
            "member", type_="member",
        )
        await self.session.flush()
        publish(WSEventType.DASHBOARD_UPDATED, {"reason": "member_rating"})
        return claim_to_detail(claim)

    # ── MemberGuard: instant alerts & member confirmation ───────────────────────
    async def send_member_alert(self, claim_ref: str, channel: str, actor: str) -> dict:
        """Send a member a verification alert for a claim and reset their response.

        Best-effort dispatch through the configured channel adapter (real for
        WhatsApp when credentials are set, log-only otherwise), and returns the
        composed message so the UI can render the alert exactly as sent.
        """
        channel = (channel or "").upper()
        if channel not in _MEMBER_CHANNELS:
            raise BusinessRuleError(f"Unsupported alert channel: {channel or '(none)'}")
        claim = await self.repo.get_by_ref(claim_ref)
        if not claim:
            raise NotFoundError(f"Claim {claim_ref} not found")
        # Re-alerting resets the member's answer, so it must never erase a dispute.
        if claim.member_response == "DISPUTED":
            raise BusinessRuleError(
                "The member has disputed this claim; resolve it through an investigation"
            )

        # Requested channel first, then the rest of the fallback chain.
        delivered = False
        message = compose_member_alert(claim, channel)
        for candidate in (channel, *(c for c in _MEMBER_CHANNELS if c != channel)):
            adapter = CHANNELS.get(candidate)
            text = compose_member_alert(claim, candidate)
            try:
                if adapter and await adapter.send(
                    claim.member.phone, "Claim verification", text
                ):
                    channel, delivered, message = candidate, True, text
                    break
            except Exception as exc:  # noqa: BLE001 - try the next channel
                log.warning(
                    "member_alert.channel_failed",
                    claim_ref=claim_ref,
                    channel=candidate,
                    error=str(exc),
                )

        claim.member_notification_sent = True
        claim.member_notification_channel = channel
        claim.member_alert_sent_at = datetime.now(UTC)
        # A fresh alert gets a fresh reply window.
        claim.flags = [f for f in claim.flags if f.code != MEMBER_NO_REPLY]
        claim.member_response = "PENDING"
        claim.member_rating = None  # a re-check asks for a fresh rating
        claim.updated_by = actor

        if delivered:
            await self._add_timeline(
                claim,
                "Member alert sent",
                f"{channel} verification alert sent to {claim.member.name} "
                f"({claim.member.phone})",
                actor,
                type_="system",
            )
        else:
            # Nothing reached the member: say so, and hand it to a person.
            log.warning("member_alert.undelivered", claim_ref=claim_ref)
            await self._add_timeline(
                claim,
                "Member alert not delivered",
                f"No channel could reach {claim.member.name}; the member can still "
                f"answer by dialling {settings.ussd_code}. Follow up manually.",
                actor,
                type_="system",
            )
            await NotificationService(self.session).create(
                user_id=None,
                title="Member alert needs manual follow-up",
                message=f"Verification alert for claim {claim_ref} could not be delivered.",
                type_="warning",
                link=f"/queue/{claim_ref}",
            )
        await self.session.flush()
        publish(WSEventType.QUEUE_UPDATED, {"claimRef": claim_ref})
        return {
            "claim": claim_to_detail(claim),
            "alert": {
                "channel": channel,
                "to": claim.member.phone,
                "memberName": claim.member.name,
                "message": message,
                "delivered": delivered,
                "ussdCode": settings.ussd_code,
            },
        }

    async def record_member_response(
        self, claim_ref: str, response: str, actor: str = "member"
    ) -> dict:
        """Record a member's confirm/dispute reply; a dispute escalates the claim."""
        response = (response or "").upper()
        if response not in _MEMBER_RESPONSES:
            raise BusinessRuleError(f"Invalid member response: {response or '(none)'}")
        claim = await self.repo.get_by_ref(claim_ref)
        if not claim:
            raise NotFoundError(f"Claim {claim_ref} not found")
        if not claim.member_notification_sent:
            raise BusinessRuleError("No verification alert has been sent for this claim")
        # A dispute stands; a confirmation can still become a dispute (the member
        # calls back), but nothing turns a dispute back into a confirmation.
        if claim.member_response == "DISPUTED":
            raise BusinessRuleError("The member has already disputed this claim")
        if claim.member_response == response:
            raise BusinessRuleError(f"The member has already {response.lower()} this claim")

        claim.member_response = response
        if response == "DISPUTED":
            # The member says they never received the service — strongest possible
            # fraud signal: escalate to the top of the queue and flag it.
            claim.priority = "CRITICAL"
            if not any(f.code == MEMBER_DISPUTED for f in claim.flags):
                claim.flags.append(
                    ClaimFlag(
                        claim_id=claim.id,
                        code=MEMBER_DISPUTED,
                        severity="CRITICAL",
                        detail="Member reported they did not receive this service",
                    )
                )

        verb = "confirmed" if response == "CONFIRMED" else "disputed"
        via = claim.member_notification_channel or "alert"
        await self._add_timeline(
            claim,
            f"Member {verb} claim",
            f"Member {verb} the service via {via}",
            actor,
            type_="member",
        )
        await self.session.flush()
        await ProviderService(self.session).recalculate(
            claim.provider.code, reason=f"member {verb} {claim_ref}"
        )
        publish(
            WSEventType.QUEUE_UPDATED,
            {"claimRef": claim_ref, "memberResponse": response},
        )
        publish(WSEventType.DASHBOARD_UPDATED, {"reason": "member_response"})
        return claim_to_detail(claim)

    # ── Ingestion ──────────────────────────────────────────────────────────────
    async def _unique_claim_ref(self) -> str:
        """Generate a CG-##### reference not already in use."""
        for _ in range(10):
            ref = f"CG-{uuid.uuid4().int % 100000:05d}"
            if not await self.repo.get_by_ref(ref):
                return ref
        # Astronomically unlikely fallback: use a wider space.
        return f"CG-{uuid.uuid4().hex[:8]}"

    async def ingest(self, payload: ClaimIngest, actor: str = "nh263") -> dict:
        """Create, signal-populate, and score an incoming claim.

        Derives the four FraudShield input signals (via fraudshield.signals) and
        persists them on the claim so scoring — now and on any later rescore —
        sees the same inputs. Idempotent when a claim_ref is supplied.
        """
        if payload.claim_ref and (existing := await self.repo.get_by_ref(payload.claim_ref)):
            return claim_to_detail(existing)  # idempotent re-delivery

        member = await MemberRepository(self.session).get_by_number(payload.member_number)
        if not member:
            raise NotFoundError(f"Member {payload.member_number} not found")
        provider = await ProviderRepository(self.session).get_by_code(payload.provider_code)
        if not provider:
            raise NotFoundError(f"Provider {payload.provider_code} not found")

        signals = derive_input_signals(
            SignalInputs(
                item_descriptions=[i.description for i in payload.items],
                member_conditions=member.conditions or [],
                provider_flags_90d=provider.flags_90d,
                provider_trust_score=provider.trust_score,
                service_date=payload.service_date,
                prescription_date=payload.prescription_date,
                has_biometric=payload.has_biometric,
                syndicate_signal=payload.syndicate_signal,
            )
        )

        now = datetime.now(UTC)
        claim = Claim(
            claim_ref=payload.claim_ref or await self._unique_claim_ref(),
            nh263_ref=payload.nh263_ref,
            member_id=member.id,
            provider_id=provider.id,
            service_date=payload.service_date,
            submitted_at=now,
            claimed_amount=payload.claimed_amount,
            member_shortfall=payload.member_shortfall,
            expected_shortfall_min=payload.expected_shortfall_min,
            expected_shortfall_max=payload.expected_shortfall_max,
            sla_deadline=now + timedelta(hours=_SLA_HOURS),
            **signals,
        )
        claim.items = [
            ClaimItem(
                description=i.description,
                quantity=i.quantity,
                unit_price=i.unit_price,
                total=i.total,
                icd10_code=i.icd10_code,
                nappi_code=i.nappi_code,
            )
            for i in payload.items
        ]
        claim.timeline = [
            TimelineEvent(
                timestamp=now,
                event="Claim submitted",
                description="Claim received via NH263 ingestion",
                actor=actor,
                type="system",
            )
        ]
        await self.repo.add(claim)
        # Re-load with relationships (member/provider/items) eagerly populated so
        # scoring and serialization never trigger an async lazy load.
        claim = await self.repo.get_by_ref(claim.claim_ref) or claim

        # Score on arrival using the freshly-derived signals.
        result = fraudshield_service.score(await self.scoring_context(claim))
        self._apply_result(claim, result)
        claim.auto_approve_at = (
            now + timedelta(hours=_AUTO_APPROVE_HOURS) if result.decision == "APPROVE" else None
        )
        claim.timeline.append(
            TimelineEvent(
                timestamp=datetime.now(UTC),
                event=f"Risk score: {result.risk_score}",
                description=f"FraudShield decision: {result.decision}",
                actor="ClaimGuard AI",
                type="system",
            )
        )
        await self.session.flush()

        scored_payload = {
            "claimRef": claim.claim_ref,
            "member": member.name,
            "riskScore": result.risk_score,
            "decision": result.decision,
            "latencyMs": result.latency_ms,
        }
        publish(WSEventType.NH263_WEBHOOK, {"claimRef": claim.claim_ref, "source": "NH263"})
        publish(WSEventType.CLAIM_SCORED, scored_payload)
        await NotificationService(self.session).create_from_event("claim_scored", scored_payload)
        await ProviderService(self.session).recalculate(
            provider.code, reason=f"claim {claim.claim_ref} scored"
        )

        fresh = await self.repo.get_by_ref(claim.claim_ref)
        return claim_to_detail(fresh or claim)

    # ── Scoring (FraudShield) ──────────────────────────────────────────────────
    @staticmethod
    def context_from_claim(claim: Claim) -> ScoringContext:
        return ScoringContext(
            claim_ref=claim.claim_ref,
            claimed_amount=float(claim.claimed_amount),
            member_shortfall=float(claim.member_shortfall),
            expected_shortfall_min=float(claim.expected_shortfall_min),
            expected_shortfall_max=float(claim.expected_shortfall_max),
            provider_trust_score=claim.provider.trust_score if claim.provider else 100,
            provider_flags_90d=claim.provider.flags_90d if claim.provider else 0,
            member_conditions=claim.member.conditions if claim.member else [],
            item_descriptions=[i.description for i in claim.items],
            prescription_after_service=claim.prescription_after_service,
            has_biometric=claim.has_biometric,
            chronic_drug_no_condition=claim.chronic_drug_no_condition,
            syndicate_signal=claim.syndicate_signal,
            items_total=sum(float(i.total) for i in claim.items) if claim.items else None,
        )

    async def scoring_context(self, claim: Claim) -> ScoringContext:
        """The claim's own signals plus what the member's other claims say about it."""
        ctx = self.context_from_claim(claim)
        history = await self.repo.member_history(claim, days=_HISTORY_DAYS)
        # A claim rejected as a data error is meant to be corrected and re-sent:
        # its corrected copy is not a duplicate, and its amount never counted.
        history = [c for c in history if c.decision != "REJECT_ERROR"]

        def _at(c: Claim) -> datetime:
            t = c.submitted_at or datetime.now(UTC)
            return t if t.tzinfo else t.replace(tzinfo=UTC)

        me = _at(claim)
        # Only an *earlier* claim makes this one a duplicate, so the original
        # isn't flagged when it is rescored later.
        earlier = [c for c in history if (_at(c), c.claim_ref) < (me, claim.claim_ref)]
        amount = float(claim.claimed_amount)
        dup = next(
            (
                c for c in earlier
                if (claim.nh263_ref and c.nh263_ref == claim.nh263_ref)
                or (
                    c.member_id == claim.member_id
                    and c.provider_id == claim.provider_id
                    and claim.service_date
                    and c.service_date == claim.service_date
                    and abs(float(c.claimed_amount) - amount) < 0.01
                )
            ),
            None,
        )
        ctx.duplicate_of = dup.claim_ref if dup else None

        same_visit = [
            c for c in history
            if c.member_id == claim.member_id
            and c.provider_id == claim.provider_id
            and claim.service_date
            and c.service_date == claim.service_date
        ]
        parts = [float(c.claimed_amount) for c in same_visit]
        ctx.split_suspected = (
            bool(parts)
            and amount < HIGH_VALUE_THRESHOLD
            and all(p < HIGH_VALUE_THRESHOLD for p in parts)
            and amount + sum(parts) >= HIGH_VALUE_THRESHOLD
        )

        week = timedelta(days=_HISTORY_DAYS)
        ctx.member_claims_7d = sum(
            1 for c in history
            if c.member_id == claim.member_id and me - week <= _at(c) <= me
        )
        return ctx

    def _apply_result(self, claim: Claim, result: ScoringResult) -> None:
        # Flags raised by people or by the lifecycle (a member's dispute, a breached
        # SLA) are facts about the claim, not model output: they outlive a rescore.
        kept = [f for f in claim.flags if f.code in PERSISTENT_FLAGS]
        disputed = any(f.code == MEMBER_DISPUTED for f in kept)
        claim.risk_score = result.risk_score
        claim.risk_level = result.risk_level
        claim.decision = result.decision
        claim.priority = "CRITICAL" if disputed else result.priority
        if any(f.code == SLA_BREACHED for f in kept):
            claim.priority = raise_priority(claim.priority, "HIGH")
        claim.ai_explanation = result.explanation
        claim.latency_ms = result.latency_ms
        claim.flags = [
            ClaimFlag(code=f.code, severity=f.severity, detail=f.detail)
            for f in result.flags
        ] + [
            ClaimFlag(code=f.code, severity=f.severity, detail=f.detail) for f in kept
        ]
        claim.shap_contributions = [
            ShapContribution(
                feature=s.feature, contribution=s.contribution, direction=s.direction
            )
            for s in result.shap
        ]

    async def rescore(self, claim_ref: str, actor: str = "system") -> dict:
        claim = await self.repo.get_by_ref(claim_ref)
        if not claim:
            raise NotFoundError(f"Claim {claim_ref} not found")
        # Rescoring sets the decision, so it must not overwrite a person's decision
        # or pull a claim out from under a pending provider request.
        self._ensure_undecided(claim)
        if claim.decision == "INFO_NEEDED":
            raise BusinessRuleError("Waiting on the provider; rescore after they answer")
        previous = claim.decision
        result = fraudshield_service.score(await self.scoring_context(claim))
        self._apply_result(claim, result)
        # A low score must not approve a claim that a person still has to clear.
        held = claim.decision == "APPROVE" and (
            claim.member_response == "DISPUTED" or await self._has_open_case(claim)
        )
        if held:
            claim.decision = previous
        await self._add_timeline(
            claim,
            "FraudShield rescored",
            f"Risk {result.risk_score} → {result.decision}"
            + (f" (held at {previous} pending review)" if held else ""),
            actor,
            type_="system",
        )
        await self.session.flush()

        scored_payload = {
            "claimRef": claim.claim_ref,
            "member": claim.member.name if claim.member else "Member",
            "riskScore": result.risk_score,
            "decision": claim.decision,
            "latencyMs": result.latency_ms,
        }
        publish(WSEventType.CLAIM_SCORED, scored_payload)
        await NotificationService(self.session).create_from_event(
            "claim_scored", scored_payload
        )
        return claim_to_detail(claim)
