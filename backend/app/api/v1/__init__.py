"""API v1 router aggregation.

Security posture: this is a PHI system, so **every data endpoint requires
authentication**. Only health checks, the auth flow, the (separately
token-authenticated) WebSocket and signature-verified webhooks are mounted
without the global auth dependency.
"""
from fastapi import APIRouter, Depends

from app.api.v1 import (
    audit,
    auth,
    claims,
    dashboard,
    demo,
    fraudshield,
    health,
    investigations,
    members,
    notifications,
    portal,
    providers,
    trustscore,
    users,
    webhooks,
    websocket,
)
from app.core.config import settings
from app.core.dependencies import get_staff_user

api_router = APIRouter()

# ── Public (no auth) ────────────────────────────────────────────────────────────
api_router.include_router(health.router)     # liveness/readiness probes
api_router.include_router(auth.router)        # login / refresh / logout
api_router.include_router(websocket.router)   # authenticates via token on connect
api_router.include_router(webhooks.router)    # authenticates via provider signature

# ── Authenticated (PHI + actions) ───────────────────────────────────────────────
# Staff only: a provider-portal login is rejected here even with a valid token.
protected = [Depends(get_staff_user)]
api_router.include_router(claims.router, dependencies=protected)
api_router.include_router(providers.router, dependencies=protected)
api_router.include_router(trustscore.router, dependencies=protected)
api_router.include_router(members.router, dependencies=protected)
api_router.include_router(dashboard.router, dependencies=protected)
api_router.include_router(fraudshield.router, dependencies=protected)
api_router.include_router(investigations.router, dependencies=protected)
api_router.include_router(users.router, dependencies=protected)
api_router.include_router(notifications.router, dependencies=protected)
api_router.include_router(audit.router, dependencies=protected)

# ── Provider portal (provider accounts only; scoped to their own provider) ─────
api_router.include_router(portal.router)

# ── Demo-only (scripted scenarios / mock broadcasts) ─────────────────────────────
# Never expose the demo scenario runner in a real deployment.
if settings.demo_mode:
    api_router.include_router(demo.router, dependencies=protected)
