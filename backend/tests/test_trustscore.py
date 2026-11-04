"""TrustScore from claim history: decay, confidence, history, percentile, triggers."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from httpx import AsyncClient

from app.modules.trustscore.service import confidence, inputs_from_claims

NOW = datetime(2026, 11, 4, tzinfo=UTC)


def _claim(days_ago: int, *, disputed=False, flagged=False, shortfall=10.0):
    return SimpleNamespace(
        submitted_at=NOW - timedelta(days=days_ago),
        member_response="DISPUTED" if disputed else "CONFIRMED",
        expected_shortfall_min=8,
        expected_shortfall_max=12,  # expected midpoint 10
        member_shortfall=shortfall,
        flags=["X"] if flagged else [],
    )


def test_inputs_from_claims_weights_recent_claims_more():
    recent_dispute = inputs_from_claims([_claim(0, disputed=True), _claim(360)], NOW)
    old_dispute = inputs_from_claims([_claim(360, disputed=True), _claim(0)], NOW)
    # Same 1-in-2 dispute count, but a fresh dispute hurts more than a stale one.
    assert recent_dispute.dispute_rate > 50 > old_dispute.dispute_rate
    assert recent_dispute.total_claims == 2


def test_inputs_from_claims_flags_and_shortfall():
    inp = inputs_from_claims(
        [_claim(10, flagged=True, shortfall=20), _claim(200, flagged=True, shortfall=20)],
        NOW,
    )
    assert inp.flags_90d == 1  # the 200-day-old flag is outside the window
    assert inp.shortfall_index == 2.0  # members paid double the expected shortfall


def test_inputs_from_no_claims_are_neutral():
    inp = inputs_from_claims([], NOW)
    assert (inp.dispute_rate, inp.shortfall_index, inp.flags_90d, inp.total_claims) == (
        0.0, 1.0, 0, 0,
    )


def test_confidence_ramps_with_claim_count():
    assert confidence(0) == 0.0
    assert confidence(50) == 0.5
    assert confidence(5000) == 1.0


async def test_recalculate_records_history_and_percentile(
    client: AsyncClient, auth_headers: dict
):
    code = (await client.get("/api/v1/trustscore", headers=auth_headers)).json()["data"][0][
        "code"
    ]
    recalc = await client.post(f"/api/v1/trustscore/{code}/recalculate", headers=auth_headers)
    assert recalc.status_code == 200, recalc.text

    resp = await client.get(f"/api/v1/trustscore/{code}/history", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["history"], "a recalculation should leave a snapshot"
    assert data["history"][-1]["score"] == data["trustScore"]
    assert data["history"][-1]["reason"] == "manual"
    assert 0 <= data["percentile"] <= 100
    assert 0 <= data["confidence"] <= 1


async def test_member_dispute_rescores_provider(client: AsyncClient, auth_headers: dict):
    await client.post(
        "/api/v1/claims/CG-00441/member-alert",
        headers=auth_headers,
        json={"channel": "SMS"},
    )
    await client.post(
        "/api/v1/claims/CG-00441/member-response",
        headers=auth_headers,
        json={"response": "DISPUTED"},
    )
    claim = (await client.get("/api/v1/claims/CG-00441", headers=auth_headers)).json()["data"]
    code = claim["provider"]["code"]
    history = (
        await client.get(f"/api/v1/trustscore/{code}/history", headers=auth_headers)
    ).json()["data"]["history"]
    assert any(h["reason"] == "member disputed CG-00441" for h in history)


async def test_history_unknown_provider_is_404(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/v1/trustscore/NOPE/history", headers=auth_headers)
    assert resp.status_code == 404
