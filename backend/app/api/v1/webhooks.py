"""Inbound provider webhooks (public: authenticated by the provider's signature)."""
from __future__ import annotations

import base64
import hashlib
import hmac
from html import escape
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse

from app.core.config import settings
from app.core.dependencies import DbSession, client_ip
from app.core.logging import get_logger
from app.repositories.claim import ClaimRepository
from app.services.audit import AuditService
from app.services.claims import ClaimService, compose_rating_prompt, compose_ussd_menu
from app.services.notifications import CHANNELS

log = get_logger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# What a member can text back. First word only, case-insensitive.
_REPLIES = {
    "YES": "CONFIRMED", "Y": "CONFIRMED", "1": "CONFIRMED",
    "NO": "DISPUTED", "N": "DISPUTED", "2": "DISPUTED",
}
_RATINGS = {"1", "2", "3", "4", "5"}
_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
_RATED = "Thank you for your feedback."


def _twiml(message: str | None = None) -> str:
    if not message:
        return _EMPTY_TWIML
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Message>{escape(message)}</Message></Response>"
    )


def twilio_signature(url: str, params: dict[str, str], token: str) -> str:
    """Twilio's X-Twilio-Signature: base64(HMAC-SHA1(url + sorted k+v pairs))."""
    data = url + "".join(k + v for k, v in sorted(params.items()))
    digest = hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


@router.post("/twilio/whatsapp", summary="Member reply to a WhatsApp alert (Twilio)")
async def twilio_whatsapp(request: Request, db: DbSession) -> Response:
    params = dict(parse_qsl((await request.body()).decode(), keep_blank_values=True))
    signature = request.headers.get("x-twilio-signature", "")
    if not settings.whatsapp_configured or not hmac.compare_digest(
        twilio_signature(str(request.url), params, settings.twilio_auth_token),
        signature,
    ):
        return Response(status_code=403)

    phone = params.get("From", "").removeprefix("whatsapp:")
    outcome, claim = await _apply_reply(
        db, request, phone, params.get("Body", ""), via="WHATSAPP"
    )
    # Acknowledge everything (even unmatched text) so Twilio doesn't retry.
    return Response(_twiml(_follow_up(outcome, claim)), media_type="text/xml")


# ── SMS / USSD (vendor-neutral gateway) ──────────────────────────────────────
# Field names follow the common gateway convention (from/text for SMS;
# sessionId/phoneNumber/text with CON/END replies for USSD). Map them in the
# gateway's callback settings, or adapt here once the vendor is chosen.


def _gateway_ok(request: Request) -> bool:
    secret = settings.gateway_webhook_secret
    return bool(secret) and hmac.compare_digest(
        request.headers.get("x-gateway-token", ""), secret
    )


async def _form(request: Request) -> dict[str, str]:
    return dict(parse_qsl((await request.body()).decode(), keep_blank_values=True))


@router.post("/sms", summary="Member SMS reply to an alert (gateway callback)")
async def sms_reply(request: Request, db: DbSession) -> Response:
    if not _gateway_ok(request):
        return Response(status_code=403)
    params = await _form(request)
    phone = params.get("from", "")
    outcome, claim = await _apply_reply(db, request, phone, params.get("text", ""), via="SMS")
    if (text := _follow_up(outcome, claim)) and (sms := CHANNELS.get("SMS")):
        try:
            await sms.send(phone, "ClaimGuard", text)
        except Exception as exc:  # noqa: BLE001 - the reply is already saved
            log.warning("webhook.sms.follow_up_failed", error=str(exc))
    return Response(status_code=204)


@router.post("/ussd", summary="USSD session step (gateway callback)")
async def ussd_session(request: Request, db: DbSession) -> Response:
    """Stateless menu: `text` carries the member's choices so far ("" then "1").

    A timed-out session just never sends the choice; the claim stays PENDING
    and the member can dial again.
    """
    if not _gateway_ok(request):
        return Response(status_code=403)
    params = await _form(request)
    phone = params.get("phoneNumber", "").replace(" ", "")
    steps = [p for p in params.get("text", "").strip().split("*") if p]
    nothing = "END ClaimGuard: no claims are waiting for your reply."

    if not steps:
        claim = await ClaimRepository(db).pending_alert_for_phone(phone)
        if claim is None:
            return PlainTextResponse(nothing)
        return PlainTextResponse("CON " + compose_ussd_menu(claim))

    if len(steps) == 2 and steps[0] == "1" and steps[1] in _RATINGS:
        outcome, _ = await _apply_reply(db, request, phone, steps[1], via="USSD", rating=True)
        return PlainTextResponse(f"END {_RATED}" if outcome else nothing)

    if len(steps) != 1 or steps[0] not in ("1", "2"):
        return PlainTextResponse(
            f"END Invalid choice. Dial {settings.ussd_code} to try again."
        )
    outcome, claim = await _apply_reply(db, request, phone, steps[0], via="USSD")
    if outcome is None:
        return PlainTextResponse(nothing)
    if outcome == "CONFIRMED":
        return PlainTextResponse("CON " + compose_rating_prompt(claim))
    return PlainTextResponse("END Thank you. We will investigate this claim.")


def _follow_up(outcome: str | None, claim) -> str | None:
    """What to text back: ask for a rating after a confirmation, thank after a rating."""
    if outcome == "CONFIRMED":
        return compose_rating_prompt(claim)
    if outcome == "RATED":
        return _RATED
    return None


async def _apply_reply(
    db: DbSession, request: Request, phone: str, text: str, *, via: str, rating: bool = False
):
    """Apply a member's text to their latest open alert, else rate their last confirmed claim.

    Returns (outcome, claim): outcome is CONFIRMED, DISPUTED, RATED or None (no match).
    """
    # ponytail: an open alert wins, so "1" or "2" answers a new alert before it
    # rates an older claim. Put the claim ref in the prompt if that ever confuses.
    words = text.strip().upper().split()
    first = words[0] if words else ""
    phone = phone.replace(" ", "")
    repo = ClaimRepository(db)

    response = None if rating else _REPLIES.get(first)
    claim = await repo.pending_alert_for_phone(phone) if response else None
    if claim is not None:
        await ClaimService(db).record_member_response(claim.claim_ref, response, actor="member")
        await _audit(db, request, claim.claim_ref, "claim.member_response",
                     {"response": response, "via": via})
        return response, claim

    claim = await repo.awaiting_rating_for_phone(phone) if first in _RATINGS else None
    if claim is not None:
        await ClaimService(db).record_member_rating(claim.claim_ref, int(first))
        await _audit(db, request, claim.claim_ref, "claim.member_rating",
                     {"rating": int(first), "via": via})
        return "RATED", claim

    log.info("webhook.reply.ignored", via=via, matched_reply=bool(response))
    return None, None


async def _audit(db: DbSession, request: Request, ref: str, action: str, changes: dict) -> None:
    await AuditService(db).record(
        action=action,
        entity_type="claim",
        entity_id=ref,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request),
        changes=changes,
    )
