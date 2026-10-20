"""One call to Upstox's TOTP token endpoint, and what its answer means for the owner.

``POST {base}/v2/totp-login/authorization/token`` with header ``x-api-key: <app API key>`` and
JSON ``{"totp": "<6 digits>", "pin": "<6 digits>"}``. The answer matches the normal Get Token
response (``access_token`` at the top level); a ``data`` wrapper is accepted too, in case Upstox
wraps it like its other v2 answers. Errors arrive as ``{"status": "error", "errors": [{"errorCode":
..., "message": ...}]}`` (``error_code`` accepted as well). Every successful call revokes the
token issued before it. Limits: 1/s, 10/min, 60/30 min.

Nothing here logs a request or response body: an error answer can echo the submitted values.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

log = logging.getLogger("sudarshan.autologin")

TOKEN_PATH = "/v2/totp-login/authorization/token"
CONNECT_TIMEOUT_S = 5.0
READ_TIMEOUT_S = 15.0
#: How long to stay away after Upstox says "too many requests".
RATE_LIMIT_COOLDOWN_S = 300

# code -> (outcome code, pause scheduled runs until the owner acts)
KNOWN_ERRORS = {
    "UDAPI100097": ("invalid_credentials", True),  # Invalid Credentials
    "UDAPI100098": ("totp_not_enabled", True),  # TOTP is not enabled for this user
    "UDAPI100099": ("rate_limited", False),  # Too Many Request Sent
    "UDAPI100101": ("not_individual_app", True),  # This API is allowed only for individual apps
}


@dataclass(frozen=True)
class LoginResult:
    ok: bool
    code: str
    token: str | None = field(default=None, repr=False)
    pause: bool = False
    cooldown_s: int = 0
    upstox_codes: tuple[str, ...] = ()
    http_status: int | None = None


def _error_codes(payload) -> tuple[str, ...]:
    if not isinstance(payload, dict):
        return ()
    errors = payload.get("errors")
    if isinstance(errors, dict):
        errors = [errors]
    codes = []
    for item in errors if isinstance(errors, list) else []:
        if isinstance(item, dict):
            code = item.get("errorCode") or item.get("error_code")
            if isinstance(code, str) and code:
                codes.append(code[:40])
    return tuple(codes)


def _access_token(payload) -> str | None:
    if not isinstance(payload, dict):
        return None
    for holder in (payload, payload.get("data")):
        if isinstance(holder, dict):
            token = holder.get("access_token")
            if isinstance(token, str) and token.strip():
                return token.strip()
    return None


def request_token(
    client: httpx.Client, base_url: str, api_key: str, totp_code: str, pin: str
) -> LoginResult:
    """Ask Upstox for today's access token. Never raises for network or HTTP problems."""
    url = base_url.rstrip("/") + TOKEN_PATH
    try:
        response = client.post(
            url,
            headers={
                "x-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={"totp": totp_code, "pin": pin},
            timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
        )
    except httpx.TimeoutException:
        log.warning("Upstox token request timed out")
        return LoginResult(ok=False, code="network")
    except httpx.HTTPError as exc:
        log.warning("Upstox token request failed: %s", type(exc).__name__)
        return LoginResult(ok=False, code="network")

    status = response.status_code
    try:
        payload = response.json()
    except ValueError:
        payload = None
    codes = _error_codes(payload)

    if status == 200:
        token = _access_token(payload)
        if token:
            return LoginResult(ok=True, code="ok", token=token, http_status=status)

    known = [KNOWN_ERRORS[upstox_code] for upstox_code in codes if upstox_code in KNOWN_ERRORS]
    refused = 400 <= status < 500 and status != 429
    if known:
        code, pause = known[0]
    elif status == 429:
        code, pause = "rate_limited", False
    elif refused:
        code, pause = "upstox_refused", True
    elif status >= 500:
        code, pause = "upstox_unavailable", False
    else:  # 200 without a token, or a redirect
        code, pause = "unexpected_response", False
    # The error codes above come from Upstox's documentation and have not all been seen live. Until
    # they have, every refusal except "too many requests" pauses: sending the same TOTP key and PIN
    # again could lock the Upstox account, and only the owner can tell what is wrong.
    pause = pause or refused
    cooldown_s = RATE_LIMIT_COOLDOWN_S if code == "rate_limited" or status == 429 else 0
    log_level = logging.ERROR if pause or code == "unexpected_response" else logging.WARNING
    log.log(
        log_level,
        "Upstox did not issue a token: HTTP %s, %s -> %s%s",
        status,
        ",".join(codes) or "no error code",
        code,
        " (auto-login paused)" if pause else "",
    )
    return LoginResult(
        ok=False,
        code=code,
        pause=pause,
        cooldown_s=cooldown_s,
        upstox_codes=codes,
        http_status=status,
    )
