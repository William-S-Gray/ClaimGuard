"""RBAC negative paths — non-admins are blocked from admin-only endpoints."""

from __future__ import annotations

from httpx import AsyncClient


async def test_agent_cannot_create_users(client: AsyncClient, agent_headers: dict):
    resp = await client.post(
        "/api/v1/auth/users",
        headers=agent_headers,
        json={
            "email": "intruder@claimguard.co.zw",
            "fullName": "Intruder",
            "password": "whatever-123",
            "roles": ["admin"],
        },
    )
    assert resp.status_code == 403


async def test_admin_can_create_users(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/api/v1/auth/users",
        headers=auth_headers,
        json={
            "email": "newanalyst@claimguard.co.zw",
            "fullName": "New Analyst",
            "password": "a-strong-password",
            "roles": ["analyst"],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["email"] == "newanalyst@claimguard.co.zw"


async def test_agent_still_reads_phi(client: AsyncClient, agent_headers: dict):
    # Authorization is role-scoped, not all-or-nothing: an agent can do their job.
    assert (await client.get("/api/v1/claims", headers=agent_headers)).status_code == 200


# Write routes check their permission, not just a signed-in user: an auditor
# (audit:read, claim:read) can read claims and investigations but change nothing.
async def test_auditor_reads_claims(client: AsyncClient, auditor_headers: dict):
    assert (await client.get("/api/v1/claims", headers=auditor_headers)).status_code == 200
    assert (await client.get("/api/v1/investigations", headers=auditor_headers)).status_code == 200


async def test_auditor_cannot_change_claims(client: AsyncClient, auditor_headers: dict):
    for path in ("approve", "reject", "rescore", "member-alert"):
        resp = await client.post(
            f"/api/v1/claims/CG-00441/{path}", headers=auditor_headers, json={}
        )
        assert resp.status_code == 403, (path, resp.text)
    resp = await client.post(
        "/api/v1/claims/CG-00441/notes", headers=auditor_headers, json={"note": "x"}
    )
    assert resp.status_code == 403


async def test_auditor_cannot_open_investigations(client: AsyncClient, auditor_headers: dict):
    resp = await client.post(
        "/api/v1/investigations", headers=auditor_headers, json={"claimRef": "CG-00441"}
    )
    assert resp.status_code == 403
