"""Login lockout, live revocation and refresh-token reuse."""
from __future__ import annotations

import uuid

from httpx import AsyncClient

from app.core.config import settings
from tests.conftest import _login

AUTH = "/api/v1/auth"


async def _new_user(client: AsyncClient, admin: dict, roles: list[str]) -> tuple[str, str]:
    email = f"t-{uuid.uuid4().hex[:8]}@claimguard.co.zw"
    resp = await client.post(f"{AUTH}/users", headers=admin, json={
        "email": email, "fullName": "Test User",
        "password": settings.first_admin_password, "roles": roles,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"], email


async def test_lockout_holds_across_ip_addresses(client: AsyncClient, monkeypatch):
    """Rotating the IP (or a spoofed forwarding header) must not reset the count."""
    monkeypatch.setattr(settings, "auth_max_failures", 100)
    monkeypatch.setattr(settings, "auth_max_failures_per_account", 3)
    email = f"nobody-{uuid.uuid4().hex[:6]}@claimguard.co.zw"
    for i in range(3):
        resp = await client.post(
            f"{AUTH}/login",
            json={"email": email, "password": "wrong-password"},
            headers={"x-forwarded-for": f"198.51.100.{i}"},
        )
        assert resp.status_code == 401
    locked = await client.post(
        f"{AUTH}/login", json={"email": email, "password": "wrong-password"},
        headers={"x-forwarded-for": "198.51.100.99"},
    )
    assert locked.status_code == 429


async def test_failed_login_is_audited(client: AsyncClient, auth_headers: dict):
    email = f"nobody-{uuid.uuid4().hex[:6]}@claimguard.co.zw"
    await client.post(f"{AUTH}/login", json={"email": email, "password": "wrong-password"})
    audit = await client.get("/api/v1/audit?page_size=200", headers=auth_headers)
    assert any(
        e["action"] == "auth.login_failed" and e.get("actorEmail") == email
        for e in audit.json()["data"]
    ), "expected an auth.login_failed entry"


async def test_deactivation_takes_effect_immediately(client: AsyncClient, auth_headers: dict):
    user_id, email = await _new_user(client, auth_headers, ["agent"])
    token = await _login(client, email, settings.first_admin_password)
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/api/v1/claims", headers=headers)).status_code == 200

    await client.patch(f"{AUTH}/users/{user_id}", headers=auth_headers, json={"isActive": False})
    # Same, still-unexpired access token: refused now, not in 30 minutes.
    assert (await client.get("/api/v1/claims", headers=headers)).status_code == 401


async def test_role_change_takes_effect_immediately(client: AsyncClient, auth_headers: dict):
    user_id, email = await _new_user(client, auth_headers, ["agent"])
    token = await _login(client, email, settings.first_admin_password)
    headers = {"Authorization": f"Bearer {token}"}
    await client.patch(f"{AUTH}/users/{user_id}", headers=auth_headers, json={"roles": ["auditor"]})
    resp = await client.post("/api/v1/claims/CG-00441/rescore", headers=headers)
    assert resp.status_code == 403  # auditors can't change claims; the old token said agent


async def test_reused_refresh_token_ends_every_session(client: AsyncClient, auth_headers: dict):
    _, email = await _new_user(client, auth_headers, ["agent"])
    login = await client.post(f"{AUTH}/login", json={
        "email": email, "password": settings.first_admin_password})
    first = login.json()["data"]["refreshToken"]
    client.cookies.clear()  # send tokens in the body only

    rotated = await client.post(f"{AUTH}/refresh", json={"refreshToken": first})
    assert rotated.status_code == 200
    second = rotated.json()["data"]["refreshToken"]
    client.cookies.clear()

    # The spent token shows up again (it was copied): refuse it...
    assert (await client.post(f"{AUTH}/refresh", json={"refreshToken": first})).status_code == 401
    client.cookies.clear()
    # ...and the legitimate holder's newer token is revoked too.
    assert (await client.post(f"{AUTH}/refresh", json={"refreshToken": second})).status_code == 401


async def test_create_user_rejects_unknown_roles(client: AsyncClient, auth_headers: dict):
    resp = await client.post(f"{AUTH}/users", headers=auth_headers, json={
        "email": f"t-{uuid.uuid4().hex[:8]}@claimguard.co.zw", "fullName": "X",
        "password": settings.first_admin_password, "roles": ["superhero"],
    })
    assert resp.status_code == 422
