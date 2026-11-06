"""Scoring inputs the submitter can't steer: biometric gaps and peer shortfall norms."""
from __future__ import annotations

import itertools
import uuid
from datetime import date, timedelta

from httpx import AsyncClient

from app.modules.fraudshield.context import ScoringContext
from app.modules.fraudshield.rule_engine import RuleEngine
from app.modules.fraudshield.signals import SignalInputs, derive_input_signals
from tests.test_payment_hold import _fresh_member_and_provider

INGEST = "/api/v1/claims/ingest"
_n = itertools.count(12000)  # service dates clear of every other test file


def _codes(**kw) -> set[str]:
    base = {"claim_ref": "X", "claimed_amount": 100, "member_shortfall": 10,
            "expected_shortfall_min": 0, "expected_shortfall_max": 1000,
            "provider_trust_score": 95, "provider_flags_90d": 0}
    return {f.code for f in RuleEngine().evaluate(ScoringContext(**{**base, **kw}))[1]}


async def _ingest(client: AsyncClient, headers: dict, *, member: str, provider: str,
                  amount: float, shortfall: float, item: str, nappi: str | None = None,
                  **extra) -> dict:
    n = next(_n)
    payload = {
        "claimRef": f"CG-S{uuid.uuid4().hex[:8].upper()}",
        "memberNumber": member, "providerCode": provider,
        "serviceDate": (date.today() - timedelta(days=n)).isoformat(),
        "claimedAmount": amount, "memberShortfall": shortfall,
        # A deliberately wide range: the old rule can't fire, so any shortfall
        # flag below comes from the peer comparison alone.
        "expectedShortfallMin": 0, "expectedShortfallMax": amount,
        "items": [{"description": item, "quantity": 1, "unitPrice": amount,
                   "total": amount, "nappiCode": nappi}],
        "hasBiometric": True,
        **extra,
    }
    resp = await client.post(INGEST, headers=headers, json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _peers(client: AsyncClient, headers: dict, item: str, count: int,
                 ratio: float, nappi: str | None = None) -> None:
    """`count` claims for `item`, each at its own provider, shortfall = ratio x claim."""
    for _ in range(count):
        member, provider = await _fresh_member_and_provider()
        await _ingest(client, headers, member=member, provider=provider, amount=100,
                      shortfall=100 * ratio, item=item, nappi=nappi)


# ── #3 Biometric: missing is "not reported", not "verified" ──────────────────
def test_missing_biometric_is_recorded_as_not_reported():
    base = {"item_descriptions": [], "member_conditions": [],
            "provider_flags_90d": 0, "provider_trust_score": 95}
    missing = derive_input_signals(SignalInputs(**base))
    # The model keeps its trained input; the gap is recorded separately.
    assert missing["has_biometric"] is True
    assert missing["biometric_reported"] is False
    told_no = derive_input_signals(SignalInputs(**base, has_biometric=False))
    assert told_no["has_biometric"] is False and told_no["biometric_reported"] is True


def test_not_reported_rule_only_for_high_value_claims():
    assert "BIOMETRIC_NOT_REPORTED" in _codes(claimed_amount=80, biometric_reported=False)
    assert "BIOMETRIC_NOT_REPORTED" not in _codes(claimed_amount=50, biometric_reported=False)
    assert "BIOMETRIC_NOT_REPORTED" not in _codes(claimed_amount=80, biometric_reported=True)


async def test_ingest_flags_a_high_value_claim_with_no_biometric_field(
    client: AsyncClient, auth_headers: dict
):
    member, provider = await _fresh_member_and_provider()
    common = {"member": member, "provider": provider, "shortfall": 5, "item": "Consult A"}
    silent = await _ingest(client, auth_headers, amount=120, **common, hasBiometric=None)
    assert "BIOMETRIC_NOT_REPORTED" in silent["flags"]
    assert "HIGH_VALUE_NO_BIOMETRIC" not in silent["flags"]  # not the same as "no"
    told = await _ingest(client, auth_headers, amount=121, **common)
    assert "BIOMETRIC_NOT_REPORTED" not in told["flags"]


# ── #4 Shortfall vs other providers ──────────────────────────────────────────
def test_peer_rule_thresholds():
    # Peers charge 10% of the claim as shortfall (US$10 on US$100).
    assert "SHORTFALL_ABOVE_PEERS" in _codes(peer_shortfall_ratio=0.10, member_shortfall=30)
    assert "SHORTFALL_ABOVE_PEERS" not in _codes(peer_shortfall_ratio=0.10, member_shortfall=12)
    # 1.4x but only a few dollars: not worth a flag.
    assert "SHORTFALL_ABOVE_PEERS" not in _codes(
        claimed_amount=10, peer_shortfall_ratio=0.10, member_shortfall=2)
    assert "SHORTFALL_ABOVE_PEERS" not in _codes(peer_shortfall_ratio=None, member_shortfall=90)


async def test_shortfall_far_above_other_providers_is_flagged(
    client: AsyncClient, auth_headers: dict
):
    item = f"Peer Item {uuid.uuid4().hex[:6]}"
    await _peers(client, auth_headers, item, count=5, ratio=0.10)
    member, provider = await _fresh_member_and_provider()
    claim = await _ingest(client, auth_headers, member=member, provider=provider,
                          amount=100, shortfall=40, item=item)
    # The claim's own range (0-100) said 40 was fine; the peers say otherwise.
    assert "SHORTFALL_INFLATION_SUSPECTED" not in claim["flags"]
    assert "SHORTFALL_ABOVE_PEERS" in claim["flags"]


async def test_too_little_history_means_no_peer_judgement(
    client: AsyncClient, auth_headers: dict
):
    item = f"Peer Item {uuid.uuid4().hex[:6]}"
    await _peers(client, auth_headers, item, count=4, ratio=0.10)  # one short
    member, provider = await _fresh_member_and_provider()
    claim = await _ingest(client, auth_headers, member=member, provider=provider,
                          amount=100, shortfall=40, item=item)
    assert "SHORTFALL_ABOVE_PEERS" not in claim["flags"]


async def test_a_provider_cannot_set_its_own_norm(client: AsyncClient, auth_headers: dict):
    item = f"Peer Item {uuid.uuid4().hex[:6]}"
    await _peers(client, auth_headers, item, count=5, ratio=0.10)
    member, provider = await _fresh_member_and_provider()
    # The same provider inflates five times first...
    for _ in range(5):
        await _ingest(client, auth_headers, member=member, provider=provider,
                      amount=100, shortfall=40, item=item)
    # ...and is still measured against the *other* providers.
    claim = await _ingest(client, auth_headers, member=member, provider=provider,
                          amount=100, shortfall=40, item=item)
    assert "SHORTFALL_ABOVE_PEERS" in claim["flags"]


async def test_peers_match_by_nappi_code_across_descriptions(
    client: AsyncClient, auth_headers: dict
):
    nappi = f"7{uuid.uuid4().int % 10**8:08d}"
    for i in range(5):
        member, provider = await _fresh_member_and_provider()
        await _ingest(client, auth_headers, member=member, provider=provider, amount=100,
                      shortfall=10, item=f"Brand {i} 500mg x 30", nappi=nappi)
    member, provider = await _fresh_member_and_provider()
    claim = await _ingest(client, auth_headers, member=member, provider=provider,
                          amount=100, shortfall=40, item="Generic 500mg", nappi=nappi)
    assert "SHORTFALL_ABOVE_PEERS" in claim["flags"]
