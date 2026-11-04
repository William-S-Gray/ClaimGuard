"""Dashboard & analytics schemas."""
from __future__ import annotations

from app.schemas.common import CamelModel


class DashboardMetrics(CamelModel):
    claims_today: int
    claims_yesterday: int
    flagged_today: int
    estimated_saved: float
    member_alerts: int
    member_rating_avg: float | None = None  # 1-5
    member_ratings: int = 0
    pending_investigation: int
    auto_approved_today: int
    avg_latency_ms: int
    detection_rate: float


class SavingsDataPoint(CamelModel):
    month: str
    savings: float
    cumulative: float


class USSDStats(CamelModel):
    """Member verification alerts sent over USSD, and how members replied."""

    alerts_sent: int
    confirmed: int
    disputed: int
    awaiting_reply: int
    code: str  # the USSD code members dial (settings.ussd_code)


class FraudTrendPoint(CamelModel):
    label: str
    flagged: int
    confirmed: int
    saved: float
