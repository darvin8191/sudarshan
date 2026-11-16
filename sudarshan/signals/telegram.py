"""Telegram's ``/trinetra``, in the owner's own chat only (B4, M16.1; P8 5.4, 3.6).

    /trinetra    TRINETRA's current readings under the freshness sentence, and the notes

One command line, so the bot keeps no conversation state, and it places and changes nothing. It
answers only the owner's own private chat (``sudarshan.guard.telegram.owner_of``, the kill
switch's rule): a group, an account not linked, an account linked to another OpenAlgo user and a
family member's chat get one sentence and no reading. The family bot never has this command.

The readings come from sudarshan-signals' ``state`` op, sealed as OpenAlgo's bridge seals it
(``SIGNALS_API_INFO``, principal ``owner``, 3 s, to a port this account holds), with no Flask
context: the bot's thread has none. The words are the view's own (``contract.WORDS``,
``words.card_words``); a reading is never called Real-time or Live, and an old one is shown only
under the sentence that says how old it is. TRINETRA's labels are shown as written, but a slash
in them is replaced, so no word of TRINETRA's becomes a command the owner taps. Nothing here is
imported from the alerts service. The reply is plain text.
"""

from __future__ import annotations

import functools
import os
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from sudarshan.alerts import loopback
from sudarshan.autologin.config import is_loopback_url
from sudarshan.autologin.handoff import seal
from sudarshan.signals import contract as c
from sudarshan.signals import words

BRAND = "सुदर्शन / SUDARSHAN"
SERVICE_URL = f"http://127.0.0.1:{c.PORT}"
CONNECT_TIMEOUT_S = 1.0
TIMEOUT_S = 30.0
#: What a screen reads for a module (the alerts' desk says the same; a test holds them together).
MODULE_WORDS = {
    "regime": "regime",
    "gap_bias": "gap bias",
    "swing_eod": "end-of-day swing",
    "swing_intraday": "intraday swing",
}
STILL_WORKING = "Still working. Send /trinetra again in a moment."
BROKE = "TRINETRA's readings could not be read just now. The TRINETRA page in the app has them."
_JOINERS = ("‌", "‍")


def _key():
    from sudarshan.vault import SIGNALS_API_INFO, fernet_for, pepper_from

    return fernet_for(pepper_from(os.environ), SIGNALS_API_INFO)


def service_url() -> str:
    """The service's local API, as the bridge finds it: the setting when it is plain http on this
    server, else 5588."""
    port = (os.environ.get("SUDARSHAN_SIGNALS_HTTP_PORT") or "").strip()
    fallback = (
        f"http://127.0.0.1:{port}" if port.isdecimal() and 0 < int(port) < 65536 else SERVICE_URL
    )
    url = (os.environ.get("SUDARSHAN_SIGNALS_API_URL") or fallback).rstrip("/")
    return url if url.startswith("http://") and is_loopback_url(url) else fallback


def _sentence(value: Any) -> str | None:
    if not isinstance(value, str) or not 0 < len(value) <= 300 or not value.endswith("."):
        return None
    return value if all(ch.isprintable() for ch in value) else None


def read_state(
    url: str | None = None, key=None, post: Callable[..., httpx.Response] | None = None
) -> tuple[dict[str, Any] | None, str]:
    """The ``state`` answer, or None and the sentence to say instead."""
    url = url or service_url()
    held = loopback.holder(url)
    if held == loopback.NOBODY:
        return None, c.WORDS["not_answering"]
    if held == loopback.OTHER:
        return None, c.WORDS["held"]
    if post is None:
        from utils.httpx_client import get_httpx_client

        post = get_httpx_client().post
    blob = seal(key or _key(), "state", principal=c.PRINCIPALS["api"], args={})
    try:
        answer = post(
            f"{url}{c.OPS_PREFIX}state",
            content=blob,
            headers={"Content-Type": "application/octet-stream", "Accept": "application/json"},
            timeout=httpx.Timeout(c.TIMEOUTS_S["read"], connect=CONNECT_TIMEOUT_S),
            follow_redirects=False,
        )
        body = answer.json()
    except (httpx.HTTPError, ValueError):
        return None, c.WORDS["not_answering"]
    if not isinstance(body, dict) or body.get("status") != "success":
        code = body.get("code") if isinstance(body, dict) else None
        return None, c.WORDS["sealed" if code in ("forbidden", "principal") else "failed"]
    data = body.get("data")
    if not isinstance(data, dict):
        return None, c.WORDS["failed"]
    if data.get("enabled") is False:
        return None, _sentence(data.get("message")) or c.WORDS["off"]
    return data, ""


def _plain(text: Any, limit: int) -> str:
    raw = "".join(ch if ch.isprintable() or ch in _JOINERS else " " for ch in str(text or ""))
    line = " ".join(raw.replace("/", "∕").split())
    return line if len(line) <= limit else line[: limit - 3].rstrip() + "..."


def _module_words(module: str) -> str:
    return MODULE_WORDS.get(module) or module.replace("_", " ")


def reading_line(found: Mapping[str, Any], now_ms: int) -> str:
    """'NIFTY regime: FII distribution (bearish), confidence 0.71, 3 sessions', then what the card
    says of it (incomplete data, no data, lapsed)."""
    bias = c.BIAS_WORDS.get(found.get("bias"), "neutral")
    line = (
        f"{found.get('instrument')} {_module_words(str(found.get('module') or ''))}: "
        f"{_plain(found.get('label'), c.DOC_LIMITS['label'])} ({bias})"
    )
    confidence = found.get("confidence")
    if isinstance(confidence, int | float) and not isinstance(confidence, bool):
        line += f", confidence {confidence:.2f}"
    sessions = found.get("sessions")
    if isinstance(sessions, int) and not isinstance(sessions, bool) and sessions > 0:
        line += f", {sessions} session" + ("" if sessions == 1 else "s")
    said = words.card_words(found, bool(found.get("lapsed")), now_ms)
    return " ".join([line + ".", *said])


def _order(found: Mapping[str, Any]) -> tuple:
    module = str(found.get("module") or "")
    known = c.KNOWN_MODULES.index(module) if module in c.KNOWN_MODULES else len(c.KNOWN_MODULES)
    place = (
        c.INSTRUMENTS.index(found["instrument"]) if found.get("instrument") in c.INSTRUMENTS else 9
    )
    return bool(found.get("lapsed")), place, known, module


def trinetra_text(answer: Mapping[str, Any]) -> str:
    """The reply for a ``state`` answer: the freshness sentence, the readings (live first, the
    lapsed below them), the notes. Pure."""
    now_ms = int(answer.get("now_ms") or time.time() * 1000)
    freshness = answer.get("freshness") if isinstance(answer.get("freshness"), Mapping) else {}
    lines = [BRAND, str(freshness.get("words") or c.WORDS["never"])]
    found = [r for r in answer.get("readings") or [] if isinstance(r, Mapping)]
    lines += [reading_line(r, now_ms) for r in sorted(found, key=_order)]
    lines += [str(n["words"]) for n in answer.get("notes") or [] if isinstance(n, Mapping)
              and n.get("words")]  # fmt: skip
    return "\n".join(lines)


def answer(
    chat, user, read: Callable[[], tuple[dict | None, str]] | None = None
) -> tuple[str, str]:
    """Run ``/trinetra`` where the app's code runs; returns the reply and the name logged."""
    from sudarshan.guard.telegram import owner_of
    from sudarshan.ops.telegram import refusal

    if owner_of(chat, user) is None:
        return refusal(chat, user, "trinetra"), "trinetra_refused"
    state, said = (read or read_state)()
    if state is None:
        return f"{BRAND}\n{said}", "trinetra"
    return trinetra_text(state), "trinetra"


async def handle(bot, update, context) -> None:
    """``/trinetra``, from TelegramBotService.cmd_trinetra."""
    from database.telegram_db import log_command
    from utils.logging import get_logger

    chat, user = update.effective_chat, update.effective_user
    logged = "trinetra"
    try:
        text, logged = await bot._in_app_world(
            functools.partial(answer, chat, user), timeout=TIMEOUT_S
        )
    except TimeoutError:
        text = STILL_WORKING
    except Exception:
        get_logger(__name__).exception("Telegram /trinetra could not finish")
        text = BROKE
    await update.effective_message.reply_text(text)
    log_command(getattr(user, "id", 0), logged, getattr(chat, "id", 0))
