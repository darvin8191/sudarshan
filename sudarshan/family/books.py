"""The family's books: positions, holdings, orders, trades and funds as a viewer's screens show
them, and the data plane's pieces (M6.7; design 2.4, 6.2; cross-design Y5, Y6, Y11, Y44).

**When it reads.** Only while a viewer screen that shows the part is open (each socket names its
screen) and inside the part's market session, from the calendar copy the family keeps (Y1) and
``sessions.py``:

    positions, orders, trades   the day's sessions, from the pre-open to the last close (09:00 to
                                15:40; 15:45 from 7 Dec 2026; a special day its own window)
    holdings                    the cash session with its pre-open (09:00 to 15:30)
    funds                       any time but 00:00 to 05:30 IST, when Upstox gives none

    | part                             | first read       | re-sync | event (nudge.py)           |
    | positions, orders, trades, funds | the screen opens | 45 s    | every live nudge, Y5       |
    | holdings                         | the screen opens | 5 min   | a nudge of kind holdings   |

A screen open while its market is closed reads once ("Market closed · figures from 15:40:12"); one
left open across the close reads once more after it. Between reads, prices, values and P&L move
only with ticks: a read's figures are the anchor at ``read_at``.

**What it keeps.** Each member's last answer of each part, in memory only: no figure of the owner's
is written to family.db (M19.11, Y44). A member reads as themselves (principal ``viewer:<id>``)
with their own mask, so one member's answer never serves another; the portfolio's five seconds of
memory makes the reads of several members one Upstox call.

**What the phone gets** (the contract's ``Book``): the portfolio's own rows with the member's mask
applied once more on the way out (``privacy.masked``: a removed field is absent, never 0), ``side``
(long, short or closed) on a position whose quantity reached this service, and the read state with
its sentence when the figures are not current (``words_for``). After every read the member's
sockets that show the part are told (Y11, ``nudge.announce``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from cryptography.fernet import Fernet

from sudarshan.alerts.market import sessions
from sudarshan.autologin.schedule import IST, TradingCalendar
from sudarshan.tasks import cancel_and_wait

from . import privacy
from .config import seal_key
from .contract import BOOK_PARTS
from .errors import Invalid
from .listener import Response
from .nudge import NUDGE_EVERY_MS, Nudges, announce
from .portfolio_client import WORDS, PortfolioClient, Refusal

log = logging.getLogger("sudarshan.family")

#: The parts each screen shows (the phone names its screen on the socket).
SCREEN_PARTS: dict[str, tuple[str, ...]] = {
    "dashboard": ("positions", "funds"),
    "positions": ("positions",),
    "holdings": ("holdings",),
    "orders": ("orders",),
    "trades": ("trades",),
}
RESYNC_MS = {"positions": 45_000, "orders": 45_000, "trades": 45_000, "funds": 45_000,
             "holdings": 300_000}  # fmt: skip
#: The portfolio's memory: a screen that opens reads unless the figures are newer than this.
FRESH_MS = 5_000
FUNDS_OPENS_MIN = 5 * 60 + 30
#: How often the re-sync looks (a screen opening or a nudge wakes it at once).
LOOP_S = 1.0
#: A member's part that no open screen has shown for this long is forgotten.
KEEP_MS = 6 * 3_600_000
ROW_PARTS = ("positions", "holdings", "orders", "trades")


def clock_text(ms: int) -> str:
    """An instant as a member reads it in India: 15:40:12."""
    return datetime.fromtimestamp(ms / 1000, UTC).astimezone(IST).strftime("%H:%M:%S")


def minute_of_day(ms: int) -> int:
    at = datetime.fromtimestamp(ms / 1000, UTC).astimezone(IST)
    return at.hour * 60 + at.minute


def words_for(part: str, state: str, read_at: int | None) -> str | None:
    """The sentence a book carries for its state (design 2.4, 6.7); None when it is current."""
    at = clock_text(read_at) if read_at is not None else None
    if state == "ok":
        return None
    if state == "closed":
        if part == "funds":
            return "Funds are available from 5:30 AM." + (f" Figures from {at}." if at else "")
        return f"Market closed · figures from {at}" if at else "Market closed"
    if state == "budget":
        return f"Figures from {at}. They refresh in a minute." if at else (
            "No figures yet. They refresh in a minute.")  # fmt: skip
    if state == "unreachable":
        return WORDS["unreachable"] + (f" Figures from {at}." if at else "")
    return WORDS[state]


class Market:
    """The parts' sessions from the exchange calendar (``is_trading_day``, ``day_window``)."""

    def __init__(self, calendar: Any):
        self.calendar = calendar

    @staticmethod
    def day_of(now: int) -> date:
        return datetime.fromtimestamp(now / 1000, UTC).astimezone(IST).date()

    def span(self, part: str, day: date) -> tuple[int, int] | None:
        """The part's session on ``day`` in epoch ms, its pre-open included; None: no session."""
        found = sessions.sessions_of(day, trading_day=bool(self.calendar.is_trading_day(day)),
                                     window=self.calendar.day_window(day))  # fmt: skip
        if part == "holdings":
            spans = [found["NSE_EQ"]] if "NSE_EQ" in found else []
        else:
            spans = [s for key, s in found.items() if sessions.AUCTION not in key]
        if not spans:
            return None
        pre_open = sessions.PRE_OPEN_MIN * 60_000
        return min(s.start for s in spans) - pre_open, max(s.end for s in spans)

    def open(self, part: str, now: int) -> bool:
        if part == "funds":
            return minute_of_day(now) >= FUNDS_OPENS_MIN  # never 00:00 to 05:29:59 IST
        span = self.span(part, self.day_of(now))
        return span is not None and span[0] <= now < span[1]

    def last_end(self, part: str, now: int) -> int | None:
        """The end of the part's latest session that ended at or before ``now``."""
        day = self.day_of(now)
        for _ in range(15):
            span = self.span(part, day)
            if span is not None and span[1] <= now:
                return span[1]
            day -= timedelta(days=1)
        return None


@dataclass(frozen=True)
class Memory:
    """One member's last answer of one part: kept in memory only (Y44)."""

    state: str
    data: Any = None
    read_at: int | None = None
    tried_at: int = 0


def with_side(row: Any) -> Any:
    """A position row with ``side`` from its quantity's sign, when the quantity is there (with
    quantities hidden the portfolio's floor removed it, and the direction cannot be told)."""
    if not isinstance(row, Mapping):
        return row
    qty = row.get("qty_u")
    if isinstance(qty, bool) or not isinstance(qty, int):
        return dict(row)
    return {**row, "side": "long" if qty > 0 else "short" if qty < 0 else "closed"}


def memory_of(part: str, view: Mapping[str, Any], now: int, old: Memory | None) -> Memory:
    """The portfolio's view of a part as kept: ``ok`` figures, or the held ones (``budget``: the
    portfolio kept its reads for the owner or could not read Upstox just now)."""
    data, read_at = view.get("data"), view.get("read_at")
    if isinstance(read_at, bool) or not isinstance(read_at, int):
        read_at = None
    if part == "positions" and isinstance(data, list):
        data = [with_side(row) for row in data]
    if data is not None and read_at is not None:
        return Memory("ok" if view.get("state") == "ok" else "budget", data, read_at, now)
    if old is not None and old.data is not None:
        return Memory("budget", old.data, old.read_at, now)
    return Memory("budget", None, None, now)


def rows_of(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    return [data] if isinstance(data, Mapping) else []  # the funds tree is one row


def position_count(rows: list[Any]) -> dict[str, int] | None:
    """Open, long and short positions; None when the rows cannot say (quantities hidden)."""
    sides = [row.get("side") for row in rows if isinstance(row, Mapping)]
    if any(side not in ("long", "short", "closed") for side in sides):
        return None
    long, short = sides.count("long"), sides.count("short")
    return {"open": long + short, "long": long, "short": short}


class Books:
    """The books of every member with an open screen. Used from the service's one event loop."""

    def __init__(self, portfolio: PortfolioClient, market: Market, *, clock: Callable[[], int]):
        self.portfolio, self.market, self.clock = portfolio, market, clock
        self.memory: dict[tuple[int, str], Memory] = {}
        #: socket -> (the member as read at its last message, the screen it shows)
        self.screens: dict[Any, tuple[Mapping[str, Any], str]] = {}
        self.dirty_at: dict[str, int] = {}
        #: Called after every read, in order: (member id, part, what is kept now).
        self.after_read: list[Callable[[int, str, Memory], None]] = []
        self.reads = 0
        self._flights: dict[tuple[int, str], asyncio.Task] = {}
        self._wake = asyncio.Event()

    # ---- screens ------------------------------------------------------------------------------
    def screen(self, sock: Any, member: Mapping[str, Any], name: str | None) -> int:
        """A socket shows ``name`` now (None or another name: no book screen). The parts it
        shows are read at once when due; how many reads started."""
        if name not in SCREEN_PARTS:
            self.screens.pop(sock, None)
            return 0
        self.screens[sock] = (member, name)
        now, started = self.clock(), 0
        for part in self._parts_of(member, name):
            if self.due(int(member["id"]), part, now, "screen"):
                self.start(member, part)
                started += 1
        self._wake.set()
        return started

    def closed(self, sock: Any) -> None:
        self.screens.pop(sock, None)

    @staticmethod
    def _parts_of(member: Mapping[str, Any], name: str) -> tuple[str, ...]:
        allowed = privacy.parts_shown(privacy.hidden_categories(member.get("privacy")))
        return tuple(part for part in SCREEN_PARTS.get(name, ()) if part in allowed)

    def shown(self) -> dict[tuple[int, str], Mapping[str, Any]]:
        """(member id, part) of every open screen, with the member, for parts they may see."""
        out: dict[tuple[int, str], Mapping[str, Any]] = {}
        for member, name in self.screens.values():
            for part in self._parts_of(member, name):
                out[(int(member["id"]), part)] = member
        return out

    def sockets_showing(self, member_id: int, part: str) -> list[Any]:
        return [sock for sock, (member, name) in self.screens.items()
                if int(member["id"]) == member_id and part in self._parts_of(member, name)]  # fmt: skip

    def watching(self, part: str, now: int) -> bool:
        """An open screen shows the part and its session is open: a nudge may read it."""
        return self.market.open(part, now) and any(p == part for _, p in self.shown())

    def mark_dirty(self, part: str, at: int) -> None:
        self.dirty_at[part] = max(self.dirty_at.get(part, 0), at)

    # ---- the rules ----------------------------------------------------------------------------
    def due(self, member_id: int, part: str, now: int, why: str) -> bool:
        """Whether a read of the member's part may be made now. ``why``: ``screen`` (a screen
        opened), ``page`` (the page asked), ``resync`` (the timer of an open screen)."""
        if part == "funds" and not self.market.open("funds", now):
            return False  # Upstox gives no funds from 00:00 to 05:30
        memory = self.memory.get((member_id, part))
        if memory is None:
            return True  # the first read, inside the session or once outside it
        since = now - memory.tried_at
        if not self.market.open(part, now):
            end = self.market.last_end(part, now)
            stale = memory.read_at is None or (end is not None and memory.read_at < end)
            return stale and since >= (RESYNC_MS[part] if why == "resync" else FRESH_MS)
        if why == "screen":
            return since >= FRESH_MS
        if why == "page" and self.dirty_at.get(part, 0) > memory.tried_at:
            return since >= NUDGE_EVERY_MS
        return since >= RESYNC_MS[part]

    # ---- reads --------------------------------------------------------------------------------
    def start(self, member: Mapping[str, Any], part: str) -> asyncio.Task:
        """The read of the member's part: a new one, or the one already on its way."""
        key = (int(member["id"]), part)
        task = self._flights.get(key)
        if task is None or task.done():
            task = asyncio.create_task(self._read(dict(member), part), name=f"family-book-{part}")
            self._flights[key] = task
            task.add_done_callback(
                lambda done, key=key: self._flights.pop(key, None)
                if self._flights.get(key) is done else None
            )  # fmt: skip
        return task

    async def read(self, member: Mapping[str, Any], part: str) -> Memory:
        """Start or join the read and wait for it; a caller that goes does not cancel it."""
        return await asyncio.shield(self.start(member, part))

    async def _read(self, member: dict[str, Any], part: str) -> Memory:
        member_id, now = int(member["id"]), self.clock()
        key, old = (member_id, part), self.memory.get((member_id, part))
        try:
            data = await self.portfolio.call("upstox_book", member_id, {"parts": [part]},
                                             mask=privacy.mask_for(member))  # fmt: skip
            view = data.get(part) if isinstance(data, Mapping) else None
            if not isinstance(view, Mapping):
                raise Refusal("unreachable")
            kept = memory_of(part, view, now, old)
        except Exception as exc:  # a refusal, or a fault here: said as the account not readable
            if not isinstance(exc, Refusal):
                log.exception("a family book read failed")
            state = exc.state if isinstance(exc, Refusal) else "unreachable"
            keep = state == "unreachable" and old is not None
            kept = Memory(state, old.data if keep else None, old.read_at if keep else None, now)
        self.memory[key] = kept
        self.reads += 1
        for hook in list(self.after_read):
            try:
                hook(member_id, part, kept)
            except Exception:
                log.exception("a family book read could not be passed on")
        return kept

    def read_shown(self, part: str) -> int:
        """Read ``part`` for every member whose open screen shows it (a nudge)."""
        members = [m for (_, p), m in self.shown().items() if p == part]
        for member in members:
            self.start(member, part)
        return len(members)

    def step(self) -> int:
        """The re-sync: start every read an open screen is due; how many started. What no screen
        has shown for ``KEEP_MS`` is forgotten (member ids only grow as members come and go)."""
        now, started, shown = self.clock(), 0, self.shown()
        for (member_id, part), member in shown.items():
            if self.due(member_id, part, now, "resync"):
                self.start(member, part)
                started += 1
        for key in [k for k, m in self.memory.items() if k not in shown
                    and now - m.tried_at >= KEEP_MS]:  # fmt: skip
            del self.memory[key]
        return started

    async def run(self) -> None:
        while True:
            self._wake.clear()
            self.step()
            try:
                await asyncio.wait_for(self._wake.wait(), LOOP_S)
            except TimeoutError:
                pass

    async def close(self) -> None:
        flying, self._flights = list(self._flights.values()), {}
        await cancel_and_wait(*flying)

    def health(self) -> dict[str, int]:
        return {"reads": self.reads, "screens": len(self.screens), "kept": len(self.memory)}

    # ---- what the phone is sent ---------------------------------------------------------------
    def book_of(self, member: Mapping[str, Any], part: str, now: int) -> dict[str, Any]:
        """The member's part as the contract's ``Book``, masked again with their mask now."""
        hidden = privacy.hidden_categories(member.get("privacy"))
        base = {"part": part, "hidden": list(hidden)}
        if part not in privacy.parts_shown(hidden):
            return {**base, "read_at": None, "state": "not_shared",
                    "words": WORDS["not_shared"], "rows": []}  # fmt: skip
        memory = self.memory.get((int(member["id"]), part)) or Memory("closed")
        state = memory.state
        if state == "ok" and not self.market.open(part, now):
            state = "closed"
        rows = privacy.masked(rows_of(memory.data), privacy.mask_of(hidden))
        return {**base, "read_at": memory.read_at, "state": state,
                "words": words_for(part, state, memory.read_at), "rows": rows}  # fmt: skip

    async def _fresh(self, member: Mapping[str, Any], part: str, now: int) -> None:
        if part in privacy.parts_shown(privacy.hidden_categories(member.get("privacy"))):
            if self.due(int(member["id"]), part, now, "page"):
                await self.read(member, part)

    async def route_book(self, request: Any, args: dict[str, Any], session: Any) -> Response:
        part = args.get("part")
        if part not in BOOK_PARTS:
            raise Invalid(field="part")
        now = self.clock()
        await self._fresh(session.member, part, now)
        return Response.ok(self.book_of(session.member, part, self.clock()))

    async def route_summary(self, request: Any, args: dict[str, Any], session: Any) -> Response:
        member, now = session.member, self.clock()
        for part in ("positions", "funds"):
            await self._fresh(member, part, now)
        now = self.clock()
        book = self.book_of(member, "positions", now)
        hidden, rows = book["hidden"], book["rows"]
        pnl = None
        if "pnl" not in hidden and rows:
            names = ("pnl_paise", "realised_paise", "unrealised_paise")
            totals = {n: sum(r[n] for r in rows) for n in names
                      if all(isinstance(r, Mapping) and type(r.get(n)) is int for r in rows)}  # fmt: skip
            pnl = privacy.masked(totals, privacy.mask_of(hidden)) or None
        funds = None
        if "funds" not in hidden:
            found = self.book_of(member, "funds", now)["rows"]
            funds = found[0] if found else None
        return Response.ok({"read_at": book["read_at"], "state": book["state"],
                            "words": book["words"], "hidden": hidden,
                            "positions": position_count(rows) if book["state"] != "not_shared"
                            else None, "pnl": pnl, "funds": funds})  # fmt: skip

    # ---- the shared portfolio tabs (read when the page asks; not market data) -----------------
    async def _tabs(self, member: Mapping[str, Any]) -> list[dict[str, Any]]:
        found = await self.portfolio.call("shares_for_viewer", int(member["id"]),
                                          mask=privacy.mask_for(member))  # fmt: skip
        tabs = found.get("tabs") if isinstance(found, Mapping) else None
        return [{"id": t.get("tab_id"), "name": t.get("name"), "kind": t.get("kind"),
                 "level": t.get("level"), "expires_at": t.get("expires_at")}
                for t in tabs or () if isinstance(t, Mapping)]  # fmt: skip

    async def route_portfolio(self, request: Any, args: dict[str, Any], session: Any) -> Response:
        member, now = session.member, self.clock()
        hidden = privacy.hidden_categories(member.get("privacy"))
        out: dict[str, Any] = {"read_at": now, "state": "ok", "words": None, "hidden": list(hidden),
                               "tabs": [], "summary": None}  # fmt: skip
        try:
            out["tabs"] = await self._tabs(member)
            summary = await self.portfolio.call("summary", int(member["id"]),
                                                mask=privacy.mask_for(member))  # fmt: skip
            out["summary"] = privacy.masked(summary, privacy.mask_of(hidden))
        except Refusal as exc:
            out.update(read_at=None, state=exc.state, words=str(exc))
        return Response.ok(out)

    async def route_portfolio_tab(self, request: Any, args: dict[str, Any],
                                  session: Any) -> Response:  # fmt: skip
        raw = str(args.get("id", ""))
        if not raw.isdigit() or not 0 < int(raw) < 2**31:
            raise Invalid(field="id")
        member, tab_id, now = session.member, int(raw), self.clock()
        hidden = privacy.hidden_categories(member.get("privacy"))
        out: dict[str, Any] = {"tab": None, "read_at": now, "state": "ok", "words": None,
                               "hidden": list(hidden), "view": None}  # fmt: skip
        try:
            tab = next((t for t in await self._tabs(member) if t["id"] == tab_id), None)
            if tab is None:
                raise Refusal("not_shared")
            view = await self.portfolio.call("tab_get", int(member["id"]), {"id": tab_id},
                                             mask=privacy.mask_for(member))  # fmt: skip
            out.update(tab=tab, view=privacy.masked(view, privacy.mask_of(hidden)))
        except Refusal as exc:
            out.update(read_at=None, state=exc.state, words=str(exc))
        return Response.ok(out)


# ---- the data plane: its keys, its pieces and its one job --------------------------------------
@dataclass(frozen=True)
class PlaneKeys:
    """The data plane's seal keys in /etc/sudarshan-family (the provision step writes them)."""

    portfolio: Fernet

    @classmethod
    def load(cls, settings: Any) -> PlaneKeys:
        return cls(seal_key(settings.etc_dir, "portfolio-family"))


class Plane:
    """The data plane of one service: its loopback HTTP client, the books, the nudges, and what
    the later lanes add (the feed link, the shared lists). ``run`` is the service's job ``data``:
    every piece runs in it, and its end closes everything that was opened."""

    def __init__(self, service: Any, keys: PlaneKeys):
        settings = service.settings
        self.service, self.keys, self.clock = service, keys, service.clock
        self.http = httpx.AsyncClient(trust_env=False, follow_redirects=False,
                                      timeout=httpx.Timeout(5.0, connect=1.0))  # fmt: skip
        self.portfolio = PortfolioClient(settings.portfolio_url, keys.portfolio, self.http)
        calendar = TradingCalendar(settings.holidays_file, settings.extra_days_file, logger=log)
        self.market = Market(calendar)
        self.books = Books(self.portfolio, self.market, clock=service.clock)
        self.nudges = Nudges(self.books, clock=service.clock)
        self.books.after_read.append(
            lambda member_id, part, kept: announce(self.books, member_id, part,
                                                   kept.read_at or self.clock())
        )  # fmt: skip
        #: name -> a coroutine function run for the plane's life (restarted after a failure).
        self.parts: dict[str, Callable[[], Awaitable[None]]] = {"books": self.books.run}
        self.closers: list[Callable[[], Awaitable[None]]] = [self.nudges.close, self.books.close]
        self.reports: dict[str, Callable[[], Any]] = {"books": self.books.health}

    def nudged(self, kind: str, sandbox: bool, at: int) -> bool:
        return self.nudges.nudged(kind, sandbox, at)

    def health(self) -> dict[str, Any]:
        """Counts and states only (/health names no one)."""
        reach = {None: "not asked yet", True: "answering", False: "not answering"}
        out = {name: report() for name, report in self.reports.items()}
        out["portfolio"] = reach[self.portfolio.reachable]
        out["nudges"] = dict(self.nudges.counts)
        return out

    async def run(self) -> None:
        tasks = [asyncio.create_task(_keep(piece, part), name=f"family-data-{piece}")
                 for piece, part in self.parts.items()]  # fmt: skip
        try:
            await asyncio.gather(*tasks)
        finally:
            steps = [lambda: cancel_and_wait(*tasks), *reversed(self.closers), self.http.aclose]
            await close_all(steps)


async def close_all(steps: list[Callable[[], Awaitable[Any]]]) -> None:
    """Run every closing step even when the job is cancelled again while it closes (the service's
    stop cancels a job that has not ended every 0.2 s): a cancel is kept and raised at the end."""
    interrupted: asyncio.CancelledError | None = None
    for step in steps:
        try:
            await step()
        except asyncio.CancelledError as exc:
            interrupted = exc
        except Exception:
            log.exception("a part of the family data plane did not close cleanly")
    if interrupted is not None:
        raise interrupted


async def _keep(piece: str, part: Callable[[], Awaitable[None]]) -> None:
    """Run a piece for ever: a failure is logged and the piece starts again after 5 s."""
    while True:
        try:
            await part()
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("the family data plane's %s stopped; it starts again in 5 s", piece)
            await asyncio.sleep(5.0)


def install(service: Any, keys: PlaneKeys) -> Plane:
    """Seat the data plane in the service: the viewer routes of the books and the portfolio, the
    nudge door's callback (through ``service.plane``) and the job ``data``."""
    plane = Plane(service, keys)
    viewer = service.viewer
    viewer._book = plane.books.route_book
    viewer._summary = plane.books.route_summary
    viewer._portfolio = plane.books.route_portfolio
    viewer._portfolio_tab = plane.books.route_portfolio_tab
    service._job_makers["data"] = lambda _service: plane.run()
    return plane
