"""The owner's desk (M10.1, M10.3, M10.4, M10.14): what the local API's ops do to market alerts.

The ops run in the alert service's loop beside the ticks, and every market.db call goes through
the runtime's one writer thread, so an owner's change and a fire are recorded in the order they
were decided (review A7.k). Changes are made one at a time and reach the Book only after the
store has accepted them (A7.m):

- the store first: an edit keeps a stop the engine made a moment before (store.update), and a
  pause, resume or restart is accepted only from the status it is for;
- then the Book (runtime.owner_put): a fresh state when the condition changed, the state resumed
  on a resume or restart, the kept one otherwise. A new, changed or resumed alert takes its side
  from the latest snapshot, so it fires on nothing it did not see happen;
- a key the feed does not hold yet (or not in the mode needed) is claimed before the alert is
  saved, while no renewal can drop it. The feed's refusal (its quota) or silence is said in
  words, and nothing is saved.

Limits (the owner's decision 5): at most ``max_instruments`` instruments and ``max_active``
Active alerts. A change that would add past either is refused in words; one that adds nothing is
never refused, even past a limit lowered since.

The page's status lines (review A7.h) are clock.status for the alert's own session (a stock that
closes by auction has its own) with the feed's last health in words.check_words, never a tick's
check: an untraded option fails every tick as outside hours all day. While market alerts are
paused (the feed down, unheard or silent), every line inside a session says so.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sudarshan.alerts.market import clock, spec, words
from sudarshan.alerts.market.api import OWNER, SERVICE, ApiError
from sudarshan.alerts.market.book import BASE_CHANGED
from sudarshan.alerts.market.engine import NOT_CHECKED, NOT_JUDGED, Reason, stop_reason
from sudarshan.alerts.market.feedlink import FeedRefused, FeedUnreachable
from sudarshan.alerts.market.sessions import AUCTION, next_session
from sudarshan.alerts.market.store import MOVES, NotFound, Refused, StoreError, VersionConflict
from sudarshan.alerts.messages import quantity
from sudarshan.autologin.schedule import IST

log = logging.getLogger("sudarshan.alerts.market")

#: The test waits this long for Telegram's answer; one still pending then is in History later.
TEST_WAIT_S = 10.0
#: History pages: the page asks for up to this many at a time.
PAGE, PAGE_MAX = 50, 100
SINCE, SINCE_MAX = 200, 200
ACTIONS = ("edit", "pause", "resume", "restart")
#: What the service takes from a draft; the bridge adds the key, tick size and expiry.
DRAFT = (
    "symbol", "exchange", "instrument_key", "tick_size", "expiry", "kind", "params", "frequency",
    "candle", "candle_min", "cooldown_s", "ends", "note", "channels", "price",
)  # fmt: skip
#: A status line's codes, best first: the engine's line is the best of its sessions' lines.
ORDER = (
    clock.OK, clock.CLOSING_AUCTION, clock.FEED_DOWN, clock.NOT_OPEN_YET,
    clock.CLOSED_BY_EXCHANGE, clock.OUTSIDE_HOURS, clock.CLOSED_TODAY,
)  # fmt: skip
#: The sessions the engine's line follows while no alert is Active.
MAIN = ("NSE_EQ", "NSE_FO", "NSE_INDEX")
#: Notes worth a line on the card; one that waits for the feed comes from the Book while it lasts.
NOTED = frozenset({NOT_CHECKED, NOT_JUDGED, BASE_CHANGED})
WORDS = {
    "starting": "Market alerts are starting. Try again in a moment.",
    "stopping": "The alert service is stopping. Try again in a minute.",
    "instruments": "Alerts can follow at most {n} instruments at once. Delete alerts on an "
    "instrument you no longer need, or add this alert to an instrument that already has one.",
    "active": "You can keep at most {n} alerts Active at once. Pause or delete one you no longer "
    "need.",
    "feed_full": "The price feed cannot take another instrument right now. Delete an alert on an "
    "instrument you no longer need, then try again.",
    "feed_silent": "The price feed is not answering, so this was not saved. Try again in a minute.",
    "ended": "This alert's end date has passed. Pick a later one, or no end.",
    "draft": "The alert could not be read. Open the form again, then save.",
    "number": "This alert could not be found on the page. Open it again.",
    "action": "Pick Edit, Pause, Resume or Restart.",
    "prefs": "Pick where alerts go: Telegram on or off, and the app on or off.",
}


class OwnerDesk:
    """The API's desk while market alerts are switched on: ``api.Desk`` over the runtime."""

    def __init__(self, runtime):
        self.rt = runtime
        self._changes = asyncio.Lock()

    def health(self) -> dict[str, Any]:
        rt = self.rt
        return {"service": SERVICE, "market_alerts": "on", "state": rt.state,
                "alerts_active": len(rt.book.alerts()), "instruments": len(rt.book.modes())}  # fmt: skip

    async def call(self, op: str, message: Mapping[str, Any]) -> Any:
        return await getattr(self, f"_{op}")(message)

    # ---- reading -------------------------------------------------------------------------
    async def _state(self, message) -> dict[str, Any]:
        rt = self.rt
        now = rt.now_ms()
        state = {"enabled": True, "engine": self.engine_line(now), "segments": {},
                 "limits": self._limits(), "prefs": rt.prefs, "alerts": [], "last_event_id": 0,
                 "server_time": now}  # fmt: skip
        if not self._running(raising=False):
            return state
        store = rt.store

        def read():
            newest = store.events(OWNER, limit=1)
            return store.alerts(OWNER), store.states(OWNER), newest[0]["id"] if newest else 0

        rows, saved, state["last_event_id"] = await self._write(read)
        views = [view for row in rows if (view := self._view(row, now, saved.get(row["id"])))]
        state["alerts"] = views
        for segment in sorted({view["instrument_key"].partition("|")[0] for view in views}):
            state["segments"][segment] = self.line(segment, now)
        return state

    async def _events(self, message) -> dict[str, Any]:
        rt = self._running()
        limit = _count(message, "limit", PAGE, PAGE_MAX)
        rows = await self._write(rt.store.events, OWNER, before=_whole(message, "before", None),
                                 limit=limit + 1, alert_id=_whole(message, "alert", None))  # fmt: skip
        return {"events": [_event(row) for row in rows[:limit]], "more": len(rows) > limit}

    async def _events_since(self, message) -> dict[str, Any]:
        rt = self._running()
        limit = _count(message, "limit", SINCE, SINCE_MAX)
        rows = await self._write(rt.store.events_since, OWNER, _whole(message, "after"), limit + 1)
        return {"events": [_event(row) for row in rows[:limit]], "more": len(rows) > limit}

    async def _prefs_get(self, message) -> dict[str, bool]:
        rt = self._running()
        return await self._write(rt.store.prefs, OWNER)

    # ---- changing ------------------------------------------------------------------------
    async def _create(self, message) -> dict[str, Any]:
        rt = self._running()
        fields = _draft(message.get("alert"))
        now = rt.now_ms()
        checked = _checked(fields, now)
        async with self._changes:
            probe = _probe(checked, 0, "active")
            self._admit(probe, None)
            async with rt.claims_held():
                try:
                    await self._claim(probe)
                    row = await self._write(rt.store.create, OWNER, checked, now)
                finally:
                    rt.wake_claims()  # a claim made for a refused change goes at once
                rt.owner_put(row)
        log.info("market alert %d set by the owner", row["id"])
        return self._view(row, now)

    async def _update(self, message) -> dict[str, Any]:
        rt = self._running()
        alert_id, version = _whole(message, "id"), _whole(message, "version")
        action = message.get("action")
        if action not in ACTIONS:
            raise ApiError(400, "invalid", WORDS["action"])
        now = rt.now_ms()
        async with self._changes:
            current = await self._write(rt.store.alert, OWNER, alert_id)
            if current is None:
                raise ApiError(404, "gone", str(NotFound()))
            if action == "edit":
                row = await self._edit(current, version, _draft(message.get("alert")), now)
            else:
                row = await self._move(current, version, action, now)
        log.info("market alert %d: %s by the owner", alert_id, action)
        return self._view(row, now)

    async def _edit(self, current: dict, version: int, fields: dict, now: int) -> dict:
        rt = self.rt
        checked = _checked(fields, now)
        if "ends" not in fields:  # an edit that does not name the end keeps it
            checked["ends_at"] = current["ends_at"]
        changed = spec.changes_condition(current, checked)
        status = spec.status_after_edit(current["status"], changed)
        if status == "active" and checked["ends_at"] is not None and checked["ends_at"] <= now:
            raise ApiError(400, "invalid", WORDS["ended"], "ends")
        probe = _probe(checked, current["id"], status)
        self._admit(probe, current["id"])
        async with rt.claims_held():
            try:
                if status == "active":
                    await self._claim(probe)
                row = await self._write(rt.store.update, OWNER, current["id"], version, checked,
                                        now, reset_state=changed)  # fmt: skip
            finally:
                rt.wake_claims()
            rt.owner_put(row, reset=changed)
        return row

    async def _move(self, current: dict, version: int, action: str, now: int) -> dict:
        rt = self.rt
        resumes = action in ("resume", "restart")
        async with rt.claims_held():
            try:
                if resumes and current["status"] == MOVES[action][0]:
                    probe = spec.Alert.of(current | {"status": "active"})
                    if stop_reason(probe, now) is None:  # else the store says why, in words
                        self._admit(probe, current["id"])
                        await self._claim(probe)
                row = await self._write(getattr(rt.store, action), OWNER, current["id"], version,
                                        now)  # fmt: skip
            finally:
                rt.wake_claims()
            rt.owner_put(row, resume=resumes)
        return row

    async def _delete(self, message) -> dict[str, bool]:
        rt = self._running()
        alert_id = _whole(message, "id")
        async with self._changes:
            if not await self._write(rt.store.delete, OWNER, alert_id):
                raise ApiError(404, "gone", str(NotFound()))
            rt.owner_drop(alert_id)
        log.info("market alert %d deleted by the owner", alert_id)
        return {"deleted": True}

    async def _prefs_set(self, message) -> dict[str, bool]:
        rt = self._running()
        telegram, app = message.get("telegram"), message.get("app")
        if not isinstance(telegram, bool) or not isinstance(app, bool):
            raise ApiError(400, "invalid", WORDS["prefs"], "channels")
        async with self._changes:
            prefs = await self._write(rt.store.set_prefs, OWNER, telegram=telegram, app=app,
                                      now=rt.now_ms())  # fmt: skip
            rt.use_prefs(prefs)
        return prefs

    async def _test(self, message) -> dict[str, Any]:
        """One test line through each channel that is on in the settings (M10.14), logged like a
        fire so History shows its deliveries; Telegram's answer is waited for up to 10 s."""
        rt = self._running()
        now = rt.now_ms()
        prefs = rt.prefs
        channels = {"telegram": prefs["telegram"]}
        event_id = await self._write(rt.store.record_test, OWNER, words.TEST_TITLE,
                                     words.TEST_LINE, now, channels)  # fmt: skip
        if channels["telegram"]:
            try:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(rt.tell(event_id, words.TEST_LINE, now), TEST_WAIT_S)
            finally:
                rt.forget_waiter(event_id)
        found = await self._write(rt.store.events, OWNER, before=event_id + 1, limit=1)
        deliveries = found[0]["deliveries"] if found and found[0]["id"] == event_id else {}
        log.info("a market alert test was sent from the Alerts page")
        return {"event_id": event_id, "deliveries": deliveries}

    # ---- the page's lines ----------------------------------------------------------------
    def engine_line(self, now: int) -> dict[str, Any]:
        """The line under "Alerts": the best of the lines of the sessions with Active alerts."""
        rt = self.rt
        if rt.state == "starting":
            return _line("starting", words.STARTING)
        if rt.state != "running":
            return _line("off", rt.detail or words.STOPPED_UNEXPECTEDLY)
        market, classes = rt.market, {}
        for alert in rt.book.alerts():
            cas = alert.key in market.auction
            classes.setdefault(alert.segment + AUCTION if cas else alert.segment, []).append(alert)
        lines = [
            self.line(cls, now, printed=all(rt.book.printed(a.key, market.today) for a in held))
            for cls, held in classes.items()
        ] or [self.line(segment, now) for segment in MAIN]
        return min(lines, key=lambda line: ORDER.index(line["code"]))

    def line(self, cls: str, now: int, *, quotes: bool = False, printed: bool = False) -> dict:
        """One session's line: 'Watching · Real-time', 'Market closed · checks from Mon 09:15',
        'Not checking since 10:41:05 · the price feed is reconnecting'."""
        rt = self.rt
        market = rt.market
        code = clock.status(market, cls, now, printed=printed)
        if code in (clock.OK, clock.CLOSING_AUCTION) and rt.down_since is not None:
            code = clock.FEED_DOWN  # paused: silent, or its health overdue (review A7.e)
        opens = auction = None
        if code in (clock.CLOSED_TODAY, clock.OUTSIDE_HOURS):
            opens = self._opens(cls, now)
        if code == clock.CLOSING_AUCTION:  # "price at about 15:30" (X6j)
            session = market.sessions[cls]
            auction = (session.end, session.auction_close)
        text = words.check_words(code, now=now, opens_at=opens, since=rt.down_since,
                                 feed=market.feed, auction=auction, quotes=quotes)  # fmt: skip
        return _line(code, text, code == clock.OK)

    def check(self, alert: spec.Alert, now: int) -> dict[str, Any] | None:
        """An Active alert's own line when it is not simply watching: its session's line, what
        it waits for from the feed, or a note from today; None while it watches."""
        rt = self.rt
        cas = alert.key in rt.market.auction
        quotes = alert.cond.source != "ltp"
        printed = cas and rt.book.printed(alert.key, rt.market.today)
        line = self.line(alert.segment + AUCTION if cas else alert.segment, now, quotes=quotes,
                         printed=printed)  # fmt: skip
        if line["code"] != clock.OK:
            return line
        waiting = rt.book.waiting(alert.id)
        if waiting is not None:
            return _line("waiting", words.note_words(Reason(waiting, now)), True)
        if quotes and (rt.last_frame(alert.key) or {}).get(alert.cond.source) is None:
            return _line("waiting", words.quote_waiting(alert.cond.source), True)  # A7.i
        noted = rt.note_of(alert.id)
        if noted is not None and noted[0].code in NOTED and _day(noted[1]) == _day(now):
            return _line("note", words.note_words(noted[0]), True)
        return None

    def _opens(self, cls: str, now: int) -> int | None:
        segment, _, cas = cls.partition("+")
        days = self.rt.days
        found = next_session(segment, now, is_trading_day=days.calendar.is_trading_day,
                             window_of=days.window, auction=bool(cas))  # fmt: skip
        return found.start if found is not None else None

    # ---- inside --------------------------------------------------------------------------
    def _view(self, row: dict, now: int, saved=None) -> dict[str, Any] | None:
        """One alert for the page (the contract's AlertRow); None for a row this release
        cannot read (it is skipped, as the runtime skips it)."""
        rt = self.rt
        try:
            alert = spec.Alert.of(row)
        except Exception:
            log.warning("market alert %s could not be read; it is left off the page", row.get("id"))
            return None
        state = rt.book.state(alert.id) or saved
        today = rt.market.today
        session = rt.market.sessions.get(alert.segment + AUCTION)
        auction = None
        if alert.key in rt.market.auction and session is not None:  # X6m
            auction = {"from": session.end, "close": session.auction_close,
                       "until": session.auction_until}  # fmt: skip
        return {
            **{name: row[name] for name in (
                "id", "version", "status", "kind", "symbol", "exchange", "instrument_key",
                "tick_size", "expiry", "params", "frequency", "candle_min", "cooldown_s",
                "ends_at", "channels", "created_at", "updated_at")},
            "status_reason": row["status_reason"] or "",
            "note": row["note"] or "",
            "words": {
                "condition": words.condition(alert),
                "meta": words.meta(alert, _day(now)),
                "checks_at": words.checks_at(alert),
            },
            "check": self.check(alert, now) if alert.status == "active" else None,
            "fired_today": state.fires_session if state and state.session == today else 0,
            "auction": auction,
        }  # fmt: skip

    def _limits(self) -> dict[str, int]:
        rt = self.rt
        return {"instruments": rt.config.max_instruments, "instruments_used": len(rt.book.modes()),
                "active": rt.config.max_active, "active_used": len(rt.book.alerts())}  # fmt: skip

    def _admit(self, after: spec.Alert, before: int | None) -> None:
        """Refuse a change that would add past a limit; one that adds nothing passes."""
        rt, config = self.rt, self.rt.config
        if after.status != "active":
            return
        active = {alert.id: alert for alert in rt.book.alerts()}
        if before not in active and len(active) + 1 > config.max_active:
            raise ApiError(409, "limit", WORDS["active"].format(n=quantity(config.max_active)))
        if after.key not in rt.book.modes():
            keys = {alert.key for alert in active.values() if alert.id != before} | {after.key}
            if len(keys) > config.max_instruments:
                n = quantity(config.max_instruments)
                raise ApiError(409, "limit", WORDS["instruments"].format(n=n))

    async def _claim(self, alert: spec.Alert) -> None:
        """The alert's key at the feed, in the mode it needs, before the alert is saved: full for
        a stock that closes by auction, whose print is known by its volume (X6i)."""
        rt = self.rt
        modes = rt.book.modes(full=rt.market.auction)
        held = modes.get(alert.key)
        need = "full" if rt.closes_by_auction(alert) else alert.cond.feed
        if held in ("full", need):
            return
        modes[alert.key] = "full" if "full" in (held, need) else need
        try:
            await rt.link.put_claims(modes)
        except FeedRefused as exc:
            log.warning("The price feed refused an instrument for a market alert: %s", exc)
            raise ApiError(409, "feed", WORDS["feed_full"]) from None
        except FeedUnreachable:
            raise ApiError(503, "feed", WORDS["feed_silent"]) from None

    async def _write(self, fn, *args, **kwargs):
        """One market.db call on the runtime's writer, its refusals in the owner's words."""
        try:
            return await self.rt.write(fn, *args, **kwargs)
        except NotFound as exc:
            raise ApiError(404, "gone", str(exc)) from None
        except VersionConflict as exc:
            raise ApiError(409, "conflict", str(exc)) from None
        except Refused as exc:
            raise ApiError(409, "refused", str(exc)) from None
        except StoreError as exc:
            log.error("market.db refused an owner's request: %s", exc.cause)
            raise ApiError(503, "record", str(exc)) from None

    def _running(self, raising: bool = True):
        rt = self.rt
        if rt.state == "running" and None not in (rt.store, rt.link, rt.lane):
            return rt
        if not raising:
            return None
        if rt.state == "starting":
            raise ApiError(503, "starting", WORDS["starting"])
        if rt.state == "off":
            raise ApiError(503, "stopped", rt.detail or words.STOPPED_UNEXPECTEDLY)
        raise ApiError(503, "stopping", WORDS["stopping"])


def _line(code: str, text: str, real_time: bool = False) -> dict[str, Any]:
    return {"code": code, "line": text, "real_time": real_time}


def _probe(fields: Mapping[str, Any], alert_id: int, status: str) -> spec.Alert:
    """The alert a change would make, for its limits and its claim."""
    return spec.Alert.of(dict(fields, id=alert_id, version=1, status=status))


def _draft(value) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ApiError(400, "invalid", WORDS["draft"])
    return {name: value[name] for name in DRAFT if name in value}


def _checked(fields: Mapping[str, Any], now: int) -> dict[str, Any]:
    try:
        return spec.check(fields, now)
    except spec.SpecError as exc:
        raise ApiError(400, "invalid", exc.message, exc.field) from None


_REQUIRED = object()


def _whole(message: Mapping[str, Any], name: str, default=_REQUIRED) -> int | None:
    value = message.get(name)
    if value is None and default is not _REQUIRED:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ApiError(400, "invalid", WORDS["number"])
    return value


def _count(message: Mapping[str, Any], name: str, default: int, most: int) -> int:
    value = _whole(message, name, default)
    return max(1, min(value, most))


def _event(row: Mapping[str, Any]) -> dict[str, Any]:
    """One History entry (the contract's AlertEvent)."""
    return {
        "id": row["id"], "alert_id": row["alert_id"], "at": row["fired_at"],
        "trade_time": row["trade_time"], "symbol": row["symbol"], "exchange": row["exchange"],
        "kind": row["kind"], "title": row["title"], "line": row["line"], "value": row["value"],
        "level": row["level"], "found_later": row["found_later"], "opening": row["opening"],
        "test": row["test"], "deliveries": row["deliveries"],
    }  # fmt: skip


def _day(ms: int):
    return datetime.fromtimestamp(ms / 1000, IST).date()
