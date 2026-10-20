"""What the owner reads: the status card, the "Test now" answer and the Telegram message.

Plain trader language: the cause and the next action, never a status code, an error class or an
endpoint (those stay in the server log). No Markdown control characters either, because the
Telegram path sends with parse_mode Markdown.
"""

from __future__ import annotations

from datetime import datetime

from .schedule import HAND_LOGIN_DEADLINE, hhmm

BRAND = "सुदर्शन / SUDARSHAN"

#: Said once wherever a pause begins or is shown. Only a newly saved TOTP key or PIN ends a pause:
#: switching auto-login off and on does not, so a refused key and PIN are not sent again by mistake.
PAUSE_NOTE = (
    "Auto-login is paused until you save the TOTP key or the PIN again on the Auto login page."
)

# The cause only; a message that pauses gets PAUSE_NOTE added once (``paused_message``).
MESSAGES = {
    "ok": "Logged in to Upstox.",
    "invalid_credentials": "Upstox did not accept the saved TOTP key or PIN.",
    "totp_not_enabled": (
        "TOTP is not turned on for this Upstox account. Turn it on in the Upstox app, then save "
        "the new TOTP key here."
    ),
    "not_individual_app": (
        "Upstox allows this login only for an individual (personal) API app, and the API key in "
        "OpenAlgo belongs to another kind of app."
    ),
    "paused": PAUSE_NOTE,
    "test_paused": (
        "Auto-login is paused because Upstox refused the last login. Save the TOTP key or the PIN "
        "again first, so the same ones are not sent to Upstox twice."
    ),
    "rate_limited": "Upstox is refusing logins for a few minutes because of too many attempts.",
    "network": "Could not reach Upstox. The server's internet connection or Upstox may be down.",
    "upstox_unavailable": "Upstox's login service is not working right now.",
    "upstox_refused": (
        "Upstox refused the login. Check the TOTP key and PIN in the Upstox app; the server log "
        "has the details."
    ),
    "unexpected_response": "Upstox answered in an unexpected way. The server log has the details.",
    "secrets_missing": "The TOTP key or the PIN is not saved yet.",
    "secrets_unreadable": (
        "The saved TOTP key and PIN can no longer be read because OpenAlgo's encryption key "
        "changed. Save them again on the Auto login page."
    ),
    "not_configured": "The Upstox API key is missing from OpenAlgo's broker settings.",
    "wrong_broker": "OpenAlgo is set up for another broker. Auto-login works only with Upstox.",
    "no_user": "OpenAlgo has no user account yet.",
    "openalgo_unreachable": "OpenAlgo is not responding on this server.",
    "openalgo_busy": "OpenAlgo on this server kept asking to wait, so the login was not tried.",
    "handoff_failed": (
        "Upstox gave a new login, but OpenAlgo did not respond, so it was not saved."
    ),
    "store_failed": "Upstox gave a new login, but OpenAlgo could not save it.",
    "handoff_rejected": (
        "OpenAlgo did not accept the auto-login service's request. Restart OpenAlgo, then try "
        "Test now."
    ),
    "already_logged_in": "Upstox is already connected for today. Nothing to do.",
    "market_hours": (
        "Not available between 09:15 and 15:30 while Upstox is already connected for today: a "
        "new login would cut off the live session."
    ),
    "disabled": "Auto-login is off.",
    "busy": "Another login attempt is running. Try again in a minute.",
    "too_soon": "Upstox allows only a few logins a minute. Try again shortly.",
    "cooling_down": "Upstox asked for a pause after too many attempts. Try again a little later.",
    "too_many": "Too many login attempts in the last 30 minutes. Try again later.",
    "crashed": "The login check stopped unexpectedly. The server log has the details.",
}


def message_for(code: str) -> str:
    return MESSAGES.get(code, MESSAGES["crashed"])


def paused_message(paused_code: str) -> str:
    """The cause of a pause, then the pause and how to end it, each said once."""
    return f"{message_for(paused_code)} {PAUSE_NOTE}"


def telegram_ok(at: datetime, *, test: bool) -> str:
    prefix = "Test: " if test else ""
    return f"{BRAND}\n{prefix}Upstox auto-login OK ({hhmm(at)} IST)."


def telegram_failed(reason: str, *, at: datetime, test: bool, next_try: datetime | None) -> str:
    """Failure text: the reason, then what happens next and what the owner should do."""
    if test:
        return f"{BRAND}\nTest: Upstox auto-login failed. {reason}"
    if next_try is not None:
        action = (
            f"Will try again at {hhmm(next_try)}. If that fails too, please log in by hand "
            f"before {HAND_LOGIN_DEADLINE}."
        )
    elif hhmm(at) < HAND_LOGIN_DEADLINE:
        action = f"Please log in by hand before {HAND_LOGIN_DEADLINE}."
    else:
        action = "Please log in by hand now."
    return f"{BRAND}\nUpstox auto-login failed. {reason} {action}"
