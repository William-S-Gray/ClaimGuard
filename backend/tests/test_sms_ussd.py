"""SMS/USSD channels: templates, honest delivery, gateway reply webhooks."""
from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.services.claims import SMS_LIMIT, compose_sms_alert, compose_ussd_menu

FORM = {"content-type": "application/x-www-form-urlencoded"}


def _claim(provider: str) -> SimpleNamespace:
    return SimpleNamespace(
        claimed_amount=1500,
        service_date="2026-11-01",
        provider=SimpleNamespace(name=provider),
    )


def test_sms_fits_one_segment_and_keeps_reply_hint():
    short = compose_sms_alert(_claim("Avenues Clinic"))
    assert "Avenues Clinic" in short and short.endswith("Reply YES or NO")
    long = compose_sms_alert(_claim("A Very Long Provider Name " * 10))
    assert len(long) <= SMS_LIMIT
    assert long.endswith("Reply YES or NO")


def test_ussd_menu_format():
    menu = compose_ussd_menu(_claim("Avenues Clinic"))
    assert menu.splitlines() == [
        "ClaimGuard Alert",
        "Claim: US$1,500.00 at Avenues Clinic",
        "Correct?",
        "1=Yes 2=No",
    ]


async def test_undelivered_alert_falls_back_to_in_app_follow_up(
    client: AsyncClient, auth_headers: dict
):
    # Test settings configure no channel, so nothing can actually be sent.
    resp = await client.post(
        "/api/v1/claims/CG-00882/member-alert",
        headers=auth_headers,
        json={"channel": "SMS"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["alert"]["delivered"] is False
    events = [t["event"] for t in data["claim"]["timeline"]]
    assert "Member alert not delivered" in events


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setattr(settings, "gateway_webhook_secret", "s3cret")
    return {**FORM, "x-gateway-token": "s3cret"}


async def _alert_phone(client: AsyncClient, headers: dict, ref: str) -> str:
    resp = await client.post(
        f"/api/v1/claims/{ref}/member-alert", headers=headers, json={"channel": "SMS"}
    )
    return resp.json()["data"]["alert"]["to"].replace(" ", "")


async def test_gateway_webhooks_reject_bad_or_missing_secret(client: AsyncClient, gateway):
    for path in ("sms", "ussd"):
        url = f"/api/v1/webhooks/{path}"
        assert (await client.post(url, content="", headers=FORM)).status_code == 403
        wrong = {**FORM, "x-gateway-token": "nope"}
        assert (await client.post(url, content="", headers=wrong)).status_code == 403


async def test_gateway_webhooks_disabled_without_secret(client: AsyncClient):
    headers = {**FORM, "x-gateway-token": ""}
    resp = await client.post("/api/v1/webhooks/sms", content="", headers=headers)
    assert resp.status_code == 403


async def test_sms_reply_confirms_claim(client: AsyncClient, auth_headers: dict, gateway):
    phone = await _alert_phone(client, auth_headers, "CG-00882")
    resp = await client.post(
        "/api/v1/webhooks/sms",
        content=urlencode({"from": phone, "text": "yes thanks"}),
        headers=gateway,
    )
    assert resp.status_code == 204
    claim = (await client.get("/api/v1/claims/CG-00882", headers=auth_headers)).json()
    assert claim["data"]["memberResponse"] == "CONFIRMED"


async def test_ussd_session_shows_menu_then_records_choice(
    client: AsyncClient, auth_headers: dict, gateway
):
    phone = await _alert_phone(client, auth_headers, "CG-00882")

    def step(text: str) -> dict:
        return {"sessionId": "s1", "phoneNumber": phone, "text": text}

    menu = await client.post("/api/v1/webhooks/ussd", content=urlencode(step("")), headers=gateway)
    assert menu.text.startswith("CON ClaimGuard Alert")
    assert "1=Yes 2=No" in menu.text

    bad = await client.post("/api/v1/webhooks/ussd", content=urlencode(step("9")), headers=gateway)
    assert bad.text.startswith("END Invalid choice")

    done = await client.post("/api/v1/webhooks/ussd", content=urlencode(step("2")), headers=gateway)
    assert done.text.startswith("END Thank you")
    claim = (await client.get("/api/v1/claims/CG-00882", headers=auth_headers)).json()
    assert claim["data"]["memberResponse"] == "DISPUTED"


async def test_ussd_with_nothing_pending(client: AsyncClient, gateway):
    body = urlencode({"sessionId": "s2", "phoneNumber": "+263000000000", "text": ""})
    resp = await client.post("/api/v1/webhooks/ussd", content=body, headers=gateway)
    assert resp.text.startswith("END ClaimGuard: no claims")
