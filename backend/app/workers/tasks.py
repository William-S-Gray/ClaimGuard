"""Background tasks: scoring, notification dispatch, TrustScore recalc, analytics.

Each task wraps an async unit of work via `run_async` so services can be reused.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.core.database import SessionFactory
from app.core.exceptions import BusinessRuleError
from app.core.logging import get_logger
from app.services.claims import ClaimService
from app.services.notifications import NotificationService
from app.services.providers import ProviderService
from app.workers.celery_app import celery_app

log = get_logger("worker")


def run_async(coro) -> Any:  # noqa: ANN001
    return asyncio.run(coro)


@celery_app.task(name="app.workers.tasks.score_claim", bind=True, max_retries=3)
def score_claim(self, claim_ref: str) -> dict:  # noqa: ANN001
    async def _work() -> dict:
        async with SessionFactory() as session:
            result = await ClaimService(session).rescore(claim_ref)
            await session.commit()
            return {"claimRef": claim_ref, "riskScore": result["riskScore"]}

    try:
        return run_async(_work())
    except BusinessRuleError as exc:
        # Decided or waiting on the provider: not rescoreable, and retrying won't change that.
        log.info("task.score_claim.skipped", claim_ref=claim_ref, reason=str(exc))
        return {"claimRef": claim_ref, "skipped": str(exc)}
    except Exception as exc:  # noqa: BLE001
        log.error("task.score_claim.failed", claim_ref=claim_ref, error=str(exc))
        raise self.retry(exc=exc, countdown=10) from exc


@celery_app.task(name="app.workers.tasks.dispatch_notification", bind=True, max_retries=5)
def dispatch_notification(
    self, user_id: str | None, title: str, message: str, channel: str | None = None
) -> dict:  # noqa: ANN001
    async def _work() -> dict:
        async with SessionFactory() as session:
            data = await NotificationService(session).create(
                user_id=user_id, title=title, message=message, channel=channel
            )
            await session.commit()
            return data

    try:
        return run_async(_work())
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc, countdown=2**self.request.retries) from exc


@celery_app.task(name="app.workers.tasks.recalculate_all_trustscores")
def recalculate_all_trustscores() -> dict:
    async def _work() -> dict:
        async with SessionFactory() as session:
            service = ProviderService(session)
            providers = await service.repo.all_ranked()
            count = 0
            for p in providers:
                await service.recalculate(p.code, reason="nightly")
                count += 1
            await session.commit()
            return {"recalculated": count}

    result = run_async(_work())
    log.info("task.trustscore_recalc", **result)
    return result


@celery_app.task(name="app.workers.tasks.aggregate_analytics")
def aggregate_analytics() -> dict:
    log.info("task.analytics.aggregate")
    return {"status": "ok"}


@celery_app.task(name="app.workers.tasks.generate_report")
def generate_report(report_type: str = "fraud_summary") -> dict:
    log.info("task.report.generate", report_type=report_type)
    return {"report": report_type, "status": "queued"}
