"""Info-needed round trip with the provider, and member care ratings."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest_asyncio
from httpx import AsyncClient

from tests.conftest import _login
from tests.test_sms_ussd import _alert_phone, gateway  # noqa: F401 - fixture

PORTAL = "/api/v1/portal/provider"


@pytest_asyncio.fixture
async def portal_headers(client: AsyncClient) -> dict[str, str]:
    token = await _login(client, "portal@citypharmacy.co.zw")
    return {"Authorization": f"Bearer {token}"}


async def _open_claim(client: AsyncClient, auth_headers: dict) -> str:
    """A fresh, risky (so undecided) claim at the portal login's provider."""
    payload = {
        "claimRef": "CG-INFO-1",
        "memberNumber": "CIM-0441209",
        "providerCode": "PROV-BYO-00441",
        "serviceDate": "2026-11-01",
        "claimedAmount": 400,
        "memberShortfall": 150,
        "expectedShortfallMin": 10,
        "expectedShortfallMax": 20,
        "hasBiometric": False,
        "syndicateSignal": True,
        "items": [{"description": "Insulin Human 100U/mL", "quantity": 2,
                   "unitPrice": 200, "total": 400}],
    }
    resp = await client.post("/api/v1/claims/ingest", headers=auth_headers, json=payload)
    data = resp.json()["data"]
    assert data["decision"] not in ("APPROVE", "REJECT_FRAUD", "REJECT_ERROR"), data["decision"]
    return data["claimRef"]


async def test_info_request_round_trip(
    client: AsyncClient, auth_headers: dict, portal_headers: dict
):
    ref = await _open_claim(client, auth_headers)
    before = (await client.get(f"/api/v1/claims/{ref}", headers=auth_headers)).json()["data"]

    resp = await client.post(
        f"/api/v1/claims/{ref}/request-info",
        headers=auth_headers,
        json={"message": "Please send the prescription."},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["decision"] == "INFO_NEEDED"
    assert resp.json()["data"]["slaDeadline"] is None  # clock paused

    again = await client.post(
        f"/api/v1/claims/{ref}/request-info", headers=auth_headers, json={"message": "Again?"}
    )
    assert again.status_code in (400, 409, 422)

    queue = await client.get("/api/v1/claims?status=INFO_NEEDED", headers=auth_headers)
    assert ref in [c["claimRef"] for c in queue.json()["data"]]

    rows = (await client.get(f"{PORTAL}/claims?days=365", headers=portal_headers)).json()["data"]
    row = next(r for r in rows if r["claimRef"] == ref)
    assert row["status"] == "INFO_NEEDED"
    assert row["infoRequest"] == "Please send the prescription."

    answer = await client.post(
        f"{PORTAL}/claims/{ref}/info",
        headers=portal_headers,
        json={"response": "Prescription attached, dated the same day."},
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["data"]["infoRequest"] is None

    after = (await client.get(f"/api/v1/claims/{ref}", headers=auth_headers)).json()["data"]
    assert after["decision"] == before["decision"]  # back where it was
    # The clock resumes with the time that was left (about 48h for a new claim).
    deadline = datetime.fromisoformat(after["slaDeadline"])
    left = deadline.replace(tzinfo=deadline.tzinfo or UTC) - datetime.now(UTC)  # SQLite: naive
    assert timedelta(hours=47) < left <= timedelta(hours=48)
    assert after["timeline"][-1]["event"] == "Provider supplied information"
    assert after["timeline"][-1]["type"] == "provider"


async def test_old_claim_waiting_on_provider_still_shows(
    client: AsyncClient, auth_headers: dict, portal_headers: dict
):
    # CG-00291 (the portal provider's) was submitted months ago: outside the 30-day list.
    await client.post(
        "/api/v1/claims/CG-00291/request-info",
        headers=auth_headers,
        json={"message": "Please send the signed prescription."},
    )
    rows = (await client.get(f"{PORTAL}/claims?days=30", headers=portal_headers)).json()["data"]
    row = next(r for r in rows if r["claimRef"] == "CG-00291")
    # Disputed by the member too: the request still wins so the provider can answer.
    assert row["status"] == "INFO_NEEDED"
    assert row["infoRequest"] == "Please send the signed prescription."


async def test_decided_claim_cannot_be_sent_back(client: AsyncClient, auth_headers: dict):
    queue = await client.get("/api/v1/claims?status=APPROVE", headers=auth_headers)
    ref = queue.json()["data"][0]["claimRef"]
    resp = await client.post(
        f"/api/v1/claims/{ref}/request-info", headers=auth_headers, json={"message": "Docs?"}
    )
    assert resp.status_code in (400, 409, 422)


async def test_provider_cannot_answer_for_another_provider(
    client: AsyncClient, auth_headers: dict, portal_headers: dict
):
    me = (await client.get(f"{PORTAL}/dashboard", headers=portal_headers)).json()["data"]
    queue = (await client.get("/api/v1/claims?status=PEND_VERIFY", headers=auth_headers)).json()
    other = next(c for c in queue["data"] if c["provider"]["code"] != me["provider"]["code"])
    await client.post(
        f"/api/v1/claims/{other['claimRef']}/request-info",
        headers=auth_headers,
        json={"message": "Please send the invoice."},
    )
    resp = await client.post(
        f"{PORTAL}/claims/{other['claimRef']}/info",
        headers=portal_headers,
        json={"response": "Not my claim but here you go."},
    )
    assert resp.status_code == 404


async def test_sms_confirm_then_rating(client: AsyncClient, auth_headers: dict, gateway):  # noqa: F811
    phone = await _alert_phone(client, auth_headers, "CG-00882")
    for text in ("yes", "4"):
        resp = await client.post(
            "/api/v1/webhooks/sms", content=urlencode({"from": phone, "text": text}),
            headers=gateway,
        )
        assert resp.status_code == 204
    claim = (await client.get("/api/v1/claims/CG-00882", headers=auth_headers)).json()["data"]
    assert claim["memberRating"] == 4

    metrics = (await client.get("/api/v1/dashboard/metrics", headers=auth_headers)).json()
    assert metrics["data"]["memberRatings"] >= 1
    assert 1 <= metrics["data"]["memberRatingAvg"] <= 5


async def test_ussd_confirm_asks_for_rating(client: AsyncClient, auth_headers: dict, gateway):  # noqa: F811
    phone = await _alert_phone(client, auth_headers, "CG-00882")

    def step(text: str) -> str:
        return urlencode({"sessionId": "r1", "phoneNumber": phone, "text": text})

    confirmed = await client.post("/api/v1/webhooks/ussd", content=step("1"), headers=gateway)
    assert confirmed.text.startswith("CON Thank you. How would you rate")
    rated = await client.post("/api/v1/webhooks/ussd", content=step("1*5"), headers=gateway)
    assert rated.text.startswith("END Thank you for your feedback")
    claim = (await client.get("/api/v1/claims/CG-00882", headers=auth_headers)).json()["data"]
    assert claim["memberRating"] == 5


async def test_disputed_claim_is_not_rated(client: AsyncClient, auth_headers: dict, gateway):  # noqa: F811
    phone = await _alert_phone(client, auth_headers, "CG-00882")
    for text in ("no", "3"):
        await client.post(
            "/api/v1/webhooks/sms", content=urlencode({"from": phone, "text": text}),
            headers=gateway,
        )
    claim = (await client.get("/api/v1/claims/CG-00882", headers=auth_headers)).json()["data"]
    assert claim["memberResponse"] == "DISPUTED"
    assert claim["memberRating"] is None


async def test_portal_shows_member_satisfaction(client: AsyncClient, portal_headers: dict):
    data = (await client.get(f"{PORTAL}/dashboard", headers=portal_headers)).json()["data"]
    assert "memberRatings" in data["breakdown"]
    sat = data["breakdown"]["memberSatisfaction"]
    assert sat is None or 1 <= sat <= 5
