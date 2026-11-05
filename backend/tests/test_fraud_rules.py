"""FraudShield gaps: duplicates, claim splitting, frequency, and ingest validation."""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from httpx import AsyncClient

from app.modules.fraudshield.context import ScoringContext
from app.modules.fraudshield.decision_engine import DecisionEngine
from app.modules.fraudshield.rule_engine import RuleEngine
from app.modules.fraudshield.signals import SignalInputs, derive_input_signals
from tests.test_claim_rules import _set_decision

INGEST = "/api/v1/claims/ingest"
# A clean pair: hypertensive member, trusted provider no other test relies on
# (these tests move its TrustScore, which would skew tests that expect it clean).
MEMBER, PROVIDER = "CIM-0441209", "PROV-HRE-00098"


def _day() -> str:
    """A service date no other test uses, so history checks only see this test."""
    return (date.today() - timedelta(days=4000 + uuid.uuid4().int % 2000)).isoformat()


def _payload(amount: float, service_date: str, **extra) -> dict:
    return {
        "claimRef": f"CG-F{uuid.uuid4().hex[:8].upper()}",
        "memberNumber": MEMBER,
        "providerCode": PROVIDER,
        "serviceDate": service_date,
        "claimedAmount": amount,
        "memberShortfall": 2,
        "expectedShortfallMin": 1,
        "expectedShortfallMax": 3,
        "items": [{"description": "GP Consultation", "quantity": 1,
                   "unitPrice": amount, "total": amount}],
        **extra,
    }


async def _ingest(client: AsyncClient, headers: dict, payload: dict) -> dict:
    resp = await client.post(INGEST, headers=headers, json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _ctx(**kw) -> ScoringContext:
    base = {"claim_ref": "X", "claimed_amount": 40, "member_shortfall": 2,
            "expected_shortfall_min": 1, "expected_shortfall_max": 3,
            "provider_trust_score": 97, "provider_flags_90d": 0}
    return ScoringContext(**{**base, **kw})


# ── Duplicates ───────────────────────────────────────────────────────────────
async def test_identical_second_claim_is_a_duplicate(client: AsyncClient, auth_headers: dict):
    day = _day()
    first = await _ingest(client, auth_headers, _payload(40, day))
    assert "DUPLICATE_CLAIM" not in first["flags"]
    assert first["decision"] == "APPROVE"  # clean on its own

    second = await _ingest(client, auth_headers, _payload(40, day))
    assert "DUPLICATE_CLAIM" in second["flags"]
    # Rule points alone wouldn't stop it paying twice: the review floor does.
    assert second["decision"] != "APPROVE"

    # The original isn't flagged as a duplicate of its own copy on rescore.
    # (It auto-approved, and decided claims can't be rescored: reopen it first.)
    await _set_decision(first["claimRef"], "PEND_VERIFY")
    again = await client.post(f"/api/v1/claims/{first['claimRef']}/rescore", headers=auth_headers)
    assert "DUPLICATE_CLAIM" not in again.json()["data"]["flags"]


async def test_resent_upstream_claim_is_a_duplicate(client: AsyncClient, auth_headers: dict):
    nh = f"NH263-T-{uuid.uuid4().hex[:6]}"
    await _ingest(client, auth_headers, _payload(40, _day(), nh263Ref=nh))
    second = await _ingest(client, auth_headers, _payload(41, _day(), nh263Ref=nh))
    assert "DUPLICATE_CLAIM" in second["flags"]


async def test_correcting_a_data_error_is_not_a_duplicate(client: AsyncClient, auth_headers: dict):
    day = _day()
    first = await _ingest(client, auth_headers, _payload(40, day))
    # Only an undecided claim can be rejected; this one auto-approved, so use a
    # fresh pending claim for the "rejected as error, then resent" story.
    pending = await _ingest(client, auth_headers, _payload(50, day, syndicateSignal=True))
    await client.post(f"/api/v1/claims/{pending['claimRef']}/reject", headers=auth_headers,
                      json={"reason": "REJECT_ERROR"})
    resent = await _ingest(client, auth_headers, _payload(50, day))
    assert "DUPLICATE_CLAIM" not in resent["flags"]
    assert first["claimRef"] != resent["claimRef"]


# ── Claim splitting ──────────────────────────────────────────────────────────
async def test_same_day_pieces_over_the_line_are_split(client: AsyncClient, auth_headers: dict):
    day = _day()
    one = await _ingest(client, auth_headers, _payload(40, day))
    assert "CLAIM_SPLITTING_SUSPECTED" not in one["flags"]  # alone it's fine
    two = await _ingest(client, auth_headers, _payload(45, day))  # 85 >= 75 together
    assert "CLAIM_SPLITTING_SUSPECTED" in two["flags"]
    assert two["decision"] != "APPROVE"


async def test_small_same_day_claims_under_the_line_are_fine(
    client: AsyncClient, auth_headers: dict
):
    day = _day()
    await _ingest(client, auth_headers, _payload(20, day))
    two = await _ingest(client, auth_headers, _payload(25, day))  # 45 < 75
    assert "CLAIM_SPLITTING_SUSPECTED" not in two["flags"]


# ── Rules and decision floor (unit) ──────────────────────────────────────────
def test_frequency_rule():
    codes = lambda ctx: {f.code for f in RuleEngine().evaluate(ctx)[1]}  # noqa: E731
    assert "HIGH_CLAIM_FREQUENCY" in codes(_ctx(member_claims_7d=5))
    assert "HIGH_CLAIM_FREQUENCY" not in codes(_ctx(member_claims_7d=4))


def test_items_mismatch_rule():
    codes = lambda ctx: {f.code for f in RuleEngine().evaluate(ctx)[1]}  # noqa: E731
    assert "ITEMS_DO_NOT_MATCH_TOTAL" in codes(_ctx(claimed_amount=50, items_total=30))
    assert "ITEMS_DO_NOT_MATCH_TOTAL" not in codes(_ctx(claimed_amount=50, items_total=50))
    assert "ITEMS_DO_NOT_MATCH_TOTAL" not in codes(_ctx(claimed_amount=50))  # unknown


def test_hard_evidence_never_auto_approves():
    engine = DecisionEngine()
    assert engine.decision(5) == "APPROVE"
    assert engine.decision(5, {"DUPLICATE_CLAIM"}) == "PEND_VERIFY"
    assert engine.decision(5, {"CLAIM_SPLITTING_SUSPECTED"}) == "PEND_VERIFY"
    assert engine.decision(90, {"DUPLICATE_CLAIM"}) == "PEND_INVESTIGATE"  # floor, not cap


def test_upstream_cannot_switch_off_the_syndicate_check():
    risky_provider = {"item_descriptions": [], "member_conditions": [],
                      "provider_flags_90d": 31, "provider_trust_score": 44}
    assert derive_input_signals(
        SignalInputs(**risky_provider, syndicate_signal=False)
    )["syndicate_signal"] is True


# ── Ingest validation ────────────────────────────────────────────────────────
def _bad(**changes) -> dict:
    p = _payload(40, _day())
    for k, v in changes.items():
        p[k] = v
    return p


@pytest.mark.parametrize("payload", [
    _bad(claimedAmount=0),
    _bad(claimedAmount=-40),
    _bad(memberShortfall=-1),
    _bad(memberShortfall=41),                              # more than the claim
    _bad(expectedShortfallMin=5, expectedShortfallMax=1),
    _bad(items=[]),
    _bad(items=[{"description": "X", "quantity": 2, "unitPrice": 10, "total": 40}]),
    _bad(items=[{"description": "X", "quantity": 0, "unitPrice": 10, "total": 0}]),
    _bad(serviceDate=(date.today() + timedelta(days=5)).isoformat()),
    _bad(serviceDate="05/07/2026"),
    _bad(prescriptionDate="yesterday"),
], ids=["zero", "negative", "neg-shortfall", "shortfall>claim", "min>max", "no-items",
        "line-total", "zero-qty", "future-date", "bad-date", "bad-rx-date"])
async def test_impossible_claims_are_rejected(client: AsyncClient, auth_headers: dict, payload):
    resp = await client.post(INGEST, headers=auth_headers, json=payload)
    assert resp.status_code == 422, resp.text
