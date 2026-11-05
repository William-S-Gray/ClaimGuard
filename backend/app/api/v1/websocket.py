"""WebSocket gateway endpoint — realtime event stream for the frontend.

Authenticated: the client must present a valid access token as the `token`
query parameter (the stream carries claim/member data). Invalid or missing
tokens are rejected before the socket is accepted.
"""
from __future__ import annotations

import asyncio
import contextlib
import time

import jwt
from fastapi import APIRouter, WebSocket, status

from app.core.database import SessionFactory
from app.core.dependencies import load_current_user
from app.core.exceptions import AppError
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
    # The feed carries claim/member events: active staff only, checked against
    # the database (not the token's claims) so a disabled account can't connect.
    try:
        async with SessionFactory() as session:
            user = await load_current_user(session, payload["sub"])
    except AppError:
        user = None
    if user is None or not user.is_staff:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    conn_id = await manager.connect(websocket)
    heartbeat = asyncio.create_task(manager.heartbeat(websocket))
    # A socket outlives no token: close it when the token expires. The client
    # refreshes and reconnects, which re-runs the checks above.
    expiry = asyncio.create_task(_close_at(websocket, payload.get("exp", 0)))
    try:
        while True:
            message = await websocket.receive_text()
            if message == "ping":
                await websocket.send_json({"type": "pong"})
    except Exception:  # WebSocketDisconnect and transport errors
        pass
    finally:
        heartbeat.cancel()
        expiry.cancel()
        await manager.disconnect(conn_id)


async def _close_at(websocket: WebSocket, exp: int) -> None:
    await asyncio.sleep(max(0, exp - time.time()))
    with contextlib.suppress(Exception):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="token expired")
