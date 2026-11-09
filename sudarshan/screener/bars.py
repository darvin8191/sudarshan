"""The 1-minute bars of the day, built from the feed's ticks (M9.1). Pure.

``Bars`` is handed every frame of every key it follows, in the order they arrive (``on_tick``), and
is asked about once a second what time alone decides (``on_clock``). It returns what became a sealed
bar, for the store to write; it opens no file, reads no clock and logs nothing.

A trade is a frame whose ``(ltt, ltp, ltq)`` changed and that passes the market alerts' gate
(clock.gate: a trading day, inside the session, the exchange open, the feed healthy, the frame
fresh). It belongs to the minute of its own time and counts when it arrives no later than the
alerts' arrival grace after that minute's end (ARRIVAL_GRACE_MS, imported). A minute is sealed once
that grace is over (CLOSE_CHECK_MS, imported, 5 s after its end). A later trade for it is counted
(``late``) and never reopens it: its volume lands in the next bar, because volume is read from the
day's running total (``vtt``). A minute without a trade has no bar.

Volume is the running total at the last trade of the minute less the total already accounted for
(``cum``), which starts at the last total seen before the open, so the pre-open volume belongs to
no bar. A restore keeps each key's ``cum`` from the store; a key the store held no bar of starts
from its first frame of today's trades after the restore (``restore(since=...)``), so nothing
traded before the restore lands in its first bar. The exchange's own 1-minute candle (``ohlc_1m``, I1) replaces a bar's prices and, when
larger, its volume if it arrives after the minute ends and before the seal, with ``ts`` the
minute's start. The share of it the ticks missed moves ``cum`` too, so the next bar does not count
it again.

A pause over GAP_S marks every bar it touched UNVERIFIED, worked out when the candles are read
(candles.py) and never stored, because a pause is often noticed after the bar it touched was
sealed. A bar's volume spans from the last trade of the bar before it, so a pause in a quiet stretch
marks the bar after it too. ``repair`` replaces those bars from the REST candles; a replaced bar is
REPAIRED and is never UNVERIFIED again.

A key let go and followed again the same day (``follow_from``; re-review MEDIUM-1) was not watched
in between, so the stretch is that key's own pause (a gap, ``gaps``) and its first frame of a trade
made after it comes back is its baseline in place of the running total: the volume traded meanwhile
belongs to no bar, and the candles that hold the gap are UNVERIFIED. A gap before the open, or no
longer than GAP_S inside the session, changes nothing. A key first followed after its session
began (a screen made or resumed at 11:00, a restart for a key the store holds no bar of) was not
watched from the open either: that stretch is its gap too, so its day candle and its first bar are
UNVERIFIED (design A1).

A stock that closes by auction (``Market.auction``) trades continuously to the continuous end and
then prints the auction's price once (clock.auction; the frame must carry a volume that rose, as
Book.on_tick says). The print belongs to the last minute of the cash session, so the 15:15 to 15:30
candle and the day close at it, and it seals that bar on arrival (from 7 Dec 2026 every stock trades
to the cash end and has sealed that minute already: the print then becomes its close and adds its
volume). The frames of one a minute from the auction's book, whose volume does not move, are no
trade.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import NamedTuple

from sudarshan.alerts.market.book import BAD_VALUE, NOT_A_TRADE, NOTHING_NEW
from sudarshan.alerts.market.clock import (
    ARRIVAL_GRACE_MS,
    AUCTION_PRINT,
    OK,
    OUTSIDE_HOURS,
    Market,
    Pause,
    auction,
    candle,
    gate,
    paused_during,
    within,
)
from sudarshan.alerts.market.engine import CLOSE_CHECK_MS
from sudarshan.alerts.market.grain import paise
from sudarshan.alerts.market.sessions import AUCTION, Session, epoch_ms, key_day, segment_of
from sudarshan.screener.types import FROM_I1, REPAIRED, UNVERIFIED, Candle
from sudarshan.screener.types import LATE as LATE_BAR

MINUTE_MS = 60_000
#: A candle that held a pause is waited for this long after its end for the repair, then the seal's
#: grace more: the 10:15 run waits until 10:16:05.
REPAIR_WAIT_MS = 60_000
#: A trade for a minute that came too late to count (``Tick.check``).
LATE_TRADE = "late"
#: The cause of a key's own pause: the screener did not follow it then (``Bars.follow_from``).
UNFOLLOWED = "unfollowed"


class Bar(NamedTuple):
    """One sealed minute. ``flags`` holds LATE, FROM_I1 and REPAIRED, never UNVERIFIED."""

    start: int
    o: int
    h: int
    low: int
    c: int
    v: int
    oi: int = -1
    atp: int = 0
    flags: int = 0
    #: The time of the bar's last trade (epoch ms): the next bar's volume counts from it.
    until: int = 0

    @property
    def end(self) -> int:
        return self.start + MINUTE_MS


class Sealed(NamedTuple):
    """A bar to write, with the running total accounted for through it (``cum``)."""

    key: str
    bar: Bar
    cum: int


class Tick(NamedTuple):
    #: OK or AUCTION_PRINT when the frame's trade is in a bar; LATE_TRADE when it came too late;
    #: otherwise why not (a gate code, NOT_A_TRADE, BAD_VALUE or NOTHING_NEW), for the counters.
    check: str
    #: Bars this frame sealed: the auction's print seals its bar at once.
    sealed: tuple[Sealed, ...] = ()


class Repair(NamedTuple):
    rows: tuple[Sealed, ...]
    #: Starts of the minutes the REST candles do not have: their bars are gone.
    removed: tuple[int, ...]


class Followed(NamedTuple):
    """What a change of the keys followed made, for the store to write."""

    #: The minutes a key followed again held open from before it was let go, sealed now.
    sealed: tuple[Sealed, ...] = ()
    #: (key, gap) for each key followed again after a stretch that counts.
    gaps: tuple[tuple[str, Pause], ...] = ()


def seal_at(end: int) -> int:
    """When the minute (or candle) that ends at ``end`` is sealed and may be judged."""
    return end + CLOSE_CHECK_MS


def repair_by(boundary: int) -> int:
    """The latest a run for a candle that ends at ``boundary`` waits for its repair."""
    return boundary + REPAIR_WAIT_MS + CLOSE_CHECK_MS


def judge_at(
    boundary: int,
    *,
    auction_end: int | None = None,
    auction_until: int | None = None,
    printed_at: int | None = None,
    unhealed: bool = False,
) -> int:
    """When the run for the candle that ends at ``boundary`` judges an instrument.

    The seal. A stock that closes by auction is judged on the day's last candle (``auction_end``) as
    its price arrives (``printed_at``), at the latest 5 s after the print may come
    (``auction_until``). A candle that holds a bar no repair has healed waits for the repair.
    """
    at = seal_at(boundary)
    if auction_end is not None and boundary == auction_end:
        at = printed_at if printed_at is not None else seal_at(auction_until or boundary)
    return max(at, repair_by(boundary)) if unhealed else at


def touched(bars: Sequence[Bar], pauses: Sequence[Pause], opens: int) -> list[bool]:
    """For each bar, whether a pause over GAP_S fell in its volume window: from the last trade of the
    bar before it (``opens`` for the first; never later than the bar's own start) to its end.
    ``pauses`` are today's, inside the session."""
    found, since = [], opens
    for bar in bars:
        found.append(bool(pauses) and paused_during(tuple(pauses), min(since, bar.start), bar.end))
        since = bar.until
    return found


def flags_of(bar: Bar, hit: bool) -> int:
    """The bar's flags as candles carry them: UNVERIFIED while a pause touched it and no repair
    has replaced it."""
    return bar.flags | (UNVERIFIED if hit and not bar.flags & REPAIRED else 0)


def _ms(value) -> int | None:
    """An epoch-ms or count field of a frame; None unless it is a finite number."""
    if value.__class__ is int:
        return value
    return int(value) if value.__class__ is float and value - value == 0 else None


def _count(value) -> int | None:
    """A volume or open interest; None unless it is a finite number of at least 0."""
    found = _ms(value)
    return found if found is not None and found >= 0 else None


def _candle(row: Candle) -> bool:
    """Whether a REST row can be a minute's candle."""
    return (
        min(row.o, row.h, row.low, row.c) > 0
        and row.low <= min(row.o, row.c)
        and max(row.o, row.c) <= row.h
        and row.v >= 0
    )


class _In(NamedTuple):
    """One frame, read once."""

    key: str
    frame: Mapping
    identity: tuple
    recv: int
    now: int
    segment: str
    #: The session key: the segment, or ``<segment>+auction`` for a stock that closes by auction.
    cls: str
    grid: Session | None
    volume: int | None
    #: The running total of the key's frame before this one.
    last: int | None

    @property
    def cas(self) -> bool:
        return self.cls != self.segment

    @property
    def at(self) -> int:
        return self.identity[0]


class _Open:
    """A minute that has trades and is not sealed."""

    __slots__ = ("start", "first", "last", "o", "h", "low", "c", "vtt", "oi", "atp", "flags", "i1")

    def __init__(self, start: int, oi: int, atp: int):
        self.start, self.oi, self.atp, self.flags = start, oi, atp, 0
        self.first = self.last = self.o = self.h = self.low = self.c = self.vtt = self.i1 = None

    def add(self, at: int, price: int, vtt: int | None, oi: int | None, atp: int | None) -> None:
        """A trade by its own time ``at``: the open is the earliest, the close the latest."""
        if self.first is None or at < self.first:
            self.first, self.o = at, price
        if self.last is None or at >= self.last:
            self.last, self.c = at, price
            if oi is not None:
                self.oi = oi
            if atp is not None:
                self.atp = atp
        self.h = price if self.h is None else max(self.h, price)
        self.low = price if self.low is None else min(self.low, price)
        if vtt is not None and (self.vtt is None or vtt > self.vtt):
            self.vtt = vtt


class _Key:
    """Everything kept for one instrument key today."""

    __slots__ = ("trade", "seen", "cum", "pre", "oi", "atp", "open", "sealed", "cums", "late",
                 "printed", "printed_at", "rebase", "again")  # fmt: skip

    def __init__(self) -> None:
        #: The identity of the last trade the gate let through.
        self.trade: tuple | None = None
        #: The running total of the key's last frame, whatever became of it (the print's test).
        self.seen: int | None = None
        #: The running total accounted for in sealed bars; None until the first bar.
        self.cum: int | None = None
        #: The largest running total seen with a last trade before the open (the pre-open volume).
        self.pre: int | None = None
        self.oi, self.atp = -1, 0
        self.open: dict[int, _Open] = {}
        self.sealed: dict[int, Bar] = {}
        self.cums: dict[int, int] = {}
        self.late = 0
        #: The day (YYYYMMDD) the closing auction's print was taken, and when it arrived.
        self.printed = 0
        self.printed_at: int | None = None
        #: Set for a key first seen after a restore: the restore's moment (epoch ms); its first
        #: frame of a trade made today becomes its baseline (``Bars._rebase``).
        self.rebase: int | None = None
        #: True for a key followed again after a gap until its baseline is taken: the baseline
        #: then replaces the running total, not only the pre-open volume.
        self.again = False


class Bars:
    def __init__(self) -> None:
        self._day = 0
        self._keys: dict[str, _Key] = {}
        #: When the bars were last restored, for the keys the store held no bar of.
        self._since: int | None = None
        #: Keys first followed after the day began, with the moment they were claimed: their
        #: first frame of a trade made today is their baseline too (see ``follow_from``).
        self._claimed: dict[str, int] = {}
        self._reset()

    def _reset(self) -> None:
        #: The keys followed, as the last ``follow_from`` named them; None until the first one
        #: after a restore or a new day.
        self._followed: frozenset[str] | None = None
        #: Keys let go today and not followed again yet, with the moment they were let go.
        self._let_go: dict[str, int] = {}
        #: Each key's gaps today: the stretches it was not followed (``follow_from``).
        self._gaps: dict[str, list[Pause]] = {}

    def follow_from(self, keys: Iterable[str], at: int, market: Market) -> Followed:
        """The whole set of keys the screener follows from ``at`` on (a screen made, resumed,
        paused, edited or deleted, a master download, an option window built, a watchlist read
        again or not, the key limit).

        A key the bars hold nothing of has traded before ``at`` and nobody counted it, so its first
        frame of a trade made today becomes its baseline and that volume belongs to no bar, as
        after a restart. Before the open this changes nothing: the pre-open frames give the
        baseline, as on any day. Nobody watched it from its session's start (or from when it was
        let go before its first trade came) either, so that stretch is its gap when it holds more
        than GAP_S of the session (design A1): its day candle and its first bar read "Not
        checked", and later minute candles are judged as usual. The first set after a restore
        counts as the first follow of every key the store holds no bar of.

        A key let go earlier today and followed again was not watched in between (re-review
        MEDIUM-1). When that stretch holds more than GAP_S of its session, the minutes it held
        open are sealed now from the running total (nothing more of them can come), its first
        frame of a trade made from now on is its baseline in place of that total, and the
        stretch becomes its gap. A shorter stretch, or one before the open, changes nothing: the
        running total carries what traded meanwhile into the next bar, as for a lost frame.

        Every key the store held is taken as let go at the moment of the restore, and the first
        set after it says which come back: on a normal start that is the same moment, so nothing
        changes, but a start that could follow nothing at once (the master contract downloading,
        the active screens unreadable) left them unwatched until that set, a gap like any other
        (re-review MEDIUM-A1). A key the first set leaves out stays let go from the restore."""
        self._roll(market.today)
        wanted = frozenset(keys)
        first = self._followed is None
        before = frozenset(self._keys) if first else self._followed
        let_go = self._since if first and self._since is not None else at
        for key in before if first else before - wanted:
            self._let_go.setdefault(key, let_go)
        sealed: list[Sealed] = []
        gaps: list[tuple[str, Pause]] = []
        for key in sorted(wanted if first else wanted - before):
            since = self._let_go.pop(key, None)
            state = self._keys.get(key)
            if state is None:  # first followed today (since None), or let go before a trade
                gap = self._stretch(key, since, at, market)
                if since is not None or key not in self._claimed:
                    self._claimed[key] = at
            elif since is not None:
                gap = self._again(key, state, since, at, market, sealed)
            else:
                gap = None
            if gap is not None:
                gaps.append((key, gap))
        self._followed = wanted
        return Followed(tuple(sealed), tuple(gaps))

    def gaps(self, key: str) -> tuple[Pause, ...]:
        """``key``'s gaps today: the stretches the screener did not follow it."""
        return tuple(self._gaps.get(key, ()))

    def restore_gaps(self, gaps: Iterable[tuple[str, Pause]]) -> None:
        """Today's gaps from the store after a start (after ``restore``): they hold all day."""
        for key, gap in gaps:
            self._gaps.setdefault(key, []).append(gap)

    # ---- what the runtime asks -------------------------------------------------------------
    @property
    def day(self) -> int:
        """The day (YYYYMMDD) the bars held are of: the last one a frame, the clock or a restore
        named. Before the first of the next day's calls it is still yesterday."""
        return self._day

    def bars(self, key: str) -> tuple[Bar, ...]:
        """The key's sealed bars of ``day``, oldest first."""
        state = self._keys.get(key)
        return () if state is None else tuple(state.sealed[start] for start in sorted(state.sealed))

    def late(self, key: str) -> int:
        """Trades for a minute that came too late to count (they stay in the day's volume)."""
        state = self._keys.get(key)
        return 0 if state is None else state.late

    def printed(self, key: str, today: int) -> bool:
        """Whether today's closing auction print of ``key`` has come."""
        state = self._keys.get(key)
        return state is not None and state.printed == today

    def auction_of(self, key: str, market: Market) -> tuple[int | None, bool]:
        """(the end of the last candle, whether the auction's price has come) for a stock that
        closes by auction: what the engine's Subject carries. (None, False) for any other key."""
        grid = market.sessions.get(segment_of(key))
        if key not in market.auction or grid is None:
            return None, False
        return grid.end, self.printed(key, market.today)

    def due(self, key: str, boundary: int, market: Market, *, unhealed: bool = False) -> int:
        """When the run for ``boundary`` judges ``key``: judge_at, with the key's own print."""
        end, printed = self.auction_of(key, market)
        own = market.sessions.get(segment_of(key) + AUCTION)
        state = self._keys.get(key)
        # A print restored from the store has no arrival time: it is known to have come, so the
        # plain seal applies.
        arrived = None
        if printed and state is not None:
            arrived = state.printed_at if state.printed_at is not None else seal_at(boundary)
        return judge_at(
            boundary,
            auction_end=end,
            auction_until=None if own is None else own.auction_until,
            printed_at=arrived,
            unhealed=unhealed,
        )

    # ---- what happens ----------------------------------------------------------------------
    def on_tick(self, frame: Mapping, now: int, market: Market) -> Tick:
        """One frame from the feed, judged at ``now`` (epoch ms)."""
        self._roll(market.today)
        key = frame.get("key")
        if key.__class__ is not str:
            return Tick(NOTHING_NEW)
        state = self._keys.get(key)
        if state is None:
            state = self._keys[key] = _Key()
            state.rebase = self._claimed.pop(key, self._since)
        recv = _ms(frame.get("ts_recv"))
        segment = segment_of(key)
        volume = _count(frame.get("vtt"))
        ltp, ltt = frame.get("ltp"), _ms(frame.get("ltt"))
        grid = market.sessions.get(segment)
        item = _In(
            key, frame, (ltt, ltp, frame.get("ltq")), now if recv is None else recv, now, segment,
            segment + AUCTION if key in market.auction else segment, grid, volume, state.seen,
        )  # fmt: skip
        if state.rebase is not None:
            self._rebase(state, frame, volume, ltt, market.today, grid)
        if volume is not None:
            state.seen = volume
        sealed: list[Sealed] = []
        check = NOTHING_NEW
        if ltp is not None and ltt is not None and item.identity != state.trade:
            check = self._trade(state, item, market, sealed)
        self._exchange_candle(state, frame.get("ohlc_1m"), item.recv)
        return Tick(check, tuple(sealed))

    def on_clock(self, now: int, market: Market) -> list[Sealed]:
        """Seal every minute whose grace is over; call it about once a second."""
        self._roll(market.today)
        sealed: list[Sealed] = []
        for key in sorted(self._keys):
            state = self._keys[key]
            for start in sorted(state.open):
                if seal_at(start + MINUTE_MS) > now:
                    break
                sealed.append(self._seal(key, state, start))
        return sealed

    # ---- a restart and a repair ------------------------------------------------------------
    def restore(
        self,
        day: int,
        rows: Iterable[Sealed],
        printed: Iterable[str] = (),
        since: int | None = None,
    ) -> None:
        """Start today again from the bars the store holds. The running total accounted for is the
        largest one stored, and the key's last frame is taken to have shown it, so the first
        frame after a restart cannot be mistaken for a print by a total that was never seen.

        ``since`` is the moment of the restore: a key the store holds no bar of then has a running
        total nobody accounted for (its pre-open volume and anything traded before the restore),
        so its first frame of a trade made today becomes its baseline (``_rebase``) and that
        volume belongs to no bar, as the pre-open volume never does."""
        self._day, self._keys, self._since = day, {}, since
        self._claimed = {}
        self._reset()
        for row in rows:
            state = self._keys.setdefault(row.key, _Key())
            state.sealed[row.bar.start] = row.bar
            state.cums[row.bar.start] = row.cum
        for state in self._keys.values():
            state.cum = state.seen = max(state.cums.values())
            last = state.sealed[max(state.sealed)]
            state.oi, state.atp = last.oi, last.atp
        for key in printed:
            self._keys.setdefault(key, _Key()).printed = day

    def repair(self, key: str, rest: Iterable[Candle], through: int, market: Market) -> Repair:
        """Replace the minutes a pause touched with the exchange's REST candles.

        ``through`` is how far the REST answer is complete (epoch ms): a minute that ends later is
        left as it is. A bar a pause touched becomes the REST candle, REPAIRED; a minute inside a
        pause that has no bar gets the REST candle if there is one; a touched bar the REST
        candles do not have is removed (the exchange had no trade). A REST row that cannot be a
        candle counts as missing for a gap and leaves a bar as it is.
        """
        segment = segment_of(key)
        grid = market.sessions.get(segment)
        own = market.sessions.get(segment + AUCTION if key in market.auction else segment)
        pauses = market.pauses + self.gaps(key)
        if grid is None or own is None or not pauses or market.today != self._day:
            return Repair((), ())
        state = self._keys.setdefault(key, _Key())
        pauses = within(pauses, own.start, own.end)
        rows = list(rest)
        have = {row.start: row for row in rows if _candle(row)}
        given = {row.start for row in rows}
        held = [state.sealed[start] for start in sorted(state.sealed)]
        marks = dict(
            zip((bar.start for bar in held), touched(held, pauses, grid.start), strict=True)
        )
        written: list[Sealed] = []
        removed: list[int] = []
        cum = state.pre or 0
        for start in range(grid.start, grid.end, MINUTE_MS):
            old = state.sealed.get(start)
            cum = state.cums.get(start, cum)
            if start + MINUTE_MS > through or (old is not None and old.flags & REPAIRED):
                continue
            if not (marks[start] if old else paused_during(pauses, start, start + MINUTE_MS)):
                continue
            row = have.get(start)
            if row is not None:
                oi = row.oi if row.oi >= 0 else (old.oi if old else -1)
                bar = Bar(start, row.o, row.h, row.low, row.c, row.v, oi,
                          old.atp if old else 0, REPAIRED, old.until if old else start)  # fmt: skip
                state.sealed[start], state.cums[start] = bar, cum
                written.append(Sealed(key, bar, cum))
            elif old is not None and start not in given:
                del state.sealed[start], state.cums[start]
                removed.append(start)
        return Repair(tuple(written), tuple(removed))

    # ---- inside ----------------------------------------------------------------------------
    def _roll(self, today: int) -> None:
        """A new trading day starts with nothing."""
        if today != self._day:
            self._day, self._keys, self._since = today, {}, None
            self._claimed = {}
            self._reset()

    def _stretch(self, key: str, since: int | None, at: int, market: Market) -> Pause | None:
        """The stretch ``key`` was not followed, from ``since`` (None: its session's start) to
        ``at``, kept as its gap; None when it holds no more than GAP_S of the session."""
        grid = market.sessions.get(segment_of(key))
        if grid is None:
            return None
        gap = Pause(grid.start if since is None else since, at, UNFOLLOWED)
        inside = within((gap,), grid.start, grid.end)
        if not inside or not paused_during(inside, grid.start, grid.end):
            return None
        self._gaps.setdefault(key, []).append(gap)
        return gap

    def _again(
        self, key: str, state: _Key, since: int, at: int, market: Market, sealed: list[Sealed]
    ) -> Pause | None:
        """A key let go at ``since`` and followed again at ``at``: its gap, or None when the
        stretch holds no more than GAP_S of its session (``follow_from``)."""
        gap = self._stretch(key, since, at, market)
        if gap is None:
            return None
        for start in sorted(state.open):  # traded before the let-go, from the running total
            sealed.append(self._seal(key, state, start))
        state.rebase, state.again = at, True
        return gap

    @staticmethod
    def _rebase(
        state: _Key,
        frame: Mapping,
        volume: int | None,
        ltt: int | None,
        today: int,
        grid: Session | None,
    ) -> None:
        """The baseline of a key first seen after a restore made once its session had begun
        (``restore(since=...)``), first followed then, or followed again after a gap: its first
        frame of a trade made today. A trade made after that moment counts its own quantity
        (``ltq``) and nothing before it; one made before it counts nothing. A frame of an earlier
        day's trade carries yesterday's total and waits for today's. A restore before the
        session's start leaves the pre-open baseline to the frames before the open, as on any
        day. A key followed again starts its running total again here (``cum`` and the pre-open
        volume give way to the baseline)."""
        since = state.rebase
        if grid is None or since is None or since < grid.start:
            state.rebase, state.again = None, False
            return
        if volume is None or ltt is None or ltt < epoch_ms(key_day(today), 0):
            return
        state.rebase = None
        quantity = _count(frame.get("ltq"))
        fresh = ltt >= since and quantity is not None and 0 < quantity <= volume
        base = volume - quantity if fresh else volume
        if state.again:
            state.again, state.cum, state.pre = False, None, base
        elif state.pre is None or base > state.pre:
            state.pre = base

    def _trade(self, state: _Key, item: _In, market: Market, sealed: list[Sealed]) -> str:
        at, grid = item.at, item.grid
        check = gate(market, item.cls, at, item.recv, item.now, trade=True)
        if check == OUTSIDE_HOURS and item.cas:  # the closing auction's print?
            check = auction(market, item.segment, at, item.recv, item.now)
            rose = item.volume is not None and item.last is not None and item.volume > item.last
            if check == AUCTION_PRINT and not rose:
                check = NOT_A_TRADE  # its volume did not move: the auction's book
        if check != OK and check != AUCTION_PRINT:
            if grid is not None and at < grid.start:
                self._before_open(state, at, item.volume, market.today)
            return check
        state.trade = item.identity
        price = paise(item.identity[1])
        if price is None:
            return BAD_VALUE
        printed = check == AUCTION_PRINT
        if printed and self.printed(item.key, market.today):
            state.late += 1  # the day's print is taken once
            return LATE_TRADE
        start = candle(grid, 1, grid.end - 1 if printed else at)[1]
        end = start + MINUTE_MS
        if not printed and (start in state.sealed or item.recv > end + ARRIVAL_GRACE_MS):
            state.late += 1
            return LATE_TRADE
        oi, atp = _count(item.frame.get("oi")), paise(item.frame.get("atp"))
        if printed:
            state.printed, state.printed_at = market.today, item.now
            sealed.append(self._print(item.key, state, start, (at, price, item.volume, oi, atp)))
            return check
        minute = state.open.get(start)
        if minute is None:
            minute = state.open[start] = _Open(start, state.oi, state.atp)
        minute.add(at, price, item.volume, oi, atp)
        if item.recv >= end:
            minute.flags |= LATE_BAR  # the minute was over when this trade came
        state.oi, state.atp = minute.oi, minute.atp
        return check

    def _print(self, key: str, state: _Key, start: int, trade: tuple) -> Sealed:
        """The closing auction's price, into the day's last minute, which it seals on arrival.

        A minute that continuous trading has already sealed (every stock from 7 Dec 2026, which
        trades to the cash end) takes the print as its close, widens its high and low, and gains the
        rise of the running total; its running total moves with it."""
        at, price, volume, oi, atp = trade
        held = state.sealed.get(start)
        if held is None:
            minute = state.open.get(start)
            if minute is None:
                minute = state.open[start] = _Open(start, state.oi, state.atp)
            minute.add(at, price, volume, oi, atp)
            return self._seal(key, state, start)
        cum = state.cum if state.cum is not None else (state.pre or 0)  # as _seal (MEDIUM-1)
        added = 0 if volume is None else max(0, volume - cum)
        bar = held._replace(
            h=max(held.h, price),
            low=min(held.low, price),
            c=price,
            v=held.v + added,
            oi=held.oi if oi is None else oi,
            atp=held.atp if atp is None else atp,
            until=at,
        )
        state.sealed[start], state.cum = bar, cum + added
        state.cums[start] = state.cum
        return Sealed(key, bar, state.cum)

    @staticmethod
    def _before_open(state: _Key, at: int, volume: int | None, today: int) -> None:
        """A frame whose last trade was before the open, today: its running total is the pre-open
        volume. A frame from an earlier day (its last trade before today began) is not."""
        if volume is None:
            return
        if at >= epoch_ms(key_day(today), 0) and (state.pre is None or volume > state.pre):
            state.pre = volume

    @staticmethod
    def _exchange_candle(state: _Key, candle_1m, recv: int) -> None:
        """Take the exchange's candle for a minute that has trades, if it came after the minute and
        before its seal, for exactly that minute, and is a candle."""
        if not isinstance(candle_1m, Mapping):
            return
        start = _ms(candle_1m.get("ts"))
        minute = state.open.get(start)
        if minute is None or not start + MINUTE_MS <= recv < seal_at(start + MINUTE_MS):
            return
        values = [paise(candle_1m.get(name)) for name in ("open", "high", "low", "close")]
        volume = _count(candle_1m.get("vol"))
        if None in values or volume is None:
            return
        o, h, low, c = values
        if low <= min(o, c) and max(o, c) <= h:
            minute.i1 = (o, h, low, c, volume)

    @staticmethod
    def _seal(key: str, state: _Key, start: int) -> Sealed:
        minute = state.open.pop(start)
        cum = state.cum if state.cum is not None else (state.pre or 0)
        volume = max(0, minute.vtt - cum) if minute.vtt is not None else 0
        o, h, low, c, flags = minute.o, minute.h, minute.low, minute.c, minute.flags
        if minute.i1 is not None:
            o, h, low, c, exchange = minute.i1
            volume, flags = max(volume, exchange), flags | FROM_I1
        state.cum = cum + volume
        bar = Bar(start, o, h, low, c, volume, minute.oi, minute.atp, flags, minute.last)
        state.sealed[start], state.cums[start] = bar, state.cum
        return Sealed(key, bar, state.cum)
