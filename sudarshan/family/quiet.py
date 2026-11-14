"""Quiet hours, the market's hours they lean on, and the member's notification settings (M10.9,
M20.4; design 2.6, 3.1, 6.4).

**The market's hours.** ``MarketHours`` gives a day's market session from the exchange calendar
the family keeps (Y1) and ``sessions.py``: from the first session's start to the last close (09:15
to 15:40; 15:45 from 7 Dec 2026; a special day its own window), with no pre-open, and none on a
holiday. The bot's ``/status`` says from it whether the market is open and which day trades next.

**Quiet hours** (``until``), per member and per kind of message:

    security   a sign-in from a new phone, a locked sign-in: never quiet
    test       a test the member asked for: never quiet
    digest     the day's summary: quiet inside the member's window when ``quiet.digest`` is on
    alerts     (later) the same with ``quiet.alerts``

A child member is quiet outside the market session as well, whatever their window says (decision
7). A window is "from" to "to" in India's time, across midnight when "from" is later ("21:00" to
"08:00", the default); the same time twice is no window. A message that falls in quiet hours waits
until they end if its time to live still covers that, and is dropped (with an audit line) if not:
the outbox decides that with ``until``.

**Settings** (``GET`` and ``PUT /family/api/settings``): the member's language, quiet hours, the
summary by push and by Telegram, whether Telegram is linked, and the member row's version (a change
names the version it was decided on). Sign-in notices are not a choice: always on, by every channel
the member has.

Pure but for the settings routes, which read and write the store.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sudarshan.alerts.market import sessions
from sudarshan.autologin.schedule import IST

from . import audit
from .bot_words import PAGE, Refused
from .contract import LANGUAGES
from .errors import Invalid, VersionConflict
from .listener import Response

#: How far ahead a next session is looked for (a long holiday run is under two weeks).
HORIZON_DAYS = 15
DEFAULT_QUIET: dict[str, Any] = {"from": "21:00", "to": "08:00", "digest": True, "alerts": True}
DEFAULT_NOTIFY: dict[str, bool] = {"digest_push": True, "digest_telegram": True}
#: The kinds of message quiet hours never hold.
NEVER_QUIET = frozenset({"security", "test"})
CLOCK = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")
DAY_MIN = 24 * 60


def day_of(at_ms: int) -> date:
    """The day in India an instant falls on."""
    return datetime.fromtimestamp(at_ms / 1000, UTC).astimezone(IST).date()


def at_minute(day: date, minute: int) -> int:
    """``minute`` minutes after midnight IST on ``day``, in epoch ms."""
    return sessions.epoch_ms(day, minute)


def minute_of(at_ms: int) -> int:
    at = datetime.fromtimestamp(at_ms / 1000, UTC).astimezone(IST)
    return at.hour * 60 + at.minute


class MarketHours:
    """The market session of each day, from the calendar (see the module's words)."""

    def __init__(self, calendar: Any):
        self.calendar = calendar

    def trading(self, day: date) -> bool:
        return bool(self.calendar.is_trading_day(day))

    def span(self, day: date) -> tuple[int, int] | None:
        """The day's market session in epoch ms (start included, end not); None: no session."""
        found = sessions.sessions_of(day, trading_day=self.trading(day),
                                     window=self.calendar.day_window(day))  # fmt: skip
        spans = [s for key, s in found.items() if sessions.AUCTION not in key]
        if not spans:
            return None
        return min(s.start for s in spans), max(s.end for s in spans)

    def is_open(self, now: int) -> bool:
        span = self.span(day_of(now))
        return span is not None and span[0] <= now < span[1]

    def next_open(self, now: int) -> int | None:
        """The start of the first session that starts after ``now``."""
        day = day_of(now)
        for offset in range(HORIZON_DAYS + 1):
            span = self.span(day + timedelta(days=offset))
            if span is not None and span[0] > now:
                return span[0]
        return None

    def next_day(self, day: date) -> date | None:
        """The first trading day after ``day``."""
        for offset in range(1, HORIZON_DAYS + 1):
            if self.span(day + timedelta(days=offset)) is not None:
                return day + timedelta(days=offset)
        return None


# ---- quiet hours -------------------------------------------------------------------------------
def clock_minutes(text: str) -> int:
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


def quiet_of(member: dict[str, Any]) -> dict[str, Any]:
    """The member's quiet hours: what they chose, the defaults for what they did not (a stored
    value that is not one is read as the default)."""
    stored = member.get("quiet") if isinstance(member.get("quiet"), dict) else {}
    out = dict(DEFAULT_QUIET)
    for name in ("from", "to"):
        if isinstance(stored.get(name), str) and CLOCK.fullmatch(stored[name]):
            out[name] = stored[name]
    for name in ("digest", "alerts"):
        if isinstance(stored.get(name), bool):
            out[name] = stored[name]
    return out


def notify_of(member: dict[str, Any]) -> dict[str, bool]:
    stored = member.get("notify") if isinstance(member.get("notify"), dict) else {}
    return {
        k: stored[k] if isinstance(stored.get(k), bool) else v for k, v in DEFAULT_NOTIFY.items()
    }


def in_window(minute: int, start: int, end: int) -> bool:
    """Whether a minute of the day is inside a window that may run across midnight."""
    if start == end:
        return False
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def window_end(now: int, start: int, end: int) -> int:
    """When a window ``now`` is inside ends: today's or tomorrow's "to", in epoch ms."""
    day = day_of(now)
    if start > end and minute_of(now) >= start:
        day += timedelta(days=1)
    return at_minute(day, end)


def until(member: dict[str, Any], category: str, now: int, market: MarketHours) -> int | None:
    """When quiet hours end for a message of ``category`` due ``now``; None when it may go now.
    A child is quiet outside the market session too; the two rules are asked in turn until
    neither holds (a window that ends inside a closed market waits for the next session)."""
    if category in NEVER_QUIET:
        return None
    quiet, child = quiet_of(member), member.get("role") == "child"
    start, end = clock_minutes(quiet["from"]), clock_minutes(quiet["to"])
    at = now
    for _ in range(8):
        ends = []
        if quiet.get(category) is True and in_window(minute_of(at), start, end):
            ends.append(window_end(at, start, end))
        if child and not market.is_open(at):
            ends.append(market.next_open(at) or at + HORIZON_DAYS * DAY_MIN * 60_000)
        if not ends:
            break
        at = max(ends)
    return at if at > now else None


# ---- the member's settings (the viewer API) --------------------------------------------------
def clean_quiet(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != set(DEFAULT_QUIET):
        raise Refused("invalid", PAGE["quiet_rule"], field="quiet")
    if not all(isinstance(raw[n], str) and CLOCK.fullmatch(raw[n]) for n in ("from", "to")):
        raise Refused("invalid", PAGE["quiet_rule"], field="quiet")
    if not all(isinstance(raw[n], bool) for n in ("digest", "alerts")):
        raise Invalid(field="quiet")
    return {n: raw[n] for n in DEFAULT_QUIET}


def clean_notify(raw: Any) -> dict[str, bool]:
    if not isinstance(raw, dict) or set(raw) != set(DEFAULT_NOTIFY):
        raise Invalid(field="notify")
    if not all(isinstance(raw[n], bool) for n in DEFAULT_NOTIFY):
        raise Invalid(field="notify")
    return {n: raw[n] for n in DEFAULT_NOTIFY}


class SettingsRoutes:
    """``GET /settings`` and ``PUT /settings`` for the member asking."""

    def __init__(self, service: Any):
        self.service = service

    async def route_settings(self, request: Any, args: dict, session: Any) -> Response:
        member_id = session.member_id

        def read(tx) -> dict[str, Any]:
            member, link = tx.member(member_id), tx.row("telegram_link", member_id=member_id)
            return settings_of(member, bool(link and link.get("chat_id_enc")))

        return Response.ok(await self.service.store.read(read))

    async def route_settings_set(self, request: Any, args: dict, session: Any) -> Response:
        version = args.get("version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise Invalid(field="version")
        values: dict[str, Any] = {}
        if "language" in args:
            if args["language"] not in LANGUAGES:
                raise Invalid(field="language")
            values["language"] = args["language"]
        if "quiet" in args:
            values["quiet"] = clean_quiet(args["quiet"])
        if "notify" in args:
            values["notify"] = clean_notify(args["notify"])
        member_id, now = session.member_id, self.service.clock()

        def change(tx) -> None:
            member = tx.member(member_id)
            changed = {k: v for k, v in values.items() if v != member.get(k)}
            if not changed:
                if member["version"] != version:
                    raise VersionConflict()
                return
            tx.member_change(member_id, version, changed, now)
            audit.record(tx, at=now, actor=audit.member_actor(member_id), kind="member_updated",
                         member_id=member_id, session=session.id_hash,
                         detail={"fields": sorted(changed), "via": "settings"})  # fmt: skip

        await self.service.store.write(change)
        return Response.ok({"ok": True, "words": None})


def settings_of(member: dict[str, Any], linked: bool) -> dict[str, Any]:
    """The contract's ``Settings`` for a member row."""
    return {"language": member.get("language") or "en", "quiet": quiet_of(member),
            "notify": notify_of(member), "telegram_linked": linked,
            "version": member["version"]}  # fmt: skip
