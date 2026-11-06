"""Input/output value objects for the FraudShield pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ScoringContext:
    """Everything FraudShield needs to score one claim — DB-agnostic."""

    claim_ref: str
    claimed_amount: float
    member_shortfall: float
    expected_shortfall_min: float
    expected_shortfall_max: float
    provider_trust_score: int
    provider_flags_90d: int
    member_conditions: list[str] = field(default_factory=list)
    item_descriptions: list[str] = field(default_factory=list)
    prescription_after_service: bool = False
    has_biometric: bool = True
    biometric_reported: bool = True
    chronic_drug_no_condition: bool = False
    syndicate_signal: bool = False
    # Claim-history signals, computed from the member's other claims at scoring
    # time (rules only: not in the ML feature vector, so models need no retrain).
    duplicate_of: str | None = None       # ref of an earlier identical claim
    split_suspected: bool = False         # same-day pieces that together cross a threshold
    member_claims_7d: int = 0             # member's other claims in the prior 7 days
    items_total: float | None = None      # sum of line items, None when unknown
    # Typical shortfall / claimed for this item at other providers (None when
    # there isn't enough history). Independent of the range the claim sends.
    peer_shortfall_ratio: float | None = None

    @property
    def shortfall_ratio(self) -> float:
        mid = (self.expected_shortfall_min + self.expected_shortfall_max) / 2 or 1.0
        return self.member_shortfall / mid


@dataclass(slots=True)
class Flag:
    code: str
    severity: str
    detail: str


@dataclass(slots=True)
class ShapValue:
    feature: str
    contribution: float
    direction: str


@dataclass(slots=True)
class ScoringResult:
    risk_score: int
    risk_level: str
    decision: str
    priority: str
    flags: list[Flag]
    shap: list[ShapValue]
    explanation: str
    latency_ms: int
    model_name: str = "unknown"
    anomaly_probability: float = 0.0
