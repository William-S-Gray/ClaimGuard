"""Decision engine — maps a final risk score to decision, risk level, priority."""
from __future__ import annotations


class DecisionEngine:
    # Thresholds are policy, kept in one place for governance/auditability.
    APPROVE_BELOW = 30
    VERIFY_BELOW = 50
    INVESTIGATE_BELOW = 80  # >= 80 is critical investigation

    def risk_level(self, score: int) -> str:
        if score >= 80:
            return "CRITICAL"
        if score >= 50:
            return "HIGH"
        if score >= 30:
            return "MEDIUM"
        return "LOW"

    # Hard evidence a person must look at, however low the blended score is: rule
    # points are only 60% of the score, so a duplicate on its own could otherwise
    # land under APPROVE_BELOW and pay twice.
    REVIEW_FLOOR_FLAGS = frozenset({"DUPLICATE_CLAIM", "CLAIM_SPLITTING_SUSPECTED"})

    def decision(self, score: int, flag_codes: set[str] | frozenset[str] = frozenset()) -> str:
        if score < self.APPROVE_BELOW:
            return "PEND_VERIFY" if flag_codes & self.REVIEW_FLOOR_FLAGS else "APPROVE"
        if score < self.VERIFY_BELOW:
            return "PEND_VERIFY"
        return "PEND_INVESTIGATE"

    def priority(self, score: int, syndicate: bool) -> str:
        if syndicate or score >= 80:
            return "CRITICAL"
        if score >= 60:
            return "HIGH"
        if score >= 40:
            return "MEDIUM"
        return "LOW"

    def fuse_scores(self, rule_score: int, ml_probability: float) -> int:
        """Blend the deterministic rule score with the ML anomaly probability.

        60% rules (explainable, governable) + 40% model (catches novel patterns).
        """
        ml_score = ml_probability * 100
        fused = 0.6 * rule_score + 0.4 * ml_score
        return int(round(min(max(fused, 0), 100)))
