"""Inbound provider webhooks (public: authenticated by the provider's signature)."""
from __future__ import annotations

import base64
import hashlib
import hmac
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse

from app.core.config import settings
from app.core.dependencies import DbSession, client_ip
from app.core.logging import get_logger
from app.repositories.claim import ClaimRepository
from app.services.audit import AuditService
from app.services.claims import USSD_CODE, ClaimService, compose_ussd_menu

log = get_logger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# What a member can text back. First word only, case-insensitive.
_REPLIES = {
    "YES": "CONFIRMED", "Y": "CONFIRMED", "1": "CONFIRMED",
    "NO": "DISPUTED", "N": "DISPUTED", "2": "DISPUTED",
}
_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


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
    await _apply_reply(db, request, phone, params.get("Body", ""), via="WHATSAPP")
    # Acknowledge everything (even unmatched text) so Twilio doesn't retry.
    return Response(_EMPTY_TWIML, media_type="text/xml")


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
    await _apply_reply(db, request, params.get("from", ""), params.get("text", ""), via="SMS")
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
    choice = params.get("text", "").strip().split("*")[-1]

    if not choice:
        claim = await ClaimRepository(db).pending_alert_for_phone(phone)
        if claim is None:
            return PlainTextResponse("END ClaimGuard: no claims are waiting for your reply.")
        return PlainTextResponse("CON " + compose_ussd_menu(claim))

    if choice not in ("1", "2"):
        return PlainTextResponse(f"END Invalid choice. Dial {USSD_CODE} to try again.")
    matched = await _apply_reply(db, request, phone, choice, via="USSD")
    if not matched:
        return PlainTextResponse("END ClaimGuard: no claims are waiting for your reply.")
    if choice == "1":
        return PlainTextResponse("END Thank you. The claim is confirmed.")
    return PlainTextResponse("END Thank you. We will investigate this claim.")


async def _apply_reply(
    db: DbSession, request: Request, phone: str, text: str, *, via: str
) -> bool:
    """Map a member's reply to their latest open alert. False if nothing matched."""
    words = text.strip().upper().split()
    response = _REPLIES.get(words[0]) if words else None
    phone = phone.replace(" ", "")
    claim = await ClaimRepository(db).pending_alert_for_phone(phone) if response else None
    if claim is None:
        log.info("webhook.reply.ignored", via=via, matched_reply=bool(response))
        return False

    await ClaimService(db).record_member_response(claim.claim_ref, response, actor="member")
    await AuditService(db).record(
        action="claim.member_response",
        entity_type="claim",
        entity_id=claim.claim_ref,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request),
        changes={"response": response, "via": via},
    )
    return True
