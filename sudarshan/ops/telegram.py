"""Telegram's ``/server``, in the owner's own chat only (B4, M16.1; P8 5.4 and 7.1).

    /server    what the server watch last saw: what needs the owner, the checks in counts, and the
               outside heartbeat when it was not sent

One command line, so the bot keeps no conversation state, and it places and changes nothing. It
answers only the owner's own private chat (``sudarshan.guard.telegram.owner_of``, the kill
switch's rule): a group, an account not linked, an account linked to another OpenAlgo user and a
family member's chat get one sentence and no figure.

``sudarshan-watch`` writes ``/var/lib/sudarshan-watch/report.json`` every 2 minutes; this reads
that file without waiting and without following a link (``sudarshan/ops/files.py``), as the
System page does: no connection, no lock, a few milliseconds. A report that is missing or cannot
be used is said as not there yet, never guessed; one older than STALE_S says how old it is. The
check names are the notice desk's words (a test holds the two tables together); nothing here is
imported from the alerts service. The bot runs the command where the web app's code runs
(``TelegramBotService._in_app_world``) and replies in plain text.
"""

from __future__ import annotations

import functools
import json
import os
import time
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sudarshan.ops.files import read_regular
from sudarshan.ops.release import TAG
from sudarshan.ops.watch import heartbeat

BRAND = "सुदर्शन / SUDARSHAN"
WATCH_DIR = "/var/lib/sudarshan-watch"
MAX_REPORT = 1 << 18
#: The watch looks every 2 minutes; a report older than this says how old it is.
STALE_S = 600
TIMEOUT_S = 30.0
IST = timezone(timedelta(hours=5, minutes=30))
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
#: What a refused chat is told. The owner's own chat whose stored key is no longer this
#: OpenAlgo's is asked to link again, as /killswitch asks.
ONLY_YOUR_CHAT = "This command works only in the account owner's own chat with the bot."
LINK_AGAIN = (
    "This chat is not linked with your current OpenAlgo API key. Link it again with "
    "/link <api_key> <host_url>, then send /{command} again."
)
STILL_WORKING = "Still working. Send /{command} again in a moment."
BROKE = "That could not be read just now. The System page in the app has the same."
NO_REPORT = (
    "The server watch has not reported yet. Once it is installed it looks every 2 minutes; the "
    "System page shows the same."
)
OLD = (
    "The server watch last reported at {time}, {age} ago, and not since: what follows is as it "
    "was then. If this stays, the watch has stopped; the System page shows the same."
)
#: A check's name in the owner's words: the notice desk's (alerts/notices.py ``_CHECK_WORDS``).
CHECK_WORDS = {
    "service_feed": "price feed",
    "service_alerts": "alerts service",
    "service_portfolio": "portfolio service",
    "service_screener": "screener",
    "service_family": "family view",
    "service_journal": "journal",
    "openalgo": "OpenAlgo",
    "login": "Upstox login",
    "feed": "price feed's connection",
    "instrument_list": "instrument list",
    "disk": "disk space",
    "memory": "memory",
    "certificate": "security certificate",
    "posture_locks": "AI assistant's safety settings",
    "posture_debug": "OpenAlgo's debug setting",
    "posture_modes": "private folders' permissions",
    "posture_ports": "a service listening on the network",
    "posture_reboot": "a restart the system asks for",
    "posture_updates": "security updates waiting",
    "backup": "nightly backup",
    "drill": "restore test of the backups",
    "historify": "Historify's size",
}
NEEDS = (("failing", "not working"), ("warning", "to look at"))


def refusal(chat, user, command: str) -> str:
    """What a chat ``owner_of`` refused is told (the kill switch's rule): the owner's own private
    chat whose key is not this OpenAlgo's now is asked to link again; any other, one sentence."""
    from sudarshan.guard.telegram import _named_owner  # the kill switch's own link check

    if getattr(chat, "type", None) == "private" and user is not None and _named_owner(user):
        return LINK_AGAIN.format(command=command)
    return ONLY_YOUR_CHAT


def watch_report(state_dir: Path | None = None) -> dict[str, Any] | None:
    """The watch's last report, or None when there is none or it cannot be used."""
    folder = state_dir or Path((os.environ.get("SUDARSHAN_WATCH_STATE_DIR") or WATCH_DIR)
                               .split(":")[0])  # fmt: skip
    raw = read_regular(folder / "report.json", MAX_REPORT)
    try:
        found = json.loads(raw) if raw else None
    except ValueError:
        return None
    if not isinstance(found, dict) or not isinstance(found.get("checks"), list):
        return None
    at = found.get("at")
    return found if isinstance(at, int | float) and not isinstance(at, bool) else None


def _clock(at: float, now: float) -> str:
    moment = datetime.fromtimestamp(at, IST)
    text = f"{moment:%H:%M}"
    if moment.date() != datetime.fromtimestamp(now, IST).date():
        text = f"{moment.day} {MONTHS[moment.month - 1]} {text}"
    return text


def _age(seconds: float) -> str:
    minutes = int(max(0, seconds) // 60)
    if minutes < 1:
        return "under a minute"
    if minutes < 120:
        return "1 minute" if minutes == 1 else f"{minutes} minutes"
    hours = minutes // 60
    return f"{hours} hours" if hours < 48 else f"{hours // 24} days"


def server_text(report: Mapping[str, Any] | None, now: float) -> str:
    """The reply: when the watch looked, what needs the owner, the rest in counts. Pure."""
    if report is None:
        return f"{BRAND}\n{NO_REPORT}"
    at = float(report["at"])
    lines = [BRAND]
    if now - at > STALE_S:
        lines.append(OLD.format(time=_clock(at, now), age=_age(now - at)))
    release = report.get("release")
    tail = f", release {release}" if isinstance(release, str) and TAG.fullmatch(release) else ""
    lines.append(f"Server checks at {_clock(at, now)} ({_age(now - at)} ago){tail}.")
    checks = [c for c in report["checks"] if isinstance(c, Mapping)]
    needs = [
        f"{CHECK_WORDS.get(str(check.get('name')), 'a server check')} ({said})"
        for level, said in NEEDS
        for check in checks
        if check.get("level") == level
    ]
    if needs:
        lines.append(
            "Needs you: " + ", ".join(dict.fromkeys(needs)) + ". The System page has each."
        )
    else:
        lines.append("Nothing needs you.")
    counts = {level: sum(1 for c in checks if c.get("level") == level)
              for level in ("fine", "off", "unread")}  # fmt: skip
    parts = [f"Fine: {counts['fine']}"]
    if counts["off"]:
        parts.append(f"switched off: {counts['off']}")
    if counts["unread"]:
        parts.append(f"could not be read just now: {counts['unread']}")
    lines.append(", ".join(parts) + ".")
    beat = report.get("heartbeat")
    state = beat.get("state") if isinstance(beat, Mapping) else None
    if state in heartbeat.WORDS and state != heartbeat.SENT:
        lines.append(heartbeat.WORDS[state])
    return "\n".join(lines)


def answer(chat, user, now: float | None = None) -> tuple[str, str]:
    """Run ``/server`` where the app's code runs; returns the reply and the name logged."""
    from sudarshan.guard.telegram import owner_of

    if owner_of(chat, user) is None:
        return refusal(chat, user, "server"), "server_refused"
    return server_text(watch_report(), time.time() if now is None else now), "server"


async def handle(bot, update, context) -> None:
    """``/server``, from TelegramBotService.cmd_server."""
    from database.telegram_db import log_command
    from utils.logging import get_logger

    chat, user = update.effective_chat, update.effective_user
    logged = "server"
    try:
        text, logged = await bot._in_app_world(
            functools.partial(answer, chat, user), timeout=TIMEOUT_S
        )
    except TimeoutError:
        text = STILL_WORKING.format(command="server")
    except Exception:
        get_logger(__name__).exception("Telegram /server could not finish")
        text = BROKE
    await update.effective_message.reply_text(text)
    log_command(getattr(user, "id", 0), logged, getattr(chat, "id", 0))
