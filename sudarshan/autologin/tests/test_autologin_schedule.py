"""Trading days, the windows that guard a working login, and the 03:30 token expiry, in IST.

Also the two calendar files as every reader on the server reads them: the times, the windows, the
date first, and the deploy tool agreeing byte for byte.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from datetime import date, datetime
from pathlib import Path

import pytest

from sudarshan.autologin import schedule
from sudarshan.autologin.messages import MESSAGES
from sudarshan.autologin.schedule import (
    DEFAULT_WINDOW,
    IST,
    MARKET_HOURS,
    TEST_BLOCKED,
    UPSTOX_TOKEN_EXPIRY,
    TradingCalendar,
    day_windows,
    minutes,
    parse_window,
    trading_session_start,
    union,
)

REPO = Path(__file__).resolve().parents[3]
MONDAY = date(2026, 10, 19)
SATURDAY = date(2026, 10, 24)
HOLIDAY = date(2026, 11, 9)  # in the fake holiday file
MUHURAT = date(2026, 11, 8)


@pytest.fixture
def extra_file(tmp_path):
    return tmp_path / "nse_extra_trading_days.txt"


def test_weekends_and_holidays_are_closed(holidays_file):
    calendar = TradingCalendar(holidays_file)
    assert calendar.is_trading_day(MONDAY)
    assert not calendar.is_trading_day(SATURDAY) and not calendar.is_trading_day(HOLIDAY)


def test_an_extra_day_opens_a_weekend_or_a_holiday(holidays_file, extra_file):
    extra_file.write_text(f"# Muhurat and a special session\n{SATURDAY}\n{HOLIDAY}  # Diwali\n")
    calendar = TradingCalendar(holidays_file, extra_file)
    assert calendar.is_trading_day(SATURDAY) and calendar.is_trading_day(HOLIDAY)
    assert calendar.is_trading_day(MONDAY)
    sunday = date(2026, 10, 25)
    assert not calendar.is_trading_day(sunday)
    # Read again when the file changes, as the holiday file is.
    extra_file.write_text(f"{sunday}\n")
    os.utime(extra_file, ns=(1, 2))
    assert calendar.is_trading_day(sunday) and not calendar.is_trading_day(SATURDAY)


def test_the_extra_days_file_is_optional_and_quiet(holidays_file, extra_file, caplog):
    """Auto-login and the alerts say nothing about it, not even at INFO: only the feed reports."""
    caplog.set_level(logging.INFO)
    calendar = TradingCalendar(holidays_file, extra_file)  # does not exist
    assert calendar.extra_days() == set() and calendar.is_trading_day(MONDAY)
    extra_file.write_text("")  # empty, as shipped
    assert calendar.extra_days() == set()
    assert caplog.text == ""


def test_a_missing_holiday_file_warns_once(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    calendar = TradingCalendar(tmp_path / "missing.txt")
    assert calendar.is_trading_day(HOLIDAY) and calendar.is_trading_day(MONDAY)
    assert caplog.text.count("only weekends count as closed") == 1


@pytest.mark.parametrize(
    ("hhmm", "market", "test_blocked"),
    [
        ("08:59", False, False),
        ("09:00", False, True),  # the pre-open: Test now would replace a working login
        ("09:14", False, True),
        ("09:15", True, True),
        ("15:29", True, True),
        ("15:30", True, True),  # the cash close: F&O trades to 15:40 (since 3 Aug 2026)
        ("15:39", True, True),
        ("15:40", False, False),
    ],
)
def test_windows(holidays_file, hhmm, market, test_blocked):
    calendar = TradingCalendar(holidays_file)
    hour, minute = map(int, hhmm.split(":"))
    at = datetime(2026, 10, 19, hour, minute, tzinfo=IST)
    assert calendar.in_market_hours(at) is market
    assert calendar.blocks_test_now(at) is test_blocked
    saturday = at.replace(day=24)
    assert not calendar.in_market_hours(saturday) and not calendar.blocks_test_now(saturday)


@pytest.mark.parametrize(
    ("at", "since"),
    [
        (datetime(2026, 10, 19, 3, 29, tzinfo=IST), datetime(2026, 10, 18, 3, 30, tzinfo=IST)),
        (datetime(2026, 10, 19, 3, 30, tzinfo=IST), datetime(2026, 10, 19, 3, 30, tzinfo=IST)),
        (datetime(2026, 10, 19, 9, 0, tzinfo=IST), datetime(2026, 10, 19, 3, 30, tzinfo=IST)),
    ],
)
def test_the_last_upstox_token_expiry(at, since):
    assert UPSTOX_TOKEN_EXPIRY == "03:30"
    assert trading_session_start(at, rollover=UPSTOX_TOKEN_EXPIRY) == since


def test_the_page_and_the_answer_give_the_hours_test_now_is_refused():
    """F&O trades to 15:40 since 3 Aug 2026: the guards, the page and the answer all say so."""
    assert MARKET_HOURS == ("09:15", "15:40") and TEST_BLOCKED == ("09:00", "15:40")
    hours = f"from {TEST_BLOCKED[0]} to {TEST_BLOCKED[1]} on a trading day"
    page = REPO / "frontend" / "src" / "sudarshan" / "pages" / "AutoLogin.tsx"
    assert hours in " ".join(page.read_text(encoding="utf-8").split())
    assert hours in MESSAGES["market_hours"]


@pytest.mark.parametrize(
    ("text", "value"),
    [("08:35", 515), ("9:15", 555), (" 15:40 ", 940), ("00:00", 0), ("24:00", 1440)],
)
def test_a_time_of_day(text, value):
    assert minutes(text) == value


@pytest.mark.parametrize(
    "text",
    [
        "15:300",
        "23:60",
        "17:75",
        "1_5:30",
        "+9:00",
        " 9 : 15",
        "०९:१५",
        "09:१५",
        "24:01",
        "25:00",
        "123:00",
        "9",
        "09:5",
        "",
        ":",
        "0x1:00",
        "-1:00",
        "9:15:00",
        "\u300015:40",  # spaces around a time are ASCII spaces only
        "15:40\xa0",
        "\x1c9:15",
    ],  # fmt: skip
)
def test_anything_else_is_not_a_time_of_day(text):
    with pytest.raises(ValueError):
        minutes(text)


def test_a_window_runs_from_its_start_to_a_later_end_and_may_end_at_midnight():
    assert parse_window("14:05-15:40") == (845, 940)
    assert parse_window("22:00-24:00") == (1320, 1440)
    for text in (
        "14:05-14:05",
        "15:40-14:05",
        "14:05",
        "14:05-",
        "14:05-15:40-16:00",
        "14:05–15:40",
    ):
        with pytest.raises(ValueError):
            parse_window(text)


def test_a_days_spans_come_in_order_with_touching_ones_joined_and_empty_ones_dropped():
    assert union([(1065, 1175), (525, 960)]) == ((525, 960), (1065, 1175))
    assert union([(960, 1000), (525, 960)]) == ((525, 1000),)  # touching: one span
    assert union([(525, 960), (600, 700), (900, 1000)]) == ((525, 1000),)
    # A session shorter than its pre-open leaves no continuous trading to watch.
    assert union([(860, 855), (600, 600)]) == ()
    assert union([(555, 940), (1095, 1090)]) == ((555, 940),)


def test_the_window_is_the_word_after_the_date():
    windows, problems = day_windows(
        "2026-11-08 14:05-15:40 F&O  # the second word, never the last\n"
        "2026-11-14  # was 17:45-19:30: a window in a comment is no window\n"
        "2026-11-8 14:05-15:40\n"  # the date cannot be read: the line is skipped, quietly
        "20261115 14:05-15:40\n"  # nor this form, which the deploy tool would never match
    )
    assert windows == {MUHURAT: (845, 940)} and problems == []


def test_a_later_line_for_a_day_replaces_an_earlier_one_and_says_so():
    windows, problems = day_windows("2026-11-08 17:45-19:30\n2026-11-08 14:05-15:40\n")
    assert windows == {MUHURAT: (845, 940)}
    assert problems == [
        "extra trading day 2026-11-08 is given more than one window; the last one, 14:05-15:40, "
        "applies"
    ]
    assert day_windows("2026-11-08 14:05-15:40\n2026-11-08 14:05-15:40\n")[1] == []


def test_a_window_that_cannot_be_used_is_told_by_what_then_happens():
    alone = day_windows("2026-11-08 14:05-1530\n")
    assert alone == (
        {},
        [
            "extra trading day 2026-11-08: '14:05-1530' is not a window (HH:MM-HH:MM, the start "
            "before the end), so the day keeps the usual hours"
        ],
    )
    beside = day_windows("2026-11-08 14:05-15:40\n2026-11-08 14:05-15.40\n")
    assert beside == (
        {MUHURAT: (845, 940)},
        [
            "extra trading day 2026-11-08: '14:05-15.40' is not a window (HH:MM-HH:MM, the start "
            "before the end), so 14:05-15:40 from another line applies"
        ],
    )


#: Lines as an editor may leave them, and whether 2026-11-08 is then listed, for every reader.
LINES = [
    (b"2026-11-08\n", True),
    (b"\xef\xbb\xbf2026-11-08 14:05-15:40\n", True),  # a byte-order mark from a Windows editor
    (b"y\n\xef\xbb\xbf2026-11-08\n", True),
    (b" \xef\xbb\xbf2026-11-08\n", False),  # a mark after a space hides the date
    (b"2026-11-08\r\n", True),
    (b"\t\x0b\x0c 2026-11-08\t14:05-15:40\n", True),
    (b"2026-11-08#Muhurat\n", True),
    (b"2026-11-08 \xff\n", True),  # a stray byte spoils nothing before it
    (b"2026-11-08\xff\n", False),
    (b"x\x002026-11-08\n", False),  # a NUL byte never ends a line
    (b"2026-11-08\x00\n", False),
    (b"2026-11-08 \x00\n", True),
    (b"x\x1c2026-11-08\n", False),  # nor do \x1c, \x85 or a lone \r
    (b"x\xc2\x852026-11-08\n", False),
    (b"x\r2026-11-08\n", False),
    (b"x\r2026-11-08 14:05-15:40\n", False),  # nor a window after any of them
    (b"x\x1c2026-11-08 14:05-15:40\n", False),
    (b"x\xc2\x852026-11-08 14:05-15:40\n", False),
    (b"2026-11-08\xe3\x80\x80x\n", False),  # an ideographic space is not a space here
    (b"2026-11-08\xc2\xa0x\n", False),  # nor a no-break space
    (b"20261108\n", False),  # forms Python's date reader also takes
    (b"2026-W45-7\n", False),
    (b"\xef\xbc\x92026-11-08\n", False),  # a full-width digit
    (b"# 2026-11-08\n", False),
    (b"2026-11-08,\n", False),
    (b"02026-11-08\n", False),
]


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_the_calendar_and_the_deploy_tool_read_every_line_alike(tmp_path):
    """The deploy tool's listed() as it ships, in a UTF-8 locale; the calendar on the same bytes."""
    tool = (REPO / "sudarshan" / "ops" / "deploy" / "sudarshan-deploy").read_text()
    start = tool.index("listed() {")
    script = [tool[start : tool.index("\n}\n", start) + 3]]
    for n, (data, _) in enumerate(LINES):
        (tmp_path / f"{n}.txt").write_bytes(data)
        script.append(f'listed 2026-11-08 "{tmp_path}/{n}.txt" && echo Y || echo N')
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}
    done = subprocess.run(
        ["bash", "-c", "\n".join(script)], capture_output=True, text=True, env=env
    )
    deploy = done.stdout.split()
    assert len(deploy) == len(LINES), done.stderr
    for n, (data, listed) in enumerate(LINES):
        calendar = TradingCalendar(tmp_path / "no-holidays.txt", tmp_path / f"{n}.txt")
        seen = (MUHURAT in calendar.extra_days(), deploy[n] == "Y")
        assert seen == (listed, listed), (data, seen)
        if not listed:  # a window only ever comes from a line whose date is read
            assert calendar.day_window(MUHURAT) is None, data


def test_a_stray_byte_spoils_only_its_own_line(tmp_path):
    holidays = tmp_path / "nse_holidays.txt"
    holidays.write_bytes(b"\xef\xbb\xbf2026-11-09\n\xff\xfe\n2026-11-10  # Bhai Dooj\n")
    calendar = TradingCalendar(holidays)
    assert calendar.holidays() == {date(2026, 11, 9), date(2026, 11, 10)}
    assert calendar.holidays_note() == "2 dates, 1 unreadable line"


def test_the_notes_are_read_fresh_and_counted_in_words(tmp_path):
    holidays, extra = tmp_path / "nse_holidays.txt", tmp_path / "nse_extra_trading_days.txt"
    holidays.write_text("2026-11-09\n")
    extra.write_text("2026-11-08 14:05-15:40\n2026-11-14\n2026-11-15 14:05-1540\n")
    calendar = TradingCalendar(holidays, extra)
    assert calendar.holidays_note() == "1 date"  # asked first: read for the answer
    assert calendar.extra_days_note() == "3 dates, 1 own window, 1 window problem"
    assert TradingCalendar(holidays).extra_days_note() == "not set"


def test_the_calendar_logs_under_the_name_it_is_given(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    holidays = tmp_path / "nse_holidays.txt"
    holidays.write_text("2026-11-09\nnot a date\n")
    calendar = TradingCalendar(holidays, logger=logging.getLogger("sudarshan.elsewhere"))
    calendar.holidays()
    TradingCalendar(
        tmp_path / "missing.txt", logger=logging.getLogger("sudarshan.elsewhere")
    ).holidays()
    assert [(r.name, r.getMessage()) for r in caplog.records] == [
        ("sudarshan.elsewhere", f"{holidays}: 1 unreadable line ignored"),
        (
            "sudarshan.elsewhere",
            f"{tmp_path / 'missing.txt'} not found: only weekends count as closed",
        ),
    ]


def test_a_problem_is_told_again_after_a_good_read(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    holidays = tmp_path / "nse_holidays.txt"
    calendar = TradingCalendar(holidays)
    for text in (None, None, "2026-11-09\n", None, None):
        if text is None:
            holidays.unlink(missing_ok=True)
        else:
            holidays.write_text(text)
        calendar.holidays()
    assert caplog.text.count("not found: only weekends count as closed") == 2


def test_one_read_gives_the_dates_and_the_windows(tmp_path, monkeypatch):
    extra = tmp_path / "nse_extra_trading_days.txt"
    extra.write_text("2026-11-08 14:05-15:40\n")
    reads = []
    real = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: reads.append(self) or real(self))
    calendar = TradingCalendar(tmp_path / "no-holidays.txt", extra)
    assert MUHURAT in calendar.extra_days() and calendar.day_window(MUHURAT) == (845, 940)
    assert calendar.extra_days_note() == "1 date, 1 own window"
    assert reads.count(extra) == 1


def test_auto_login_may_start_until_the_f_and_o_close():
    assert parse_window(DEFAULT_WINDOW) == (8 * 60 + 30, 15 * 60 + 40)


def test_only_a_reporting_calendar_logs_the_windows_it_cannot_use(tmp_path, caplog):
    """Auto-login ignores windows and the alerts log their own: neither gets a second line."""
    caplog.set_level(logging.INFO)
    extra = tmp_path / "nse_extra_trading_days.txt"
    extra.write_text("2026-11-08 14:05-1540\n")
    quiet = TradingCalendar(tmp_path / "nse_holidays.txt", extra)
    assert quiet.day_window(MUHURAT) is None and MUHURAT in quiet.extra_days()
    assert "is not a window" not in caplog.text and " read: " not in caplog.text
    TradingCalendar(tmp_path / "nse_holidays.txt", extra, report=True).extra_days()
    assert caplog.text.count("is not a window") == 1
    assert caplog.text.count(f"{extra} read: extra trading days 2026-11-08 (usual)") == 1


def test_a_second_thread_never_gets_the_old_dates_once_the_file_changed(tmp_path, monkeypatch):
    """Under gthread two requests can read one calendar at once. A reads the changed file; B,
    arriving while A is still parsing it, must get the new dates and windows, never the old ones
    under the new stamp (the stamp was once published before the dates)."""
    extra = tmp_path / "extra.txt"
    extra.write_text("2026-11-08 14:05-15:40\n")
    calendar = TradingCalendar(tmp_path / "holidays.txt", extra)
    assert calendar.extra_days() == {MUHURAT}
    extra.write_text("2026-11-14 09:00-12:00\n")
    stamp = extra.stat().st_mtime_ns + 10**9  # a new stamp even where timestamps are coarse
    os.utime(extra, ns=(stamp, stamp))
    parsing, answered = threading.Event(), threading.Event()
    real_parse = schedule.parse_holidays

    def a_parse_that_waits_for_b(text):
        if threading.current_thread() is reader_a:
            parsing.set()
            assert answered.wait(5), "B never answered"
        return real_parse(text)

    monkeypatch.setattr(schedule, "parse_holidays", a_parse_that_waits_for_b)
    seen_by_a = []
    reader_a = threading.Thread(target=lambda: seen_by_a.append(calendar.extra_days()))
    reader_a.start()
    try:
        assert parsing.wait(5), "A never read the changed file"
        seen_by_b = (calendar.extra_days(), calendar.day_window(date(2026, 11, 14)))
    finally:
        answered.set()
        reader_a.join(5)
    assert seen_by_b == ({date(2026, 11, 14)}, (540, 720))
    assert seen_by_a == [{date(2026, 11, 14)}]
    assert calendar.extra_days() == {date(2026, 11, 14)} and calendar.day_window(MUHURAT) is None
