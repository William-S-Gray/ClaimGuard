"""Claim aggregate: Claim + ClaimItem, ClaimFlag, ShapContribution, TimelineEvent."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, BaseEntity


class Claim(BaseEntity):
    claim_ref: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    nh263_ref: Mapped[str | None] = mapped_column(String(32), index=True)

    member_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("members.id", ondelete="RESTRICT"), index=True
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("providers.id", ondelete="RESTRICT"), index=True
    )

    service_date: Mapped[str | None] = mapped_column(String(10))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    claimed_amount: Mapped[float] = mapped_column(Numeric(14, 2))
    approved_amount: Mapped[float | None] = mapped_column(Numeric(14, 2))
    member_shortfall: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    expected_shortfall_min: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    expected_shortfall_max: Mapped[float] = mapped_column(Numeric(14, 2), default=0)

    # FraudShield input signals — persisted so a rescore reproduces the same
    # inputs the scorer saw (these aren't derivable from the other columns).
    prescription_after_service: Mapped[bool] = mapped_column(default=False)
    has_biometric: Mapped[bool] = mapped_column(default=True)
    chronic_drug_no_condition: Mapped[bool] = mapped_column(default=False)
    syndicate_signal: Mapped[bool] = mapped_column(default=False)

    # FraudShield outputs
    risk_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    risk_level: Mapped[str] = mapped_column(String(16), default="LOW")
    decision: Mapped[str] = mapped_column(String(24), default="APPROVE", index=True)
    priority: Mapped[str] = mapped_column(String(16), default="LOW", index=True)
    ai_explanation: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)

    # SLA / lifecycle
    auto_approve_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Member engagement
    member_notification_sent: Mapped[bool] = mapped_column(default=False)
    member_notification_channel: Mapped[str | None] = mapped_column(String(16))
    member_response: Mapped[str] = mapped_column(String(16), default="PENDING")
    # 1-5, asked after a member confirms they received the service.
    member_rating: Mapped[int | None] = mapped_column(Integer)

    agent_notes: Mapped[str | None] = mapped_column(Text)

    # Relationships
    member: Mapped[Member] = relationship(back_populates="claims", lazy="selectin")  # noqa: F821
    provider: Mapped[Provider] = relationship(back_populates="claims", lazy="selectin")  # noqa: F821
    items: Mapped[list[ClaimItem]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", lazy="selectin"
    )
    flags: Mapped[list[ClaimFlag]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", lazy="selectin"
    )
    shap_contributions: Mapped[list[ShapContribution]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", lazy="selectin"
    )
    timeline: Mapped[list[TimelineEvent]] = relationship(
        back_populates="claim",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="TimelineEvent.timestamp",
    )


class ClaimItem(BaseEntity):
    claim_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("claims.id", ondelete="CASCADE"), index=True
    )
    description: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float] = mapped_column(Numeric(14, 2))
    total: Mapped[float] = mapped_column(Numeric(14, 2))
    icd10_code: Mapped[str | None] = mapped_column(String(16))
    nappi_code: Mapped[str | None] = mapped_column(String(32))

    claim: Mapped[Claim] = relationship(back_populates="items")


class ClaimFlag(BaseEntity):
    claim_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("claims.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="MEDIUM")
    detail: Mapped[str | None] = mapped_column(String(512))

    claim: Mapped[Claim] = relationship(back_populates="flags")


class ShapContribution(BaseEntity):
    __tablename__ = "shap_contributions"

    claim_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("claims.id", ondelete="CASCADE"), index=True
    )
    feature: Mapped[str] = mapped_column(String(128))
    contribution: Mapped[float] = mapped_column(Numeric(8, 4))
    direction: Mapped[str] = mapped_column(String(8))  # positive | negative

    claim: Mapped[Claim] = relationship(back_populates="shap_contributions")


class TimelineEvent(BaseEntity):
    claim_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("claims.id", ondelete="CASCADE"), index=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(String(512))
    actor: Mapped[str | None] = mapped_column(String(128))
    type: Mapped[str] = mapped_column(String(16), default="system")
    event_metadata: Mapped[dict] = mapped_column(JSON, default=dict)

    claim: Mapped[Claim] = relationship(back_populates="timeline")
