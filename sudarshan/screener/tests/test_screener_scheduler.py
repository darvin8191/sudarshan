"""When a screen runs (SV6, design 3.6): boundaries per segment, the grace, special days, late
runs, recorded keys, the after-close run, the auction hold and the next check."""

from __future__ import annotations

from datetime import date

from sudarshan.alerts.market.sessions import epoch_ms, sessions_of
from sudarshan.screener import scheduler as sch
from sudarshan.screener.types import RunKey

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
    held = sch.due([CAS], NORMAL, at_close, lambda k: False, printed=lambda s: False)
    assert [d.boundary.k for d in held][-1] == 23, "the 15:15-15:30 candle waits for the prints"
    printed = sch.due([CAS], NORMAL, at_close, lambda k: False, printed=lambda s: True)
    assert [d.boundary.k for d in printed][-1] == 24
    latest = sch.due(
        [CAS], NORMAL, hm(DAY, "15:35") + 5_000, lambda k: False, printed=lambda s: False
    )
    assert [d.boundary.k for d in latest][-1] == 24, "15:35:05 at the latest (S12)"
    assert [
        d.boundary.k
        for d in sch.due(
            [CAS], NORMAL, hm(DAY, "15:35") + 4_999, lambda k: False, printed=lambda s: False
        )
    ][-1] == 23


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
