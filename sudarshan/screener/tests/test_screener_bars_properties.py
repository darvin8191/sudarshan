"""bars.py and candles.py against a plain model, the alerts' own Book, and themselves.

The vectors pin what the design names. These tests pin what must hold for any day: the bars are what
a plain reading of the trades gives, however the trades of a minute arrive; a restart in the middle
of a day changes nothing; a repair leaves no bar unverified and no other bar touched; a candle of a
longer timeframe is the aggregate of the candles inside it; and the closing auction's frames are
judged by the same rule as the alerts' Book judges them.
"""

from __future__ import annotations

import ast
import random
from datetime import date
from pathlib import Path

import pytest
from screener_helpers import day_key, ms

from sudarshan.alerts.market import clock, sessions, spec
from sudarshan.alerts.market.book import Book
from sudarshan.screener import bars as B
from sudarshan.screener import candles as C
from sudarshan.screener import types as T

DAY = "2026-10-21"
KEY = "NSE_EQ|INE002A01018"
SOURCE = Path(__file__).parents[1]
MINUTE = 60_000


def market_for(day: str, now: int, *, auction=(), pauses=(), age: int = 0) -> clock.Market:
    return clock.Market(
        today=day_key(day),
        sessions=sessions.sessions_of(date.fromisoformat(day), trading_day=True),
        previous=20261019,
        feed=clock.Feed("connected", True, now - age),
        pauses=tuple(pauses),
        auction=frozenset(auction),
    )


# ---- the alerts' Book judges the closing auction's frames the way Bars does -----------------------
def price_alert(key: str) -> spec.Alert:
    stock = {"instrument_key": key, "symbol": "SBIN", "exchange": "NSE", "tick_size": "0.05"}
    fields = {"kind": "price", "params": {"op": "at_or_above", "level": "1"}}
    fields |= {"frequency": "every_time", "cooldown_s": 60}  # stays Active through the whole day
    row = spec.check(stock | fields, ms(DAY, "09:00"))
    return spec.Alert.of(row | {"id": 1, "version": 1, "status": "active"})


#: (looked at, last trade at, price, quantity, running total or None, age of the feed's health in ms)
COMMON = [
    ("15:14:58.050", "15:14:58.000", 812.00, 5, 100000, 0),
    ("15:20:00.050", "15:20:00.000", 812.00, 5, 100000, 0),
    ("15:21:00.050", "15:21:00.000", 812.00, 5, None, 0),
    ("15:22:00.050", "15:22:00.000", 812.00, 5, 99000, 0),
    ("15:31:11.000", "15:31:10.000", 812.40, 2000, 102000, 20000),
    ("15:31:12.000", "15:31:11.000", 812.40, 2001, 102000, 0),
    ("15:31:13.000", "15:31:12.000", 812.40, 2002, 102500, 0),
    ("15:31:14.000", "15:31:13.000", 812.45, 100, 102600, 0),
    ("15:35:00.100", "15:35:00.000", 812.60, 100, 102700, 0),
    ("15:35:30.000", "15:35:20.000", 812.70, 100, 102800, 0),
]
SEVENTH_DEC = [
    ("15:29:10.050", "15:29:10.000", 812.00, 5, 100000, 0),
    ("15:29:59.550", "15:29:59.500", 812.10, 3, 100500, 0),
    ("15:31:00.100", "15:31:00.000", 812.10, 3, 100500, 0),
    ("15:38:20.100", "15:38:20.000", 811.60, 2500, 103000, 0),
    ("15:39:00.100", "15:39:00.000", 811.70, 100, 103100, 0),
    ("15:40:00.100", "15:40:00.000", 811.80, 100, 103200, 0),
    ("15:40:30.100", "15:40:30.000", 811.90, 100, 103300, 0),
]


@pytest.mark.parametrize(
    ("day", "frames", "auction"),
    [
        ("2026-10-21", COMMON, True),
        ("2026-10-21", COMMON, False),
        ("2026-12-08", SEVENTH_DEC, True),
        ("2026-12-08", SEVENTH_DEC, False),
    ],
    ids=["3-aug-cas", "3-aug-continuous", "7-dec-cas", "7-dec-continuous"],
)
def test_the_closing_auctions_frames_are_judged_as_the_alerts_book_judges_them(
    day, frames, auction
):
    book, bars = Book(), B.Bars()
    book.put(price_alert(KEY))
    today = day_key(day)
    for looked, last, price, quantity, total, age in frames:
        now = ms(day, looked)
        frame = {"key": KEY, "ts_recv": now, "ltq": quantity, "ltp": price, "ltt": ms(day, last)}
        if total is not None:
            frame["vtt"] = total
        world = market_for(day, now, auction=[KEY] if auction else (), age=age)
        theirs = book.on_tick(frame, now, world).check
        mine = bars.on_tick(frame, now, world).check
        # The Book judges the print once and then sees nothing new; Bars counts a second one as late.
        mine = {clock.AUCTION_PRINT: clock.OK, B.LATE_TRADE: "nothing_new"}.get(mine, mine)
        assert mine == theirs, (looked, last)
        assert bars.printed(KEY, today) == book.printed(KEY, today), looked


# ---- a plain model of the day -----------------------------------------------------------------
def session_trades(rng: random.Random, minutes: list[int]) -> tuple[int, list[list[tuple]]]:
    """Per minute (minute number from midnight), a few trades (offset ms, paise, running total)."""
    total = rng.randrange(0, 5000)
    pre = total
    out, price = [], 10000
    for _ in minutes:
        trades = []
        offsets = sorted(rng.sample(range(0, 59_000), rng.randrange(1, 6)))
        for offset in offsets:
            price = max(1000, price + rng.randrange(-25, 26))
            total += rng.randrange(0, 400)
            trades.append((offset, price, total))
        out.append(trades)
    return pre, out


def model(pre: int, minutes: list[int], per_minute) -> list[dict]:
    rows, behind = [], pre
    for minute, trades in zip(minutes, per_minute, strict=True):
        prices = [p for _, p, _ in trades]
        top = max(t for _, _, t in trades)
        rows.append(
            {
                "start": ms(DAY, f"{minute // 60:02d}:{minute % 60:02d}"),
                "o": trades[0][1],
                "h": max(prices),
                "l": min(prices),
                "c": trades[-1][1],
                "v": max(0, top - behind),
                "last": trades[-1][0],
            }
        )
        behind = max(behind, top)
    return rows


def play(bars: B.Bars, pre: int, minutes, per_minute, rng: random.Random | None = None) -> list:
    """Feed the pre-open frame and every minute's trades (arrival order shuffled when ``rng``), seal
    each minute as its grace ends; return every row the Bars wrote."""
    wrote = []
    early = ms(DAY, "09:10")
    start = {"key": KEY, "ltt": early, "ltp": 100.0, "ltq": 1, "vtt": pre, "ts_recv": early + 50}
    bars.on_tick(start, early + 50, market_for(DAY, early + 50))
    for minute, trades in zip(minutes, per_minute, strict=True):
        begin = ms(DAY, f"{minute // 60:02d}:{minute % 60:02d}")
        order = list(range(len(trades)))
        if rng is not None:
            rng.shuffle(order)
        for step, index in enumerate(order):
            offset, price, total = trades[index]
            now = begin + 59_990 + step
            frame = {"key": KEY, "ltt": begin + offset, "ltp": price / 100, "ltq": index + 1,
                     "vtt": total, "ts_recv": now}  # fmt: skip
            result = bars.on_tick(frame, now, market_for(DAY, now))
            assert result.check == clock.OK
            wrote.extend(result.sealed)
        sealed_at = begin + MINUTE + 5000
        wrote.extend(bars.on_clock(sealed_at, market_for(DAY, sealed_at)))
    return wrote


def random_minutes(rng: random.Random) -> list[int]:
    count = rng.randrange(1, 25)
    return sorted(rng.sample(range(9 * 60 + 16, 15 * 60 + 14), count))


@pytest.mark.parametrize("seed", range(25))
def test_the_bars_are_what_a_plain_reading_of_the_trades_gives(seed):
    rng = random.Random(seed)
    minutes = random_minutes(rng)
    pre, per_minute = session_trades(rng, minutes)
    bars = B.Bars()
    wrote = play(bars, pre, minutes, per_minute)
    wanted = model(pre, minutes, per_minute)
    got = bars.bars(KEY)
    assert [(b.start, b.o, b.h, b.low, b.c, b.v, b.until - b.start) for b in got] == [
        (w["start"], w["o"], w["h"], w["l"], w["c"], w["v"], w["last"]) for w in wanted
    ]
    assert all(b.flags == 0 for b in got)
    assert [row.bar for row in wrote] == list(got)
    # the running total accounted for is the largest total seen, and the volumes add up to it
    top = max(t for trades in per_minute for _, _, t in trades)
    assert wrote[-1].cum == max(top, pre)
    assert pre + sum(b.v for b in got) == wrote[-1].cum
    assert [row.cum for row in wrote] == sorted(row.cum for row in wrote)


@pytest.mark.parametrize("seed", range(25))
def test_trades_of_a_minute_in_any_arrival_order_make_the_same_bar(seed):
    rng = random.Random(1000 + seed)
    minutes = random_minutes(rng)
    pre, per_minute = session_trades(rng, minutes)
    in_order, shuffled = B.Bars(), B.Bars()
    play(in_order, pre, minutes, per_minute)
    play(shuffled, pre, minutes, per_minute, rng)
    assert shuffled.bars(KEY) == in_order.bars(KEY)


@pytest.mark.parametrize("seed", range(12))
def test_a_restart_between_two_minutes_changes_nothing(seed):
    rng = random.Random(2000 + seed)
    minutes = random_minutes(rng)
    pre, per_minute = session_trades(rng, minutes)
    straight = B.Bars()
    play(straight, pre, minutes, per_minute)
    cut = rng.randrange(1, len(minutes) + 1)
    before, after = B.Bars(), B.Bars()
    wrote = play(before, pre, minutes[:cut], per_minute[:cut])
    after.restore(day_key(DAY), wrote)
    # the rest of the day, played into the restored Bars (no pre-open frame is repeated)
    for minute, trades in zip(minutes[cut:], per_minute[cut:], strict=True):
        begin = ms(DAY, f"{minute // 60:02d}:{minute % 60:02d}")
        for step, (offset, price, total) in enumerate(trades):
            now = begin + 59_990 + step
            frame = {"key": KEY, "ltt": begin + offset, "ltp": price / 100, "ltq": step + 1,
                     "vtt": total, "ts_recv": now}  # fmt: skip
            assert after.on_tick(frame, now, market_for(DAY, now)).check == clock.OK
        sealed_at = begin + MINUTE + 5000
        after.on_clock(sealed_at, market_for(DAY, sealed_at))
    assert after.bars(KEY) == straight.bars(KEY)


# ---- a repair leaves no bar unverified, and touches no other ----------------------------------------
@pytest.mark.parametrize("seed", range(20))
def test_a_repair_replaces_the_touched_minutes_and_only_them(seed):
    rng = random.Random(3000 + seed)
    minutes = random_minutes(rng)
    pre, per_minute = session_trades(rng, minutes)
    start = ms(DAY, "09:15")
    pauses = []
    for _ in range(rng.randrange(1, 4)):
        begin = start + rng.randrange(0, 5 * 3600) * 1000
        pauses.append(clock.Pause(begin, begin + rng.randrange(1, 300) * 1000, "feed"))
    pauses.sort()
    bars = B.Bars()
    play(bars, pre, minutes, per_minute)
    world = market_for(DAY, ms(DAY, "16:00"), pauses=pauses)
    grid = world.sessions["NSE_EQ"]
    before = {b.start: b for b in bars.bars(KEY)}
    flagged = C.read(bars, KEY, 1, world, grid.end)
    unverified = {
        int(flagged.start[i]) for i in range(len(flagged)) if flagged.flags[i] & T.UNVERIFIED
    }
    marks = dict(zip(before, B.touched(list(before.values()), pauses, grid.start), strict=True))
    rest = []
    for begin in range(grid.start, grid.end, MINUTE):
        low = 9000 + rng.randrange(0, 50)
        rest.append(
            T.Candle(begin, begin + MINUTE, day_key(DAY), low + 5, low + 20, low, low + 10, 7)
        )
    outcome = bars.repair(KEY, rest, grid.end, world)
    after = {b.start: b for b in bars.bars(KEY)}
    hidden = {
        begin
        for begin in range(grid.start, grid.end, MINUTE)
        if begin not in before and clock.paused_during(tuple(pauses), begin, begin + MINUTE)
    }
    replaced = {start for start, hit in marks.items() if hit} | hidden
    assert {row.bar.start for row in outcome.rows} == replaced
    assert outcome.removed == ()
    for begin, bar in after.items():
        if begin in replaced:
            assert bar.flags & T.REPAIRED and bar.v == 7
        else:
            assert bar == before[begin]
    series = C.read(bars, KEY, 1, world, grid.end)
    assert not any(int(flag) & T.UNVERIFIED for flag in series.flags)
    assert unverified == {start for start, hit in marks.items() if hit}
    again = bars.repair(KEY, rest, grid.end, world)
    assert again.rows == () and again.removed == ()


# ---- candles are aggregates of the candles inside them ----------------------------------------------
def random_bars(rng: random.Random, grid) -> list[B.Bar]:
    minutes = sorted(rng.sample(range(0, 375), rng.randrange(1, 120)))
    out, price = [], 10000
    for index in minutes:
        begin = grid.start + index * MINUTE
        opened = price
        price = max(1000, price + rng.randrange(-30, 31))
        high = max(opened, price) + rng.randrange(0, 10)
        low = min(opened, price) - rng.randrange(0, 10)
        flags = rng.choice([0, 0, 0, T.LATE, T.FROM_I1, T.REPAIRED])
        out.append(B.Bar(begin, opened, high, low, price, rng.randrange(0, 900), rng.choice([-1, 5000 + index]),
                         rng.choice([0, 10000 + index]), flags, begin + 30_000))  # fmt: skip
    return out


@pytest.mark.parametrize("seed", range(25))
def test_longer_candles_are_the_aggregate_of_the_shorter_ones_inside_them(seed):
    rng = random.Random(4000 + seed)
    grid = sessions.sessions_of(date.fromisoformat(DAY), trading_day=True)["NSE_EQ"]
    found = random_bars(rng, grid)
    boundary = grid.end
    ones, _ = C.aggregate(found, 1, grid, boundary)
    assert [(c.start, c.o, c.h, c.low, c.c, c.v) for c in ones] == [
        (b.start, b.o, b.h, b.low, b.c, b.v) for b in found
    ]
    for minutes in (3, 5, 15, 30, 60, 0):
        longer, forming = C.aggregate(found, minutes, grid, boundary)
        assert forming is (bool(longer) and longer[-1].end > boundary)
        assert sum(c.v for c in longer) == sum(b.v for b in found)
        for candle in longer:
            inside = [c for c in ones if candle.start <= c.start < candle.end]
            assert inside, "a candle with no bar does not exist"
            assert candle.o == inside[0].o and candle.c == inside[-1].c
            assert candle.h == max(c.h for c in inside) and candle.low == min(c.low for c in inside)
            assert candle.v == sum(c.v for c in inside)
            flags = 0
            for c in inside:
                flags |= c.flags
            assert candle.flags == flags
            kept = [c.oi for c in inside if c.oi >= 0]
            assert candle.oi == (kept[-1] if kept else -1)
            vwap = [c.atp for c in inside if c.atp > 0]
            assert candle.atp == (vwap[-1] if vwap else 0)
        assert [c.start for c in longer] == sorted({c.start for c in longer})


@pytest.mark.parametrize("seed", range(10))
def test_a_boundary_cuts_the_bars_by_their_ends(seed):
    rng = random.Random(5000 + seed)
    grid = sessions.sessions_of(date.fromisoformat(DAY), trading_day=True)["NSE_EQ"]
    found = random_bars(rng, grid)
    for _ in range(8):
        boundary = grid.start + rng.randrange(1, 376) * MINUTE
        kept = [b for b in found if b.end <= boundary]
        ones, forming = C.aggregate(found, 1, grid, boundary)
        assert [c.start for c in ones] == [b.start for b in kept]
        assert forming is False
        for minutes in (3, 5, 15, 30, 60, 0):
            longer, forming = C.aggregate(found, minutes, grid, boundary)
            assert sum(c.v for c in longer) == sum(b.v for b in kept)
            assert forming is (bool(longer) and longer[-1].end > boundary)


# ---- purity -------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["bars.py", "candles.py"])
def test_the_modules_do_no_io_read_no_clock_and_log_nothing(name):
    tree = ast.parse((SOURCE / name).read_text("utf-8"))
    banned = {"time", "datetime", "logging", "os", "sys", "socket", "sqlite3", "threading",
              "asyncio", "random", "subprocess", "pathlib", "json", "httpx", "requests"}  # fmt: skip
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert not imported & banned
    calls = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert not calls & {"open", "print", "input", "exec", "eval"}
