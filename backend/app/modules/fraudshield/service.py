"""FraudShieldService — orchestrates the scoring pipeline."""
from __future__ import annotations

import time

from app.modules.fraudshield.context import ScoringContext, ScoringResult
from app.modules.fraudshield.decision_engine import DecisionEngine
from app.modules.fraudshield.explanation_engine import ExplanationEngine
from app.modules.fraudshield.ml_engine import MLEngine, get_ml_engine
from app.modules.fraudshield.rule_engine import RuleEngine


class FraudShieldService:
    """Composition root for the fraud-scoring adapters (Dependency Injection)."""

    def __init__(
        self,
        rule_engine: RuleEngine | None = None,
        ml_engine: MLEngine | None = None,
        explanation_engine: ExplanationEngine | None = None,
        decision_engine: DecisionEngine | None = None,
    ) -> None:
        self.rules = rule_engine or RuleEngine()
        self.ml = ml_engine or get_ml_engine()
        self.explain = explanation_engine or ExplanationEngine()
        self.decide = decision_engine or DecisionEngine()

    def score(self, ctx: ScoringContext) -> ScoringResult:
        start = time.perf_counter()

        rule_score, flags, rule_contrib = self.rules.evaluate(ctx)
        ml_prob = self.ml.anomaly_probability(ctx)
        ml_weights = self.ml.feature_weights(ctx)

        final_score = self.decide.fuse_scores(rule_score, ml_prob)
        risk_level = self.decide.risk_level(final_score)
        decision = self.decide.decision(final_score, {f.code for f in flags})
        priority = self.decide.priority(final_score, ctx.syndicate_signal)

        shap = self.explain.build_shap(rule_contrib, ml_weights)
        narrative = self.explain.narrate(ctx, final_score, flags, ml_prob)

        latency_ms = int((time.perf_counter() - start) * 1000)

        return ScoringResult(
            risk_score=final_score,
            risk_level=risk_level,
            decision=decision,
            priority=priority,
            flags=flags,
            shap=shap,
            explanation=narrative,
            latency_ms=max(latency_ms, 1),
            model_name=self.ml.name,
            anomaly_probability=round(ml_prob, 4),
        )


# Stateless — safe to share a single instance.
fraudshield_service = FraudShieldService()
