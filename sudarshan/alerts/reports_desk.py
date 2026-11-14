"""The journal's reports on Telegram: text first, then the file, then the outcome back (M15.7).

    sudarshan-journal  -> /var/lib/sudarshan-journal/events.jsonl   one line per report, ids only
      -> ReportDesk (this file, inside sudarshan-alerts): picks what is new, claims it in alerts.db
      -> report_get (sealed, journal/delivery/v1)  -> OpenAlgo /api/v1/telegram/notify (the text)
      -> report_file (sealed)  -> sha256 checked   -> OpenAlgo /api/v1/telegram/notify-file
      -> delivery (sealed): sent, text_only or not_sent, the reason in the owner's words

A line of the spool (``ops/events.py``) carries ``id``, ``src`` journal, ``kind`` report, the
report's id (``report``), its ``report_kind`` and the ``sha256`` of its file (none when it has no
file); never its text (cross-design Y8). The desk asks the journal for the text and the file
through the journal's local API on 127.0.0.1:5586, sealed under ``JOURNAL_DELIVERY_INFO``, which
opens only ``report_get``, ``report_file`` and ``delivery``. The journal runs as this service's
user; before each call the port's listener is looked up (loopback.py) and a port held by anybody
else is not sent to. The text goes through OpenAlgo's ``/notify`` as every alert does
(``OpenAlgoTelegram``), the file through ``/notify-file``, whose rules are ``report_files.py``.
This service never holds the bot token or a chat id.

  Once, as far as a record can make it. Each event id is claimed in alerts.db before its text
        goes out (the notice desk's table, under the key ``report:<id>``, which no notice id can
        be). Its row then records how far it got: ``text`` once the text went, an outcome code once
        it is over, ``told`` once the journal has the outcome. A restart carries on where the row
        says. In three narrow windows a message can go twice, never be lost: a text whose send is
        cut off by a stop has its claim given back and is sent again; a process that stops
        between Telegram's answer and the row's write leaves a claim taken over after CLAIM_S; and
        a file upload slower than FILE_TIMEOUT_S may have gone when it is tried again. That is
        at-least-once at the edges, the right side to err on for a report (review L4). An older
        line of a report the journal has announced again (a rebuild of the whole period) is
        passed over unsent while it has not started: the newer line sends the report as it is.
  Not stuck. A report that cannot be moved on (its text or file not given, or not taken) is
        looked at again on each sweep; after STUCK_LOOKS failed looks in a row it waits on its own
        pause (RETRY_S) while the reports after it go, so one bad file never holds the Day report
        and the next Morning brief for a day (review M3).
  Paced. The reports lane of the process's one Pacer (outbox.py): a report takes no slot while an
        order alert or a market alert waits, at most REPORTS_PER_MINUTE slots a minute, one for
        its text and one for its file, with the gap between any two messages. The order alerts
        can go first between a report's text and its file.
  Not quiet. A report is never due while the market trades (the journal's schedule), so the
        notices' quiet hours do not apply; one asked for during the day goes when it is made.
  Text only. A 404 from /notify-file is an older OpenAlgo: the text already went, and it ends
        with LINK_LINE (added when the journal's text lacks it), so nothing is lost. A file whose
        bytes match neither the event's sha256 nor the sha256 the journal gives with them (a
        rebuild after the announcement keeps the report's newest file) is never sent, and the
        reason says so (review L11).
  Not sent. OpenAlgo's bot stopped, the owner's chat not linked, or Telegram not confirming the
        text after UNCONFIRMED_TRIES: the outcome is told to the journal in words and the report
        is not tried again. A report older than MAX_AGE_S that never went is history; its reason
        names the side that did not answer when the desk knows it (the journal, or OpenAlgo and
        Telegram, or a missing API key), and says only that it could not be sent within a day
        when it does not.

  Follows the journal. There is one switch, ``SUDARSHAN_JOURNAL`` in OpenAlgo's .env, which a
        release sets and the journal reads (Y37). The desk asks the journal's unsealed /health
        whether it is on (``journal_state.enabled``) at most once in ASK_EVERY_S, and delivers
        only while it says so. Switched off there, nothing is delivered and the desk asks again a
        minute later. Not answering (not running, a port another user holds, an answer that is not
        the journal's), nothing is delivered either, one log line says so, and the desk asks again
        after RETRY_S's growing pauses. ``SUDARSHAN_JOURNAL=off`` in /etc/sudarshan/alerts.env
        (``Settings.journal``) takes this desk and the journal's nudges away whatever the journal
        says; any other value there, or none, leaves the journal to decide.
  Told. ``health()`` is the ``report_desk`` block of the service's /health: on or off and why,
        how many reports wait, and when the last one went to Telegram. The journal reads it for
        the Reports page (``alerts_on``). With sudarshan-alerts stopped nothing delivers reports;
        the Reports page says so (cross-design Y38).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from cryptography.fernet import Fernet

from sudarshan.autologin.config import is_loopback_url
from sudarshan.autologin.handoff import seal
from sudarshan.autologin.messages import BRAND
from sudarshan.ops.events import JOURNAL_SPOOL, Event, read_events

from . import loopback, outbox, report_files
from .outbox import DROP, RETRY, SENT, UNCONFIRMED, Throttle
from .store import Store

log = logging.getLogger("sudarshan.alerts")

# Timings are module constants so the tests can shrink them.
POLL_S = 15.0  # how often the spool is looked at
RETRY_S = (15.0, 30.0, 60.0, 120.0, 300.0)  # pauses after a look that could not finish
CLAIM_S = 300.0  # a claim older than this was left by a process that stopped
MAX_AGE_S = 24 * 3600.0  # a report older than this that never went is history
TELL_WITHIN_S = 7 * 24 * 3600.0  # the journal is told about a report too old to send only this long
TELL_FOR_S = 2 * 24 * 3600.0  # the journal is asked to take an outcome for this long
UNCONFIRMED_TRIES = 3  # Telegram did not confirm a message (OpenAlgo answered "queued")
STUCK_LOOKS = 3  # failed looks in a row at one report before the reports after it go first
GIVE_WAY_S = 1.0  # look again this soon while order or market alerts wait
SLOT_POLL_S = 0.2
CONNECT_TIMEOUT_S = 2.0
JOURNAL_TIMEOUTS_S = {"report_get": 5.0, "report_file": 20.0, "delivery": 5.0}
#: How long an answer of the journal's /health (on, or switched off) holds before it is asked
#: again.
ASK_EVERY_S = 60.0
HEALTH_TIMEOUT_S = 2.0
#: OpenAlgo waits up to 30 s for Telegram to take a file, after the upload.
FILE_TIMEOUT_S = 45.0
#: Telegram takes at most 4096 characters in a message.
TEXT_MAX = 4000

JOURNAL_URL = "http://127.0.0.1:5586"
JOURNAL_SERVICE = "sudarshan-journal"
PRINCIPAL = "sudarshan-alerts"
#: The desk's states, as the report_desk block of /health names them. ``on`` delivers; the two
#: journal ones wait for the journal and will deliver as soon as it says it is on; ``off`` is this
#: service's own doing (ORDER_ALERTS_OFF, SWITCHED_OFF_HERE, NO_SEAL) and lasts until it is
#: restarted with other settings.
STARTING, ON, OFF = "starting", "on", "off"
JOURNAL_OFF, JOURNAL_DOWN = "journal_off", "journal_down"
#: Why the desk waits for the journal, in words (the log and the report_desk block).
JOURNAL_SWITCHED_OFF = "the journal is switched off on the server"
JOURNAL_NOT_RUNNING = "the journal service is not running"
JOURNAL_PORT_HELD = "the journal's port is held by another user's program, so it is not asked"
JOURNAL_SILENT = "the journal service is not answering"
#: Why there is no desk at all.
ORDER_ALERTS_OFF = (
    "order alerts are switched off (SUDARSHAN_ORDER_ALERTS=off), and reports go out with them"
)
SWITCHED_OFF_HERE = (
    "switched off for this service: SUDARSHAN_JOURNAL=off in /etc/sudarshan/alerts.env"
)
NO_SEAL = "its requests to the journal cannot be sealed: OpenAlgo's pepper could not be read"
CHANNEL = "telegram"
NOTIFY_FILE_PATH = "/api/v1/telegram/notify-file"
LINK_LINE = "Open the full report: SUDARSHAN > Reports"
KEY_PREFIX = "report:"
CLAIMED, TEXT, TOLD, DROPPED = "claimed", "text", "told", "dropped"
TITLES = {
    "brief": "Morning brief",
    "eod": "Day report",
    "weekly": "Week in review",
    "monthly": "Monthly statement",
    "fy": "Tax pack",
}
#: How a report ended (the code its row keeps until the journal is told), and what the journal is
#: told: the status and the reason in the owner's words.
OUTCOMES = {
    "sent": ("sent", None),
    "to:route": ("text_only", "this OpenAlgo cannot send report files yet, so only the text went"),
    "to:stop": ("text_only", "OpenAlgo's Telegram bot is stopped"),
    "to:link": ("text_only", "the owner's chat is not linked"),
    "to:conf": ("text_only", "Telegram did not confirm the file after 3 tries"),
    "to:sha": ("text_only", "the report's file did not match the journal's record, so it was not sent"),
    "to:size": ("text_only", "the report's file is larger than Telegram takes"),
    "to:bad": ("text_only", "the journal's file for this report could not be read"),
    "to:ref": ("text_only", "OpenAlgo refused the report's file"),
    "to:gone": ("text_only", "the journal no longer has the report's file"),
    "to:old": ("text_only", "the file could not be sent within a day"),
    "to:old:j": ("text_only", "the file could not be sent within a day: the journal did not give it to the alerts service"),
    "to:old:t": ("text_only", "the file could not be sent within a day: OpenAlgo or Telegram did not take it"),
    "ns:stop": ("not_sent", "OpenAlgo's Telegram bot is stopped"),
    "ns:link": ("not_sent", "the owner's chat is not linked"),
    "ns:conf": ("not_sent", "Telegram did not confirm it after 3 tries"),
    "ns:ref": ("not_sent", "OpenAlgo refused the message"),
    "ns:gone": ("not_sent", "the journal no longer has this report"),
    "ns:empty": ("not_sent", "the report had no text to send"),
    "ns:old": ("not_sent", "it could not be sent within a day"),
    "ns:old:j": ("not_sent", "it could not be sent within a day: the journal did not give it to the alerts service"),
    "ns:old:t": ("not_sent", "it could not be sent within a day: OpenAlgo or Telegram did not take it"),
    "ns:old:k": ("not_sent", "it could not be sent within a day: OpenAlgo's API key could not be read"),
}  # fmt: skip
#: Which side a wait was on (``_later``), for the reason of a report that could not go in a day.
JOURNAL_SIDE, TELEGRAM_SIDE, KEY_SIDE = "j", "t", "k"
#: A text OpenAlgo refused for good (``OpenAlgoTelegram``'s reasons) and its outcome code.
TEXT_DROPS = {outbox.DROP_REASONS[409]: "ns:stop", outbox.DROP_REASONS[404]: "ns:link"}
_ID = re.compile(r"[0-9]{1,18}")


def _report_id(event: Event) -> int | None:
    raw = event.fact("report")
    return int(raw) if _ID.fullmatch(raw) and int(raw) > 0 else None


def _text_of(data: Any, has_file: bool) -> str | None:
    """The report's text as it goes out: at most TEXT_MAX characters, and with a file, ending with
    LINK_LINE (so a text that has to go alone still says where the full report is)."""
    text = data.get("text") if isinstance(data, Mapping) else None
    if not isinstance(text, str) or not text.strip():
        return None
    body = text.strip()
    tail = ""
    if has_file:
        body = body.replace(LINK_LINE, "").rstrip()
        tail = "\n" + LINK_LINE
    room = TEXT_MAX - len(tail)
    if len(body) > room:
        cut = body[: room - 4]
        body = (cut.rsplit("\n", 1)[0] if "\n" in cut else cut).rstrip() + "\n..."
    return body + tail if body else None


def _file_of(data: Any) -> tuple[str, bytes] | None:
    """The file's name and bytes from ``report_file``'s answer, or None when it cannot be read."""
    if not isinstance(data, Mapping):
        return None
    name, encoded = data.get("file_name"), data.get("file_b64")
    if not isinstance(name, str) or not isinstance(encoded, str) or not encoded:
        return None
    try:
        return name, base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None


class JournalLink:
    """The desk's three sealed ops at the journal's local API (127.0.0.1:5586).

    ``call`` answers ``(kind, data)``: ``ok`` with the op's data, ``off`` (the journal is switched
    off), ``gone`` (the report is not in the journal: a 404 the service words), ``refused`` (the
    journal will not take this request: a wrong seal, a principal or a request it calls wrong) or
    ``down`` (not running, not answering, busy, or too old to know the op; try again later).
    """

    def __init__(self, base_url: str, fernet: Fernet, client: httpx.AsyncClient, *,
                 tables=loopback.TCP_TABLES):  # fmt: skip
        self.base_url = base_url.rstrip("/")
        self.fernet, self.client, self.tables = fernet, client, tables
        self.throttle = Throttle()

    async def call(self, op: str, args: dict) -> tuple[str, Any]:
        held = loopback.holder(self.base_url, self.tables)
        if held == loopback.NOBODY:
            self.throttle(logging.INFO, "journal-down", "The journal service is not running")
            return "down", None
        if held == loopback.OTHER:
            self.throttle(logging.ERROR, "journal-held", "Nothing is sent to the journal: %s",
                          loopback.refusal(self.base_url, self.tables))  # fmt: skip
            return "down", None
        body = seal(self.fernet, op, principal=PRINCIPAL, args=args)
        try:
            answer = await self.client.post(
                f"{self.base_url}/v1/{op}",
                content=body,
                headers={"Content-Type": "application/octet-stream", "Accept": "application/json"},
                timeout=httpx.Timeout(JOURNAL_TIMEOUTS_S[op], connect=CONNECT_TIMEOUT_S),
            )
            reply = answer.json()
        except (httpx.HTTPError, ValueError):
            self.throttle(logging.WARNING, "journal-call", "The journal did not answer a %s", op)
            return "down", None
        if not isinstance(reply, dict) or reply.get("status") not in ("success", "error"):
            return "down", None
        if reply.get("status") == "success":
            data = reply.get("data")
            if isinstance(data, dict) and data.get("enabled") is False:
                return "off", None
            return "ok", data
        error = reply.get("error") if isinstance(reply.get("error"), dict) else reply
        code, message = error.get("code"), error.get("message")
        status = answer.status_code
        bare = not isinstance(message, str) or message.strip().lower() in ("", "not found")
        if status == 404 and code == "not_found" and not bare:
            return "gone", None
        if status in (400, 403, 404, 409, 422) and code not in ("not_found", "not_ready"):
            self.throttle(logging.ERROR, f"journal-{code}",
                          "The journal refused the report desk's %s (%s)", op, code)  # fmt: skip
            return "refused", None
        return "down", None  # busy, failing, or a journal that does not have the op yet


class JournalWatch:
    """Whether the journal says it is on: ``journal_state.enabled`` on its /health (127.0.0.1:5586).

    ``ask`` answers ``(ON, None)``, ``(JOURNAL_OFF, why)`` (switched off, or kept off by the
    journal itself: its own sentence when it gives one) or ``(JOURNAL_DOWN, why)`` (not running, a
    port another user's program holds, or no answer that reads as the journal's). The document
    holds nothing secret and the request carries nothing, but a port another user holds is not
    asked all the same: its answer could not be the journal's.
    """

    def __init__(self, base_url: str, client: httpx.AsyncClient, *, tables=loopback.TCP_TABLES):
        self.url = base_url.rstrip("/") + "/health"
        self.client, self.tables = client, tables

    async def ask(self) -> tuple[str, str | None]:
        held = loopback.holder(self.url, self.tables)
        if held == loopback.NOBODY:
            return JOURNAL_DOWN, JOURNAL_NOT_RUNNING
        if held == loopback.OTHER:
            return JOURNAL_DOWN, JOURNAL_PORT_HELD
        try:
            answer = await self.client.get(
                self.url,
                headers={"Accept": "application/json"},
                timeout=httpx.Timeout(HEALTH_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
            )
            doc = answer.json()
        except (httpx.HTTPError, ValueError):
            return JOURNAL_DOWN, JOURNAL_SILENT
        if not isinstance(doc, dict) or answer.status_code != 200:
            return JOURNAL_DOWN, JOURNAL_SILENT
        state = doc.get("journal_state")
        if doc.get("service") != JOURNAL_SERVICE or not isinstance(state, dict):
            return JOURNAL_DOWN, JOURNAL_SILENT
        if state.get("enabled") is True:
            return ON, None
        why = state.get("reason")
        if isinstance(why, str) and why.strip():
            return JOURNAL_OFF, why.strip()[:300]
        return JOURNAL_OFF, JOURNAL_SWITCHED_OFF


class OpenAlgoFiles:
    """One report file to the owner's linked chat, through OpenAlgo's /notify-file.

    ``send`` answers ``sent``, ``unconfirmed`` (Telegram did not take it: try again), ``retry``
    (OpenAlgo could not be asked or was busy) or the outcome code of a file that will not go:
    ``to:route`` (an older OpenAlgo without the route), ``to:stop``, ``to:link``, ``to:size`` or
    ``to:ref``. The owner's key goes only to a port this service's own user listens on.
    """

    def __init__(self, base_url: str, client: httpx.AsyncClient, owner, reload_owner):
        self.url = base_url.rstrip("/") + NOTIFY_FILE_PATH
        self.client, self.owner, self.reload_owner = client, owner, reload_owner
        self.throttle = Throttle()

    async def send(self, name: str, data: bytes, mime: str, caption: str) -> str:
        owner = self.owner()
        if owner is None:
            return "retry"
        refused = loopback.refusal(self.url)
        if refused:
            self.throttle(logging.ERROR, "listener", "The API key is not sent to OpenAlgo: %s",
                          refused)  # fmt: skip
            return "retry"
        outcome = await self._post(owner, name, data, mime, caption)
        if outcome == "key":
            fresh = self.reload_owner()
            if fresh is not None and fresh.api_key != owner.api_key:
                outcome = await self._post(fresh, name, data, mime, caption)
            if outcome == "key":
                self.throttle(logging.ERROR, "key", "OpenAlgo refused the stored API key")
                outcome = "retry"
        return outcome

    async def _post(self, owner, name: str, data: bytes, mime: str, caption: str) -> str:
        form = {
            "apikey": owner.api_key,
            "username": owner.username,
            "caption": caption,
            "wait_for_delivery": "true",
        }
        try:
            answer = await self.client.post(
                self.url,
                data=form,
                files={"file": (name, data, mime)},
                timeout=httpx.Timeout(FILE_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
            )
        except httpx.HTTPError:
            self.throttle(logging.WARNING, "down", "OpenAlgo did not answer; the file waits")
            return "retry"
        try:
            body = answer.json()
        except ValueError:
            body = None
        body = body if isinstance(body, dict) else {}
        status = answer.status_code
        if status == 200:
            said = str(body.get("message", "")).lower()
            return "unconfirmed" if "queued" in said else "sent"
        if status in (401, 403):
            return "key"
        if status == 404:  # a route that refuses says why; an older OpenAlgo has no such route
            return "to:link" if body.get("code") == "not_linked" else "to:route"
        if status == 409:
            return "to:stop"
        if status == 413:
            return "to:size"
        if status == 502:
            return "unconfirmed"
        if status == 429 or status >= 500:
            return "retry"
        return "to:ref"


class ReportDesk:
    """Looks at the journal's spool and delivers each new report once: text, then file, then the
    outcome back to the journal.

    Args:
        spool: The journal's events file (JOURNAL_SPOOL).
        store: The alert record (alerts.db): the once-only rule and how far each report got.
        journal: The sealed link to the journal (JournalLink).
        send_text: Sends one text; returns (outcome, reason) as OpenAlgoTelegram.send_with_reason.
        files: Sends one file (OpenAlgoFiles).
        owner_ready: True when the owner's key is loaded, reading it first if need be.
        clock: The service clock (aware datetimes).
        pacer: The process's one outbox.Pacer; the desk takes the reports lane's slots.
        watch: Asks the journal whether it is on (JournalWatch); the desk delivers only while it
            says so. None delivers without asking (a desk driven on its own, as the tests do).
    """

    def __init__(
        self,
        spool: Path,
        store: Store,
        journal: JournalLink,
        send_text: Callable[[str], Awaitable[tuple[str, str]]],
        files: OpenAlgoFiles,
        owner_ready: Callable[[], bool],
        clock: Callable[[], datetime],
        pacer: outbox.Pacer,
        *,
        lane: str = outbox.REPORTS,
        watch: JournalWatch | None = None,
    ):
        self.spool, self.store, self.journal = Path(spool), store, journal
        self.send_text, self.files = send_text, files
        self.owner_ready, self.clock, self.pacer, self.lane = owner_ready, clock, pacer, lane
        self.watch = watch
        self.stats = {"sent": 0, "text_only": 0, "not_sent": 0, "told": 0}
        #: What the report_desk block of /health shows (health()).
        self.state, self.reason = (STARTING if watch is not None else ON), None
        self.waiting = 0  # reports in the spool not over yet, at the last look
        self.last_delivery_ms: int | None = None  # when a report last went to Telegram
        self._asked: float | None = None  # monotonic: when the journal was last asked
        self._ask_failures = 0  # asks in a row the journal did not answer
        self._failures = 0  # looks in a row that could not finish
        self._tries: dict[tuple[str, str], int] = {}  # (event id, text or file) -> unconfirmed
        self._looks: dict[str, int] = {}  # event id -> failed looks in a row
        self._held: dict[str, float] = {}  # event id -> when it may be looked at again
        self._side: dict[str, str] = {}  # event id -> the side its last wait was on
        self._quiet_at: float | None = None  # when the journal last said it was not on
        self._current: str | None = None  # the event the desk is moving on, for ``_later``
        self._sending = False
        self._closing = False
        #: How the desk waits for a slot: a test drives it with the pacer's own clock.
        self.sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep

    def health(self) -> dict[str, Any]:
        """The report_desk block of the service's /health: plain facts from memory, nothing secret.

        ``state`` is STARTING, ON, JOURNAL_OFF or JOURNAL_DOWN (``on`` is true for ON only),
        ``reason`` why nothing is delivered, ``waiting`` the reports in the journal's spool that
        are not over yet, ``last_delivery_ms`` when a report last went to Telegram (sent, or its
        text only), since this start."""
        return {
            "state": self.state,
            "on": self.state == ON,
            "reason": self.reason,
            "waiting": self.waiting,
            "last_delivery_ms": self.last_delivery_ms,
        }

    async def run(self) -> None:
        """Look, wait, look again, until closed. A failure costs one look, never the service."""
        while not self._closing:
            try:
                wait = await self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("the journal's reports could not be looked at")
                wait = RETRY_S[-1]
            await asyncio.sleep(wait)

    async def close(self, timeout: float) -> None:
        """No new look from now; a message on its way gets up to ``timeout`` seconds to finish."""
        self._closing = True
        deadline = time.monotonic() + timeout
        while self._sending and time.monotonic() < deadline:
            await asyncio.sleep(0.05)

    async def sweep(self) -> float:
        """One look: carries the oldest unfinished report one or more steps, while the journal
        says it is on. Returns how many seconds to wait before the next look."""
        now = self.clock().timestamp()
        events = [e for e in read_events(self.spool) if e.kind == "report" and e.src == "journal"]
        states = self.store.notice_states([KEY_PREFIX + e.id for e in events]) if events else {}

        def over(event: Event) -> bool:
            return states.get(KEY_PREFIX + event.id, (None, 0.0))[0] in (TOLD, DROPPED)

        unfinished = sorted((e for e in events if not over(e)), key=lambda e: (e.at, e.id))
        self.waiting = sum(1 for e in unfinished if now - e.at <= TELL_WITHIN_S)
        self._forget({e.id for e in unfinished})
        hold = await self._journal_on()
        if hold is not None:
            return hold
        newest: dict[str, str] = {}
        for event in unfinished:
            newest[event.fact("report")] = event.id
        waits = []
        for event in unfinished:
            key = KEY_PREFIX + event.id
            state, at = states.get(key, (None, 0.0))
            if state == CLAIMED and now - at < CLAIM_S:
                continue  # on its way
            if state is None and newest.get(event.fact("report")) != event.id:
                # announced again since (a rebuild of the whole period): the newer line sends it
                self.store.settle_notices([key], DROPPED, now)
                continue
            until = self._held.get(event.id)
            if until is not None and until > now:
                waits.append(until - now)  # stuck: on its own pause while the others go
                continue
            self._current = event.id
            try:
                wait = await self._advance(event, key, state, now)
            finally:
                self._current = None
            if self._held.get(event.id, 0.0) > now:
                continue  # it failed once too often: the next report goes in this same sweep
            return wait
        self._failures = 0
        return min([POLL_S, *waits])

    def _forget(self, live: set[str]) -> None:
        """Keep the desk's notes only for reports still in the spool and not over."""
        for notes in (self._looks, self._held, self._side):
            for event_id in [k for k in notes if k not in live]:
                notes.pop(event_id, None)

    async def _journal_on(self) -> float | None:
        """None while the journal says it is on; otherwise how long to wait before the next look.
        It is asked at most once in ASK_EVERY_S while its answer holds, and after RETRY_S's
        growing pauses while it does not answer; each change is logged once (_become)."""
        if self.watch is None:
            return None
        clock = time.monotonic()
        if self._asked is None or clock - self._asked >= self._holds_s():
            state, why = await self.watch.ask()
            self._asked = clock
            self._ask_failures = self._ask_failures + 1 if state == JOURNAL_DOWN else 0
            self._become(state, why)
        if self.state == ON:
            return None
        self._quiet_at = self.clock().timestamp()
        return max(self._asked + self._holds_s() - clock, 0.05)

    def _holds_s(self) -> float:
        """How long the journal's last answer holds: ASK_EVERY_S, or while it does not answer
        the next pause of RETRY_S."""
        if self._ask_failures == 0:
            return ASK_EVERY_S
        return RETRY_S[min(self._ask_failures - 1, len(RETRY_S) - 1)]

    def _become(self, state: str, why: str | None) -> None:
        """The desk takes the journal's answer as its state; a change is logged once."""
        if (state, why) == (self.state, self.reason):
            return
        self.state, self.reason = state, why
        if state == ON:
            log.info("Reports: the journal says it is on, so its reports are delivered")
            return
        level = {JOURNAL_PORT_HELD: logging.ERROR, JOURNAL_SILENT: logging.WARNING}.get(
            why or "", logging.INFO
        )
        log.log(level, "Reports: none are delivered: %s. The journal is asked again %s.",
                (why or JOURNAL_SILENT).rstrip("."),
                "in a minute" if state == JOURNAL_OFF else "after a pause")  # fmt: skip

    # -- the steps -------------------------------------------------------------------------------

    async def _advance(self, event: Event, key: str, state: str | None, now: float) -> float:
        report_id = _report_id(event)
        if report_id is None:
            self.store.settle_notices([key], DROPPED, now)
            log.warning("A report line in the journal's spool names no report; it is not sent")
            return 0.5
        if state in OUTCOMES:
            return await self._tell(event, key, report_id, state, now)
        if state == TEXT:
            return await self._file(event, key, report_id, now)
        age = now - event.at
        if age > TELL_WITHIN_S:  # history: nobody waits for this outcome any more
            self.store.settle_notices([key], TOLD, now)
            self._tries.pop((event.id, "text"), None)
            return 0.5
        if age > MAX_AGE_S:
            return await self._end(event, key, report_id, self._old("ns:old", event), now)
        if not self.owner_ready():
            return self._later("there is no OpenAlgo API key to send with yet", KEY_SIDE)
        if not self.store.claim_notices([key], now, CLAIM_S):
            return POLL_S
        try:
            return await self._text(event, key, report_id, now)
        except asyncio.CancelledError:
            self.store.release_notices([key])  # only a claim is given back: a text sent stays sent
            raise

    async def _text(self, event: Event, key: str, report_id: int, now: float) -> float:
        has_file = bool(event.fact("sha256"))
        kind, data = await self.journal.call("report_get", {"id": report_id})
        if kind == "gone":
            return await self._end(event, key, report_id, "ns:gone", now)
        if kind != "ok":
            self.store.release_notices([key])
            return self._later("the journal did not give the report's text", JOURNAL_SIDE)
        text = _text_of(data, has_file)
        if text is None:
            return await self._end(event, key, report_id, "ns:empty", now)
        if not await self._slot():
            self.store.release_notices([key])
            return 0.0
        self.pacer.took(self.lane)
        outcome, why = await self._sending_of(self.send_text(text), (RETRY, ""))
        if outcome == SENT:
            self._tries.pop((event.id, "text"), None)  # the file's tries are its own
            self._looks.pop(event.id, None)  # a step went: the count of failed looks starts again
            if not has_file:
                return await self._end(event, key, report_id, "sent", now)
            self.store.settle_notices([key], TEXT, now)
            return await self._file(event, key, report_id, now)
        if outcome == DROP:
            return await self._end(event, key, report_id, TEXT_DROPS.get(why, "ns:ref"), now)
        if outcome == UNCONFIRMED and self._tried(event.id, "text"):
            return await self._end(event, key, report_id, "ns:conf", now)
        self.store.release_notices([key])
        return self._later("OpenAlgo or Telegram could not take the text", TELEGRAM_SIDE)

    async def _file(self, event: Event, key: str, report_id: int, now: float) -> float:
        if now - event.at > MAX_AGE_S:
            return await self._end(event, key, report_id, self._old("to:old", event), now)
        kind, data = await self.journal.call("report_file", {"id": report_id})
        if kind == "gone":
            return await self._end(event, key, report_id, "to:gone", now)
        if kind != "ok":
            return self._later("the journal did not give the report's file", JOURNAL_SIDE)
        found = _file_of(data)
        if found is None:
            return await self._end(event, key, report_id, "to:bad", now)
        name, raw = found
        digest = hashlib.sha256(raw).hexdigest()
        given = data.get("file_sha256") if isinstance(data.get("file_sha256"), str) else ""
        if digest not in (event.fact("sha256").strip().lower(), given.strip().lower()):
            log.error("A report's file did not match the journal's record; it was not sent")
            return await self._end(event, key, report_id, "to:sha", now)
        problem = report_files.problem(name, raw)
        if problem is not None:
            return await self._end(event, key, report_id, "to:size" if problem == "size"
                                   else "to:bad", now)  # fmt: skip
        if not await self._slot():
            return 0.0
        self.pacer.took(self.lane)
        caption = f"{BRAND}: {TITLES.get(event.fact('report_kind'), 'Report')}"
        mime = report_files.kind_of(name).mime
        outcome = await self._sending_of(self.files.send(name, raw, mime, caption), "retry")
        if outcome == "sent" or outcome in OUTCOMES:
            return await self._end(event, key, report_id, outcome, now)
        if outcome == "unconfirmed" and self._tried(event.id, "file"):
            return await self._end(event, key, report_id, "to:conf", now)
        return self._later("OpenAlgo or Telegram could not take the file", TELEGRAM_SIDE)

    async def _end(self, event: Event, key: str, report_id: int, code: str, now: float) -> float:
        """Record how a report ended, then tell the journal."""
        self.store.settle_notices([key], code, now)
        self._tries.pop((event.id, "text"), None)
        self._tries.pop((event.id, "file"), None)
        for notes in (self._looks, self._held, self._side):
            notes.pop(event.id, None)
        status = OUTCOMES[code][0]
        self.stats[status] += 1
        if status in ("sent", "text_only"):
            self.last_delivery_ms = int(self.clock().timestamp() * 1000)
        kind = TITLES.get(event.fact("report_kind"), "report").lower()
        if status == "sent":
            log.info("A %s was sent to Telegram", kind)
        else:
            log.warning("A %s was %s: %s", kind, "sent as text only" if status == "text_only"
                        else "not sent", OUTCOMES[code][1])  # fmt: skip
        return await self._tell(event, key, report_id, code, now)

    async def _tell(self, event: Event, key: str, report_id: int, code: str, now: float) -> float:
        status, reason = OUTCOMES[code]
        args = {"report_id": report_id, "channel": CHANNEL, "status": status, "reason": reason}
        kind, _ = await self.journal.call("delivery", args)
        if kind in ("ok", "gone", "refused") or now - event.at > TELL_FOR_S:
            self.store.settle_notices([key], TOLD, now)
            if kind == "ok":
                self.stats["told"] += 1
            self._failures = 0
            return 0.5
        return self._later("the journal did not take the report's outcome")

    # -- helpers ---------------------------------------------------------------------------------

    async def _slot(self) -> bool:
        """Until the reports lane may take a slot: none while order alerts or market alerts wait,
        and the pacer's gap and minute budgets. False when the desk is closing."""
        while not self._closing:
            if self.pacer.blocked(self.lane):
                await self.sleep(GIVE_WAY_S)
                continue
            wait = self.pacer.allowed_at(self.lane) - self.pacer.clock()
            if wait <= 0:
                return True
            await self.sleep(min(wait, SLOT_POLL_S))
        return False

    async def _sending_of(self, send: Awaitable, failed):
        """Await one send; one that raises counts as ``failed`` (a retry). A text cancelled on its
        way has its claim given back by ``_advance``: it is tried again (it may have gone)."""
        self._sending = True
        try:
            return await send
        except Exception:
            log.exception("a report could not be sent")
            return failed
        finally:
            self._sending = False

    def _tried(self, event_id: str, what: str) -> bool:
        """One more unconfirmed try; True once there have been UNCONFIRMED_TRIES."""
        tries = self._tries.get((event_id, what), 0) + 1
        if tries >= UNCONFIRMED_TRIES:
            self._tries.pop((event_id, what), None)
            return True
        self._tries[(event_id, what)] = tries
        return False

    def _later(self, why: str, side: str | None = None) -> float:
        """A look that could not move a report on: the desk's pause before the next look, and
        the report's own count (after STUCK_LOOKS in a row it waits on its own pause while the
        reports after it go) and the side it waited on, for the reason if it never goes."""
        delay = RETRY_S[min(self._failures, len(RETRY_S) - 1)]
        if self._failures == 0:
            log.info("A report is waiting: %s; trying again in %.0f s", why, delay)
        self._failures += 1
        event_id = self._current
        if event_id is not None:
            if side is not None:
                self._side[event_id] = side
            looks = self._looks.get(event_id, 0) + 1
            self._looks[event_id] = looks
            if looks >= STUCK_LOOKS:
                pause = RETRY_S[min(looks - STUCK_LOOKS, len(RETRY_S) - 1)]
                self._held[event_id] = self.clock().timestamp() + pause
                if looks == STUCK_LOOKS:
                    log.warning("A report could not be moved on after %d tries (%s); the reports "
                                "after it go first, and it is tried again in %.0f s",
                                looks, why, pause)  # fmt: skip
        return delay

    def _old(self, code: str, event: Event) -> str:
        """The outcome code of a report that could not go within a day, naming the side it last
        waited on (the journal, OpenAlgo and Telegram, the API key); the journal when it said it
        was not on while the report waited; else the plain code, which guesses no cause."""
        side = self._side.get(event.id)
        if side is None and self._quiet_at is not None and self._quiet_at >= event.at:
            side = JOURNAL_SIDE
        found = f"{code}:{side}" if side else code
        return found if found in OUTCOMES else code


def journal_url(environ: Mapping[str, str]) -> str:
    """The journal's local API: SUDARSHAN_JOURNAL_API_URL when it is plain http on this server."""
    url = (environ.get("SUDARSHAN_JOURNAL_API_URL") or JOURNAL_URL).rstrip("/")
    return url if url.startswith("http://") and is_loopback_url(url) else JOURNAL_URL


def report_desk_for(service, telegram, client: httpx.AsyncClient) -> ReportDesk | None:
    """The desk for a running AlertService, which delivers while the journal says it is on; None
    when alerts.env says ``SUDARSHAN_JOURNAL=off`` (``Settings.journal``) or its requests cannot
    be sealed (``off_reason`` says which). Never raises: reports never stop order alerts."""
    if not service.settings.journal:
        log.info("Reports: %s", SWITCHED_OFF_HERE)
        return None
    from sudarshan.vault import JOURNAL_DELIVERY_INFO, fernet_for, pepper_from

    try:
        fernet = fernet_for(pepper_from(os.environ), JOURNAL_DELIVERY_INFO)
    except Exception:
        log.exception("Reports cannot be delivered: their requests to the journal cannot be sealed")
        return None
    spool = Path(os.environ.get("SUDARSHAN_JOURNAL_SPOOL") or JOURNAL_SPOOL)
    base = journal_url(os.environ)
    return ReportDesk(
        spool,
        service.store,
        JournalLink(base, fernet, client),
        telegram.send_with_reason,
        OpenAlgoFiles(service.settings.openalgo_url, client, telegram.owner, telegram.reload_owner),
        service._owner_ready,
        service.clock,
        service.outbox.pacer,
        watch=JournalWatch(base, client),
    )


def off_reason(settings) -> str:
    """Why ``report_desk_for`` made no desk for these settings: switched off here, or no seal."""
    return SWITCHED_OFF_HERE if not settings.journal else NO_SEAL
