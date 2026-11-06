"""Derive FraudShield input signals at claim ingestion.

The four boolean risk signals the scorer consumes must be populated on every
claim as it enters the system (see docs/FRAUDSHIELD_PRODUCTION_DATA.md §2.3),
otherwise the trained model is blind to those features. This module is the single
place that derivation happens, so ingestion and any future feed share one
implementation.

Two signals come straight from the source system (`has_biometric`,
`prescription_after_service`); two are derived from reference data
(`chronic_drug_no_condition` from items × member conditions, `syndicate_signal`
from provider risk). Each derived rule is a transparent, auditable heuristic —
swap `syndicate_signal` for a real network/graph detector when one exists.
"""
from __future__ import annotations

from dataclasses import dataclass

# Chronic medication (lowercased keyword) → the condition it implies. Billing one
# of these while the member has no matching registered condition is a fraud signal.
CHRONIC_MEDICATIONS: dict[str, str] = {
    "insulin": "diabetes",
    "metformin": "diabetes",
    "glibenclamide": "diabetes",
    "gliclazide": "diabetes",
    "lisinopril": "hypertension",
    "enalapril": "hypertension",
    "amlodipine": "hypertension",
    "atenolol": "hypertension",
    "losartan": "hypertension",
    "hydrochlorothiazide": "hypertension",
    "atorvastatin": "hyperlipidemia",
    "simvastatin": "hyperlipidemia",
    "salbutamol": "asthma",
    "budesonide": "asthma",
    "levothyroxine": "hypothyroidism",
    "warfarin": "cardiac",
}

# `syndicate_signal` derivation: a provider with abnormally high flag velocity AND
# low trust is watchlist-tier — a proxy for syndicate membership until a real
# cross-claim network detector replaces it.
SYNDICATE_FLAG_THRESHOLD = 20
SYNDICATE_TRUST_THRESHOLD = 50


@dataclass(slots=True)
class SignalInputs:
    """Everything needed to derive the four signals for one claim."""

    item_descriptions: list[str]
    member_conditions: list[str]
    provider_flags_90d: int
    provider_trust_score: int
    service_date: str | None = None
    prescription_date: str | None = None
    # Source-provided (None = not supplied by the upstream system).
    has_biometric: bool | None = None
    syndicate_signal: bool | None = None


def _covers(conditions: list[str], implied: str) -> bool:
    """True if a registered condition plausibly covers the implied condition."""
    for c in conditions:
        cl = c.lower()
        if implied in cl or cl in implied:
            return True
    return False


def _chronic_drug_no_condition(item_descriptions: list[str], conditions: list[str]) -> bool:
    for desc in item_descriptions:
        d = desc.lower()
        for drug, implied in CHRONIC_MEDICATIONS.items():
            if drug in d and not _covers(conditions, implied):
                return True
    return False


def derive_input_signals(inp: SignalInputs) -> dict[str, bool]:
    """Return the four persisted claim signals for the given inputs."""
    # prescription dated after the service it supposedly supports
    if inp.prescription_date and inp.service_date:
        prescription_after_service = inp.prescription_date > inp.service_date
    else:
        prescription_after_service = False

    # biometric verification: an explicit "false" is the strong signal. A missing
    # field is *unknown*: the model still sees it as verified (as it was trained),
    # and `biometric_reported` lets a lighter rule flag the gap on large claims.
    has_biometric = True if inp.has_biometric is None else bool(inp.has_biometric)
    biometric_reported = inp.has_biometric is not None

    # syndicate: the upstream flag can raise the signal but never clear it. The
    # submitter of a claim must not be able to switch off our own risk check.
    syndicate_signal = bool(inp.syndicate_signal) or (
        inp.provider_flags_90d >= SYNDICATE_FLAG_THRESHOLD
        and inp.provider_trust_score < SYNDICATE_TRUST_THRESHOLD
    )

    return {
        "chronic_drug_no_condition": _chronic_drug_no_condition(
            inp.item_descriptions, inp.member_conditions
        ),
        "prescription_after_service": prescription_after_service,
        "has_biometric": has_biometric,
        "biometric_reported": biometric_reported,
        "syndicate_signal": syndicate_signal,
    }
