"""ClaimGuard 360° — FastAPI application factory & lifecycle."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import register_middleware
from app.core.redis import redis_client
from app.core.websocket import manager
from app.services.demo_activity import ensure_recent_activity

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    await redis_client.connect()
    # Per-worker subscriber for cross-process realtime fan-out (safe to run many).
    await manager.start_subscriber()
    log.info(
        "app.startup",
        app=settings.app_name,
        env=settings.environment,
        version=settings.version,
    )
    # Demo deployments only: give the last week its sample claims in the
    # background, so a host waking from sleep doesn't block on it.
    top_up = asyncio.create_task(ensure_recent_activity()) if settings.demo_mode else None
    yield
    if top_up:
        await top_up
    await manager.stop_subscriber()
    await redis_client.close()
    log.info("app.shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description=(
            "Enterprise healthcare fraud-prevention backend — "
            "Every claim verified. Every member protected. Every provider accountable."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-request-id", "x-correlation-id", "x-process-time-ms"],
    )
    register_middleware(app)
    register_exception_handlers(app)

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/", tags=["root"], include_in_schema=False)
    async def root() -> dict:
        return {
            "service": settings.app_name,
            "version": settings.version,
            "docs": "/docs",
            "health": f"{settings.api_v1_prefix}/health",
        }

    return app


app = create_app()
