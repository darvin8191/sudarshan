"""RFC 6238 time-based one-time passwords (HMAC-SHA1, 30 s steps, 6 digits), stdlib only."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
from urllib.parse import parse_qs, urlsplit

STEP_S = 30
DIGITS = 6
MIN_SECRET_LEN = 16
MAX_SECRET_LEN = 64
_BASE32 = re.compile(r"[A-Z2-7]+")


class InvalidSecret(ValueError):
    """Not a base32 TOTP key of an acceptable length."""


def _secret_from_qr_text(raw: str) -> str:
    """The key inside an authenticator QR code's text (``otpauth://totp/...?secret=...``).

    Upstox shows TOTP setup as a QR code; a phone camera can copy the QR's text, so the owner may
    paste that whole text instead of the bare key. Only a standard code is accepted (SHA1, 6
    digits, 30 s), because that is the only kind ``totp`` computes.

    Raises:
        InvalidSecret: not a TOTP link, no key in it, or a non-standard code.
    """
    link = urlsplit(raw.strip())
    if link.scheme.lower() != "otpauth" or link.netloc.lower() != "totp":
        raise InvalidSecret("not a TOTP link")
    params = {key.lower(): values[-1] for key, values in parse_qs(link.query).items()}
    if params.get("algorithm", "SHA1").upper() != "SHA1":
        raise InvalidSecret("not SHA1")
    if params.get("digits", str(DIGITS)) != str(DIGITS):
        raise InvalidSecret("not 6 digits")
    if params.get("period", str(STEP_S)) != str(STEP_S):
        raise InvalidSecret("not 30 seconds")
    secret = params.get("secret", "")
    if not secret:
        raise InvalidSecret("no key in the link")
    return secret


def normalize_secret(raw: str) -> str:
    """The canonical form of a TOTP key as typed or pasted: no whitespace, upper case, no padding.

    The whole text of an authenticator QR code (``otpauth://totp/...``) is accepted too; its key
    is taken out of it.

    Raises:
        InvalidSecret: not 16-64 base32 characters (A-Z, 2-7), or not decodable.
    """
    if not isinstance(raw, str):
        raise InvalidSecret("not text")
    if raw.strip().lower().startswith("otpauth:"):
        raw = _secret_from_qr_text(raw)
    secret = re.sub(r"\s+", "", raw).upper().rstrip("=")
    if not MIN_SECRET_LEN <= len(secret) <= MAX_SECRET_LEN:
        raise InvalidSecret("wrong length")
    if not _BASE32.fullmatch(secret):
        raise InvalidSecret("not base32")
    try:
        _decode(secret)
    except (binascii.Error, ValueError) as exc:  # a length base32 cannot end on
        raise InvalidSecret("not decodable") from exc
    return secret


def _decode(secret: str) -> bytes:
    return base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=False)


def hotp(key: bytes, counter: int, digits: int = DIGITS, digest=hashlib.sha1) -> str:
    """RFC 4226 HOTP value for ``counter``."""
    mac = hmac.new(key, counter.to_bytes(8, "big"), digest).digest()
    offset = mac[-1] & 0x0F
    value = int.from_bytes(mac[offset : offset + 4], "big") & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def totp(
    secret: str, at: float, *, digits: int = DIGITS, step: int = STEP_S, digest=hashlib.sha1
) -> str:
    """The TOTP code for a normalised base32 ``secret`` at Unix time ``at``."""
    return hotp(_decode(secret), int(at // step), digits=digits, digest=digest)


def seconds_left(at: float, step: int = STEP_S) -> float:
    """Seconds until the code valid at ``at`` is replaced by the next one."""
    return step - (at % step)
