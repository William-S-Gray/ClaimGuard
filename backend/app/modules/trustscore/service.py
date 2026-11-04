"""Provider TrustScore computation (pure functions — easily testable)."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# Older claims count for less: a claim this many days old weighs half a new one.
HALF_LIFE_DAYS = 180
# Claims needed before the score is fully trusted (confidence 1.0).
# ponytail: linear ramp, swap for a Wilson interval if thin providers matter.
FULL_CONFIDENCE_CLAIMS = 100


@dataclass(slots=True)
class TrustInputs:
    dispute_rate: float       # %
    shortfall_index: float    # ratio, 1.0 == expected
    flags_90d: int
    total_claims: int


def inputs_from_claims(claims: Iterable[Any], now: datetime) -> TrustInputs:
    """Derive TrustScore inputs from a provider's claim history.

    Dispute rate and shortfall index are decay-weighted (HALF_LIFE_DAYS) so a
    provider that cleaned up recently recovers; flags count claims flagged in
    the last 90 days; total_claims is the plain all-time count.
    """
    claims = list(claims)
    weight_sum = disputed = ratio_weight = ratio_sum = 0.0
    flagged_90d = 0
    for c in claims:
        submitted = c.submitted_at
        if submitted.tzinfo is None:  # SQLite hands back naive datetimes
            submitted = submitted.replace(tzinfo=UTC)
        age_days = max((now - submitted).days, 0)
        weight = 0.5 ** (age_days / HALF_LIFE_DAYS)
        weight_sum += weight
        if c.member_response == "DISPUTED":
            disputed += weight
        expected = (float(c.expected_shortfall_min) + float(c.expected_shortfall_max)) / 2
        if expected > 0:
            ratio_sum += weight * float(c.member_shortfall) / expected
            ratio_weight += weight
        if c.flags and age_days <= 90:
            flagged_90d += 1
    return TrustInputs(
        dispute_rate=round(100 * disputed / weight_sum, 2) if weight_sum else 0.0,
        shortfall_index=round(ratio_sum / ratio_weight, 2) if ratio_weight else 1.0,
        flags_90d=flagged_90d,
        total_claims=len(claims),
    )


def confidence(total_claims: int) -> float:
    """0..1: how much claim history the score rests on."""
    return round(min(1.0, total_claims / FULL_CONFIDENCE_CLAIMS), 2)


class TrustScoreService:
    @staticmethod
    def badge_for(score: int) -> str:
        if score >= 90:
            return "VERIFIED"
        if score >= 70:
            return "STANDARD"
        if score >= 50:
            return "CAUTION"
        if score >= 30:
            return "REVIEW"
        return "WATCHLIST"

    def compute(self, inp: TrustInputs) -> int:
        """Start at 100, deduct for risk signals. Clamped to [0, 100]."""
        score = 100.0
        score -= min(inp.dispute_rate * 4, 40)            # disputes hurt most
        score -= max(0.0, (inp.shortfall_index - 1.0)) * 30
        score -= min(inp.flags_90d * 1.5, 30)
        if inp.total_claims < 20:                          # thin track record
            score -= 5
        return int(round(max(0.0, min(100.0, score))))

    def recompute(self, inp: TrustInputs) -> tuple[int, str]:
        score = self.compute(inp)
        return score, self.badge_for(score)


trustscore_service = TrustScoreService()
