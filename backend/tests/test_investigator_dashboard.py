"""Investigator dashboard: KPIs, queue sort/filter/search, similar claims."""
from __future__ import annotations

from httpx import AsyncClient


async def _queue(client: AsyncClient, headers: dict, **params) -> list[dict]:
    resp = await client.get(
        "/api/v1/claims", headers=headers, params={"page_size": 50, **params}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def test_investigator_kpis_reflect_resolved_cases(
    client: AsyncClient, auth_headers: dict
):
    opened = await client.post(
        "/api/v1/investigations",
        headers=auth_headers,
        json={"claimId": "CG-00088", "priority": "HIGH"},
    )
    inv_id = opened.json()["data"]["id"]
    await client.patch(
        f"/api/v1/investigations/{inv_id}",
        headers=auth_headers,
        json={"status": "RESOLVED", "resolution": "CONFIRMED_FRAUD"},
    )

    resp = await client.get("/api/v1/dashboard/investigator", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    kpis = resp.json()["data"]
    assert isinstance(kpis["pendingClaims"], int)
    assert kpis["casesResolved"] >= 1
    assert kpis["avgReviewHours"] is not None and kpis["avgReviewHours"] >= 0
    assert 0 < kpis["detectionAccuracy"] <= 100
    assert set(kpis["riskDistribution"]) == {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
    total = (await client.get("/api/v1/claims", headers=auth_headers)).json()
    assert sum(kpis["riskDistribution"].values()) == total["metadata"]["pagination"]["totalItems"]


async def test_queue_sorts_by_amount(client: AsyncClient, auth_headers: dict):
    amounts = [c["claimedAmount"] for c in await _queue(client, auth_headers, sort="amount")]
    assert len(amounts) > 1
    assert amounts == sorted(amounts, reverse=True)


async def test_queue_rejects_unknown_sort(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/v1/claims?sort=bogus", headers=auth_headers)
    assert resp.status_code == 422


async def test_queue_filters_by_risk_level(client: AsyncClient, auth_headers: dict):
    rows = await _queue(client, auth_headers, riskLevel="LOW")
    assert rows and all(c["riskLevel"] == "LOW" for c in rows)


async def test_queue_searches_by_provider_code(client: AsyncClient, auth_headers: dict):
    detail = (await client.get("/api/v1/claims/CG-00291", headers=auth_headers)).json()["data"]
    code = detail["provider"]["code"]
    rows = await _queue(client, auth_headers, search=code)
    assert rows and all(c["provider"]["code"] == code for c in rows)


async def test_queue_filters_by_investigator(client: AsyncClient, auth_headers: dict):
    me = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()["data"]
    await client.post(
        "/api/v1/investigations",
        headers=auth_headers,
        json={"claimId": "CG-00441", "priority": "MEDIUM", "assignedTo": me["id"]},
    )
    refs = {c["claimRef"] for c in await _queue(client, auth_headers, investigator=me["id"])}
    assert "CG-00441" in refs

    bad = await client.get("/api/v1/claims?investigator=not-a-uuid", headers=auth_headers)
    assert bad.status_code == 422


async def test_claim_detail_lists_similar_claims(client: AsyncClient, auth_headers: dict):
    detail = (await client.get("/api/v1/claims/CG-00291", headers=auth_headers)).json()["data"]
    similar = detail["similarClaims"]
    assert isinstance(similar, list)
    for c in similar:
        assert c["claimRef"] != "CG-00291"
        assert c["member"]["id"] == detail["member"]["id"]
        assert c["provider"]["code"] == detail["provider"]["code"]
