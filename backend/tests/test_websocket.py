"""WebSocket gateway auth — the realtime PHI stream must reject unauthenticated sockets.

Uses Starlette's sync TestClient (httpx's ASGI transport can't do WebSockets). The
app lifespan isn't started; accepted sockets belong to seeded users, because the
gateway checks the account in the database, not just the token.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import settings
from app.core.security import create_access_token
from app.main import app

client = TestClient(app)  # no context manager → lifespan not started


def _token_for(email: str) -> str:
    """A real access token for a seeded user (the demo team shares one password)."""
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": settings.first_admin_password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["accessToken"]


def test_ws_rejects_missing_token():
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/api/v1/ws"):
        pass


def test_ws_rejects_invalid_token():
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        "/api/v1/ws?token=not-a-real-token"
    ):
        pass


def test_ws_rejects_refresh_token():
    # A refresh token must not be accepted where an access token is required.
    from app.core.security import create_refresh_token

    refresh, _, _ = create_refresh_token("11111111-1111-1111-1111-111111111111")
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        f"/api/v1/ws?token={refresh}"
    ):
        pass


def test_ws_accepts_valid_access_token():
    token = _token_for("farai.nyathi@claimguard.co.zw")  # seeded agent
    with client.websocket_connect(f"/api/v1/ws?token={token}") as ws:
        welcome = ws.receive_json()  # server greets on connect
        assert welcome["type"] == "system_health"
        ws.send_text("ping")
        assert ws.receive_json() == {"type": "pong"}


@pytest.mark.parametrize(
    ("roles", "provider_id"),
    [
        ([], None),  # no staff role
        (["provider"], "22222222-2222-2222-2222-222222222222"),  # portal account
        (["agent"], "22222222-2222-2222-2222-222222222222"),  # portal + staff role
    ],
)
def test_ws_rejects_non_staff_tokens(roles, provider_id):
    token = create_access_token(
        "11111111-1111-1111-1111-111111111111", roles=roles, provider_id=provider_id
    )
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        f"/api/v1/ws?token={token}"
    ):
        pass


def test_ws_rejects_a_real_provider_login():
    token = _token_for("portal@citypharmacy.co.zw")
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        f"/api/v1/ws?token={token}"
    ):
        pass


def test_ws_rejects_a_valid_token_for_an_unknown_user():
    # Signed and unexpired, but the account is gone: the database has the final say.
    token = create_access_token(
        "11111111-1111-1111-1111-111111111111", roles=["admin"], permissions=["*"]
    )
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        f"/api/v1/ws?token={token}"
    ):
        pass
