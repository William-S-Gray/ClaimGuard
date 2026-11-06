"""Phone-number normalisation for matching member replies to member records.

Gateways and members write the same Zimbabwe number many ways: "+263 77 291
8470", "263772918470", "0772918470", "00263772918470", "+263-77-291-8470".
Replies are matched to members by number, so every form has to reduce to one.
"""
from __future__ import annotations

import re

DEFAULT_COUNTRY_CODE = "263"  # Zimbabwe
_NON_DIGITS = re.compile(r"\D")


def normalize_phone(raw: str | None, country_code: str = DEFAULT_COUNTRY_CODE) -> str:
    """E.164 ("+263772918470"), or "" when there is no usable number.

    "00…" is the international prefix and a single leading "0" is a national
    number, which gets `country_code`. Anything else is taken to already carry
    its country code, with or without the "+".
    """
    if not raw:
        return ""
    digits = _NON_DIGITS.sub("", raw)
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = country_code + digits[1:]
    # E.164 numbers are 8-15 digits including the country code.
    if not 8 <= len(digits) <= 15:
        return ""
    return "+" + digits


def national_tail(e164: str, length: int = 9) -> str:
    """The last `length` digits: a cheap, format-proof key for a database prefilter."""
    return _NON_DIGITS.sub("", e164)[-length:]
