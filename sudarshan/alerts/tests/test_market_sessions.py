"""Sessions per segment (amendments A1 and A2): usual hours, special days, the calendar helpers."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from sudarshan.alerts.market import sessions
from sudarshan.autologin.schedule import IST, parse_window

WED = date(2026, 10, 21)
MUHURAT = date(2026, 11, 8)  # a Sunday


def clock(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, IST).strftime("%Y-%m-%d %H:%M")


@pytest.mark.parametrize(
    ("segment", "start", "end"),
    [
        ("NSE_EQ", "09:15", "15:30"),
        ("BSE_EQ", "09:15", "15:30"),
        ("NSE_INDEX", "09:15", "15:30"),
        ("BSE_INDEX", "09:15", "15:30"),
        ("NSE_FO", "09:15", "15:40"),
        ("BSE_FO", "09:15", "15:40"),
    ],
)
def test_usual_session_per_segment(segment, start, end):
    found = sessions.session(segment, WED, trading_day=True)
    assert (clock(found.start), clock(found.end)) == (f"2026-10-21 {start}", f"2026-10-21 {end}")
    assert found.key == 20261021 and found.segment == segment


@pytest.mark.parametrize(
    ("segment", "start", "end"),
    [("NSE_EQ", "14:20", "15:30"), ("NSE_INDEX", "14:20", "15:30"), ("NSE_FO", "14:20", "15:40")],
)
def test_muhurat_window_gives_each_segment_its_share(segment, start, end):
    """The extra-days line ``2026-11-08 14:05-15:40``: pre-open 14:05, normal market 14:20."""
    found = sessions.session(segment, MUHURAT, trading_day=True, window=parse_window("14:05-15:40"))
    assert (clock(found.start), clock(found.end)) == (f"2026-11-08 {start}", f"2026-11-08 {end}")


def test_an_evening_window_keeps_the_offsets():
    window = parse_window("17:45-19:30")
    assert sessions.hours("NSE_EQ", window, day=WED) == (18 * 60, 19 * 60 + 20)
    assert sessions.hours("NSE_EQ", window, day=WED, auction=True) == (18 * 60, 19 * 60 + 5)
    assert sessions.hours("NSE_FO", window, day=WED) == (18 * 60, 19 * 60 + 30)
    # From 7 Dec 2026 the derivatives close 5 minutes after the 10-minute auction.
    assert sessions.hours("NSE_EQ", window, day=DEC7) == (18 * 60, 19 * 60 + 15)
    assert sessions.hours("NSE_EQ", window, day=DEC7, auction=True) == (18 * 60, 19 * 60 + 15)
    assert sessions.hours("NSE_FO", window, day=DEC7) == (18 * 60, 19 * 60 + 30)


def test_a_window_too_short_for_a_segment_leaves_it_closed():
    window = parse_window("14:00-14:20")
    assert sessions.hours("NSE_EQ", window, day=WED) is None  # would be 14:15-14:10
    assert sessions.hours("NSE_FO", window, day=WED) == (14 * 60 + 15, 14 * 60 + 20)
    assert sessions.session("NSE_EQ", WED, trading_day=True, window=window) is None


DEC7 = date(2026, 12, 7)


@pytest.mark.parametrize(
    ("segment", "day", "auction", "hours"),
    [
        ("NSE_EQ", WED, True, ("09:15", "15:15")),  # NSE/CMTR/75479: CAS securities' close
        ("BSE_EQ", WED, True, ("09:15", "15:15")),
        ("NSE_EQ", WED, False, ("09:15", "15:30")),
        ("NSE_INDEX", WED, True, ("09:15", "15:30")),  # an index does not close by auction
        ("NSE_FO", WED, True, ("09:15", "15:40")),
        ("NSE_EQ", DEC7, True, ("09:15", "15:30")),  # SEBI, 15 Oct 2026: auction 15:30-15:40
        ("NSE_EQ", DEC7, False, ("09:15", "15:30")),
        ("NSE_FO", DEC7, False, ("09:15", "15:45")),
        ("BSE_FO", DEC7, False, ("09:15", "15:45")),
        ("NSE_EQ", date(2026, 12, 4), True, ("09:15", "15:15")),  # the Friday before
    ],
)
def test_the_closing_auction_and_the_7_december_timings(segment, day, auction, hours):
    found = sessions.session(segment, day, trading_day=True, auction=auction)
    assert (clock(found.start)[11:], clock(found.end)[11:]) == hours
    stock = auction and segment in sessions.AUCTION_SEGMENTS
    assert found.segment == (segment + "+auction" if stock else segment)


@pytest.mark.parametrize(
    ("day", "window", "expected", "close"),
    [
        (WED, None, ("15:15", "15:35"), "15:30"),  # 27 Oct 2026: printed at 15:28:09 (X6j)
        (MUHURAT, "14:05-15:40", ("15:15", "15:35"), "15:30"),  # also on 8 Nov 2026 (X6b)
        (DEC7, None, ("15:30", "15:40"), "15:40"),  # every stock's auction 15:30-15:40
    ],
)
def test_the_auction_window_a_cas_key_carries(day, window, expected, close):
    span = sessions.auction_window(
        "NSE_EQ", day, trading_day=True, window=parse_window(window) if window else None
    )
    assert (clock(span[0])[11:], clock(span[1])[11:]) == expected
    found = sessions.session("NSE_EQ", day, trading_day=True, auction=True,
                             window=parse_window(window) if window else None)  # fmt: skip
    assert (found.end, found.auction_until) == span
    expected_close = sessions.auction_close(
        "NSE_EQ", day, trading_day=True, window=parse_window(window) if window else None
    )
    assert clock(expected_close)[11:] == close == clock(found.auction_close)[11:]
    assert sessions.auction_window("NSE_INDEX", day, trading_day=True) is None
    assert sessions.auction_close("NSE_INDEX", day, trading_day=True) is None  # X6k
    assert sessions.auction_window("NSE_EQ", day, trading_day=False) is None
    assert sessions.auction_close("NSE_EQ", day, trading_day=False) is None
    plain = sessions.session("NSE_EQ", day, trading_day=True)
    assert (plain.auction_until, plain.auction_close) == (None, None)


@pytest.mark.parametrize(
    ("segment", "day", "derivatives", "expected"),
    [
        ("NSE_EQ", WED, True, True),
        ("NSE_EQ", WED, False, False),  # no futures: trades on to 15:30
        ("BSE_EQ", WED, True, True),  # BSE taken as NSE's list until a BSE check
        ("NSE_EQ", DEC7, False, True),  # from 7 Dec 2026 every stock
        ("NSE_INDEX", WED, True, False),
        ("NSE_FO", DEC7, True, False),
    ],
)
def test_which_stocks_close_by_auction(segment, day, derivatives, expected):
    assert sessions.closes_by_auction(segment, day, derivatives=derivatives) is expected


def test_the_muhurat_session_of_a_stock_that_closes_by_auction():
    """NSE/CMTR/76941 (8 Nov 2026): normal market 14:20; CAS securities close 15:15; others 15:30."""
    window = parse_window("14:05-15:40")
    every = sessions.sessions_of(MUHURAT, trading_day=True, window=window)
    assert {k: (clock(v.start)[11:], clock(v.end)[11:]) for k, v in every.items()} == {
        "NSE_EQ": ("14:20", "15:30"), "NSE_EQ+auction": ("14:20", "15:15"),
        "BSE_EQ": ("14:20", "15:30"), "BSE_EQ+auction": ("14:20", "15:15"),
        "NSE_INDEX": ("14:20", "15:30"), "BSE_INDEX": ("14:20", "15:30"),
        "NSE_FO": ("14:20", "15:40"), "BSE_FO": ("14:20", "15:40"),
    }  # fmt: skip
    assert sessions.sessions_of(WED, trading_day=False) == {}


@pytest.mark.parametrize(
    ("symbol", "index"),
    [
        ("NIFTY08DEC2625900CE", True),
        ("BANKNIFTY29DEC26FUT", True),
        ("NIFTYNXT5029DEC26FUT", True),
        ("SENSEX10DEC2682000PE", True),
        ("SENSEX5010DEC26FUT", True),
        ("RELIANCE29DEC261400CE", False),
        ("BANKBARODA29DEC26FUT", False),
        ("NIFTY", False),  # the index itself
        ("NIFTYBEES", False),
    ],
)
def test_index_derivatives_are_told_from_stock_derivatives(symbol, index):
    assert sessions.index_derivative(symbol) is index


@pytest.mark.parametrize(
    ("expiry", "index", "closes"),
    [
        (WED, True, "15:40"),
        (WED, False, "15:40"),
        (date(2026, 12, 8), True, "15:30"),  # an expiring index contract stops at 15:30
        (date(2026, 12, 29), False, "15:45"),  # a stock contract trades to 15:45 on expiry
    ],
)
def test_a_contract_stops_on_its_expiry_day(expiry, index, closes):
    assert clock(sessions.expiry_close("NSE_FO", expiry, index=index))[11:] == closes
    assert sessions.expiry_close("MCX_FO", expiry) is None


def test_no_session_on_a_closed_day_or_an_uncovered_segment():
    assert sessions.session("NSE_EQ", WED, trading_day=False) is None
    assert sessions.session("MCX_FO", WED, trading_day=True) is None
    assert sessions.hours("NCD_FO") is None


def test_segment_and_day_keys():
    assert sessions.segment_of("NSE_INDEX|Nifty 50") == "NSE_INDEX"
    assert sessions.day_key(MUHURAT) == 20261108 and sessions.key_day(20261108) == MUHURAT
    assert (
        sessions.epoch_ms(WED, 555)
        == int(datetime(2026, 10, 21, 9, 15, tzinfo=IST).timestamp()) * 1000
    )
    assert clock(sessions.epoch_ms(WED, 24 * 60)) == "2026-10-22 00:00"


HOLIDAYS = {date(2026, 10, 20)}  # Tuesday


def trades(day: date) -> bool:
    return day == MUHURAT or (day.weekday() < 5 and day not in HOLIDAYS)


def test_previous_trading_day_skips_weekends_and_holidays():
    assert sessions.previous_trading_day(WED, trades) == date(2026, 10, 19)  # over the holiday
    assert sessions.previous_trading_day(date(2026, 10, 26), trades) == date(2026, 10, 23)
    assert sessions.previous_trading_day(date(2026, 11, 9), trades) == MUHURAT
    assert sessions.previous_trading_day(WED, lambda day: False) is None


def test_next_session_for_the_market_closed_words():
    def window_of(day):
        return parse_window("14:05-15:40") if day == MUHURAT else None

    def nxt(at: datetime, segment="NSE_EQ"):
        found = sessions.next_session(
            segment, int(at.timestamp() * 1000), is_trading_day=trades, window_of=window_of
        )
        return clock(found.start) if found else None

    assert nxt(datetime(2026, 10, 21, 8, 0, tzinfo=IST)) == "2026-10-21 09:15"
    assert nxt(datetime(2026, 10, 21, 9, 15, tzinfo=IST)) == "2026-10-22 09:15"  # started
    assert nxt(datetime(2026, 10, 23, 15, 30, tzinfo=IST)) == "2026-10-26 09:15"  # Fri close
    assert nxt(datetime(2026, 11, 6, 16, 0, tzinfo=IST)) == "2026-11-08 14:20"  # Muhurat
    assert nxt(datetime(2026, 11, 6, 16, 0, tzinfo=IST), "NSE_FO") == "2026-11-08 14:20"
    never = sessions.next_session(
        "NSE_EQ", 0, is_trading_day=lambda day: False, window_of=window_of
    )
    assert never is None
