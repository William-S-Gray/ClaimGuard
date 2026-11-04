"""Fill the demo with a year of claim activity, scored by the real FraudShield engine.

The base seed holds five hand-written claims from one week, which leaves the
dashboard nearly empty. This adds a dozen more members and ~140 claims spread
over the last twelve months (busier recently, a few today and yesterday),
played forward the way the team would have handled them. The generator lives
in app.services.demo_activity, which also keeps the last week topped up while
the API runs.

Create-only and deterministic, like scripts.seed: re-running skips what exists.
Refuses to run unless DEMO_MODE is on, so a real deployment stays clean.

    python -m scripts.seed_activity
"""
from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime

from app.core.database import SessionFactory, engine
from app.services.demo_activity import HISTORY_CLAIMS, DemoModeOff, seed_history
from app.services.providers import ProviderService


async def main() -> None:
    try:
        async with SessionFactory() as session:
            created = await seed_history(session, datetime.now(UTC))
            # Score providers from the claims just written (and start their history).
            providers = ProviderService(session)
            for p in await providers.repo.all_ranked():
                await providers.recalculate(p.code, reason="seed")
            await session.commit()
    except DemoModeOff as exc:
        sys.exit(f"seed_activity: {exc}")
    finally:
        await engine.dispose()
    print(f"seed_activity: {created} claims added ({HISTORY_CLAIMS - created} already present).")


if __name__ == "__main__":
    asyncio.run(main())
