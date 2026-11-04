"""MemberGuard: channel fallback and the signed Twilio WhatsApp reply webhook."""
from __future__ import annotations

from urllib.parse import urlencode

import pytest
from httpx import AsyncClient

import app.services.notifications as notif_mod
from app.api.v1.webhooks import twilio_signature
from app.core.config import settings

URL = "http://test/api/v1/webhooks/twilio/whatsapp"


class _Boom:
    channel = "WHATSAPP"

    async def send(self, to, title, message):
        raise RuntimeError("twilio down")


class _Ok:
    channel = "SMS"

    async def send(self, to, title, message):
        return True


async def test_alert_falls_back_to_next_channel(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    monkeypatch.setitem(notif_mod.CHANNELS, "WHATSAPP", _Boom())
    monkeypatch.setitem(notif_mod.CHANNELS, "SMS", _Ok())
    resp = await client.post(
        "/api/v1/claims/CG-00291/member-alert",
        headers=auth_headers,
        json={"channel": "WHATSAPP"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["alert"]["channel"] == "SMS"
    assert data["alert"]["delivered"] is True
    assert data["claim"]["memberNotificationChannel"] == "SMS"


@pytest.fixture
def twilio(monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", "AC123")
    monkeypatch.setattr(settings, "twilio_auth_token", "tok")
    monkeypatch.setattr(settings, "twilio_whatsapp_from", "+14155238886")


async def _reply(client: AsyncClient, phone: str, body: str, *, sign: bool = True):
    params = {"From": f"whatsapp:{phone}", "Body": body}
    headers = {"content-type": "application/x-www-form-urlencoded"}
    if sign:
        headers["x-twilio-signature"] = twilio_signature(URL, params, "tok")
    return await client.post(URL, content=urlencode(params), headers=headers)


async def test_webhook_rejects_unsigned_requests(client: AsyncClient, twilio):
    assert (await _reply(client, "+263772918470", "NO", sign=False)).status_code == 403


async def test_webhook_rejected_when_twilio_unconfigured(client: AsyncClient):
    assert (await _reply(client, "+263772918470", "NO")).status_code == 403


async def test_member_whatsapp_reply_disputes_claim(
    client: AsyncClient, auth_headers: dict, twilio
):
    alert = await client.post(
        "/api/v1/claims/CG-00291/member-alert",
        headers=auth_headers,
        json={"channel": "WHATSAPP"},
    )
    phone = alert.json()["data"]["alert"]["to"].replace(" ", "")

    # Chatter that isn't a YES/NO is acknowledged and ignored.
    ignored = await _reply(client, phone, "who is this?")
    assert ignored.status_code == 200
    assert "<Response>" in ignored.text

    resp = await _reply(client, phone, "no")
    assert resp.status_code == 200, resp.text

    claim = (await client.get("/api/v1/claims/CG-00291", headers=auth_headers)).json()
    assert claim["data"]["memberResponse"] == "DISPUTED"
    assert claim["data"]["priority"] == "CRITICAL"

    audit = await client.get(
        "/api/v1/audit?entity_type=claim&pageSize=200", headers=auth_headers
    )
    actions = {
        e["action"] for e in audit.json()["data"] if e["entityId"] == "CG-00291"
    }
    assert {"claim.member_alert", "claim.member_response"} <= actions
