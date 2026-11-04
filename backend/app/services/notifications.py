"""Notification service + multi-channel dispatch (real Email/WhatsApp + fallbacks)."""
from __future__ import annotations

from email.message import EmailMessage
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.core.websocket import WSEventType, publish
from app.repositories.notification import NotificationRepository
from app.schemas.investigation import NotificationOut

log = get_logger(__name__)


# ── Channel adapters ─────────────────────────────────────────────────────────
class ChannelAdapter(Protocol):
    channel: str

    async def send(self, to: str, title: str, message: str) -> bool: ...


class _LoggingAdapter:
    """Unconfigured channel: logs the dispatch and reports it as NOT delivered.

    Returning False is what lets the member-alert fallback move on to the next
    channel, and keeps "delivered" honest when nothing actually went out.
    """

    def __init__(self, channel: str) -> None:
        self.channel = channel

    async def send(self, to: str, title: str, message: str) -> bool:
        log.info("notification.dispatch", channel=self.channel, to=to, title=title)
        return False


class SMTPEmailAdapter:
    """Sends email via async SMTP (aiosmtplib). Configured from SMTP_* settings."""

    channel = "EMAIL"

    async def send(self, to: str, title: str, message: str) -> bool:
        import aiosmtplib

        msg = EmailMessage()
        msg["From"] = settings.smtp_from
        msg["To"] = to
        msg["Subject"] = title
        msg.set_content(message)
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user or None,
            password=settings.smtp_password or None,
            start_tls=settings.smtp_starttls,
            timeout=15,
        )
        log.info("notification.email.sent", to=to, title=title)
        return True


class TwilioWhatsAppAdapter:
    """Sends WhatsApp messages via the Twilio REST API. Configured from TWILIO_*."""

    channel = "WHATSAPP"

    async def send(self, to: str, title: str, message: str) -> bool:
        import httpx

        url = (
            "https://api.twilio.com/2010-04-01/Accounts/"
            f"{settings.twilio_account_sid}/Messages.json"
        )
        form = {
            "From": f"whatsapp:{settings.twilio_whatsapp_from}",
            "To": f"whatsapp:{to.replace(' ', '')}",  # Twilio needs E.164
            "Body": f"*{title}*\n\n{message}",
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                url,
                data=form,
                auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            )
            resp.raise_for_status()
        log.info("notification.whatsapp.sent", to=to, title=title)
        return True


def _build_channels() -> dict[str, ChannelAdapter]:
    """Pick a real adapter when its channel is configured, else log-only."""
    email: ChannelAdapter = (
        SMTPEmailAdapter() if settings.email_configured else _LoggingAdapter("EMAIL")
    )
    whatsapp: ChannelAdapter = (
        TwilioWhatsAppAdapter()
        if settings.whatsapp_configured
        else _LoggingAdapter("WHATSAPP")
    )
    return {
        "WHATSAPP": whatsapp,
        "SMS": _LoggingAdapter("SMS"),
        "USSD": _LoggingAdapter("USSD"),
        "EMAIL": email,
        "APP_FEED": _LoggingAdapter("APP_FEED"),
    }


CHANNELS: dict[str, ChannelAdapter] = _build_channels()


class NotificationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = NotificationRepository(session)

    async def list_for_user(
        self, user_id: str, *, page: int, page_size: int
    ) -> tuple[list[dict], int, int]:
        offset = (max(page, 1) - 1) * page_size
        rows, total = await self.repo.list_for_user(
            user_id, offset=offset, limit=page_size
        )
        unread = await self.repo.unread_count(user_id)
        items = [
            NotificationOut.model_validate(n).model_dump(by_alias=True) for n in rows
        ]
        return items, total, unread

    async def create(
        self,
        *,
        user_id: str | None,
        title: str,
        message: str,
        type_: str = "info",
        channel: str | None = None,
        link: str | None = None,
        recipient: str | None = None,
    ) -> dict:
        notif = await self.repo.create(
            user_id=user_id,
            title=title,
            message=message,
            type=type_,
            channel=channel,
            link=link,
        )
        # Out-of-band delivery (email/WhatsApp). Never let a delivery failure break
        # the in-app notification — it's persisted and pushed over WS regardless.
        if channel and channel in CHANNELS:
            to = recipient or (str(user_id) if user_id else "broadcast")
            try:
                notif.delivered = await CHANNELS[channel].send(
                    to=to, title=title, message=message
                )
            except Exception as exc:
                log.warning(
                    "notification.delivery_failed",
                    channel=channel,
                    to=to,
                    error=str(exc),
                )
                notif.delivered = False
        await self.session.flush()
        publish(
            WSEventType.NOTIFICATION_SENT,
            {"title": title, "type": type_, "link": link},
        )
        return NotificationOut.model_validate(notif).model_dump(by_alias=True)

    async def mark_read(self, notification_id: str) -> None:
        notif = await self.repo.get(notification_id)
        if not notif:
            raise NotFoundError("Notification not found")
        notif.read = True
        await self.session.flush()

    async def mark_all_read(self, user_id: str) -> None:
        await self.repo.mark_all_read(user_id)

    async def clear(self, user_id: str) -> None:
        await self.repo.clear_for_user(user_id)

    async def create_from_event(
        self, event_type: str, payload: dict
    ) -> dict | None:
        """Map a domain event to a persisted broadcast notification.

        Mirrors the rules that previously lived in the frontend so notification
        content is now generated and stored at the source of truth.
        """
        spec = _event_to_notification(event_type, payload)
        if spec is None:
            return None
        return await self.create(user_id=None, **spec)


def _event_to_notification(event_type: str, p: dict) -> dict | None:
    """Return {title, message, type_, link} for notifiable events, else None."""
    if event_type == "claim_scored":
        ref = p.get("claimRef", "CG-Claim")
        member = p.get("member", "Member")
        score = int(p.get("riskScore", 0) or 0)
        link = f"/queue/{ref}"
        if score >= 80:
            return {
                "title": "🚨 High Risk Claim Flagged",
                "message": f"Claim {ref} ({member}) scored {score} and requires immediate review.",
                "type_": "alert",
                "link": link,
            }
        if score >= 50:
            return {
                "title": "⚠️ Moderate Risk Claim",
                "message": f"Claim {ref} ({member}) scored {score} and is pending verification.",
                "type_": "warning",
                "link": link,
            }
        return {
            "title": "✓ Claim Auto-Approved",
            "message": f"Claim {ref} ({member}) scored {score} and has been approved.",
            "type_": "info",
            "link": link,
        }

    if event_type == "member_response":
        ref = p.get("claimRef", "CG-Claim")
        member = p.get("member", "Member")
        link = f"/queue/{ref}"
        if p.get("response") == "DISPUTED":
            return {
                "title": "❌ Claim Disputed by Member",
                "message": f"{member} disputed claim {ref} via WhatsApp/USSD.",
                "type_": "alert",
                "link": link,
            }
        if p.get("response") == "CONFIRMED":
            return {
                "title": "✓ Claim Confirmed by Member",
                "message": f"{member} confirmed claim {ref} via WhatsApp/USSD.",
                "type_": "info",
                "link": link,
            }
        return None

    if event_type == "trustscore_updated":
        provider = p.get("provider", "Provider")
        code = p.get("code", "PROV")
        return {
            "title": "📉 Provider TrustScore Alert",
            "message": f"{provider} score dropped from {p.get('oldScore')} to {p.get('newScore')}.",
            "type_": "warning",
            "link": f"/trustscore/{code}",
        }

    return None


async def record_event_notification(event_type: str, payload: dict) -> None:
    """Persist a notification for a domain event using a fresh session.

    Safe to call from fire-and-forget tasks (e.g. the demo runner) that have no
    request-scoped session. No-op for non-notifiable events.
    """
    from app.core.database import SessionFactory

    if _event_to_notification(event_type, payload) is None:
        return
    async with SessionFactory() as session:
        await NotificationService(session).create_from_event(event_type, payload)
        await session.commit()
