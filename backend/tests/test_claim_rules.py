"""Claim-workflow rules: decisions are final, disputes stand, cases decide claims.

Each test covers a loophole that used to let a decision or a member's dispute
be overwritten without a trace.
"""
from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import update

from app.core.database import SessionFactory
from app.models.claim import Claim
from app.modules.fraudshield.decision_engine import DecisionEngine
from tests.conftest import _login, ingest_claim

CLAIMS = "/api/v1/claims"
CASES = "/api/v1/investigations"


async def _claim(client: AsyncClient, headers: dict, ref: str) -> dict:
    return (await client.get(f"{CLAIMS}/{ref}", headers=headers)).json()["data"]


async def _set_decision(ref: str, decision: str) -> None:
    """Put a claim into a given state directly (scores are hard to steer)."""
    async with SessionFactory() as session:
        await session.execute(
            update(Claim).where(Claim.claim_ref == ref).values(decision=decision)
        )
        await session.commit()


async def _disputed(client: AsyncClient, headers: dict, *, risky: bool = True) -> str:
    ref = await ingest_claim(client, headers, risky=risky)
    if not risky:
        await _set_decision(ref, "PEND_VERIFY")
    await client.post(f"{CLAIMS}/{ref}/member-alert", headers=headers, json={"channel": "SMS"})
    resp = await client.post(
        f"{CLAIMS}/{ref}/member-response", headers=headers, json={"response": "DISPUTED"}
    )
    assert resp.status_code == 200, resp.text
    return ref


# ── Decisions are final ──────────────────────────────────────────────────────
async def test_rejected_claim_cannot_be_approved(client: AsyncClient, auth_headers: dict):
    ref = await ingest_claim(client, auth_headers, risky=True)
    assert (await client.post(f"{CLAIMS}/{ref}/reject", headers=auth_headers,
                              json={"reason": "REJECT_FRAUD"})).status_code == 200
    resp = await client.post(f"{CLAIMS}/{ref}/approve", headers=auth_headers)
    assert resp.status_code == 422
    assert (await _claim(client, auth_headers, ref))["decision"] == "REJECT_FRAUD"


async def test_approved_claim_cannot_be_rejected(client: AsyncClient, auth_headers: dict):
    ref = await ingest_claim(client, auth_headers, risky=True)
    assert (await client.post(f"{CLAIMS}/{ref}/approve", headers=auth_headers)).status_code == 200
    resp = await client.post(
        f"{CLAIMS}/{ref}/reject", headers=auth_headers, json={"reason": "REJECT_ERROR"}
    )
    assert resp.status_code == 422


async def test_claim_waiting_on_provider_cannot_be_approved(
    client: AsyncClient, auth_headers: dict
):
    ref = await ingest_claim(client, auth_headers, risky=True)
    await client.post(f"{CLAIMS}/{ref}/request-info", headers=auth_headers,
                      json={"message": "Send the prescription please."})
    assert (await client.post(f"{CLAIMS}/{ref}/approve", headers=auth_headers)).status_code == 422
    assert (await client.post(f"{CLAIMS}/{ref}/rescore", headers=auth_headers)).status_code == 422


async def test_decided_claim_cannot_be_rescored(client: AsyncClient, auth_headers: dict):
    ref = await ingest_claim(client, auth_headers, risky=True)
    await client.post(f"{CLAIMS}/{ref}/reject", headers=auth_headers, json={})
    assert (await client.post(f"{CLAIMS}/{ref}/rescore", headers=auth_headers)).status_code == 422


async def test_open_investigation_blocks_approval(client: AsyncClient, auth_headers: dict):
    ref = await ingest_claim(client, auth_headers, risky=True)
    case = (await client.post(CASES, headers=auth_headers, json={"claimId": ref})).json()["data"]
    assert (await client.post(f"{CLAIMS}/{ref}/approve", headers=auth_headers)).status_code == 422

    await client.patch(f"{CASES}/{case['id']}", headers=auth_headers,
                       json={"status": "RESOLVED", "resolution": "FALSE_POSITIVE"})
    assert (await client.post(f"{CLAIMS}/{ref}/approve", headers=auth_headers)).status_code == 200


# ── A member's dispute stands ────────────────────────────────────────────────
async def test_realert_cannot_erase_a_dispute(client: AsyncClient, auth_headers: dict):
    ref = await _disputed(client, auth_headers)
    resp = await client.post(
        f"{CLAIMS}/{ref}/member-alert", headers=auth_headers, json={"channel": "SMS"}
    )
    assert resp.status_code == 422
    assert (await _claim(client, auth_headers, ref))["memberResponse"] == "DISPUTED"


async def test_staff_cannot_flip_a_dispute_to_confirmed(client: AsyncClient, auth_headers: dict):
    ref = await _disputed(client, auth_headers)
    resp = await client.post(
        f"{CLAIMS}/{ref}/member-response", headers=auth_headers, json={"response": "CONFIRMED"}
    )
    assert resp.status_code == 422
    assert (await _claim(client, auth_headers, ref))["memberResponse"] == "DISPUTED"


async def test_confirmation_can_still_become_a_dispute(client: AsyncClient, auth_headers: dict):
    ref = await ingest_claim(client, auth_headers, risky=True)
    await client.post(f"{CLAIMS}/{ref}/member-alert", headers=auth_headers, json={"channel": "SMS"})
    for response in ("CONFIRMED", "DISPUTED"):
        resp = await client.post(
            f"{CLAIMS}/{ref}/member-response", headers=auth_headers, json={"response": response}
        )
        assert resp.status_code == 200, resp.text
    flags = (await _claim(client, auth_headers, ref))["flags"]
    assert flags.count("MEMBER_DISPUTED") == 1


async def test_approving_over_a_dispute_needs_a_reason(client: AsyncClient, auth_headers: dict):
    ref = await _disputed(client, auth_headers)
    assert (await client.post(f"{CLAIMS}/{ref}/approve", headers=auth_headers)).status_code == 422
    short = await client.post(f"{CLAIMS}/{ref}/approve", headers=auth_headers,
                              json={"reason": "ok"})
    assert short.status_code == 422

    reason = "Member called back: their spouse used the card with consent."
    resp = await client.post(f"{CLAIMS}/{ref}/approve", headers=auth_headers,
                             json={"reason": reason})
    assert resp.status_code == 200, resp.text
    approved = [t for t in resp.json()["data"]["timeline"] if t["event"] == "Claim approved"]
    assert reason in approved[-1]["description"]

    audit = await client.get("/api/v1/audit?entity_type=claim&pageSize=200", headers=auth_headers)
    entry = next(e for e in audit.json()["data"]
                 if e["entityId"] == ref and e["action"] == "claim.approve")
    assert entry["changes"]["reason"] == reason


async def test_rescore_keeps_the_dispute(client: AsyncClient, auth_headers: dict):
    ref = await _disputed(client, auth_headers)
    resp = await client.post(f"{CLAIMS}/{ref}/rescore", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    claim = await _claim(client, auth_headers, ref)
    assert "MEMBER_DISPUTED" in claim["flags"]
    assert claim["priority"] == "CRITICAL"


async def test_low_score_does_not_approve_a_disputed_claim(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    ref = await _disputed(client, auth_headers, risky=False)
    # Whatever the model makes of it (provider risk drifts as other tests run),
    # the case under test is the model saying APPROVE.
    monkeypatch.setattr(DecisionEngine, "decision", lambda self, score, flags=(): "APPROVE")
    resp = await client.post(f"{CLAIMS}/{ref}/rescore", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert (await _claim(client, auth_headers, ref))["decision"] == "PEND_VERIFY"


# ── Investigations decide their claim ────────────────────────────────────────
async def test_confirmed_fraud_rejects_the_claim(client: AsyncClient, auth_headers: dict):
    ref = await ingest_claim(client, auth_headers, risky=True)
    case = (await client.post(CASES, headers=auth_headers, json={"claimId": ref})).json()["data"]
    resp = await client.patch(f"{CASES}/{case['id']}", headers=auth_headers,
                              json={"status": "RESOLVED", "resolution": "CONFIRMED_FRAUD"})
    assert resp.status_code == 200, resp.text
    claim = await _claim(client, auth_headers, ref)
    assert claim["decision"] == "REJECT_FRAUD"
    assert claim["approvedAmount"] == 0


async def test_confirmed_fraud_on_a_paid_claim_flags_recovery(
    client: AsyncClient, auth_headers: dict
):
    ref = await ingest_claim(client, auth_headers, risky=True)
    await client.post(f"{CLAIMS}/{ref}/approve", headers=auth_headers)
    case = (await client.post(CASES, headers=auth_headers, json={"claimId": ref})).json()["data"]
    await client.patch(f"{CASES}/{case['id']}", headers=auth_headers,
                       json={"status": "CLOSED", "resolution": "CONFIRMED_FRAUD"})
    claim = await _claim(client, auth_headers, ref)
    assert claim["decision"] == "APPROVE"
    assert "RECOVERY_REQUIRED" in claim["flags"]


async def test_case_rules(client: AsyncClient, auth_headers: dict):
    ref = await ingest_claim(client, auth_headers, risky=True)
    case = (await client.post(CASES, headers=auth_headers, json={"claimId": ref})).json()["data"]
    url = f"{CASES}/{case['id']}"

    assert (await client.patch(url, headers=auth_headers,
                               json={"status": "DONE"})).status_code == 422
    assert (await client.patch(url, headers=auth_headers,
                               json={"resolution": "MAYBE"})).status_code == 422
    # Closing needs a resolution.
    assert (await client.patch(url, headers=auth_headers,
                               json={"status": "RESOLVED"})).status_code == 422

    closed = await client.patch(url, headers=auth_headers,
                                json={"status": "RESOLVED", "resolution": "NO_ACTION"})
    assert closed.json()["data"]["resolvedAt"] is not None
    # A closed case's outcome can't be rewritten in place...
    assert (await client.patch(url, headers=auth_headers,
                               json={"resolution": "FALSE_POSITIVE"})).status_code == 422
    # ...it has to be reopened, which clears the resolved time.
    reopened = await client.patch(url, headers=auth_headers, json={"status": "IN_PROGRESS"})
    assert reopened.status_code == 200
    assert reopened.json()["data"]["resolvedAt"] is None


async def test_cases_go_to_active_staff_only(client: AsyncClient, auth_headers: dict):
    ref = await ingest_claim(client, auth_headers, risky=True)
    case = (await client.post(CASES, headers=auth_headers, json={"claimId": ref})).json()["data"]

    portal = {"Authorization": f"Bearer {await _login(client, 'portal@citypharmacy.co.zw')}"}
    provider_user = (await client.get("/api/v1/auth/me", headers=portal)).json()["data"]["id"]
    for bad in (provider_user, "not-a-user-id"):
        resp = await client.patch(f"{CASES}/{case['id']}", headers=auth_headers,
                                  json={"assignedTo": bad})
        assert resp.status_code == 422, bad
