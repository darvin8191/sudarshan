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
  Quiet in market hours. While the market trades (09:15-15:40, or a listed day's own window) only
        a run that holds a failure (FAILURES) goes out; the rest wait for the close, still inside
        MAX_AGE_S. The owner's phone is for fills and rejections then.

M10.5 hook: once the process has one outbox.Pacer for every lane, pass ``pace=LanePace(pacer)``
where AlertService builds the desk (marked there); a notice is then the lowest priority in it.

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
FAILURES = frozenset({"rolled_back", "unit_refused"})
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
UNIT = re.compile(r"sudarshan-[a-z0-9-]+\.(?:service|timer)")
#: The order the facts of one run are told in.
KIND_ORDER = ("installed", "rolled_back", "unit_refused", "waiting")

#: Why an install was undone, in the owner's words (the cause only; ``rolled_back`` adds the rest).
ROLLBACK_CAUSES = {
    "health": "the app did not come up healthy",
    "migrations": "a database update did not finish",
    "settings": "a settings update did not finish",
}
ROLLBACK_OTHER = "the update stopped unexpectedly"


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
        return [
            f"{start}, and the earlier version did not start either. SUDARSHAN is not running. "
            "The server needs attention now."
        ]
    was = _release(event.fact("was"))
    back = f"{was} is back" if was else "the earlier version is back"
    return [f"{start}, so {back}. Nothing for you to do; the release will be looked at."]


def _refused(events: list[Event]) -> list[str]:
    units = sorted({e.fact("unit") for e in events if UNIT.fullmatch(e.fact("unit"))})
    tag = next((t for e in events if (t := _release(e.fact("tag")))), "the new release")
    if not units:
        return []
    if len(units) == 1:
        what, they = f"A background service in {tag} was not installed: {units[0]}.", "It"
    else:
        names = ", ".join(units)
        what, they = (
            f"{len(units)} background services in {tag} were not installed: {names}.",
            "They",
        )
    return [
        f"{what} {they} did not pass the server's safety check. "
        "The rest of the release is in place. Nothing for you to do; it will be looked at."
    ]


def _waiting(event: Event, now: datetime) -> list[str]:
    tag = _release(event.fact("tag")) or "A new release"
    return [
        f"{tag} is ready. It will be installed when the market is closed. Nothing for you to do."
    ]


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
        make = {"installed": _installed, "rolled_back": _rolled_back, "waiting": _waiting}[kind]
        for event in of_kind:
            lines += make(event, now)
    return lines


def compose(events: Sequence[Event], now: datetime) -> str:
    """The Telegram message: the brand line, then each run's lines in the order they happened."""
    runs: dict[str, list[Event]] = {}
    for event in sorted(events, key=lambda e: (e.at, e.id)):
        runs.setdefault(event.run, []).append(event)
    lines: list[str] = []
    for run in runs.values():
        lines += run_lines(run, now)
    return "\n".join([BRAND, *lines])


class Pace(Protocol):
    """What the desk asks before a send, and tells it after: when the next may start.

    ``now`` is the service clock in seconds; a pace that keeps its own time may ignore it.
    """

    def wait(self, now: float) -> float:
        """Seconds until the next message may start; 0 when it may start now."""

    def took(self, now: float) -> None:
        """A message is starting now."""


class NoticePace:
    """The notices' own pace, until they share the process's Telegram slots (see LanePace).

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
    """M10.5 hook, not wired yet: the notices' place in the one outbox.Pacer of the process.

    The Pacer (alerts/outbox.py, with the market alerts lanes) hands out the send slots of every
    lane: one gap and one minute budget for all, so a notice can make a fill wait one gap and no
    more. A notice is the lowest priority there: it takes no slot while order alerts wait. Wire
    it where AlertService builds the desk: ``pace=LanePace(outbox.pacer)``.
    """

    LANE = "notices"

    def __init__(self, pacer, lane: str = LANE) -> None:
        self.pacer, self.lane = pacer, lane

    def wait(self, now: float) -> float:
        if self.pacer.orders_waiting():
            return GIVE_WAY_S
        return max(0.0, self.pacer.allowed_at(self.lane) - time.monotonic())

    def took(self, now: float) -> None:
        self.pacer.took(self.lane)


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
            return wait + 0.5
        if not events:
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

    def _unsent(self, now: float) -> list[Event]:
        """The notices not told yet, oldest first; the ones too old to matter are marked."""
        seen: dict[str, Event] = {}
        for spool in self.spools:
            for event in read_events(spool):
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
        urgent = {e.run for e in events if e.kind in FAILURES}
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
