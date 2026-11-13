"""Setting a forgotten confirm phrase back to the default, on the server (owner only).

    python -m sudarshan.guard.phrase --status    say whether the phrase is still the default
    python -m sudarshan.guard.phrase --reset     set it back to the default (STOP)

Without the phrase the kill switch cannot be turned off and no limit can be loosened, and the
app never shows it. Whoever can run this has the server, which is the owner by the deployment
model (CLAUDE.md "Security and Deployment Model"), so it asks for nothing more. It never prints
the phrase, only whether it is the default.

It runs in its own process. A reset writes the risk database, writes a History line ("set back
to the default on the server") and tells the owner on Telegram, so a reset nobody asked for is
seen. The app asks for the phrase fresh from the database every time (store.settings(fresh=True)),
so the reset applies at the next try, within seconds, with no restart.

Run it from the app folder as the service user, as the deploy tool does (sudarshan/README.md).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from utils.logging import get_logger

from . import store as risk_store
from . import words
from .schema import DEFAULTS

logger = get_logger(__name__)

APPLIES = "The app uses it from the next try, within seconds. Nothing needs restarting."
IS_DEFAULT = "The confirm phrase is the default."
NOTHING_CHANGED = "Nothing was changed."
NOT_DEFAULT = "The confirm phrase is not the default; it was changed in Settings > Risk."
UNREADABLE = "The risk database could not be read, so nothing was changed. Check the app's log."
NOT_SAVED = "The confirm phrase could not be set back, so nothing was changed. Check the app's log."


def _say(line: str) -> None:
    sys.stdout.write(line + "\n")


def is_default(settings: risk_store.RiskSettings) -> bool:
    """Whether the phrase is the shipped one (compared as the gate compares, never printed)."""
    return settings.confirm_phrase.strip().casefold() == str(DEFAULTS["confirm_phrase"]).casefold()


def told_words(at: datetime) -> str:
    """The Telegram notice: what happened, when, and what to do if it was not the owner."""
    clock = at.astimezone(risk_store.IST).strftime("%H:%M")
    return (
        f"The confirm phrase was set back to the default on the server at {clock}. "
        "If this was not you, change it in Settings > Risk and check who can reach the server."
    )


def tell_owner(text: str) -> str:
    """Send the notice on Telegram now, the way the guard's other notices go; says what happened.

    The guard's own notices queue to the app's alert pool. This is a process of its own that ends
    right after, so it sends in line instead, over the same call (``send_alert_sync``) and to the
    same linked chat, only when the bot is on.
    """
    try:
        from database.telegram_db import get_telegram_user_by_username
        from database.user_db import find_user_by_username
        from services.telegram_alert_service import telegram_alert_service
        from sudarshan.books.telegram_words import markdown_safe

        if not telegram_alert_service.is_bot_active():
            return "Telegram was not told: the Telegram bot is off."
        admin = find_user_by_username()
        user = get_telegram_user_by_username(admin.username) if admin is not None else None
        if not user:
            return "Telegram was not told: no Telegram account is linked."
        if telegram_alert_service.send_alert_sync(user["telegram_id"], markdown_safe(text)):
            return "Telegram was told."
        return "Telegram was not told: the message could not be sent."
    except Exception:
        logger.exception("The confirm phrase's reset could not be told on Telegram")
        return "Telegram was not told: it could not be reached from here."


def status(out=_say) -> int:
    try:
        settings = risk_store.STORE.settings(fresh=True)
    except Exception:
        logger.exception("The risk settings could not be read for the phrase status")
        out(UNREADABLE)
        return 1
    out(IS_DEFAULT if is_default(settings) else NOT_DEFAULT)
    return 0


def reset(out=_say, *, tell=tell_owner) -> int:
    """Set the phrase back to the default, record it, tell the owner; 0 when done or default."""
    try:
        settings = risk_store.STORE.settings(fresh=True)
    except Exception:
        logger.exception("The risk settings could not be read for a phrase reset")
        out(UNREADABLE)
        return 1
    if is_default(settings):
        out(f"{IS_DEFAULT} {NOTHING_CHANGED}")
        return 0
    try:
        risk_store.STORE.save_settings("server", confirm_phrase=DEFAULTS["confirm_phrase"])
    except Exception:
        logger.exception("The confirm phrase could not be set back")
        out(NOT_SAVED)
        return 1
    out("The confirm phrase is the default again.")
    out(APPLIES)
    try:
        risk_store.STORE.audit(
            "phrase_reset",
            words.PHRASE_RESET_AUDIT,
            surface="server",
            actor="server",
        )
        out("History shows it.")
    except Exception:
        logger.exception("The confirm phrase's reset could not be audited")
        out("History could not be written; the phrase was set back all the same.")
    out(tell(told_words(risk_store.utcnow())))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sudarshan.guard.phrase",
        description="Set a forgotten confirm phrase back to the default, on the server.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="say whether it is the default")
    group.add_argument("--reset", action="store_true", help="set it back to the default")
    args = parser.parse_args(argv)
    return reset() if args.reset else status()


if __name__ == "__main__":
    sys.exit(main())
