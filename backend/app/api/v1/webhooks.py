"""Inbound provider webhooks (public: authenticated by the provider's signature)."""
from __future__ import annotations

import base64
import hashlib
import hmac
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request, Response

from app.core.config import settings
from app.core.dependencies import DbSession, client_ip
from app.core.logging import get_logger
from app.repositories.claim import ClaimRepository
from app.services.audit import AuditService
from app.services.claims import ClaimService

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

    words = params.get("Body", "").strip().upper().split()
    response = _REPLIES.get(words[0]) if words else None
    phone = params.get("From", "").removeprefix("whatsapp:").replace(" ", "")
    claim = await ClaimRepository(db).pending_alert_for_phone(phone) if response else None
    if claim is None:
        # Unrecognised text or no open alert: acknowledge so Twilio doesn't retry.
        log.info("webhook.whatsapp.ignored", matched_reply=bool(response))
        return Response(_EMPTY_TWIML, media_type="text/xml")

    await ClaimService(db).record_member_response(claim.claim_ref, response, actor="member")
    await AuditService(db).record(
        action="claim.member_response",
        entity_type="claim",
        entity_id=claim.claim_ref,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request),
        changes={"response": response, "via": "WHATSAPP"},
    )
    return Response(_EMPTY_TWIML, media_type="text/xml")
