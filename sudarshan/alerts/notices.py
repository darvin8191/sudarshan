"""The server's own notices on Telegram: what the deploy tool recorded about a release (M18.x).

    sudarshan-deploy (root, a timer)  -> /var/lib/sudarshan/events.jsonl    the facts, JSON lines
      -> NoticeDesk (this file, inside sudarshan-alerts): looks at the file, picks what is new
      -> one message in the owner's words, claimed in alerts.db before it goes out
      -> OpenAlgoTelegram.send: the path the order alerts take to the owner's chat

The deploy tool never holds a Telegram or OpenAlgo secret (it runs as root); it only records what
happened (sudarshan/ops/events.py has the format). This service owns the words, the pacing and
the once-only rule. Nothing here depends on the alert window, and the market matters only for the
quiet hours below: a release is installed in the evening or at the weekend, so the owner's key is
read when a notice is due and none is loaded.

  Once. A notice is claimed by its event id in alerts.db before the message goes out, and marked
        sent after it. A restart, a second look at the file or a rewritten file never repeats it. A
        message that could not go out gives its claim back and is tried again; a claim left by a
        process that stopped mid-send is taken over after CLAIM_S.
  One message. The facts of one run are told together (an install that left a service file
        refused is one message): nothing goes out until SETTLE_S have passed without a new notice
        appearing, so the rest of a run's facts are in. The wait is measured on this service's own
        clock from when it first saw each notice, never from the time the notice carries, so a
        clock that was wrong when it was written cannot hold it back. Everything waiting at a look
        goes out in the same message, so a service that was down for hours tells the owner once,
        not once per notice.
  Not after downtime. A notice older than MAX_AGE_S is history: it is marked and never sent. A
        notice that cannot go out is retried with a growing pause until it ages out.
  Dropped when sending cannot work (OpenAlgo's bot stopped, the owner's chat not linked): the same
        rule as the order alerts, they are never held back for later.
  Paced. A notice never jumps the queue for Telegram: at least outbox.MIN_GAP_S between two
        messages and at most NOTICES_PER_MINUTE a minute (NoticePace). The slot is taken just
        before the send, after the claim is known to be ours.
  Quiet in market hours. While the market trades (09:15 to the F&O close: 15:40, 15:45 from
        7 Dec 2026; or a listed day's own window) only a run that holds a failure (FAILURES, a
        failing check of the watch or its recovery) goes out; the rest wait for the close, still
        inside MAX_AGE_S. The owner's phone is for fills
        and rejections then.

Slots. The service builds the desk with ``pace=LanePace(pacer)`` (service.py): a notice takes a
        slot of the process's one outbox.Pacer, in its ``notices`` lane (at most NOTICES_PER_MINUTE
        a minute), after the order alerts, so it can make a fill wait one gap and no more (R8,
        Y18). NoticePace is the same rule for a desk built without a Pacer.

SUDARSHAN_ORDER_ALERTS=off stops this service and so the notices with it.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol

from sudarshan.autologin.messages import BRAND
from sudarshan.autologin.schedule import IST
from sudarshan.ops.events import Event, read_events
from sudarshan.ops.release import TAG

from . import outbox
from .outbox import DROP, RETRY, SENT, UNCONFIRMED
from .store import Store

log = logging.getLogger("sudarshan.alerts")

# Timings are module constants so the tests can shrink them.
POLL_S = 20.0  # how often the files are looked at
SETTLE_S = 45.0  # no new notice for this long before they are told: the rest of a run is in
MAX_AGE_S = 12 * 3600.0  # older than this a notice is history
CLAIM_S = 300.0  # a claim older than this was left by a process that stopped
#: Pauses after a look that could not send, growing to the last.
RETRY_S = (30.0, 60.0, 120.0, 300.0, 600.0)
UNCONFIRMED_TRIES = 3  # Telegram did not confirm the message (OpenAlgo answered "queued")
MAX_EVENTS = 20  # facts in one message
NOTICES_PER_MINUTE = 1  # messages; the gap between two is outbox.MIN_GAP_S
MINUTE_S = 60.0
GIVE_WAY_S = 1.0  # LanePace: look again this soon when order alerts are waiting
#: The kinds that are told at once, in market hours too. Everything else waits for the close.
FAILURES = frozenset({"rolled_back", "unit_refused", "backup_failed"})
#: The family service's notices (src family, family_check_failed from deploy) that are told at
#: once too: a sign-in or a device the owner may need to act on, a removal, a refused bot token,
#: a failed server check. The rest (invite_used, push_failing, store_newer) wait for the close.
FAMILY_AT_ONCE = frozenset(
    {"lockout", "new_device", "member_removed", "bot_token_rejected", "family_check_failed"}
)
#: The watch's notices (src watch, M19.5): a failure is told at once, and so are the recovery from
#: one and its news (a new AI connection, a Telegram account linked or unlinked); a warning and
#: the return from a warning wait for the close (``_at_once``).
AT_ONCE = FAILURES | FAMILY_AT_ONCE | {"watch_failing", "watch_new"}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
UNIT = re.compile(r"sudarshan-[a-z0-9-]+\.(?:service|timer)")
#: The order the facts of one run are told in.
KIND_ORDER = (
    "installed",
    "rolled_back",
    "unit_refused",
    "backup_failed",
    "webhooks_open",
    "waiting",
    "lockout",
    "new_device",
    "member_removed",
    "bot_token_rejected",
    "family_check_failed",
    "invite_used",
    "push_failing",
    "store_newer",
    "watch_failing",
    "watch_warning",
    "watch_fine",
    "watch_new",
    "drill_failed",
    "drill_passed",
    "watch_summary",
)

#: Why an install was undone, in the owner's words (the cause only; ``rolled_back`` adds the rest).
ROLLBACK_CAUSES = {
    "health": "the app did not come up healthy",
    "migrations": "a database update did not finish",
    "settings": "a settings update did not finish",
}
ROLLBACK_OTHER = "the update stopped unexpectedly"

#: The nightly backup (ops/backup.py) names the cause of a failed run in ``why``: the words for
#: each are in ``_backup_failed``. A figure or a file name in the event that is not plain is left
#: out of the sentence rather than quoted.
BACKUP_AGAIN = "It tries again at its next scheduled time."
_AMOUNT = re.compile(r"[0-9]{1,6}(?:\.[0-9]{1,2})?")
_FILE_LIST = re.compile(
    r"[A-Za-z0-9._/-]{1,60}(?:, [A-Za-z0-9._/-]{1,60}){0,9}"
)  # a folder: etc/x/
_HOURS = re.compile(r"[0-9]{1,5}")
#: Why a run left the night undone (backup.py: STEPPED_ASIDE), in the owner's words.
_BECAUSE = {
    "deploy": "A server update was still running when it tried.",
    "busy": "Another backup was still going when it tried.",
    "market": "It tried during market hours.",
    "soon": "It tried just before the market opens.",
}


#: Where the README's offsite set-up makes a new upload link (steps 4, 5 and 9) and where it saves
#: the settings (step 9). sudarshan/ops/tests/test_ops_docs.py holds the steps to these numbers.
NEW_LINK_STEPS = "(offsite set-up steps 4, 5 and 9 in the README)"
SET_UP_AGAIN = "Run the offsite set-up again (step 9 in the README)"
#: Why an offsite upload failed (ops/offsite.py REASONS), in the owner's words, with the next step
#: where the owner has one (review L4). Each ends without a full stop; the sentence adds it.
_OFFSITE_REASON = {
    "openssl": "the server has no encryption tool installed",
    "encrypt": f"the backup could not be encrypted. {SET_UP_AGAIN}",
    "space": "the server's disk is too full to prepare the copy",
    "network": "the server could not reach Oracle's storage",
    "refused": (
        "Oracle's storage refused the file. If the upload link is no longer listed in the bucket, "
        f"make a new one {NEW_LINK_STEPS}"
    ),
    "busy": "Oracle's storage was not accepting files",
    "expired": f"the upload link has reached its expiry date. Make a new one {NEW_LINK_STEPS}",
    "config": f"the offsite settings on the server cannot be used. {SET_UP_AGAIN}",
}
_ISO_DATE = re.compile(r"(20[0-9]{2})-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])")


def _date(value: str) -> str:
    """'2026-11-26' as '26 Nov 2026'; '' when it is not a date."""
    found = _ISO_DATE.fullmatch(value)
    return f"{int(found[3])} {MONTHS[int(found[2]) - 1]} {found[1]}" if found else ""


def _release(value: str) -> str | None:
    return value if TAG.fullmatch(value) else None


def _when(at: float, now: datetime) -> str:
    """'18:52 IST', with the date when it is not today's ('24 Oct 18:52 IST')."""
    moment = datetime.fromtimestamp(at, IST)
    clock = f"{moment:%H:%M} IST"
    if moment.date() == now.astimezone(IST).date():
        return clock
    return f"{moment.day} {MONTHS[moment.month - 1]} {clock}"


def _installed(event: Event, now: datetime) -> list[str]:
    tag = _release(event.fact("tag")) or "The new release"
    line = f"{tag} installed at {_when(event.at, now)}."
    if event.fact("backup") == "yes":
        line += " A backup was taken."
    return [line]


def _rolled_back(event: Event, now: datetime) -> list[str]:
    tag = _release(event.fact("tag")) or "the new release"
    cause = ROLLBACK_CAUSES.get(event.fact("why"), ROLLBACK_OTHER)
    start = f"Could not install {tag} at {_when(event.at, now)}: {cause}"
    if event.fact("up") == "no":
        # Told once OpenAlgo is back, so it says what was true at that time, not what is true now.
        return [
            f"{start}, and the earlier version did not start either, so SUDARSHAN was not "
            f"running at {_when(event.at, now)}. If it is still not running, the server needs "
            "attention now."
        ]
    was = _release(event.fact("was"))
    back = f"{was} is back" if was else "the earlier version is back"
    return [f"{start}, so {back}. Nothing for you to do; the release will be looked at."]


def _unit_words(unit: str) -> str:
    """'sudarshan-market-alerts.service' -> 'the market alerts service' (a timer: 'schedule')."""
    name, _, kind = unit.removeprefix("sudarshan-").rpartition(".")
    return f"the {name.replace('-', ' ')} {'schedule' if kind == 'timer' else 'service'}"


def _refused(events: list[Event]) -> list[str]:
    units = sorted({e.fact("unit") for e in events if UNIT.fullmatch(e.fact("unit"))})
    tag = next((t for e in events if (t := _release(e.fact("tag")))), "the new release")
    if not units:
        return []
    words = [_unit_words(unit) for unit in units]
    if len(units) == 1:
        what, they = f"A background service in {tag} was not installed: {words[0]}.", "It"
    else:
        names = f"{', '.join(words[:-1])} and {words[-1]}"
        what, they = (
            f"{len(units)} background services in {tag} were not installed: {names}.",
            "They",
        )
    return [
        f"{what} {they} did not pass the server's safety check. "
        "The rest of the release is in place. Nothing for you to do; it will be looked at."
    ]


def _backup_failed(event: Event) -> list[str]:
    """One line for one failed run of the nightly backup."""
    why = event.fact("why")
    free, need, cap = (
        value if _AMOUNT.fullmatch(value) else ""
        for value in (event.fact("free_gb"), event.fact("need_gb"), event.fact("cap_gb"))
    )
    names = event.fact("names")
    names = names if _FILE_LIST.fullmatch(names) else ""
    start = "The nightly backup was not made:"
    if why == "space":
        detail = f" ({free} GB free, about {need} GB needed)" if free and need else ""
        text = (
            f"{start} the server's disk does not have enough free space{detail}. "
            "Free some space on it."
        )
    elif why == "cap":
        room = f"the {cap} GB" if cap else "the space"
        text = (
            f"{start} the backups already take {room} they are allowed. "
            "Raise that limit or remove old backups."
        )
    elif why == "write":
        text = (
            "The nightly backup could not be written to the server's disk. "
            "Earlier backups are untouched."
        )
    elif why == "source":
        return [
            f"The nightly backup is incomplete: {names or 'some files'} could not be saved. "
            "Everything else was saved. It tries again at its next scheduled time; "
            "if it is incomplete again, have the server looked at."
        ]
    elif why == "integrity":
        return [
            f"The nightly backup was made, but {names or 'a database'} failed its safety check, "
            "so that copy may be damaged. Earlier backups are kept. Have the server looked at soon."
        ]
    elif why == "offsite":
        reason = _OFFSITE_REASON.get(event.fact("reason"), "the offsite copy did not work")
        year = event.fact("fy")
        if _FY_LABEL.fullmatch(year):
            # A financial year's archive (kept 8 years) has no offsite copy yet (review L4).
            lead = (
                f"Tonight's backup is the {year} year's archive, and its offsite copy did not "
                f"upload: {reason}."
                if event.fact("fy_tonight") == "yes"
                else f"The {year} year's archive still has no offsite copy: {reason}."
            )
            return [
                f"{lead} It is sent again at the next backup, and the System page says so "
                "until it has gone. The copy on the server is kept."
            ]
        return [f"Tonight's offsite backup did not upload: {reason}. The local backup is fine."]
    elif why == "offsite_soon":
        days, when = event.fact("days"), _date(event.fact("date"))
        if not (_HOURS.fullmatch(days) and when):
            return []
        left = {"0": "today", "1": "tomorrow"}.get(days, f"in {days} days")
        return [
            f"The upload link for the offsite backup expires {left}, on {when}. Make a new one "
            f"before then {NEW_LINK_STEPS}, or the offsite copies will stop."
        ]
    elif why == "settings":
        return [
            f"{start} its settings on the server cannot be used. Have the backup settings fixed."
        ]
    elif why == "lock":
        text = (
            f"{start} it could not check whether a server update is running, so it did not run. "
            "Have the server looked at."
        )
    elif why == "overdue":
        hours = event.fact("hours")
        old = f"{hours} hours old" if _HOURS.fullmatch(hours) else "more than a day old"
        because = _BECAUSE.get(event.fact("because"), "")
        return [
            f"The nightly backup is overdue: the last complete backup is {old}. "
            f"{because + ' ' if because else ''}{BACKUP_AGAIN[:-1]}; if this message comes "
            "again tomorrow, have the server looked at."
        ]
    else:
        text = "The nightly backup could not be completed. Earlier backups are untouched."
    return [f"{text} {BACKUP_AGAIN}"]


def _waiting(event: Event, now: datetime) -> list[str]:
    tag = _release(event.fact("tag")) or "A new release"
    return [
        f"{tag} is ready. It will be installed when the market is closed. Nothing for you to do."
    ]


def _webhooks_open(event: Event, now: datetime) -> list[str]:
    """The nginx lines that fence the alert links are not on the server (M12.2)."""
    return [
        "The alert links on this server can still be reached from any address, not only from "
        "TradingView. Nothing stopped working. To close them, install the nginx lines once: the "
        'steps are in sudarshan/README.md under "The fence around the alert links".'
    ]


#: Facts of the family kinds come from another account's file, so a value goes into a sentence
#: only when it matches one of these narrow patterns; anything else is left out of the sentence.
#: A device label has no slash (Telegram makes "/word" a command the owner can tap) and is left out
#: when it reads like a web address ("name.tld"), which Telegram makes a link (review L11).
_DEVICE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .+()\u00b7-]{0,39}")
_LINKISH = re.compile(r"[A-Za-z0-9-]+\.[A-Za-z]{2,}")
_ATTEMPTS = re.compile(r"[0-9]{1,2}")
_EPOCH = re.compile(r"[0-9]{9,11}")
_HANDLE = re.compile(r"[a-z0-9][a-z0-9._-]{0,31}")


def _handle(event: Event) -> str:
    """The member's handle when the owner turned naming on (``name_members`` = 1), else ''.
    A display name is never used, and a handle that is not plain is not used either."""
    handle = event.fact("handle")
    if event.fact("name_members") == "1" and _HANDLE.fullmatch(handle):
        return handle
    return ""


def _device(event: Event) -> str:
    device = event.fact("device")
    return device if _DEVICE.fullmatch(device) and not _LINKISH.search(device) else ""


def _lockout(event: Event, now: datetime) -> list[str]:
    attempts = event.fact("attempts")
    tries = (
        f"{attempts} wrong attempts" if _ATTEMPTS.fullmatch(attempts) else "several wrong attempts"
    )
    device = _device(event)
    until = event.fact("until")
    who, handle = "A family sign-in was", _handle(event)
    if handle:
        who = f"The sign-in of family member {handle} was"
    line = f"{who} locked after {tries}" + (f" ({device})" if device else "") + "."
    if _EPOCH.fullmatch(until):
        line += f" It opens again at {_when(float(until), now)}."
    return [line]


def _new_device(event: Event, now: datetime) -> list[str]:
    handle = _handle(event)
    who = f"Family member {handle}" if handle else "A family member"
    where = f": {_device(event)}, " if _device(event) else " at "
    return [
        f"{who} signed in on a new phone{where}{_when(event.at, now)}. "
        "Not you or them? Open Family and remove the device."
    ]


def _invite_used(event: Event, now: datetime) -> list[str]:
    handle = _handle(event)
    who = handle if handle else "the member"
    return [f"A family invite was used: {who} is signed in, {_when(event.at, now)}."]


def _member_removed(event: Event, now: datetime) -> list[str]:
    handle = _handle(event)
    who = f"Family member {handle} was" if handle else "A family member was"
    return [f"{who} removed. Their phones are signed out and their Telegram unlinked."]


def _push_failing(event: Event, now: datetime) -> list[str]:
    handle = _handle(event)
    whose = f"{handle}'s phone" if handle else "a family member's phone"
    return [
        f"Notifications to {whose} keep failing. "
        "They can turn notifications off and on in Settings."
    ]


def _bot_token_rejected(event: Event, now: datetime) -> list[str]:
    return [
        "Telegram refused the family bot's token. Make a new token in BotFather "
        "and give it to the server with the laptop .bat."
    ]


def _store_newer(event: Event, now: datetime) -> list[str]:
    return [
        "The family data was written by a newer release. "
        "Install that release again; nothing was changed."
    ]


def _family_check_failed(event: Event, now: datetime) -> list[str]:
    return [
        "The family view stays off: the server check found something the family account can "
        "read but must not, or a setting it cannot read. "
        "Run the family set-up again from the laptop."
    ]


#: The watch (sudarshan/ops/watch, M19.5) tells a check's change of level. What each ring service
#: is to the owner, and what stops while it does not answer.
_SERVICES = {
    "feed": ("price feed", "Prices, market alerts and the screener are stopped."),
    "alerts": (
        "alerts service",
        "Order alerts, market alerts and server notices were not sent while it was silent.",
    ),
    "portfolio": ("portfolio service", "The Portfolio page shows no figures."),
    "screener": ("screener", "Screens are not running and their alerts are stopped."),
    "family": ("family view", "Family members cannot open the app."),
    "journal": ("journal", "The Journal and Reports pages are not updating."),
}
#: Where the owner looks (M20.8): the System page shows every check the watch makes.
LOOK = "open the System page and have the server looked at"
RESTARTS = f"The server restarts it by itself; if this message comes again, {LOOK}."
_MINUTES = re.compile(r"[0-9]{1,5}")
_GB = re.compile(r"[0-9]{1,5}\.[0-9]")
_DRILL_NAME = re.compile(r"[A-Za-z0-9._-]{1,60}")


def _began(event: Event, now: datetime) -> str:
    """When the trouble began: the event's time less the minutes the check had counted."""
    minutes = event.fact("minutes")
    back = int(minutes) * 60 if _MINUTES.fullmatch(minutes) else 0
    return _when(event.at - back, now)


def _watch_failing(event: Event, now: datetime) -> list[str]:
    check = event.fact("check")
    if check.startswith("service_") and check[8:] in _SERVICES:
        name, stopped = _SERVICES[check[8:]]
        return [
            f"SUDARSHAN's {name} stopped answering at {_began(event, now)}. {stopped} {RESTARTS}"
        ]
    if check == "login":
        if event.fact("when") == "before_open":
            return [
                "There is no Upstox login for today. Orders, charts and order alerts will not "
                "work until you connect Upstox in the app."
            ]
        return [
            f"Order updates from Upstox have not been arriving since {_began(event, now)}. A "
            "fill or a rejection may not show at once. It reconnects by itself; if the app asks "
            "you to connect Upstox, do that."
        ]
    if check == "feed":
        return [
            f"The price feed has not been connected to Upstox since {_began(event, now)}. "
            "Prices on your screens are not Real-time and market alerts are stopped. It "
            f"reconnects by itself; if this message comes again, {LOOK}."
        ]
    if check == "instrument_list":
        return [
            "Today's instrument list was not refreshed. New weekly contracts may be missing and "
            "expired ones may still show. Open Master Contract and press Download."
        ]
    if check == "disk":
        if event.fact("cause") == "files":
            return [
                "The server's disk has almost no room left for new files. Backups and the "
                "databases can stop working. Have old files cleared now."
            ]
        free = event.fact("free_gb")
        left = f": {free} GB free" if _GB.fullmatch(free) else ""
        return [
            f"The server's disk is almost full{left}. Backups and the databases can stop "
            "working. Free space on it now."
        ]
    if check == "certificate":
        hours = event.fact("hours")
        if hours == "0":
            return [
                "The server's security certificate has run out and has not renewed. Phones "
                "refuse the app until it is renewed; have the server looked at now (the "
                "System page shows it)."
            ]
        left = f"in {hours} hours" if _MINUTES.fullmatch(hours) else "within two days"
        return [
            f"The server's security certificate runs out {left} and has not renewed. Phones "
            "will refuse the app after that."
        ]
    return []


def _watch_warning(event: Event, now: datetime) -> list[str]:
    check = event.fact("check")
    if check == "disk":
        if event.fact("cause") == "files":
            return [
                "The server's disk is running short of room for new files. Nothing has stopped; "
                "have old files cleared."
            ]
        free = event.fact("free_gb")
        left = f": {free} GB free" if _GB.fullmatch(free) else ""
        return [
            f"The server's disk is getting full{left}. Nothing has stopped; have old files "
            "cleared before it runs out."
        ]
    if check == "memory":
        return [
            "The server has been short of memory for several minutes. Nothing has stopped; if "
            f"the app feels slow, {LOOK}."
        ]
    if check == "posture_locks":
        if event.fact("cause") == "approval":
            return [
                "The AI assistant is switched on, but a new AI client would be approved without "
                "asking you. Set MCP_OAUTH_REQUIRE_APPROVAL=True in OpenAlgo's settings and "
                "restart OpenAlgo."
            ]
        return [
            "The AI assistant's safety settings are not as SUDARSHAN ships them, so the "
            "assistant stays switched off. Set OPENALGO_MCP_READ_ONLY=1 and "
            "MCP_OAUTH_WRITE_SCOPE_ENABLED=False in OpenAlgo's settings and restart OpenAlgo."
        ]
    if check == "posture_debug":
        return [
            "OpenAlgo is running with its debug setting on, which is not safe on a server "
            "that is on the internet. Set FLASK_DEBUG=False in OpenAlgo's settings and restart "
            "OpenAlgo."
        ]
    if check == "posture_modes":
        place = {
            "settings": "SUDARSHAN's settings folder",
            "vault": "The folder that holds your sealed Upstox sign-in details",
            "env": "OpenAlgo's settings file",
        }.get(event.fact("what"), "One of SUDARSHAN's private folders")
        return [
            f"{place} can be read by other accounts on the server. Nothing has stopped; have "
            "its permissions set right (the System page names it)."
        ]
    if check == "posture_ports":
        return [
            "One of SUDARSHAN's services is listening on the network, not only inside the "
            f"server. Nothing has stopped; {LOOK}."
        ]
    if check == "drill":
        days = event.fact("days")
        age = f"{days} days" if _MINUTES.fullmatch(days) else "over 100 days"
        return [
            f"The last restore test of the backups was {age} ago. It runs by itself on the "
            "first Sunday of each month; the System page shows why it has not, and the README "
            "has the line to run it by hand."
        ]
    if check == "historify":
        size = event.fact("gb")
        grown = f" to {size} GB" if _GB.fullmatch(size) else ""
        return [
            f"Historify's downloaded history has grown{grown}. Nothing has stopped; delete "
            "what you no longer need on the Historify page."
        ]
    return []


def _watch_new(event: Event, now: datetime) -> list[str]:
    """Something the watch counted one more (or one fewer) of since its last look."""
    check, what, at = event.fact("check"), event.fact("what"), _when(event.at, now)
    if check == "assistant" and what == "connection":
        return [
            f"A new AI connection to SUDARSHAN was approved shortly before {at}. If this was "
            "not you, press Disconnect the assistant on the System page."
        ]
    if check == "telegram_links" and what == "linked":
        return [
            f"A Telegram account was linked to your bot shortly before {at}. Linking needs "
            "your API key. If this was not you, make a new API key on the API Key page and "
            "link your own Telegram again."
        ]
    if check == "telegram_links" and what == "unlinked":
        return [f"A Telegram account was unlinked from your bot shortly before {at}."]
    return []


def _watch_fine(event: Event, now: datetime) -> list[str]:
    check, at = event.fact("check"), _when(event.at, now)
    if check.startswith("service_") and check[8:] in _SERVICES:
        return [f"SUDARSHAN's {_SERVICES[check[8:]][0]} is answering again since {at}."]
    return {
        "login": [f"Upstox is connected again since {at}: order updates are arriving."],
        "feed": [f"The price feed is connected to Upstox again since {at}."],
        "instrument_list": ["Today's instrument list is now refreshed."],
        "disk": ["The server's disk has enough free space again."],
        "memory": ["The server's memory is back to normal."],
        "certificate": ["The server's security certificate was renewed."],
        "posture_locks": ["The AI assistant's safety settings are as they should be again."],
        "posture_debug": ["OpenAlgo's debug setting is off again."],
        "posture_modes": ["SUDARSHAN's private folders are closed to other accounts again."],
        "posture_ports": ["SUDARSHAN's services listen only inside the server again."],
        "historify": ["Historify's downloaded history is back under 3 GB."],
    }.get(check, [])


#: A check's name in the owner's words, for the weekly summary's "needs you" line.
_CHECK_WORDS = {
    **{f"service_{name}": words[0] for name, words in _SERVICES.items()},
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
    "backup": "nightly backup",  # late, or a year's archive with no offsite copy: the lines say
    "drill": "restore test of the backups",
    "historify": "Historify's size",
}
_SIZE_WORDS = (
    ("db", "OpenAlgo's databases"),
    ("log", "its logs"),
    ("hist", "Historify"),
    ("ring", "SUDARSHAN's own stores"),
    ("bak", "the backups"),
)
_FY_LABEL = re.compile(r"\d{4}-\d{2}")
_SIZES = re.compile(r"[a-z]{2,6}=[0-9]{1,8}(?:,[a-z]{2,6}=[0-9]{1,8}){0,9}")
_NAMES = re.compile(r"[a-z_]{2,40}(?:,[a-z_]{2,40}){0,20}")


def _size(megabytes: int) -> str:
    return f"{megabytes / 1024:.1f} GB" if megabytes >= 1024 else f"{megabytes} MB"


def _watch_summary(event: Event, now: datetime) -> list[str]:
    """The Sunday summary: what needs the owner first, then the week in figures."""
    number = lambda name: event.fact(name) if _MINUTES.fullmatch(event.fact(name)) else ""  # noqa: E731
    needs = []
    for level, word in (("failing", "not working"), ("warning", "to look at")):
        names = event.fact(level)
        if _NAMES.fullmatch(names):
            needs += [
                f"{_CHECK_WORDS.get(name, 'a server check')} ({word})" for name in names.split(",")
            ]
    lines = ["Weekly server summary."]
    if needs:
        lines.append(
            "Needs you: " + ", ".join(dict.fromkeys(needs)) + ". The System page has each."
        )
    else:
        lines.append("Nothing needs you.")
    fine, unread = number("fine"), number("unread")
    if fine:
        could_not = f", {unread} could not be read" if unread not in ("", "0") else ""
        lines.append(f"Checks fine: {fine}{could_not}.")
    free = event.fact("disk_free_gb")
    parts = []
    if _SIZES.fullmatch(event.fact("sizes")):
        measured = dict(pair.split("=") for pair in event.fact("sizes").split(","))
        parts = [
            f"{words} {_size(int(measured[key]))}" for key, words in _SIZE_WORDS if key in measured
        ]
    if _GB.fullmatch(free) or parts:
        lead = f"Disk: {free} GB free. " if _GB.fullmatch(free) else "Disk: "
        lines.append((lead + (", ".join(parts) + "." if parts else "")).strip())
    age, cause, days = number("backup_age_h"), event.fact("drill_cause"), number("drill_days")
    proof = [f"Newest backup: {age} hours old." if age else ""]
    owed = event.fact("backup_fy_owed")
    if _FY_LABEL.fullmatch(owed):
        proof.append(
            f"The {owed} year's archive has no offsite copy yet; it is sent again at the next "
            "backup."
        )
    proof.append(
        {
            "passed": f"Restore test: passed {days} days ago." if days else "Restore test: passed.",
            "failed": "Restore test: FAILED.",
            "late": f"Restore test: last done {days} days ago."
            if days
            else "Restore test: overdue.",
            "never": "Restore test: never done yet.",
        }.get(cause, "")
    )
    if any(proof):
        lines.append(" ".join(part for part in proof if part))
    waiting, security = number("updates_all"), number("updates_security")
    if waiting:
        asks = " The system asks for a restart." if event.fact("reboot") == "yes" else ""
        lines.append(
            f"System updates waiting: {waiting}, {security or '0'} of them security. They are "
            f"yours to apply by hand on a non-trading day.{asks}"
        )
    cpu, mem = number("cpu95"), number("mem95")
    if cpu and mem:
        lines.append(
            f"This week the server's processor stayed under {cpu}% and its memory under {mem}% "
            "for 95% of the time."
        )
    return lines


def _drill_passed(event: Event, now: datetime) -> list[str]:
    """What the drill tried, and no more (review L3): "the vault opens" only when a sealed
    record was opened; an empty vault is said as empty."""
    count = event.fact("databases")
    opened = f"its {count} databases open" if _MINUTES.fullmatch(count) else "its databases open"
    lead = f"The restore test of the backups passed: the newest backup reads back whole, {opened}"
    vault = event.fact("vault")
    if vault == "opened":
        return [f"{lead}, and the vault opens with the backup's own keys."]
    if vault == "empty":
        return [
            f"{lead}. The vault holds nothing sealed yet, so there was no sealed record to open "
            "with the backup's keys; the next test after you save your Upstox sign-in details "
            "on the Auto login page checks that too."
        ]
    return [f"{lead}."]


def _drill_failed(event: Event, now: datetime) -> list[str]:
    from sudarshan.ops.drill import WORDS  # the same sentences the System page shows

    cause = WORDS.get(event.fact("item"), WORDS["other"])
    name = event.fact("name")
    about = f" ({name})" if _DRILL_NAME.fullmatch(name) else ""
    return [
        f"The restore test of the backups FAILED. {cause}{about} Until this is put right the "
        "backups may not restore: open the System page and have the server looked at."
    ]


def _at_once(event: Event) -> bool:
    """Whether a notice goes out in market hours too."""
    if event.kind == "watch_fine":
        return event.fact("was") == "failing"
    return event.kind in AT_ONCE


#: The words of the kinds that are made one event at a time. ``report`` has none on purpose: a
#: report is not a notice, it belongs to the report desk.
_FAMILY_WORDS = {
    "lockout": _lockout,
    "new_device": _new_device,
    "invite_used": _invite_used,
    "member_removed": _member_removed,
    "push_failing": _push_failing,
    "bot_token_rejected": _bot_token_rejected,
    "store_newer": _store_newer,
    "family_check_failed": _family_check_failed,
    "watch_failing": _watch_failing,
    "watch_warning": _watch_warning,
    "watch_fine": _watch_fine,
    "watch_new": _watch_new,
    "drill_passed": _drill_passed,
    "drill_failed": _drill_failed,
    "watch_summary": _watch_summary,
}


def run_lines(events: list[Event], now: datetime) -> list[str]:
    """The lines for the facts of one run, in KIND_ORDER."""
    lines: list[str] = []
    for kind in KIND_ORDER:
        of_kind = [e for e in events if e.kind == kind]
        if not of_kind:
            continue
        if kind == "unit_refused":
            lines += _refused(of_kind)
            continue
        if kind == "backup_failed":
            lines += [line for event in of_kind for line in _backup_failed(event)]
            continue
        if kind in _FAMILY_WORDS:
            lines += [line for event in of_kind for line in _FAMILY_WORDS[kind](event, now)]
            continue
        make = {
            "installed": _installed,
            "rolled_back": _rolled_back,
            "waiting": _waiting,
            "webhooks_open": _webhooks_open,
        }[kind]
        for event in of_kind:
            lines += make(event, now)
    return lines


def batch_lines(events: Sequence[Event], now: datetime) -> list[str]:
    """Each run's lines, in the order the runs happened. Empty when no event has words."""
    runs: dict[str, list[Event]] = {}
    for event in sorted(events, key=lambda e: (e.at, e.id)):
        runs.setdefault(event.run, []).append(event)
    lines: list[str] = []
    for run in runs.values():
        lines += run_lines(run, now)
    return list(dict.fromkeys(lines))  # the same sentence twice (two members) is said once


def compose(events: Sequence[Event], now: datetime) -> str:
    """The Telegram message: the brand line, then each run's lines in the order they happened."""
    return "\n".join([BRAND, *batch_lines(events, now)])


class Pace(Protocol):
    """What the desk asks before a send, and tells it after: when the next may start.

    ``now`` is the service clock in seconds; a pace that keeps its own time may ignore it. The
    desk also says ``idle()`` when it has nothing ready to go (a pace may leave it out).
    """

    def wait(self, now: float) -> float:
        """Seconds until the next message may start; 0 when it may start now."""

    def took(self, now: float) -> None:
        """A message is starting now."""


class NoticePace:
    """The notices' own pace, for a desk built without the process's Pacer (see LanePace).

    At least outbox.MIN_GAP_S between two messages and at most NOTICES_PER_MINUTE in any minute.
    The limits are read when asked, so the tests can shrink them. Time is the service clock's,
    which the desk already keeps its settle times and claims on.
    """

    def __init__(self) -> None:
        self._starts: deque[float] = deque()

    def wait(self, now: float) -> float:
        starts = self._recent(now)
        allowed = starts[-1] + outbox.MIN_GAP_S if starts else 0.0
        if len(starts) >= NOTICES_PER_MINUTE:
            allowed = max(allowed, starts[-NOTICES_PER_MINUTE] + MINUTE_S)
        return min(max(0.0, allowed - now), MINUTE_S)  # a clock set back holds it a minute at most

    def took(self, now: float) -> None:
        self._recent(now).append(now)

    def _recent(self, now: float) -> deque[float]:
        while self._starts and self._starts[0] <= now - MINUTE_S:
            self._starts.popleft()
        return self._starts


class LanePace:
    """The notices' place in the one outbox.Pacer of the process (R8, Y18).

    The Pacer (alerts/outbox.py) hands out the send slots of every lane: one gap and one minute
    budget for all, so a notice can make a fill wait one gap and no more. A notice takes no slot
    while order alerts wait. It tells the Pacer from when a notice ready to go has found no slot,
    so that through a market storm the market lane gives way once it has waited
    outbox.NOTICE_WAIT_S (review L2). Wired where AlertService builds the desk:
    ``pace=LanePace(self.outbox.pacer)``.
    """

    LANE = "notices"

    def __init__(self, pacer, lane: str = LANE) -> None:
        self.pacer, self.lane = pacer, lane
        self._clock = getattr(pacer, "clock", time.monotonic)
        #: The Pacer's time a notice ready to go first found no slot; None while none waits.
        self._since: float | None = None
        if hasattr(pacer, "set_waiting"):
            pacer.set_waiting(lane, lambda: self._since is not None, since=lambda: self._since)

    def wait(self, now: float) -> float:
        if self.pacer.orders_waiting():
            wait = GIVE_WAY_S
        else:
            wait = max(0.0, self.pacer.allowed_at(self.lane) - self._clock())
        if wait <= 0:
            self._since = None  # it goes now: the desk takes the slot before it next awaits
        elif self._since is None:
            self._since = self._clock()
        return wait

    def took(self, now: float) -> None:
        self._since = None
        self.pacer.took(self.lane)

    def idle(self) -> None:
        """Nothing is ready to go (none due, or a run still settling): no wait to count."""
        self._since = None


class NoticeDesk:
    """Looks at the notice files and tells the owner what is new, once.

    Args:
        spools: The files the deploy tool and the backup job record in.
        store: The alert record (alerts.db): the once-only rule.
        send: Sends one text; returns SENT, RETRY, UNCONFIRMED, KEY or DROP (OpenAlgoTelegram.send).
        owner_ready: True when the owner's key is loaded, reading it first if need be.
        clock: The service clock (aware datetimes).
        market_open: True while the market trades (the trading calendar's in_market_hours); in
            market hours only the failures go out. None: no quiet hours.
        pace: When the next message may start (NoticePace; LanePace once the Pacer is merged).
    """

    def __init__(
        self,
        spools: Sequence[Path],
        store: Store,
        send: Callable[[str], Awaitable[str]],
        owner_ready: Callable[[], bool],
        clock: Callable[[], datetime],
        *,
        market_open: Callable[[datetime], bool] | None = None,
        pace: Pace | None = None,
    ):
        self.spools = tuple(spools)
        self.store = store
        self.send = send
        self.owner_ready = owner_ready
        self.clock = clock
        self.market_open = market_open
        self.pace: Pace = pace if pace is not None else NoticePace()
        self.stats = {"messages": 0, "notices": 0, "dropped": 0, "stale": 0}
        self._held: set[str] = set()  # notice ids waiting for the market to close
        self._market_error = False
        self._first_seen: dict[str, float] = {}  # notice id -> when this service first saw it
        self._failures = 0  # looks in a row that could not send
        self._unconfirmed = 0  # tries in a row Telegram did not confirm
        self._sending = False
        self._closing = False

    async def run(self) -> None:
        """Look, wait, look again, until cancelled. A failure costs one look, never the service."""
        while not self._closing:
            try:
                wait = await self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("the notices could not be looked at")
                wait = RETRY_S[-1]
            await asyncio.sleep(wait)

    async def close(self, timeout: float) -> None:
        """No new look from now; a message on its way gets up to ``timeout`` seconds to finish."""
        self._closing = True
        deadline = time.monotonic() + timeout
        while self._sending and time.monotonic() < deadline:
            await asyncio.sleep(0.05)

    async def sweep(self) -> float:
        """One look. Returns how many seconds to wait before the next."""
        now = self.clock().timestamp()
        events = self._due(self._unsent(now))
        if (wait := self._settling(events, now)) > 0:
            self._idle()
            return wait + 0.5
        if not events:
            self._idle()
            self._failures = self._unconfirmed = 0
            return POLL_S
        if (wait := self.pace.wait(now)) > 0:  # before the claim: a notice held back is not ours
            return wait + 0.05
        batch = events[:MAX_EVENTS]
        claimed = set(self.store.claim_notices([e.id for e in batch], now, CLAIM_S))
        batch = [e for e in batch if e.id in claimed]
        if not batch:
            return POLL_S
        ids = [e.id for e in batch]
        try:
            if not batch_lines(batch, self.clock()):
                # No words for any of them: a message of the brand line alone is never sent.
                self.store.settle_notices(ids, "dropped", now)
                self.stats["dropped"] += len(ids)
                log.info("%d server notice(s) had nothing to tell and were not sent", len(ids))
                return 1.0
            if not self.owner_ready():
                self.store.release_notices(ids)
                return self._try_later("there is no OpenAlgo API key to send with yet")
            text = compose(batch, self.clock())
            self.pace.took(now)
            self._sending = True
            outcome = await self.send(text)
        except asyncio.CancelledError:
            self.store.release_notices(ids)  # it may or may not have gone out: told again
            raise
        except Exception:
            log.exception("a server notice could not be sent")
            outcome = RETRY
        finally:
            self._sending = False
        return self._after(outcome, ids, now)

    # -- internals ------------------------------------------------------------------------------

    def _idle(self) -> None:
        """Nothing is ready to go: the pace stops counting a wait for a slot (LanePace, L2)."""
        idle = getattr(self.pace, "idle", None)
        if idle is not None:
            idle()

    def _unsent(self, now: float) -> list[Event]:
        """The notices not told yet, oldest first; the ones too old to matter are marked."""
        seen: dict[str, Event] = {}
        for spool in self.spools:
            for event in read_events(spool):
                if event.kind not in KIND_ORDER:
                    continue  # a report is the report desk's, not a notice
                seen.setdefault(event.id, event)
        if not seen:
            return []
        states = self.store.notice_states(seen)
        fresh: list[Event] = []
        old: list[Event] = []
        for event in sorted(seen.values(), key=lambda e: (e.at, e.id)):
            held = states.get(event.id)
            if held is not None and (held[0] != "claimed" or now - held[1] < CLAIM_S):
                continue  # told, dropped, marked, or on its way
            (old if now - event.at > MAX_AGE_S else fresh).append(event)
        if old:
            self.store.settle_notices([e.id for e in old], "stale", now)
            self.stats["stale"] += len(old)
            log.info(
                "%d server notices from more than %d hours ago were not sent",
                len(old), MAX_AGE_S // 3600,
            )  # fmt: skip
        return fresh

    def _due(self, events: list[Event]) -> list[Event]:
        """What may go now: in market hours only the runs that hold a failure; the rest wait."""
        if not events or not self._trading():
            self._held.clear()
            return events
        urgent = {e.run for e in events if _at_once(e)}
        due = [e for e in events if e.run in urgent]
        held = {e.id for e in events if e.run not in urgent}
        if held and held != self._held:
            log.info("%d server notice(s) wait for the market to close", len(held))
        self._held = held
        return due

    def _trading(self) -> bool:
        if self.market_open is None:
            return False
        try:
            return bool(self.market_open(self.clock()))
        except Exception:  # the calendar failing must not keep a notice from the owner
            if not self._market_error:
                log.exception("the market hours could not be read; notices are not held back")
                self._market_error = True
            return False

    def _settling(self, events: list[Event], now: float) -> float:
        """Seconds still to wait for the rest of a run, 0 when nothing new has appeared for
        SETTLE_S. The notices no longer waiting are forgotten."""
        self._first_seen = {e.id: self._first_seen.get(e.id, now) for e in events}
        if not self._first_seen:
            return 0.0
        return max(0.0, SETTLE_S - (now - max(self._first_seen.values())))

    def _after(self, outcome: str, ids: list[str], now: float) -> float:
        if outcome == SENT:
            self.store.settle_notices(ids, "sent", now)
            self.stats["messages"] += 1
            self.stats["notices"] += len(ids)
            self._failures = self._unconfirmed = 0
            log.info("Told the owner about %d server notice(s)", len(ids))
            return POLL_S
        if outcome == DROP:
            self.store.settle_notices(ids, "dropped", now)
            self.stats["dropped"] += len(ids)
            log.warning(
                "%d server notice(s) were not sent: Telegram cannot be reached that way", len(ids)
            )
            return POLL_S
        if outcome == UNCONFIRMED:
            self._unconfirmed += 1
            if self._unconfirmed >= UNCONFIRMED_TRIES:
                self.store.settle_notices(ids, "dropped", now)
                self.stats["dropped"] += len(ids)
                self._unconfirmed = 0
                log.error(
                    "Telegram did not confirm a server notice after %d tries; it is not sent again",
                    UNCONFIRMED_TRIES,
                )
                return POLL_S
        self.store.release_notices(ids)
        return self._try_later("OpenAlgo or Telegram could not take it")

    def _try_later(self, why: str) -> float:
        delay = RETRY_S[min(self._failures, len(RETRY_S) - 1)]
        if self._failures == 0:
            log.info(
                "A server notice is waiting to be sent: %s; trying again in %.0f s", why, delay
            )
        self._failures += 1
        return delay
