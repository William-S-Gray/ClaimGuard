"""Deterministic rule engine — clinical & financial fraud heuristics.

Each rule contributes weighted risk points and (optionally) a typed flag.
Rules are data-driven so new ones can be added without touching the engine.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.modules.fraudshield.context import Flag, ScoringContext

# A claim at or above this amount needs biometric verification. Also the line
# claim-splitting tries to stay under, so both rules share it.
HIGH_VALUE_THRESHOLD = 75
FREQUENT_CLAIMS_7D = 5  # a member's other claims in a week before it looks unusual


def _unitemised(c: ScoringContext) -> bool:
    return c.items_total is not None and abs(c.claimed_amount - c.items_total) > 0.01


@dataclass(slots=True)
class Rule:
    code: str
    weight: int           # risk points if triggered
    severity: str
    description: str
    predicate: Callable[[ScoringContext], bool]


RULES: list[Rule] = [
    Rule(
        code="PRESCRIPTION_DATE_AFTER_SERVICE",
        weight=22,
        severity="HIGH",
        description="Prescription dated after the service date",
        predicate=lambda c: c.prescription_after_service,
    ),
    Rule(
        code="HIGH_VALUE_NO_BIOMETRIC",
        weight=18,
        severity="HIGH",
        description="High-value claim without biometric verification",
        predicate=lambda c: c.claimed_amount >= HIGH_VALUE_THRESHOLD and not c.has_biometric,
    ),
    Rule(
        code="DUPLICATE_CLAIM",
        weight=35,
        severity="CRITICAL",
        description="Same member, provider, service date and amount as an earlier claim",
        predicate=lambda c: c.duplicate_of is not None,
    ),
    Rule(
        code="CLAIM_SPLITTING_SUSPECTED",
        weight=24,
        severity="HIGH",
        description=(
            "Same-day claims at one provider, each under the high-value line, "
            "that together cross it"
        ),
        predicate=lambda c: c.split_suspected,
    ),
    Rule(
        code="HIGH_CLAIM_FREQUENCY",
        weight=12,
        severity="MEDIUM",
        description="Member has an unusual number of claims this week",
        predicate=lambda c: c.member_claims_7d >= FREQUENT_CLAIMS_7D,
    ),
    Rule(
        code="ITEMS_DO_NOT_MATCH_TOTAL",
        weight=10,
        severity="MEDIUM",
        description="Claimed amount differs from the sum of its line items",
        predicate=_unitemised,
    ),
    Rule(
        code="CHRONIC_DRUG_NO_CONDITION_REGISTERED",
        weight=20,
        severity="HIGH",
        description="Chronic medication billed with no registered condition",
        predicate=lambda c: c.chronic_drug_no_condition,
    ),
    Rule(
        code="SHORTFALL_INFLATION_SUSPECTED",
        weight=16,
        severity="MEDIUM",
        description="Member shortfall materially above expected range",
        predicate=lambda c: c.shortfall_ratio >= 1.4,
    ),
    Rule(
        code="STATISTICAL_ANOMALY_DETECTED",
        weight=14,
        severity="MEDIUM",
        description="Provider flag velocity is abnormally high",
        predicate=lambda c: c.provider_flags_90d >= 10,
    ),
    Rule(
        code="POTENTIAL_FRAUD_SYNDICATE_DETECTED",
        weight=28,
        severity="CRITICAL",
        description="Pattern matches a suspected fraud syndicate",
        predicate=lambda c: c.syndicate_signal,
    ),
]


class RuleEngine:
    def __init__(self, rules: list[Rule] | None = None) -> None:
        self._rules = rules or RULES

    def evaluate(self, ctx: ScoringContext) -> tuple[int, list[Flag], dict[str, float]]:
        """Return (rule_score 0-100, flags, per-feature contributions)."""
        score = 0
        flags: list[Flag] = []
        contributions: dict[str, float] = {}
        for rule in self._rules:
            try:
                triggered = rule.predicate(ctx)
            except Exception:
                triggered = False
            if triggered:
                score += rule.weight
                flags.append(Flag(rule.code, rule.severity, rule.description))
                contributions[rule.code] = float(rule.weight)
        # Provider reputation modifier: trusted providers reduce rule pressure.
        if ctx.provider_trust_score >= 90:
            score = max(0, score - 6)
            contributions["PROVIDER_TRUSTED"] = -6.0
        return min(score, 100), flags, contributions
