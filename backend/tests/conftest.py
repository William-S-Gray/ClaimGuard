"""Pytest fixtures — SQLite-backed app instance with seeded demo data."""
from __future__ import annotations

import os

# Must be set BEFORE importing app modules (settings is cached at import).
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_claimguard.db")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6399/0")  # forces fallback
os.environ.setdefault("RATE_LIMIT_PER_MINUTE", "100000")

import contextlib  # noqa: E402

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.core.database import SessionFactory, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from scripts import seed  # noqa: E402


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _prepare_database():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with SessionFactory() as session:
        await seed.seed_rbac(session)
        await seed.seed_admin(session)
        await seed.seed_team(session)
        await seed.seed_members(session)
        await seed.seed_providers(session)
        await seed.seed_provider_users(session)
        await seed.seed_claims(session)
        await seed.seed_notifications(session)
        await seed.seed_audit(session)
        await session.commit()
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
    with contextlib.suppress(OSError):
        os.remove("./test_claimguard.db")


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _login(client: AsyncClient, email: str, password: str = "ChangeMe!2026") -> str:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["accessToken"]


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    token = await _login(client, "admin@claimguard.co.zw")
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def agent_headers(client: AsyncClient) -> dict[str, str]:
    """A seeded non-admin (agent) — for RBAC negative tests."""
    token = await _login(client, "farai.nyathi@claimguard.co.zw")
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def auditor_headers(client: AsyncClient) -> dict[str, str]:
    """A seeded auditor — audit-trail read access without admin rights."""
    token = await _login(client, "tapiwa.sithole@claimguard.co.zw")
    return {"Authorization": f"Bearer {token}"}
