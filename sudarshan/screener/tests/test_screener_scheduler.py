"""When a screen runs (SV6, design 3.6): boundaries per segment, the grace, special days, late
runs, recorded keys, the after-close run, the auction hold and the next check."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest

from sudarshan.alerts.market.sessions import epoch_ms, sessions_of
from sudarshan.screener import membership as mem
from sudarshan.screener import scheduler as sch
from sudarshan.screener.runs import Member
from sudarshan.screener.types import HIT, MISS, RunKey

#: A screen whose stocks that close by auction have not all printed yet.
NOT_PRINTED = sch.Waits(printed=None)
#: ... and one whose prints are in (or that holds no such stock).
PRINTED = sch.Waits(printed=0)

DAY = date(2026, 10, 28)  # a normal trading Wednesday
MUHURAT = date(2026, 11, 8)  # 14:05-15:40 window: cash 14:20-15:30, F&O 14:20-15:40
NORMAL = sessions_of(DAY, trading_day=True)
SPECIAL = sessions_of(MUHURAT, trading_day=True, window=(14 * 60 + 5, 15 * 60 + 40))
CASH = sch.Screen(1, 1, "close", 15, frozenset({"NSE_EQ"}))
FO = sch.Screen(2, 1, "close", 15, frozenset({"NSE_FO"}))
BOTH = sch.Screen(3, 2, "close", 15, frozenset({"NSE_EQ", "NSE_FO"}))
CAS = sch.Screen(4, 1, "close", 15, frozenset({"NSE_EQ", "NSE_EQ+auction"}))
EOD = sch.Screen(5, 1, "after_close", 0, frozenset({"NSE_EQ"}))


def hm(day: date, text: str) -> int:
    hours, minutes = text.split(":")
    return epoch_ms(day, int(hours) * 60 + int(minutes))


def times(day: date, found: list[sch.Boundary], field: str = "end") -> list[str]:
    out = []
    for b in found:
        at = getattr(b, field)
        minutes = (at - epoch_ms(day, 0)) // 60_000
        seconds = (at - epoch_ms(day, 0)) % 60_000 // 1000
        out.append(
            f"{minutes // 60:02d}:{minutes % 60:02d}" + (f":{seconds:02d}" if seconds else "")
        )
    return out


def test_a_15_minute_cash_screen_runs_at_every_candle_end_plus_five_seconds():
    found = sch.boundaries(CASH, NORMAL)
    ends = times(DAY, found)
    assert ends[:2] == ["09:30", "09:45"] and ends[-1] == "15:30" and len(ends) == 25
    assert times(DAY, found, "at")[0] == "09:30:05"  # S11: end + 5 s, never + 2 s
    assert [b.k for b in found] == list(range(25)) and found[0].tf == "15"
    assert found[0].start == NORMAL["NSE_EQ"].start


def test_muhurat_boundaries_match_the_designs_special_day_vectors():
    assert times(MUHURAT, sch.boundaries(CASH, SPECIAL)) == [
        "14:35",
        "14:50",
        "15:05",
        "15:20",
        "15:30",
    ]
    assert times(MUHURAT, sch.boundaries(FO, SPECIAL)) == [
        "14:35",
        "14:50",
        "15:05",
        "15:20",
        "15:35",
        "15:40",
    ]
    both = sch.boundaries(BOTH, SPECIAL)
    assert times(MUHURAT, both) == ["14:35", "14:50", "15:05", "15:20", "15:30", "15:35", "15:40"]
    assert [b.k for b in both] == list(range(7)), "one numbering across the union"


def test_after_close_runs_once_at_16_05_over_the_whole_day():
    found = sch.boundaries(EOD, NORMAL)
    assert len(found) == 1 and found[0].tf == "after_close" and found[0].k == 0
    assert times(DAY, found, "at") == ["16:05"]
    assert found[0].start == NORMAL["NSE_EQ"].start and found[0].end == NORMAL["NSE_EQ"].end
    assert sch.boundaries(EOD, sessions_of(date(2026, 10, 25), trading_day=False)) == []


def test_due_runs_are_those_past_their_moment_and_not_recorded_oldest_first():
    now = hm(DAY, "10:01") + 5_000  # 10:01:05: 09:30, 09:45 and 10:00 are due for both screens
    seen = {str(RunKey(1, 1, 20261028, "15", 0))}
    found = sch.due([FO, CASH], NORMAL, now, seen.__contains__)
    keys = [str(d.key) for d in found]
    assert keys == [
        "2:v1:20261028:15:0", "1:v1:20261028:15:1", "2:v1:20261028:15:1",
        "1:v1:20261028:15:2", "2:v1:20261028:15:2",
    ]  # fmt: skip
    assert [d.late for d in found] == [True, True, True, False, False]
    assert sch.due([CASH], NORMAL, hm(DAY, "09:30") + 4_999, lambda k: False) == []
    assert len(sch.due([CASH], NORMAL, hm(DAY, "09:30") + 5_000, lambda k: False)) == 1
    assert (
        sch.due([CASH], sessions_of(date(2026, 10, 25), trading_day=False), now, lambda k: False)
        == []
    )


def test_the_last_cash_boundary_waits_for_the_auction_prints_until_15_35_05():
    at_close = hm(DAY, "15:30") + 5_000
    untouched = sch.due([CASH], NORMAL, at_close, lambda k: False)
    assert [d.boundary.k for d in untouched][-1] == 24, (
        "a screen without auction stocks runs at 15:30:05"
    )
    held = sch.due([CAS], NORMAL, at_close, lambda k: False, waits=lambda s: NOT_PRINTED)
    assert [d.boundary.k for d in held][-1] == 23, "the 15:15-15:30 candle waits for the prints"
    printed = sch.due([CAS], NORMAL, at_close, lambda k: False, waits=lambda s: PRINTED)
    assert [d.boundary.k for d in printed][-1] == 24
    latest = sch.due(
        [CAS], NORMAL, hm(DAY, "15:35") + 5_000, lambda k: False, waits=lambda s: NOT_PRINTED
    )
    assert [d.boundary.k for d in latest][-1] == 24, "15:35:05 at the latest (S12)"
    assert [
        d.boundary.k
        for d in sch.due(
            [CAS], NORMAL, hm(DAY, "15:35") + 4_999, lambda k: False, waits=lambda s: NOT_PRINTED
        )
    ][-1] == 23


@pytest.mark.parametrize(
    ("day", "close", "latest"),
    [
        (date(2026, 12, 4), "15:30", "15:35"),  # the Friday before: auction to 15:35
        (date(2026, 12, 3), "15:30", "15:35"),  # a SENSEX expiry before the change
        (date(2026, 12, 8), "15:30", "15:45"),  # auction 15:30-15:40, prints to 15:45 (L4)
        (date(2026, 12, 10), "15:30", "15:45"),
    ],
)
def test_the_last_cash_boundary_waits_for_the_prints_on_both_sides_of_7_december(day, close, latest):
    """From 7 Dec 2026 a stock that closes by auction trades to the cash end and prints 10 minutes
    later: its 15:15-15:30 candle still waits for the print, now to 15:45:05 (the print may come
    until five minutes after the auction's latest close, 15:40)."""
    found = sessions_of(day, trading_day=True)
    last = sch.boundaries(CAS, found)[-1]
    assert times(day, [last]) == [close]
    at_close = hm(day, close) + 5_000
    plain = sch.due([CASH], found, at_close, lambda k: False)
    assert [d.boundary.end for d in plain][-1] == last.end, "without auction stocks: on time"
    held = sch.due([CAS], found, at_close, lambda k: False, waits=lambda s: NOT_PRINTED)
    assert last.k not in [d.boundary.k for d in held], "the last candle waits for the prints"
    printed = sch.due([CAS], found, at_close, lambda k: False, waits=lambda s: PRINTED)
    assert [d.boundary.k for d in printed][-1] == last.k
    for at, runs in ((hm(day, latest) + 4_999, False), (hm(day, latest) + 5_000, True)):
        due = sch.due([CAS], found, at, lambda k: False, waits=lambda s: NOT_PRINTED)
        assert (last.k in [d.boundary.k for d in due]) is runs


def test_next_at_is_the_next_run_today_or_the_first_of_a_later_day():
    assert sch.next_at([CASH, EOD], NORMAL, hm(DAY, "09:20")) == hm(DAY, "09:30") + 5_000
    assert sch.next_at([CASH], NORMAL, hm(DAY, "15:30") + 5_000) is None
    assert sch.next_at([CASH, EOD], NORMAL, hm(DAY, "15:31")) == hm(DAY, "16:05")

    def sessions(day: date):
        trading = day.weekday() < 5 and day != date(2026, 10, 29)  # a holiday on the Thursday
        return sessions_of(day, trading_day=trading)

    friday = date(2026, 10, 30)
    found = sch.next_trading_boundary([CASH], hm(DAY, "16:10"), sessions_of=sessions)
    assert found == hm(friday, "09:30") + 5_000
    assert sch.next_trading_boundary([CASH], hm(DAY, "16:10"), sessions_of=lambda d: {}) is None


# ---- a screen made, resumed or edited during the session (round-3 review MEDIUM-B6) -------------
def ends(found: list[sch.Due]) -> list[tuple[str, bool]]:
    return [(times(DAY, [item.boundary])[0], item.late) for item in found]


def test_a_screen_made_during_the_session_gets_one_run_on_the_latest_closed_candle():
    """The review's probe: a 3-minute screen made at 14:00:10 had every boundary of the day due at
    once, 95 runs and 94 of them late, each a list change and an alert for an old candle."""
    made = hm(DAY, "14:00") + 10_000
    screen = sch.Screen(7, 1, "close", 3, frozenset({"NSE_EQ"}), since=made)
    first = sch.due([screen], NORMAL, made, lambda key: False)
    assert ends(first) == [("14:00", False)]
    then = sch.due([screen], NORMAL, hm(DAY, "14:03") + 5_000, {str(first[0].key)}.__contains__)
    assert ends(then) == [("14:03", False)], "then its schedule, on time"


def test_the_first_run_of_a_new_version_is_not_marked_late():
    """Resumed at 14:02:30, nearly two minutes after the 14:00 candle closed: its first look is
    that candle, and nothing about it was missed by a restart."""
    resumed = hm(DAY, "14:02") + 30_000
    screen = sch.Screen(7, 2, "close", 3, frozenset({"NSE_EQ"}), since=resumed)
    assert ends(sch.due([screen], NORMAL, resumed + 1_000, lambda key: False)) == [("14:00", False)]


def test_a_version_not_begun_yet_has_no_run():
    """The replay check walks a day from its start: a version made at 10:00:10 runs nothing
    before then, not even the 10:00 candle whose moment, 10:00:05, came first."""
    screen = sch.Screen(7, 1, "close", 15, frozenset({"NSE_EQ"}), since=hm(DAY, "10:00") + 10_000)
    for moment in ("09:45", "10:00"):
        now = hm(DAY, moment) + 6_000
        assert sch.due([screen], NORMAL, now, lambda key: False) == [], moment
    assert ends(sch.due([screen], NORMAL, hm(DAY, "10:00") + 10_000, lambda key: False)) == [
        ("10:00", False)
    ]


def test_checks_a_restart_missed_after_the_version_began_are_still_run_late():
    """The guard: a version from before the open, and one edited at 10:00:10 with the service
    down from 10:05 to 11:00, keep their missed checks, each marked late as before."""
    early = sch.Screen(7, 1, "close", 15, frozenset({"NSE_EQ"}), since=hm(DAY, "08:50"))
    assert ends(sch.due([early], NORMAL, hm(DAY, "10:16"), lambda key: False)) == [
        ("09:30", True), ("09:45", True), ("10:00", True), ("10:15", False),
    ]  # fmt: skip
    edited = sch.Screen(7, 2, "close", 15, frozenset({"NSE_EQ"}), since=hm(DAY, "10:00") + 10_000)
    assert ends(sch.due([edited], NORMAL, hm(DAY, "11:01"), lambda key: False)) == [
        ("10:00", True), ("10:15", True), ("10:30", True), ("10:45", True), ("11:00", False),
    ]  # fmt: skip


def test_an_after_close_screen_made_in_the_evening_runs_once_and_not_late():
    screen = sch.Screen(8, 1, "after_close", 0, frozenset({"NSE_EQ"}), since=hm(DAY, "17:00"))
    found = sch.due([screen], NORMAL, hm(DAY, "17:00") + 1_000, lambda key: False)
    assert [(item.boundary.tf, item.late) for item in found] == [("after_close", False)]


# ---- a new version's first run, at its edges (round-4 review LOW-A4) ----------------------------
def test_an_edit_just_after_a_close_first_runs_that_candle_once_and_no_older_one():
    """Edited at 10:00:03, the 15-minute candle that closed at 10:00 is the latest closed: its run
    waits for its moment (10:00:05) and nothing runs for 09:45, which the old rule ran first."""
    edited = hm(DAY, "10:00") + 3_000
    screen = sch.Screen(7, 2, "close", 15, frozenset({"NSE_EQ"}), since=edited)
    done: set[str] = set()
    assert sch.due([screen], NORMAL, edited, done.__contains__) == []
    first = sch.due([screen], NORMAL, hm(DAY, "10:00") + 5_000, done.__contains__)
    assert ends(first) == [("10:00", False)]
    done |= {str(item.key) for item in first}
    assert sch.due([screen], NORMAL, hm(DAY, "10:00") + 9_000, done.__contains__) == []
    assert ends(sch.due([screen], NORMAL, hm(DAY, "10:15") + 5_000, done.__contains__)) == [
        ("10:15", False)
    ]


def test_a_first_run_a_restart_delayed_is_marked_late():
    """Edited at 10:00:10 and the service back at 11:01: the 10:00 candle's check is an hour old,
    so its history line says so. At 10:00:30 (the loop 20 s behind) it is still on time."""
    edited = hm(DAY, "10:00") + 10_000
    screen = sch.Screen(7, 2, "close", 15, frozenset({"NSE_EQ"}), since=edited)
    delayed = sch.due([screen], NORMAL, hm(DAY, "11:01"), lambda key: False)
    assert ends(delayed)[0] == ("10:00", True)
    assert ends(sch.due([screen], NORMAL, edited + 20_000, lambda key: False)) == [("10:00", False)]


def test_an_edit_in_the_evening_runs_the_days_last_candle_once_on_time():
    """Decided (design 3.13): kept. One run at the edit, not marked late."""
    edited = hm(DAY, "20:00")
    screen = sch.Screen(7, 2, "close", 3, frozenset({"NSE_EQ"}), since=edited)
    found = sch.due([screen], NORMAL, edited + 1_000, lambda key: False)
    assert ends(found) == [("15:30", False)]


INDICES = sch.Screen(6, 1, "close", 15, frozenset({"NSE_INDEX"}))
#: The segments a watchlist screen gets from its universe's kind (screen_ops.SEGMENTS_OF).
WATCHLIST = frozenset({"NSE_EQ", "NSE_EQ+auction", "NSE_FO", "NSE_INDEX"})
WATCH = sch.Screen(7, 1, "close", 15, WATCHLIST)
#: Holds an index among its members.
INDEXED = sch.Waits(printed=0, index=True)


@pytest.mark.parametrize(
    ("day", "latest"),
    [(date(2026, 12, 4), None), (date(2026, 12, 10), "15:45")],
)
def test_an_index_screen_waits_for_the_closing_value_from_7_december(day, latest):
    """Review M2: from 7 Dec an index's closing value may come after the cash close, until 15:45.
    A screen holding an index holds its 15:15-15:30 candle until 15:45:05, prints or not (a closing
    value cannot be told from a later one); before 7 Dec it runs at 15:30:05, as it did."""
    found = sessions_of(day, trading_day=True)
    last = sch.boundaries(INDICES, found)[-1]
    assert times(day, [last]) == ["15:30"]
    at_close = hm(day, "15:30") + 5_000
    for screen in (INDICES, WATCH):
        due = sch.due([screen], found, at_close, lambda k: False, waits=lambda s: INDEXED)
        ran = last.k in [d.boundary.k for d in due]
        assert ran is (latest is None), screen.segments
        if latest is not None:
            for at, runs in ((hm(day, latest) + 4_999, False), (hm(day, latest) + 5_000, True)):
                due = sch.due([screen], found, at, lambda k: False, waits=lambda s: INDEXED)
                assert (last.k in [d.boundary.k for d in due]) is runs
    # A screen without indices is not held for them.
    plain = sch.due([CASH], found, at_close, lambda k: False)
    assert [d.boundary.end for d in plain][-1] == last.end


# ---- the holds are the members', and a screen's runs keep their order (review N1) --------------
def walk(
    screen: sch.Screen,
    day: date,
    start: str,
    end: str,
    waits: Callable[[int], sch.Waits],
    made_before: str | None = None,
) -> list[tuple[sch.Due, int]]:
    """Every run made for ``screen`` from ``start`` to ``end``, the runner's tick each second,
    each recorded as it is made: (the run, the moment it was made). Every boundary due before
    ``made_before`` (``start`` when not given: the service was down in between) counts as made on
    time. ``waits(now)`` is what the members say at ``now``."""
    sessions = sessions_of(day, trading_day=True)
    first, done_by = hm(day, start), hm(day, made_before or start)
    done = {
        str(RunKey(screen.id, screen.version, sessions["NSE_EQ"].key, b.tf, b.k))
        for b in sch.boundaries(screen, sessions)
        if b.at < done_by
    }
    made: list[tuple[sch.Due, int]] = []
    for now in range(first, hm(day, end) + 1, 1_000):
        said = waits(now)
        for item in sch.due([screen], sessions, now, done.__contains__, waits=lambda s, w=said: w):
            done.add(str(item.key))
            made.append((item, now))
    return made


def clock(day: date, at: int) -> str:
    return times(day, [sch.Boundary(0, 0, at, at, "")])[0]


def made_at(day: date, made: list[tuple[sch.Due, int]]) -> list[tuple[str, str]]:
    """(candle end, moment made) per run, in the order made."""
    return [(clock(day, item.boundary.end), clock(day, now)) for item, now in made]


@pytest.mark.parametrize("day", [date(2026, 12, 4), date(2026, 12, 10)])
def test_the_index_hold_is_decided_by_the_members_not_by_the_universes_kind(day):
    """Review N1: a watchlist screen trades under every segment a list may hold, NSE_INDEX among
    them, so the first fix held the 15:30 check of every watchlist screen until 15:45:05, after
    the F&O close, whatever the list held. Only a screen whose members include an index waits for
    the index's closing value; one whose members do not runs at 15:30:05, on both sides of 7 Dec,
    as a screen of indices whose universe is not built yet does."""
    found = sessions_of(day, trading_day=True)
    at_close = hm(day, "15:30") + 5_000
    last = next(b for b in sch.boundaries(WATCH, found) if clock(day, b.end) == "15:30")
    for screen, waits in ((WATCH, PRINTED), (INDICES, PRINTED), (WATCH, sch.NO_WAITS)):
        due = sch.due([screen], found, at_close, lambda k: False, waits=lambda s, w=waits: w)
        assert last.end in [d.boundary.end for d in due], (screen.segments, waits)
    held = sch.due([WATCH], found, at_close, lambda k: False, waits=lambda s: INDEXED)
    assert (last.end in [d.boundary.end for d in held]) is (day < date(2026, 12, 7))


def test_the_members_say_what_the_last_candle_waits_for():
    """``waits_of``: an index among the members, and the auction prints of the stocks that close
    by auction (when the last one arrived; None while one has not come)."""
    nifty = Member("NSE_INDEX|Nifty 50", "NIFTY", "NSE_INDEX", "NSE_INDEX")
    reliance = Member("NSE_EQ|INE002A01018", "RELIANCE", "NSE", "NSE_EQ", cas=True)
    sbin = Member("NSE_EQ|INE062A01020", "SBIN", "NSE", "NSE_EQ", cas=True)
    plain = Member("NSE_EQ|INE009A01099", "PLAIN", "NSE", "NSE_EQ")
    arrived = {reliance: 1_000, sbin: 2_000}
    assert sch.waits_of([], arrived.get) == sch.NO_WAITS
    assert sch.waits_of([plain], arrived.get) == sch.Waits(printed=0, index=False)
    assert sch.waits_of([plain, nifty], arrived.get) == sch.Waits(printed=0, index=True)
    assert sch.waits_of([reliance, sbin, plain], arrived.get) == sch.Waits(printed=2_000)
    assert sch.waits_of([nifty, reliance], {}.get) == sch.Waits(printed=None, index=True)
    assert sch.waits_of(iter([sbin, nifty]), arrived.get) == sch.Waits(2_000, True)
    assert sch.waits_of([reliance], {reliance: 0}.get) == sch.Waits(printed=0), "arrival unknown"


def test_a_held_index_check_holds_the_screens_later_checks_in_order():
    """Thu 10 Dec, a 5-minute watchlist screen holding an index: its 15:30 check waits until
    15:45:05, and its 15:35 and 15:40 checks wait with it instead of running first (the first fix
    ran them at 15:35:05 and 15:40:05); at 15:45:05 all four run in boundary order."""
    day = date(2026, 12, 10)
    screen = sch.Screen(9, 1, "close", 5, WATCHLIST)
    made = walk(screen, day, "15:30", "15:46", lambda now: INDEXED)
    assert made_at(day, made) == [
        ("15:30", "15:45:05"), ("15:35", "15:45:05"), ("15:40", "15:45:05"), ("15:45", "15:45:05"),
    ]  # fmt: skip


def test_a_check_held_for_the_prints_holds_the_screens_later_checks_in_order():
    """Thu 10 Dec, a 5-minute watchlist screen with stocks that close by auction and no index:
    the prints arrive at 15:39:40. The 15:35 check no longer runs before the held 15:30 one; both
    run as the prints arrive, then 15:40 and 15:45 on time."""
    day = date(2026, 12, 10)
    screen = sch.Screen(9, 1, "close", 5, WATCHLIST)
    prints = hm(day, "15:39") + 40_000

    def waits(now: int) -> sch.Waits:
        return sch.Waits(printed=prints) if now >= prints else NOT_PRINTED

    made = walk(screen, day, "15:30", "15:46", waits)
    assert made_at(day, made) == [
        ("15:30", "15:39:40"), ("15:35", "15:39:40"), ("15:40", "15:40:05"), ("15:45", "15:45:05"),
    ]  # fmt: skip


def test_before_7_december_a_missing_print_holds_a_one_minute_screen_in_order():
    """Fri 4 Dec: a 1-minute watchlist screen with a stock whose print has not come holds its
    15:30 check until 15:35:05, the window's end; its 15:31 to 15:34 checks (F&O trades to 15:40)
    wait with it and then run in order, instead of running first."""
    day = date(2026, 12, 4)
    screen = sch.Screen(9, 1, "close", 1, WATCHLIST)
    made = walk(screen, day, "15:30", "15:37", lambda now: NOT_PRINTED)
    assert made_at(day, made) == [
        ("15:30", "15:35:05"), ("15:31", "15:35:05"), ("15:32", "15:35:05"),
        ("15:33", "15:35:05"), ("15:34", "15:35:05"), ("15:35", "15:35:05"),
        ("15:36", "15:36:05"),
    ]  # fmt: skip
    on_time = walk(screen, day, "15:30", "15:33", lambda now: PRINTED)
    assert made_at(day, on_time) == [
        ("15:30", "15:30:05"), ("15:31", "15:31:05"), ("15:32", "15:32:05"),
    ]  # fmt: skip


def test_no_member_is_marked_missing_and_then_back_because_of_the_hold():
    """The review's case on Thu 10 Dec: a member of a 5-minute screen holding an index that does
    not match on the 15:25-15:30 candle and matches from 15:35. Its runs applied as they are made
    enter it at 15:35 and keep it; the first fix applied 15:35 and 15:40 first, then the held
    15:30 run marked it missing, and 15:45 put it back."""
    day = date(2026, 12, 10)
    screen = sch.Screen(9, 1, "close", 5, WATCHLIST)
    outcome = {"15:30": MISS, "15:35": HIT, "15:40": HIT, "15:45": HIT}
    row, events = None, []
    for item, _ in walk(screen, day, "15:30", "15:46", lambda now: INDEXED):
        end = clock(day, item.boundary.end)
        result = mem.Result("NSE_FO|1", "FUT", "NFO", outcome[end], price=10_000)
        run = mem.Run(item.boundary.k, item.boundary.end, item.key.session, last=item.last)
        row, event = mem.step(row, result, run, mem.Grace(mem.RUNS, 1))
        events.append((end, event))
    assert events == [
        ("15:30", None), ("15:35", mem.EV_ENTERED), ("15:40", mem.EV_STILL),
        ("15:45", mem.EV_STILL),
    ]  # fmt: skip
    assert row is not None and row.status == mem.ACTIVE and row.first_seen_at == hm(day, "15:35")


# ---- a held run is late only when made late after its hold ended (review N2) --------------------
CAS_15 = sch.Screen(10, 1, "close", 15, frozenset({"NSE_EQ", "NSE_EQ+auction"}))


def late_at(day: date, made: list[tuple[sch.Due, int]]) -> list[tuple[str, str, bool]]:
    """(candle end, moment made, marked late) per run, in the order made."""
    return [(clock(day, d.boundary.end), clock(day, now), d.late) for d, now in made]


def prints_at(at: int) -> Callable[[int], sch.Waits]:
    """The members' word when every print has arrived by ``at``."""
    return lambda now: sch.Waits(printed=at) if now >= at else NOT_PRINTED


def test_a_held_check_made_as_its_hold_ends_is_not_late():
    """Thu 10 Dec: the 15:30 check of an F&O stocks screen made as the prints arrive (15:39:40),
    or at 15:45:05 when one never comes, and of an index screen at 15:45:05, is on time; so are
    the later checks the hold kept waiting. The first fix marked every one "checked late ... after
    a restart", on the Screener page and in the Telegram digest, when nothing had restarted."""
    day = date(2026, 12, 10)
    prints = hm(day, "15:39") + 40_000
    assert late_at(day, walk(CAS_15, day, "15:30", "15:46", prints_at(prints))) == [
        ("15:30", "15:39:40", False),
    ]
    assert late_at(day, walk(CAS_15, day, "15:30", "15:46", lambda now: NOT_PRINTED)) == [
        ("15:30", "15:45:05", False),
    ]
    assert late_at(day, walk(INDICES, day, "15:30", "15:46", lambda now: INDEXED)) == [
        ("15:30", "15:45:05", False),
    ]
    five = sch.Screen(9, 1, "close", 5, WATCHLIST)
    assert late_at(day, walk(five, day, "15:30", "15:46", lambda now: INDEXED)) == [
        ("15:30", "15:45:05", False), ("15:35", "15:45:05", False),
        ("15:40", "15:45:05", False), ("15:45", "15:45:05", False),
    ]  # fmt: skip
    assert late_at(day, walk(five, day, "15:30", "15:46", prints_at(prints))) == [
        ("15:30", "15:39:40", False), ("15:35", "15:39:40", False),
        ("15:40", "15:40:05", False), ("15:45", "15:45:05", False),
    ]  # fmt: skip


def test_a_restart_past_the_hold_is_still_late():
    """Thu 10 Dec, the service down from 15:20 to 15:50 (a restart forgets the prints it saw): the
    held checks made at 15:50 are late, each counted from its hold's end, 15:45:05. A restart that
    ended inside the hold delays nothing: the checks come at 15:45:05, on time. A check made more
    than a minute after the last print arrived is late too."""
    day = date(2026, 12, 10)
    back = hm(day, "15:50")
    five = sch.Screen(9, 1, "close", 5, WATCHLIST)
    for screen, waits in ((CAS_15, NOT_PRINTED), (INDICES, INDEXED), (five, INDEXED)):
        due = sch.due([screen], sessions_of(day, trading_day=True), back, set().__contains__,
                      waits=lambda s, w=waits: w)  # fmt: skip
        mine = [d for d in due if d.boundary.end >= hm(day, "15:30")]
        assert mine and all(d.late for d in mine), screen.segments
    for screen, waits in ((CAS_15, NOT_PRINTED), (INDICES, INDEXED)):
        made = walk(screen, day, "15:44", "15:46", lambda now, w=waits: w, made_before="15:20")
        assert late_at(day, made) == [("15:30", "15:45:05", False)], screen.segments
    prints = hm(day, "15:39") + 40_000
    for when, late in ((prints + 60_000, False), (prints + 61_000, True)):
        due = sch.due([CAS_15], sessions_of(day, trading_day=True), when, set().__contains__,
                      waits=lambda s: sch.Waits(printed=prints))  # fmt: skip
        assert [(clock(day, d.boundary.end), d.late) for d in due][-1] == ("15:30", late)


def test_before_7_december_a_check_is_late_as_it_was():
    """Fri 4 Dec: the prints come inside the session (15:28:09), so the 15:30 check is made at
    15:30:05, on time, and one a restart delayed past its moment is late, as before. A print that
    never comes holds it to 15:35:05, the window's end: on time, where the first fix said late."""
    day = date(2026, 12, 4)
    sessions = sessions_of(day, trading_day=True)
    early = sch.Waits(printed=hm(day, "15:28") + 9_000)
    assert late_at(day, walk(CAS_15, day, "15:29", "15:31", lambda now: early)) == [
        ("15:30", "15:30:05", False),
    ]
    restarted = sch.due([CAS_15], sessions, hm(day, "15:32"), lambda k: False, waits=lambda s: early)
    assert [(clock(day, d.boundary.end), d.late) for d in restarted][-1] == ("15:30", True)
    missed = sch.due(
        [CAS_15], sessions, hm(day, "15:40"), lambda k: False, waits=lambda s: NOT_PRINTED
    )
    assert [(clock(day, d.boundary.end), d.late) for d in missed][-1] == ("15:30", True)
    assert late_at(day, walk(CAS_15, day, "15:30", "15:36", lambda now: NOT_PRINTED)) == [
        ("15:30", "15:35:05", False),
    ]
