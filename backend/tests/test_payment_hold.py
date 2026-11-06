"""Payment hold: a model approval waits before payment and can still be stopped."""
from __future__ import annotations

import itertools
import uuid
from datetime import UTC, date, datetime, timedelta

from httpx import AsyncClient

from app.core.config import settings
from app.core.database import SessionFactory
from app.models.member import Member
from app.models.provider import Provider
from app.repositories.claim import ClaimRepository
from tests.test_claim_sweep import _claim, _set, _sweep

CLAIMS = "/api/v1/claims"
_days = itertools.count(8000)  # clear of every other test's service dates


async def _fresh_member_and_provider() -> tuple[str, str]:
    """A member and provider no other test touches.

    Scoring looks at history: a seeded member other tests keep claiming for trips
    the frequency rule, and a provider whose claims get disputed gains flags.
    Either would stop the claim from scoring APPROVE, which these tests need.
    """
    n = next(_days)
    async with SessionFactory() as session:
        session.add(Member(member_number=f"CIM-H{n}", name=f"Hold Test {n}", plan="GOLD",
                           city="Harare", phone=f"+263 71 {n:07d}", conditions=[]))
        session.add(Provider(code=f"PROV-HLD-{n}", name=f"Hold Test Clinic {n}",
                             type="GP", city="Harare", trust_score=95, badge="VERIFIED"))
        await session.commit()
    return f"CIM-H{n}", f"PROV-HLD-{n}"


async def _model_approved(client: AsyncClient, headers: dict) -> str:
    """A clean claim FraudShield approves on its own (so it starts on hold)."""
    member, provider = await _fresh_member_and_provider()
    amount = 30.0
    payload = {
        "claimRef": f"CG-H{uuid.uuid4().hex[:8].upper()}",
        "memberNumber": member,
        "providerCode": provider,
        "serviceDate": (date.today() - timedelta(days=next(_days))).isoformat(),
        "claimedAmount": amount, "memberShortfall": 2,
        "expectedShortfallMin": 1, "expectedShortfallMax": 3,
        "items": [{"description": "GP Consultation", "quantity": 1,
                   "unitPrice": amount, "total": amount}],
    }
    resp = await client.post(f"{CLAIMS}/ingest", headers=headers, json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["decision"] == "APPROVE", data["flags"]
    return data["claimRef"]


def _events(claim: dict) -> list[str]:
    return [t["event"] for t in claim["timeline"]]


async def test_model_approval_starts_on_hold(client: AsyncClient, auth_headers: dict):
    claim = await _claim(client, auth_headers, await _model_approved(client, auth_headers))
    assert claim["autoApproveAt"] is not None
    assert claim["approvedAmount"] is None  # not paid yet


async def test_held_claim_can_still_be_rejected(client: AsyncClient, auth_headers: dict):
    ref = await _model_approved(client, auth_headers)
    resp = await client.post(f"{CLAIMS}/{ref}/reject", headers=auth_headers,
                             json={"reason": "REJECT_FRAUD"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["autoApproveAt"] is None


async def test_member_dispute_stops_a_held_payment(client: AsyncClient, auth_headers: dict):
    ref = await _model_approved(client, auth_headers)
    await client.post(f"{CLAIMS}/{ref}/member-alert", headers=auth_headers,
                      json={"channel": "SMS"})
    await client.post(f"{CLAIMS}/{ref}/member-response", headers=auth_headers,
                      json={"response": "DISPUTED"})
    claim = await _claim(client, auth_headers, ref)
    assert claim["decision"] == "PEND_INVESTIGATE"
    assert claim["autoApproveAt"] is None
    assert "Payment stopped" in _events(claim)


async def test_opening_a_case_stops_a_held_payment(client: AsyncClient, auth_headers: dict):
    ref = await _model_approved(client, auth_headers)
    await client.post("/api/v1/investigations", headers=auth_headers, json={"claimId": ref})
    claim = await _claim(client, auth_headers, ref)
    assert claim["decision"] == "PEND_INVESTIGATE"
    assert claim["autoApproveAt"] is None


async def test_info_request_stops_the_hold_and_the_answer_returns_to_review(
    client: AsyncClient, auth_headers: dict
):
    ref = await _model_approved(client, auth_headers)
    resp = await client.post(f"{CLAIMS}/{ref}/request-info", headers=auth_headers,
                             json={"message": "Please send the consultation notes."})
    assert resp.status_code == 200, resp.text
    claim = resp.json()["data"]
    assert claim["decision"] == "INFO_NEEDED"
    assert claim["autoApproveAt"] is None
    # The provider's answer restores `previousDecision`: it must be review, not
    # a payable approval.
    async with SessionFactory() as session:
        stored = await ClaimRepository(session).get_by_ref(ref)
        event = next(t for t in reversed(stored.timeline) if t.event == "Information requested")
        assert event.event_metadata["previousDecision"] == "PEND_VERIFY"


async def test_manual_approval_releases_a_held_claim_now(client: AsyncClient, auth_headers: dict):
    ref = await _model_approved(client, auth_headers)
    resp = await client.post(f"{CLAIMS}/{ref}/approve", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    claim = resp.json()["data"]
    assert claim["autoApproveAt"] is None
    assert claim["approvedAmount"] == claim["claimedAmount"]
    assert any("Released for payment early" in t["description"] for t in claim["timeline"])


async def test_hold_ends_payment_is_released_and_final(client: AsyncClient, auth_headers: dict):
    ref = await _model_approved(client, auth_headers)
    await _set(ref, auto_approve_at=datetime.now(UTC) - timedelta(minutes=1))
    assert (await _sweep()).payments_released >= 1

    claim = await _claim(client, auth_headers, ref)
    assert claim["approvedAmount"] == claim["claimedAmount"]
    assert claim["autoApproveAt"] is None
    assert "Payment released" in _events(claim)
    # Paid: now the approval is final.
    resp = await client.post(f"{CLAIMS}/{ref}/reject", headers=auth_headers,
                             json={"reason": "REJECT_FRAUD"})
    assert resp.status_code == 422


async def test_hold_ending_under_a_dispute_is_not_paid(client: AsyncClient, auth_headers: dict):
    # A hold older than this feature (a dispute recorded before holds existed).
    ref = await _model_approved(client, auth_headers)
    await _set(ref, auto_approve_at=datetime.now(UTC) - timedelta(minutes=1),
               member_response="DISPUTED")
    await _sweep()
    claim = await _claim(client, auth_headers, ref)
    assert claim["decision"] == "PEND_INVESTIGATE"
    assert claim["approvedAmount"] is None


async def test_rescore_keeps_the_hold_clock(client: AsyncClient, auth_headers: dict):
    ref = await _model_approved(client, auth_headers)
    before = (await _claim(client, auth_headers, ref))["autoApproveAt"]
    resp = await client.post(f"{CLAIMS}/{ref}/rescore", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    after = await _claim(client, auth_headers, ref)
    if after["decision"] == "APPROVE":
        assert after["autoApproveAt"] == before  # not extended
    else:
        assert after["autoApproveAt"] is None


async def test_zero_hold_pays_at_once(client: AsyncClient, auth_headers: dict, monkeypatch):
    monkeypatch.setattr(settings, "payment_hold_hours", 0)
    ref = await _model_approved(client, auth_headers)
    assert (await _claim(client, auth_headers, ref))["autoApproveAt"] is None
    resp = await client.post(f"{CLAIMS}/{ref}/reject", headers=auth_headers,
                             json={"reason": "REJECT_FRAUD"})
    assert resp.status_code == 422  # no hold, so the approval is final at once
