"""Claims API + queue + lifecycle tests."""
from __future__ import annotations

from httpx import AsyncClient


async def test_queue_is_paginated(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/v1/claims?page=1&page_size=2", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) <= 2
    pagination = body["metadata"]["pagination"]
    assert pagination["totalItems"] >= 5
    assert pagination["pageSize"] == 2


async def test_queue_requires_auth(client: AsyncClient):
    assert (await client.get("/api/v1/claims")).status_code == 401


async def test_queue_accepts_batch_page_size(client: AsyncClient, auth_headers: dict):
    # The SPA batch-fetches page_size=200 for client-side filtering; must be allowed.
    resp = await client.get("/api/v1/claims?page=1&page_size=200", headers=auth_headers)
    assert resp.status_code == 200


async def test_queue_search_filter(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/v1/claims?search=Tendai", headers=auth_headers)
    assert resp.status_code == 200
    refs = [c["claimRef"] for c in resp.json()["data"]]
    assert "CG-00291" in refs


async def test_claim_detail(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/v1/claims/CG-00291", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["claimRef"] == "CG-00291"
    assert data["member"]["name"] == "Tendai Moyo"
    assert len(data["items"]) == 4
    assert "PRESCRIPTION_DATE_AFTER_SERVICE" in data["flags"]
    assert isinstance(data["expectedShortfall"], list)


async def test_claim_not_found(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/v1/claims/CG-DOES-NOT-EXIST", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["success"] is False


async def test_approve_requires_auth(client: AsyncClient):
    resp = await client.post("/api/v1/claims/CG-00441/approve")
    assert resp.status_code == 401


async def test_approve_claim(client: AsyncClient, auth_headers: dict):
    resp = await client.post("/api/v1/claims/CG-00441/approve", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["decision"] == "APPROVE"


async def test_rescore_preserves_persisted_signals(client: AsyncClient, auth_headers: dict):
    # CG-00112 was seeded with a syndicate flag → syndicate_signal is persisted on
    # the claim. Rescoring must reproduce that input (re-fire the syndicate rule),
    # not silently drop it, so the claim stays critical.
    resp = await client.post("/api/v1/claims/CG-00112/rescore", headers=auth_headers)
    assert resp.status_code == 200

    detail = (
        await client.get("/api/v1/claims/CG-00112", headers=auth_headers)
    ).json()["data"]
    assert "POTENTIAL_FRAUD_SYNDICATE_DETECTED" in detail["flags"]
    assert detail["priority"] == "CRITICAL"  # syndicate signal drives critical priority
    assert detail["riskLevel"] in {"HIGH", "CRITICAL"}


async def test_reject_claim(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/api/v1/claims/CG-00112/reject",
        headers=auth_headers,
        json={"reason": "REJECT_FRAUD"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["decision"] == "REJECT_FRAUD"


# ─── MemberGuard: alerts & confirmation ─────────────────────────────────────────
async def test_member_alert_and_confirm(client: AsyncClient, auth_headers: dict):
    # A response before any alert has been sent is rejected (CG-00882 is seeded
    # with member_notification_sent=False, so this runs before its alert below).
    premature = await client.post(
        "/api/v1/claims/CG-00882/member-response",
        headers=auth_headers,
        json={"response": "CONFIRMED"},
    )
    assert premature.status_code == 422

    alert = await client.post(
        "/api/v1/claims/CG-00882/member-alert",
        headers=auth_headers,
        json={"channel": "WHATSAPP"},
    )
    assert alert.status_code == 200, alert.text
    data = alert.json()["data"]
    assert data["alert"]["channel"] == "WHATSAPP"
    assert "did you receive this service" in data["alert"]["message"].lower()
    assert data["claim"]["memberNotificationSent"] is True
    assert data["claim"]["memberNotificationChannel"] == "WHATSAPP"
    assert data["claim"]["memberResponse"] == "PENDING"

    confirm = await client.post(
        "/api/v1/claims/CG-00882/member-response",
        headers=auth_headers,
        json={"response": "CONFIRMED"},
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["data"]["memberResponse"] == "CONFIRMED"


async def test_member_dispute_escalates_claim(client: AsyncClient, auth_headers: dict):
    await client.post(
        "/api/v1/claims/CG-00088/member-alert",
        headers=auth_headers,
        json={"channel": "SMS"},
    )
    disputed = await client.post(
        "/api/v1/claims/CG-00088/member-response",
        headers=auth_headers,
        json={"response": "DISPUTED"},
    )
    assert disputed.status_code == 200, disputed.text
    data = disputed.json()["data"]
    assert data["memberResponse"] == "DISPUTED"
    assert data["priority"] == "CRITICAL"
    assert "MEMBER_DISPUTED" in data["flags"]


async def test_member_alert_rejects_unknown_channel(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/api/v1/claims/CG-00291/member-alert",
        headers=auth_headers,
        json={"channel": "CARRIER_PIGEON"},
    )
    assert resp.status_code == 422


async def test_member_alert_requires_auth(client: AsyncClient):
    resp = await client.post(
        "/api/v1/claims/CG-00291/member-alert", json={"channel": "SMS"}
    )
    assert resp.status_code == 401


async def test_fraudshield_score_endpoint(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/api/v1/fraudshield/score",
        headers=auth_headers,
        json={
            "claimRef": "TEST-1",
            "claimedAmount": 88,
            "memberShortfall": 22,
            "expectedShortfallMin": 12,
            "expectedShortfallMax": 18,
            "providerTrustScore": 81,
            "providerFlags90d": 14,
            "prescriptionAfterService": True,
            "hasBiometric": False,
            "chronicDrugNoCondition": True,
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["riskScore"] >= 50
    assert data["aiExplanation"]

    audit = await client.get(
        "/api/v1/audit?entity_type=claim&pageSize=200", headers=auth_headers
    )
    assert any(
        e["action"] == "fraudshield.score" and e["entityId"] == "TEST-1"
        for e in audit.json()["data"]
    ), "every score should leave an audit row"


async def test_fraudshield_score_rejects_bad_input(
    client: AsyncClient, auth_headers: dict
):
    for bad in ({"claimedAmount": -5}, {"claimedAmount": 10, "providerTrustScore": 140}):
        resp = await client.post(
            "/api/v1/fraudshield/score", headers=auth_headers, json=bad
        )
        assert resp.status_code == 422, bad


async def test_dashboard_metrics(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/v1/dashboard/metrics", headers=auth_headers)
    assert resp.status_code == 200
    assert "claimsToday" in resp.json()["data"]


async def test_dashboard_requires_auth(client: AsyncClient):
    assert (await client.get("/api/v1/dashboard/metrics")).status_code == 401
    assert (await client.get("/api/v1/providers")).status_code == 401
    assert (await client.get("/api/v1/members")).status_code == 401


async def test_providers_paginated(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/v1/providers?page=1&page_size=3", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()["data"]) <= 3


async def test_trustscore_summary(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/v1/trustscore/summary", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["total"] == 6


# ─── Investigations ─────────────────────────────────────────────────────────────
async def test_open_investigation(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/api/v1/investigations",
        headers=auth_headers,
        json={"claimId": "CG-00291", "priority": "HIGH"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["claimRef"] == "CG-00291"
    assert data["status"] == "OPEN"
    assert data["memberName"] == "Tendai Moyo"


async def test_investigation_lifecycle(client: AsyncClient, auth_headers: dict):
    # Open (idempotent for CG-00112)
    opened = await client.post(
        "/api/v1/investigations",
        headers=auth_headers,
        json={"claimId": "CG-00112", "priority": "CRITICAL"},
    )
    inv_id = opened.json()["data"]["id"]

    # Comment
    c = await client.post(
        f"/api/v1/investigations/{inv_id}/comments",
        headers=auth_headers,
        json={"body": "Contacted provider for records."},
    )
    assert c.status_code == 200
    assert len(c.json()["data"]["comments"]) == 1

    # Resolve
    u = await client.patch(
        f"/api/v1/investigations/{inv_id}",
        headers=auth_headers,
        json={"status": "RESOLVED", "resolution": "CONFIRMED_FRAUD"},
    )
    assert u.status_code == 200
    data = u.json()["data"]
    assert data["status"] == "RESOLVED"
    assert data["resolvedAt"] is not None


async def test_investigations_list_paginated(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/v1/investigations?page=1&page_size=10", headers=auth_headers)
    assert resp.status_code == 200
    assert "pagination" in resp.json()["metadata"]


async def test_users_directory(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/v1/users", headers=auth_headers)
    assert resp.status_code == 200
    users = resp.json()["data"]
    assert len(users) >= 4  # admin + seeded team
    assert any(u["fullName"] == "Rudo Chidziva" for u in users)


async def test_assign_investigation(client: AsyncClient, auth_headers: dict):
    users = (await client.get("/api/v1/users", headers=auth_headers)).json()["data"]
    assignee = next(u for u in users if u["fullName"] == "Farai Nyathi")

    opened = await client.post(
        "/api/v1/investigations",
        headers=auth_headers,
        json={"claimId": "CG-00088", "priority": "MEDIUM"},
    )
    inv_id = opened.json()["data"]["id"]

    assigned = await client.patch(
        f"/api/v1/investigations/{inv_id}",
        headers=auth_headers,
        json={"assignedTo": assignee["id"]},
    )
    assert assigned.status_code == 200
    data = assigned.json()["data"]
    assert data["assignedTo"] == assignee["id"]
    assert data["assignedToName"] == "Farai Nyathi"

    # Unassign via empty string.
    unassigned = await client.patch(
        f"/api/v1/investigations/{inv_id}",
        headers=auth_headers,
        json={"assignedTo": ""},
    )
    assert unassigned.json()["data"]["assignedTo"] is None


async def test_assignment_notifies_assignee(client: AsyncClient, auth_headers: dict):
    users = (await client.get("/api/v1/users", headers=auth_headers)).json()["data"]
    farai = next(u for u in users if u["fullName"] == "Farai Nyathi")

    opened = await client.post(
        "/api/v1/investigations",
        headers=auth_headers,
        json={"claimId": "CG-00882", "priority": "LOW"},
    )
    inv_id = opened.json()["data"]["id"]
    await client.patch(
        f"/api/v1/investigations/{inv_id}",
        headers=auth_headers,
        json={"assignedTo": farai["id"]},
    )

    # Log in as Farai and confirm the targeted notification arrived.
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "farai.nyathi@claimguard.co.zw", "password": "ChangeMe!2026"},
    )
    token = login.json()["data"]["accessToken"]
    resp = await client.get(
        "/api/v1/notifications", headers={"Authorization": f"Bearer {token}"}
    )
    titles = [n["title"] for n in resp.json()["data"]]
    assert "📋 Case Assigned to You" in titles


async def test_investigation_reads_require_auth(client: AsyncClient):
    assert (await client.get("/api/v1/investigations")).status_code == 401
    assert (await client.get("/api/v1/investigations/does-not-exist")).status_code == 401
