"""The demo's daily sample claims: once per day, never in the future, demo mode only."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import SessionFactory
from app.models.claim import Claim
from app.services import demo_activity
from app.services.demo_activity import DemoModeOff, top_up_recent


async def test_each_recent_day_is_filled_once():
    now = datetime.now(UTC)
    async with SessionFactory() as session:
        await top_up_recent(session, now)
        await session.commit()
    async with SessionFactory() as session:
        assert await top_up_recent(session, now) == 0  # already there: nothing added

        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        for back in range(demo_activity.RECENT_DAYS):
            start = today - timedelta(days=back)
            count = (await session.execute(
                select(func.count()).select_from(Claim).where(
                    Claim.claim_ref.like(f"CG-D{start:%y%m%d}%")
                )
            )).scalar_one()
            assert 4 <= count <= 7, (start.date(), count)

        latest = (await session.execute(
            select(func.max(Claim.submitted_at)).where(Claim.claim_ref.like("CG-D%"))
        )).scalar_one()
        latest = latest if latest.tzinfo else latest.replace(tzinfo=UTC)
        assert latest <= now


async def test_refuses_outside_demo_mode(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    async with SessionFactory() as session:
        with pytest.raises(DemoModeOff):
            await top_up_recent(session, datetime.now(UTC))


async def test_api_hook_does_nothing_outside_demo_mode(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(demo_activity, "_filled_for", None)

    async def boom(*_args, **_kwargs):
        raise AssertionError("top-up ran with demo mode off")

    monkeypatch.setattr(demo_activity, "top_up_recent", boom)
    await demo_activity.ensure_recent_activity()
