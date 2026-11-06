"""Lifecycle sweep (SLA breach, unanswered alerts) and phone-proof reply matching."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update

from app.core.database import SessionFactory
from app.core.phone import normalize_phone
from app.core.redis import redis_client
from app.models.claim import Claim
from app.models.notification import Notification
from app.repositories.claim import ClaimRepository
from app.services import claim_sweep
from tests.conftest import ingest_claim
from tests.test_sms_ussd import gateway  # noqa: F401 - fixture

CLAIMS = "/api/v1/claims"


async def _claim(client: AsyncClient, headers: dict, ref: str) -> dict:
    return (await client.get(f"{CLAIMS}/{ref}", headers=headers)).json()["data"]


async def _set(ref: str, **values) -> None:
    async with SessionFactory() as session:
        await session.execute(update(Claim).where(Claim.claim_ref == ref).values(**values))
        await session.commit()


async def _sweep() -> claim_sweep.SweepResult:
    async with SessionFactory() as session:
        result = await claim_sweep.sweep(session)
        await session.commit()
        return result


async def _alert(client: AsyncClient, headers: dict, ref: str) -> str:
    resp = await client.post(f"{CLAIMS}/{ref}/member-alert", headers=headers,
                             json={"channel": "SMS"})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["alert"]["to"]


async def _sms(client: AsyncClient, gateway: dict, phone: str, text: str):  # noqa: F811
    return await client.post("/api/v1/webhooks/sms",
                             content=urlencode({"from": phone, "text": text}), headers=gateway)


# ── Phone numbers ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw", [
    "+263 77 291 8470", "+263772918470", "263772918470", "0772918470",
    "00263772918470", "+263-77-291-8470", "(077) 291 8470",
])
def test_every_way_of_writing_a_number_matches(raw):
    assert normalize_phone(raw) == "+263772918470"


@pytest.mark.parametrize("raw", ["", None, "abc", "12", "+1234567890123456"])
def test_unusable_numbers_normalise_to_nothing(raw):
    assert normalize_phone(raw) == ""


async def test_reply_from_a_national_number_finds_the_member(
    client: AsyncClient, auth_headers: dict, gateway  # noqa: F811
):
    ref = await ingest_claim(client, auth_headers, risky=True)
    e164 = normalize_phone(await _alert(client, auth_headers, ref))
    national = "0" + e164[len("+263"):]  # how a local gateway may send it
    assert (await _sms(client, gateway, national, "no")).status_code == 204
    assert (await _claim(client, auth_headers, ref))["memberResponse"] == "DISPUTED"


# ── Which alert a reply answers ──────────────────────────────────────────────
async def test_reply_answers_the_newest_alert_not_the_last_edited_claim(
    client: AsyncClient, auth_headers: dict, gateway  # noqa: F811
):
    older = await ingest_claim(client, auth_headers, "CIM-0291847", risky=True)
    newer = await ingest_claim(client, auth_headers, "CIM-0291847", risky=True)
    phone = await _alert(client, auth_headers, older)
    await _set(older, member_alert_sent_at=datetime.now(UTC) - timedelta(hours=2))
    await _alert(client, auth_headers, newer)
    # Touching the older claim afterwards used to make it the one a "YES" answered.
    await client.post(f"{CLAIMS}/{older}/notes", headers=auth_headers,
                      json={"note": "Checked the provider's register."})

    await _sms(client, gateway, phone, "yes")
    assert (await _claim(client, auth_headers, newer))["memberResponse"] == "CONFIRMED"
    assert (await _claim(client, auth_headers, older))["memberResponse"] == "PENDING"


async def test_a_lapsed_alert_no_longer_takes_replies(client: AsyncClient, auth_headers: dict):
    ref = await ingest_claim(client, auth_headers, "CIM-0441209", risky=True)
    phone = await _alert(client, auth_headers, ref)
    async with SessionFactory() as session:
        assert (await ClaimRepository(session).pending_alert_for_phone(phone)).claim_ref == ref
    await _set(ref, member_alert_sent_at=datetime.now(UTC) - timedelta(days=30))
    # Checked at the lookup, not by texting: a real reply would (rightly) go on
    # to answer this member's other open alerts and disturb other tests.
    async with SessionFactory() as session:
        match = await ClaimRepository(session).pending_alert_for_phone(phone)
    assert match is None or match.claim_ref != ref


# ── Sweep: unanswered alerts ─────────────────────────────────────────────────
async def test_unanswered_alert_is_flagged_once_and_cleared_by_a_new_alert(
    client: AsyncClient, auth_headers: dict
):
    ref = await ingest_claim(client, auth_headers, risky=True)
    await _alert(client, auth_headers, ref)
    await _set(ref, member_alert_sent_at=datetime.now(UTC) - timedelta(days=8))

    assert (await _sweep()).alerts_lapsed >= 1
    claim = await _claim(client, auth_headers, ref)
    assert claim["flags"].count("MEMBER_NO_REPLY") == 1
    assert any(t["event"] == "Member did not reply" for t in claim["timeline"])

    await _sweep()  # idempotent
    assert (await _claim(client, auth_headers, ref))["flags"].count("MEMBER_NO_REPLY") == 1

    await _alert(client, auth_headers, ref)  # a fresh alert, a fresh window
    assert "MEMBER_NO_REPLY" not in (await _claim(client, auth_headers, ref))["flags"]


# ── Sweep: SLA breach ────────────────────────────────────────────────────────
async def test_breached_sla_escalates_once_and_survives_rescore(
    client: AsyncClient, auth_headers: dict
):
    ref = await ingest_claim(client, auth_headers, risky=True)
    await _set(ref, sla_deadline=datetime.now(UTC) - timedelta(hours=1), priority="LOW",
               decision="PEND_VERIFY")

    assert (await _sweep()).sla_breached >= 1
    claim = await _claim(client, auth_headers, ref)
    assert claim["flags"].count("SLA_BREACHED") == 1
    assert claim["priority"] in {"HIGH", "CRITICAL"}
    assert any(t["event"] == "SLA breached" for t in claim["timeline"])

    await _sweep()
    assert (await _claim(client, auth_headers, ref))["flags"].count("SLA_BREACHED") == 1

    # Rescoring replaces model flags, not lifecycle facts.
    assert (await client.post(f"{CLAIMS}/{ref}/rescore", headers=auth_headers)).status_code == 200
    claim = await _claim(client, auth_headers, ref)
    assert "SLA_BREACHED" in claim["flags"]
    assert claim["priority"] in {"HIGH", "CRITICAL"}


async def test_paused_and_decided_claims_are_not_escalated(
    client: AsyncClient, auth_headers: dict
):
    past = datetime.now(UTC) - timedelta(hours=1)
    waiting = await ingest_claim(client, auth_headers, risky=True)
    await _set(waiting, decision="INFO_NEEDED", sla_deadline=past)
    decided = await ingest_claim(client, auth_headers, risky=True)
    await _set(decided, decision="REJECT_FRAUD", sla_deadline=past)

    await _sweep()
    for ref in (waiting, decided):
        assert "SLA_BREACHED" not in (await _claim(client, auth_headers, ref))["flags"]


async def _count_titled(title: str) -> int:
    async with SessionFactory() as session:
        stmt = select(func.count()).select_from(Notification).where(Notification.title == title)
        return (await session.execute(stmt)).scalar_one()


async def test_one_summary_notification_per_sweep(client: AsyncClient, auth_headers: dict):
    past = datetime.now(UTC) - timedelta(hours=1)
    for _ in range(3):
        ref = await ingest_claim(client, auth_headers, risky=True)
        await _set(ref, decision="PEND_VERIFY", sla_deadline=past)
    before = await _count_titled("SLA breached")
    result = await _sweep()
    assert result.sla_breached >= 3
    assert await _count_titled("SLA breached") == before + 1


async def test_a_second_worker_skips_while_one_is_sweeping():
    await redis_client.client.incr(claim_sweep._LOCK_KEY)  # someone else holds it
    try:
        assert await claim_sweep.run_sweep_once() is None
    finally:
        await redis_client.client.delete(claim_sweep._LOCK_KEY)
    assert await claim_sweep.run_sweep_once() is not None
