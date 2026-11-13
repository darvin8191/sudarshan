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

  Once. Each event id is claimed in alerts.db before its text goes out (the notice desk's table,
        under the key ``report:<id>``, which no notice id can be). Its row then records how far it
        got: ``text`` once the text went, an outcome code once it is over, ``told`` once the
        journal has the outcome. A restart carries on where the row says, so a text is never sent
        twice; a claim left by a process that stopped mid-send is taken over after CLAIM_S.
  Paced. The reports lane of the process's one Pacer (outbox.py): a report takes no slot while an
        order alert or a market alert waits, at most REPORTS_PER_MINUTE slots a minute, one for
        its text and one for its file, with the gap between any two messages. The order alerts
        can go first between a report's text and its file.
  Not quiet. A report is never due while the market trades (the journal's schedule), so the
        notices' quiet hours do not apply; one asked for during the day goes when it is made.
  Text only. A 404 from /notify-file is an older OpenAlgo: the text already went, and it ends
        with LINK_LINE (added when the journal's text lacks it), so nothing is lost. A file whose
        bytes do not match the event's sha256 is never sent, and the reason says so.
  Not sent. OpenAlgo's bot stopped, the owner's chat not linked, or Telegram not confirming the
        text after UNCONFIRMED_TRIES: the outcome is told to the journal in words and the report
        is not tried again. A report older than MAX_AGE_S that never went is history.

Only while ``SUDARSHAN_JOURNAL=on`` in /etc/sudarshan/alerts.env (``Settings.nudge_journal``, the
switch of the journal's nudges too). With sudarshan-alerts stopped nothing delivers reports; the
Reports page says so (cross-design Y38).
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
GIVE_WAY_S = 1.0  # look again this soon while order or market alerts wait
SLOT_POLL_S = 0.2
CONNECT_TIMEOUT_S = 2.0
JOURNAL_TIMEOUTS_S = {"report_get": 5.0, "report_file": 20.0, "delivery": 5.0}
#: OpenAlgo waits up to 30 s for Telegram to take a file, after the upload.
FILE_TIMEOUT_S = 45.0
#: Telegram takes at most 4096 characters in a message.
TEXT_MAX = 4000

JOURNAL_URL = "http://127.0.0.1:5586"
PRINCIPAL = "sudarshan-alerts"
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
    "ns:stop": ("not_sent", "OpenAlgo's Telegram bot is stopped"),
    "ns:link": ("not_sent", "the owner's chat is not linked"),
    "ns:conf": ("not_sent", "Telegram did not confirm it after 3 tries"),
    "ns:ref": ("not_sent", "OpenAlgo refused the message"),
    "ns:gone": ("not_sent", "the journal no longer has this report"),
    "ns:empty": ("not_sent", "the report had no text to send"),
    "ns:old": ("not_sent", "it waited more than a day for the alerts service, so it was not sent"),
}  # fmt: skip
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
    ):
        self.spool, self.store, self.journal = Path(spool), store, journal
        self.send_text, self.files = send_text, files
        self.owner_ready, self.clock, self.pacer, self.lane = owner_ready, clock, pacer, lane
        self.stats = {"sent": 0, "text_only": 0, "not_sent": 0, "told": 0}
        self._failures = 0  # looks in a row that could not finish
        self._tries: dict[tuple[str, str], int] = {}  # (event id, text or file) -> unconfirmed
        self._sending = False
        self._closing = False

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
        """One look: carries the oldest unfinished report one or more steps. Returns how many
        seconds to wait before the next look."""
        now = self.clock().timestamp()
        events = [e for e in read_events(self.spool) if e.kind == "report" and e.src == "journal"]
        if not events:
            self._failures = 0
            return POLL_S
        states = self.store.notice_states([KEY_PREFIX + e.id for e in events])
        for event in sorted(events, key=lambda e: (e.at, e.id)):
            key = KEY_PREFIX + event.id
            state, at = states.get(key, (None, 0.0))
            if state in (TOLD, DROPPED):
                continue
            if state == CLAIMED and now - at < CLAIM_S:
                continue  # on its way
            return await self._advance(event, key, state, now)
        self._failures = 0
        return POLL_S

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
            return await self._end(event, key, report_id, "ns:old", now)
        if not self.owner_ready():
            return self._later("there is no OpenAlgo API key to send with yet")
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
            return self._later("the journal did not give the report's text")
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
            if not has_file:
                return await self._end(event, key, report_id, "sent", now)
            self.store.settle_notices([key], TEXT, now)
            return await self._file(event, key, report_id, now)
        if outcome == DROP:
            return await self._end(event, key, report_id, TEXT_DROPS.get(why, "ns:ref"), now)
        if outcome == UNCONFIRMED and self._tried(event.id, "text"):
            return await self._end(event, key, report_id, "ns:conf", now)
        self.store.release_notices([key])
        return self._later("OpenAlgo or Telegram could not take the text")

    async def _file(self, event: Event, key: str, report_id: int, now: float) -> float:
        if now - event.at > MAX_AGE_S:
            return await self._end(event, key, report_id, "to:old", now)
        kind, data = await self.journal.call("report_file", {"id": report_id})
        if kind == "gone":
            return await self._end(event, key, report_id, "to:gone", now)
        if kind != "ok":
            return self._later("the journal did not give the report's file")
        found = _file_of(data)
        if found is None:
            return await self._end(event, key, report_id, "to:bad", now)
        name, raw = found
        if hashlib.sha256(raw).hexdigest() != event.fact("sha256").strip().lower():
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
        return self._later("OpenAlgo or Telegram could not take the file")

    async def _end(self, event: Event, key: str, report_id: int, code: str, now: float) -> float:
        """Record how a report ended, then tell the journal."""
        self.store.settle_notices([key], code, now)
        self._tries.pop((event.id, "text"), None)
        self._tries.pop((event.id, "file"), None)
        status = OUTCOMES[code][0]
        self.stats[status] += 1
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
                await asyncio.sleep(GIVE_WAY_S)
                continue
            wait = self.pacer.allowed_at(self.lane) - self.pacer.clock()
            if wait <= 0:
                return True
            await asyncio.sleep(min(wait, SLOT_POLL_S))
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

    def _later(self, why: str) -> float:
        delay = RETRY_S[min(self._failures, len(RETRY_S) - 1)]
        if self._failures == 0:
            log.info("A report is waiting: %s; trying again in %.0f s", why, delay)
        self._failures += 1
        return delay


def journal_url(environ: Mapping[str, str]) -> str:
    """The journal's local API: SUDARSHAN_JOURNAL_API_URL when it is plain http on this server."""
    url = (environ.get("SUDARSHAN_JOURNAL_API_URL") or JOURNAL_URL).rstrip("/")
    return url if url.startswith("http://") and is_loopback_url(url) else JOURNAL_URL


def report_desk_for(service, telegram, client: httpx.AsyncClient) -> ReportDesk | None:
    """The desk for a running AlertService, or None unless ``SUDARSHAN_JOURNAL=on`` (alerts.env).
    Never raises: reports never stop order alerts, so a missing pepper means no desk."""
    if not service.settings.nudge_journal:
        return None
    from sudarshan.vault import JOURNAL_DELIVERY_INFO, fernet_for, pepper_from

    try:
        fernet = fernet_for(pepper_from(os.environ), JOURNAL_DELIVERY_INFO)
    except Exception:
        log.exception("Reports are on but their requests to the journal cannot be sealed")
        return None
    spool = Path(os.environ.get("SUDARSHAN_JOURNAL_SPOOL") or JOURNAL_SPOOL)
    return ReportDesk(
        spool,
        service.store,
        JournalLink(journal_url(os.environ), fernet, client),
        telegram.send_with_reason,
        OpenAlgoFiles(service.settings.openalgo_url, client, telegram.owner, telegram.reload_owner),
        service._owner_ready,
        service.clock,
        service.outbox.pacer,
    )
