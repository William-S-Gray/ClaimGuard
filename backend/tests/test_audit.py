"""PHI-read auditing + audit-trail access control."""
from __future__ import annotations

from httpx import AsyncClient


async def test_viewing_a_claim_is_audited(client: AsyncClient, auth_headers: dict):
    await client.get("/api/v1/claims/CG-00291", headers=auth_headers)
    resp = await client.get(
        "/api/v1/audit?entity_type=claim&page_size=200", headers=auth_headers
    )
    entries = resp.json()["data"]
    views = [
        e for e in entries
        if e["action"] == "claim.view" and e["entityId"] == "CG-00291"
    ]
    assert views, "claim detail view should be recorded in the audit log"
    assert views[0]["actorId"]  # attributed to the viewer


async def test_viewing_a_member_is_audited(client: AsyncClient, auth_headers: dict):
    listed = await client.get("/api/v1/members?page=1&page_size=1", headers=auth_headers)
    member_id = listed.json()["data"][0]["id"]
    await client.get(f"/api/v1/members/{member_id}", headers=auth_headers)

    resp = await client.get(
        "/api/v1/audit?entity_type=member&page_size=200", headers=auth_headers
    )
    entries = resp.json()["data"]
    assert any(
        e["action"] == "member.view" and e["entityId"] == member_id for e in entries
    )


async def test_audit_requires_auth(client: AsyncClient):
    assert (await client.get("/api/v1/audit")).status_code == 401


async def test_audit_forbidden_for_agent(client: AsyncClient, agent_headers: dict):
    # A non-admin/non-auditor (agent) must not read the audit trail.
    assert (await client.get("/api/v1/audit", headers=agent_headers)).status_code == 403


async def test_audit_accessible_to_auditor(client: AsyncClient, auditor_headers: dict):
    resp = await client.get("/api/v1/audit", headers=auditor_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "pagination" in body["metadata"]
    assert len(body["data"]) >= 1


async def test_audit_lists_actor_name(client: AsyncClient, auth_headers: dict):
    resp = await client.get(
        "/api/v1/audit?action=claim.approve&page_size=100", headers=auth_headers
    )
    assert resp.status_code == 200
    rows = resp.json()["data"]
    named = [r for r in rows if r.get("actorName") == "Rudo Chidziva"]
    assert named, "seeded claim.approve should resolve to its actor's name"


async def test_audit_search_by_person_name(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/v1/audit?search=Rudo", headers=auth_headers)
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert rows, "expected at least one entry for Rudo"
    assert all(
        "rudo" in (r.get("actorName") or "").lower()
        or "rudo" in (r.get("actorEmail") or "").lower()
        for r in rows
    )


async def test_audit_search_by_email_matches_login(client: AsyncClient, auth_headers: dict):
    # Login rows carry only actor_email; searching the address still finds them.
    resp = await client.get("/api/v1/audit?search=tapiwa.sithole", headers=auth_headers)
    assert resp.status_code == 200
    actions = {r["action"] for r in resp.json()["data"]}
    assert "auth.login" in actions


async def test_audit_filter_by_action(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/v1/audit?action=claim.approve", headers=auth_headers)
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert rows and all(r["action"] == "claim.approve" for r in rows)


async def test_audit_filters_endpoint(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/v1/audit/filters", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "claim.approve" in data["actions"]
    assert "claim" in data["entityTypes"]


async def test_audit_entry_includes_actor_roles(client: AsyncClient, auth_headers: dict):
    resp = await client.get(
        "/api/v1/audit?action=claim.approve&page_size=100", headers=auth_headers
    )
    row = next(r for r in resp.json()["data"] if r.get("actorName") == "Rudo Chidziva")
    assert "analyst" in row["actorRoles"]


async def test_audit_detail_shows_entity_history(client: AsyncClient, auth_headers: dict):
    # The seeded reject of CG-00291; its detail should surface the record's history.
    listed = await client.get(
        "/api/v1/audit?action=claim.reject&entity_type=claim&page_size=100",
        headers=auth_headers,
    )
    entry = next(r for r in listed.json()["data"] if r["entityId"] == "CG-00291")

    detail = await client.get(f"/api/v1/audit/{entry['id']}", headers=auth_headers)
    assert detail.status_code == 200
    data = detail.json()["data"]
    assert data["entry"]["id"] == entry["id"]
    assert data["entry"]["entityId"] == "CG-00291"
    # Related events are all for the same record, and include the earlier view.
    assert data["related"], "expected related history for CG-00291"
    assert all(r["entityId"] == "CG-00291" for r in data["related"])
    assert any(r["action"] == "claim.view" for r in data["related"])


async def test_audit_detail_not_found(client: AsyncClient, auth_headers: dict):
    resp = await client.get(
        "/api/v1/audit/00000000-0000-0000-0000-000000000000", headers=auth_headers
    )
    assert resp.status_code == 404


async def test_audit_detail_forbidden_for_agent(client: AsyncClient, agent_headers: dict):
    resp = await client.get(
        "/api/v1/audit/00000000-0000-0000-0000-000000000000", headers=agent_headers
    )
    assert resp.status_code == 403
