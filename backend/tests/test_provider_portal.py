"""Provider portal: isolation from staff data, own-provider scoping, appeals."""
from __future__ import annotations

import re

import pytest
import pytest_asyncio
from httpx import AsyncClient

from tests.conftest import _login

OWN_CODE = "PROV-BYO-00441"  # the demo portal login's provider


@pytest_asyncio.fixture
async def portal_headers(client: AsyncClient) -> dict[str, str]:
    token = await _login(client, "portal@citypharmacy.co.zw")
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/claims",
        "/api/v1/claims/CG-00291",
        "/api/v1/members",
        "/api/v1/providers",
        f"/api/v1/providers/{OWN_CODE}/claims",
        "/api/v1/trustscore",
        "/api/v1/trustscore/appeals",
        "/api/v1/dashboard/metrics",
        "/api/v1/investigations",
        "/api/v1/notifications",
        "/api/v1/audit",
        "/api/v1/users",
    ],
)
async def test_provider_login_is_locked_out_of_staff_routes(
    client: AsyncClient, portal_headers: dict, path: str
):
    resp = await client.get(path, headers=portal_headers)
    assert resp.status_code == 403, f"{path} -> {resp.status_code}"


async def test_staff_cannot_use_portal_routes(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/v1/portal/provider/dashboard", headers=auth_headers)
    assert resp.status_code == 403


async def test_portal_requires_login(client: AsyncClient):
    assert (await client.get("/api/v1/portal/provider/dashboard")).status_code == 401


async def test_me_exposes_provider_link(client: AsyncClient, portal_headers: dict):
    me = (await client.get("/api/v1/auth/me", headers=portal_headers)).json()["data"]
    assert me["roles"] == ["provider"]
    assert me["providerId"]


async def test_dashboard_shows_own_provider(client: AsyncClient, portal_headers: dict):
    resp = await client.get("/api/v1/portal/provider/dashboard", headers=portal_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["provider"]["code"] == OWN_CODE
    assert 0 <= data["trustScore"] <= 100
    assert {"claimAccuracy", "investigationSuccess", "memberSatisfaction"} <= set(
        data["breakdown"]
    )
    assert data["breakdown"]["memberSatisfaction"] is None
    assert data["peers"]["count"] >= 1
    assert isinstance(data["history"], list)


async def test_claims_are_own_and_masked(
    client: AsyncClient, portal_headers: dict, auth_headers: dict
):
    staff_view = await client.get(f"/api/v1/providers/{OWN_CODE}/claims", headers=auth_headers)
    own_refs = {c["claimRef"] for c in staff_view.json()["data"]}

    resp = await client.get(
        "/api/v1/portal/provider/claims?days=365", headers=portal_headers
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()["data"]
    assert rows, "seeded claims for this provider should be visible"
    for row in rows:
        assert row["claimRef"] in own_refs
        assert re.fullmatch(r"(\w\. ?)+", row["member"]), row["member"]  # initials only
        assert "riskScore" not in row and "aiExplanation" not in row
        assert row["status"] in {"APPROVED", "UNDER_REVIEW", "DISPUTED", "REJECTED"}


async def test_report_downloads_as_csv(client: AsyncClient, portal_headers: dict):
    resp = await client.get(
        "/api/v1/portal/provider/report.csv?days=365", headers=portal_headers
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.text.splitlines()[0].startswith("claimRef,submittedAt")


async def test_appeal_workflow(
    client: AsyncClient,
    portal_headers: dict,
    auth_headers: dict,
    agent_headers: dict,
):
    url = "/api/v1/portal/provider/appeals"
    short = await client.post(url, headers=portal_headers, json={"reason": "unfair"})
    assert short.status_code == 422

    reason = "Two disputed claims were data-entry errors at the member's scheme."
    made = await client.post(url, headers=portal_headers, json={"reason": reason})
    assert made.status_code == 200, made.text
    appeal = made.json()["data"]
    assert appeal["status"] == "OPEN"

    again = await client.post(url, headers=portal_headers, json={"reason": reason})
    assert again.status_code == 422  # one open appeal at a time

    queue = await client.get("/api/v1/trustscore/appeals?status=OPEN", headers=auth_headers)
    assert any(a["id"] == appeal["id"] for a in queue.json()["data"])

    decide = f"/api/v1/trustscore/appeals/{appeal['id']}"
    body = {"outcome": "UPHELD", "outcomeNotes": "Confirmed scheme-side errors."}
    assert (await client.patch(decide, headers=agent_headers, json=body)).status_code == 403
    done = await client.patch(decide, headers=auth_headers, json=body)
    assert done.status_code == 200, done.text
    assert (await client.patch(decide, headers=auth_headers, json=body)).status_code == 422

    mine = (await client.get(url, headers=portal_headers)).json()["data"]
    assert mine[0]["status"] == "UPHELD"
    assert mine[0]["outcomeNotes"] == "Confirmed scheme-side errors."
