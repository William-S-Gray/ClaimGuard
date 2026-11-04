"""WebSocket gateway endpoint — realtime event stream for the frontend.

Authenticated: the client must present a valid access token as the `token`
query parameter (the stream carries claim/member data). Invalid or missing
tokens are rejected before the socket is accepted.
"""
from __future__ import annotations

import asyncio

import jwt
from fastapi import APIRouter, WebSocket, status

from app.core.dependencies import CurrentUser
from app.core.logging import get_logger
from app.core.security import decode_token
from app.core.websocket import manager

router = APIRouter(tags=["websocket"])
log = get_logger(__name__)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token", "")
    try:
        payload = decode_token(token, expected_type="access")
    except jwt.PyJWTError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    # The feed carries claim/member events: staff only.
    if not CurrentUser(
        id=payload["sub"],
        roles=payload.get("roles", []),
        permissions=payload.get("perms", []),
        provider_id=payload.get("prov"),
    ).is_staff:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    conn_id = await manager.connect(websocket)
    heartbeat = asyncio.create_task(manager.heartbeat(websocket))
    try:
        while True:
            message = await websocket.receive_text()
            if message == "ping":
                await websocket.send_json({"type": "pong"})
    except Exception:  # WebSocketDisconnect and transport errors
        pass
    finally:
        heartbeat.cancel()
        await manager.disconnect(conn_id)
