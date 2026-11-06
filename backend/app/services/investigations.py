"""Investigation workflow service."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError, NotFoundError, ValidationError
from app.models.claim import ClaimFlag, TimelineEvent
from app.models.investigation import Investigation, InvestigationComment
from app.repositories.claim import ClaimRepository
from app.repositories.investigation import (
    CommentRepository,
    InvestigationRepository,
)
from app.repositories.user import UserRepository
from app.schemas.investigation import CommentOut, InvestigationOut
from app.services.claims import CASE_TERMINAL as _TERMINAL
from app.services.claims import is_on_hold
from app.services.notifications import NotificationService

# Kept in step with the frontend's CASE_STATUSES / CASE_RESOLUTIONS.
STATUSES = {"OPEN", "IN_PROGRESS", "ESCALATED", "RESOLVED", "CLOSED"}
RESOLUTIONS = {"CONFIRMED_FRAUD", "FALSE_POSITIVE", "DATA_ERROR", "RECOVERED", "NO_ACTION"}
PRIORITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
_OPEN_DECISIONS = {"PEND_VERIFY", "PEND_INVESTIGATE", "INFO_NEEDED"}


def _to_dict(inv: Investigation) -> dict:
    claim = inv.claim
    return InvestigationOut(
        id=str(inv.id),
        claim_id=str(inv.claim_id),
        claim_ref=claim.claim_ref if claim else None,
        decision=claim.decision if claim else None,
        risk_score=claim.risk_score if claim else None,
        member_name=claim.member.name if claim and claim.member else None,
        provider_name=claim.provider.name if claim and claim.provider else None,
        claimed_amount=float(claim.claimed_amount) if claim else None,
        assigned_to=str(inv.assigned_to) if inv.assigned_to else None,
        assigned_to_name=inv.assignee.full_name if inv.assignee else None,
        status=inv.status,
        priority=inv.priority,
        resolution=inv.resolution,
        resolution_notes=inv.resolution_notes,
        resolved_at=inv.resolved_at,
        created_at=inv.created_at,
        comments=[
            CommentOut(
                id=str(c.id),
                author_name=c.author_name,
                body=c.body,
                created_at=c.created_at,
            )
            for c in inv.comments
        ],
    ).model_dump(by_alias=True)


class InvestigationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = InvestigationRepository(session)
        self.comments = CommentRepository(session)
        self.claims = ClaimRepository(session)
        self.users = UserRepository(session)
        self.notifications = NotificationService(session)

    async def _notify_assignment(self, inv: Investigation, assignee_id: str, actor: str) -> None:
        """Notify a user their case was assigned — unless they assigned it themselves.

        Delivered in-app (bell + WS) AND by email to the assignee. Email is a
        no-op log when SMTP isn't configured (demo)."""
        if not assignee_id or assignee_id == actor:
            return
        claim_ref = inv.claim.claim_ref if inv.claim else "a claim"
        assignee = await self.users.get(assignee_id)
        link = f"/investigations/{inv.id}"
        await self.notifications.create(
            user_id=assignee_id,
            title="📋 Case Assigned to You",
            message=(
                f"You've been assigned the investigation for claim {claim_ref}. "
                f"Open it in ClaimGuard: {link}"
            ),
            type_="info",
            channel="EMAIL",
            link=link,
            recipient=assignee.email if assignee else None,
        )

    async def list_page(
        self, *, status: str | None, page: int, page_size: int
    ) -> tuple[list[dict], int]:
        offset = (max(page, 1) - 1) * page_size
        rows, total = await self.repo.list_page(
            status=status, offset=offset, limit=page_size
        )
        return [_to_dict(i) for i in rows], total

    async def get(self, investigation_id: str) -> dict:
        inv = await self.repo.get(investigation_id)
        if not inv:
            raise NotFoundError("Investigation not found")
        return _to_dict(inv)

    async def open_case(
        self, claim_ref: str, priority: str, assigned_to: str | None, actor: str
    ) -> dict:
        claim = await self.claims.get_by_ref(claim_ref)
        if not claim:
            raise NotFoundError(f"Claim {claim_ref} not found")
        existing = await self.repo.get_by_claim(claim.id)
        if existing:
            return _to_dict(existing)
        self._check_values({"priority": priority or None})
        if assigned_to:
            await self._check_assignee(assigned_to)
        # An investigation stops a model approval that hasn't been paid yet.
        if is_on_hold(claim):
            claim.decision = "PEND_INVESTIGATE"
            claim.auto_approve_at = None
            claim.approved_amount = None
            claim.timeline.append(TimelineEvent(
                claim_id=claim.id, timestamp=datetime.now(UTC), event="Payment stopped",
                description="Model approval withdrawn before payment: investigation opened",
                actor=actor, type="system",
            ))
        inv = await self.repo.create(
            claim_id=claim.id,
            assigned_to=assigned_to,
            priority=priority or claim.priority,
            status="OPEN",
            created_by=actor,
        )
        await self.session.flush()
        # Reload so the claim relationship (selectin) is populated for _to_dict.
        inv = await self.repo.get(inv.id)
        if assigned_to:
            await self._notify_assignment(inv, assigned_to, actor)
        return _to_dict(inv)

    async def _check_assignee(self, user_id: str) -> None:
        """Cases go to active insurer staff only — never a provider login."""
        try:
            user = await self.users.get(user_id)
        except Exception:  # noqa: BLE001 - a malformed id is just an unknown user
            user = None
        if not user or not user.is_active or user.provider_id is not None:
            raise ValidationError("Cases can only be assigned to active staff")

    @staticmethod
    def _check_values(changes: dict) -> None:
        for key, allowed in (
            ("status", STATUSES), ("resolution", RESOLUTIONS), ("priority", PRIORITIES)
        ):
            value = changes.get(key)
            if value is not None and value not in allowed:
                raise ValidationError(f"Unknown {key}: {value}")

    async def _apply_outcome(self, inv: Investigation, actor: str) -> None:
        """A case closed as confirmed fraud decides its claim."""
        claim = inv.claim
        if inv.resolution != "CONFIRMED_FRAUD" or claim is None:
            return
        now = datetime.now(UTC)
        if claim.decision in _OPEN_DECISIONS:
            claim.decision = "REJECT_FRAUD"
            claim.approved_amount = 0
            claim.updated_by = actor
            event = "Claim rejected"
            detail = "Rejected (REJECT_FRAUD): investigation confirmed fraud"
        elif claim.decision == "APPROVE":
            # Already paid: the decision stands, but the money has to come back.
            if not any(f.code == "RECOVERY_REQUIRED" for f in claim.flags):
                claim.flags.append(ClaimFlag(
                    claim_id=claim.id, code="RECOVERY_REQUIRED", severity="CRITICAL",
                    detail="Paid claim confirmed as fraud; recover the payment",
                ))
            event, detail = "Recovery required", "Investigation confirmed fraud on a paid claim"
        else:
            return
        claim.timeline.append(TimelineEvent(
            claim_id=claim.id, timestamp=now, event=event, description=detail,
            actor=actor, type="agent",
        ))

    async def update(self, investigation_id: str, actor: str, **changes) -> dict:
        inv = await self.repo.get(investigation_id)
        if not inv:
            raise NotFoundError("Investigation not found")
        self._check_values(changes)
        if changes.get("assigned_to"):
            await self._check_assignee(changes["assigned_to"])

        was_closed = inv.status in _TERMINAL
        next_status = changes.get("status") or inv.status
        next_resolution = changes.get("resolution") or inv.resolution
        if next_status in _TERMINAL and not next_resolution:
            raise BusinessRuleError("Choose a resolution before closing the case")
        if was_closed and next_status in _TERMINAL and next_resolution != inv.resolution:
            raise BusinessRuleError("Reopen the case before changing its resolution")

        prev_assignee = str(inv.assigned_to) if inv.assigned_to else None
        for key, value in changes.items():
            if value is None:
                continue
            # Empty string on assigned_to means "unassign".
            if key == "assigned_to" and value == "":
                inv.assigned_to = None
            else:
                setattr(inv, key, value)
        if inv.status in _TERMINAL and inv.resolved_at is None:
            inv.resolved_at = datetime.now(UTC)
        elif inv.status not in _TERMINAL:
            inv.resolved_at = None  # reopened
        if inv.status in _TERMINAL and not was_closed:
            await self._apply_outcome(inv, actor)
        inv.updated_by = actor
        await self.session.flush()
        # Refresh the assignee relationship so its name reflects the new assigned_to.
        await self.session.refresh(inv, attribute_names=["assignee"])
        new_assignee = str(inv.assigned_to) if inv.assigned_to else None
        if new_assignee and new_assignee != prev_assignee:
            await self._notify_assignment(inv, new_assignee, actor)
        return _to_dict(inv)

    async def add_comment(
        self, investigation_id: str, body: str, author_id: str, author_name: str
    ) -> dict:
        inv = await self.repo.get(investigation_id)
        if not inv:
            raise NotFoundError("Investigation not found")
        inv.comments.append(
            InvestigationComment(
                investigation_id=inv.id,
                author_id=author_id,
                author_name=author_name,
                body=body,
            )
        )
        await self.session.flush()
        return _to_dict(inv)
