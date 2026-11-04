"""Dashboard figures must agree with each other and with the claims behind them."""
from __future__ import annotations

from httpx import AsyncClient


async def test_today_card_counts_only_today(client: AsyncClient, auth_headers: dict):
    data = (await client.get("/api/v1/dashboard/metrics", headers=auth_headers)).json()["data"]

    # "0 claims today" once sat over "2 auto-approved": the approvals were all-time.
    assert data["autoApprovedToday"] <= data["claimsToday"]
    assert data["flaggedToday"] <= data["claimsToday"]
    assert data["claimsYesterday"] >= 0


async def test_savings_chart_is_built_from_the_claims(client: AsyncClient, auth_headers: dict):
    series = (await client.get("/api/v1/dashboard/savings", headers=auth_headers)).json()["data"]

    assert len(series) == 12
    running = 0.0
    for point in series:
        assert point["savings"] >= 0
        running += point["savings"]
        assert abs(point["cumulative"] - running) < 0.01
        assert "target" not in point

    # The 12-month window can never hold more than the all-time figure on the card.
    metrics = (await client.get("/api/v1/dashboard/metrics", headers=auth_headers)).json()["data"]
    assert series[-1]["cumulative"] <= metrics["estimatedSaved"] + 0.01


async def test_ussd_counts_add_up(client: AsyncClient, auth_headers: dict):
    data = (await client.get("/api/v1/dashboard/ussd", headers=auth_headers)).json()["data"]

    counts = {"alertsSent", "confirmed", "disputed", "awaitingReply"}
    assert set(data) == counts | {"code"}
    assert data["code"]  # the configured USSD code
    assert data["confirmed"] + data["disputed"] + data["awaitingReply"] == data["alertsSent"]
    assert min(data[k] for k in counts) >= 0


async def test_member_alerts_counts_disputed_replies(client: AsyncClient, auth_headers: dict):
    data = (await client.get("/api/v1/dashboard/metrics", headers=auth_headers)).json()["data"]

    # The seed's CG-00291 was disputed over WhatsApp. A dispute is stored as the
    # member's reply, never as a decision, so the card used to read 0.
    assert data["memberAlerts"] >= 1
