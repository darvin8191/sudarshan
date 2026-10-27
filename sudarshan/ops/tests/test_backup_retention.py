"""M18.11: which nightly archives are kept: 30 daily, 12 monthly, inside a size cap.

``retention.plan`` is a decision and nothing else (no files, no clock): these tests give it lists of
archives and read back what it keeps and removes. The things that must not go wrong are the ones the
owner would only find out about on the night he needs a backup: the newest good archive removed
because a run of partial ones pushed it out, a monthly removed to make room, a file that is not ours
(a note, a copy he made by hand) taken for an archive and deleted.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from sudarshan.ops import retention
from sudarshan.ops.retention import Backup, make_name, parse, plan

MB = 1 << 20


def at(year: int, month: int, day: int, hour: int = 16, minute: int = 30, second: int = 0):
    return datetime(year, month, day, hour, minute, second)


def archive(when: datetime, size: int = MB, *, partial: bool = False) -> Backup:
    found = parse(make_name(when, partial=partial), size)
    assert found is not None
    return found


def nightly(first: datetime, days: int, size: int = MB) -> list[Backup]:
    """One complete archive a day for ``days`` days from ``first``."""
    return [archive(first + timedelta(days=n), size) for n in range(days)]


def names(backups) -> list[str]:
    return [b.name for b in backups]


# ---- the names --------------------------------------------------------------------------------


def test_a_name_is_the_moment_it_was_made_and_reads_back():
    when = at(2026, 10, 27, 16, 31, 2)
    assert make_name(when) == "sudarshan-backup-20261027-163102.tar.gz"
    assert make_name(when, partial=True) == "sudarshan-backup-20261027-163102-partial.tar.gz"
    whole = parse(make_name(when), 123)
    assert whole == Backup("sudarshan-backup-20261027-163102.tar.gz", when, 123, partial=False)
    assert parse(make_name(when, partial=True), 5).partial is True
    assert (whole.day, whole.month) == ("20261027", "202610")


@pytest.mark.parametrize(
    "name",
    [
        "",
        "README",
        "notes.tar.gz",
        "sudarshan-backup-20261027-163102.tar.gz.partial",  # a write in progress, never listed
        "sudarshan-backup-20261027-163102.tar",
        "sudarshan-backup-20261027-163102.tar.gz.bak",
        "sudarshan-backup-20261027-163102-partial.tar.gz.old",
        "my-sudarshan-backup-20261027-163102.tar.gz",
        "sudarshan-backup-20261027-1631.tar.gz",
        "sudarshan-backup-2026-10-27.tar.gz",
        "SUDARSHAN-BACKUP-20261027-163102.tar.gz",
        "sudarshan-backup-20261027-163102-PARTIAL.tar.gz",
        "sudarshan-backup-20261340-163102.tar.gz",  # month 13: not a date
        "sudarshan-backup-20261027-256102.tar.gz",  # hour 25: not a time
        "sudarshan-backup-20260230-163102.tar.gz",  # 30 February
    ],
)
def test_a_file_that_is_not_one_of_ours_is_not_an_archive(name):
    assert parse(name, 10) is None


def test_a_size_below_zero_is_read_as_zero():
    assert parse(make_name(at(2026, 10, 27)), -5).size == 0


# ---- daily and monthly ------------------------------------------------------------------------


def test_nothing_to_do_with_nothing():
    result = plan([], daily=30, monthly=12)
    assert (result.keep, result.delete, result.over_cap, result.kept_bytes) == ((), (), False, 0)


def test_thirty_daily_and_twelve_monthly_over_a_year_and_a_half():
    backups = nightly(at(2025, 4, 10), 570)  # 10 Apr 2025 to 31 Oct 2026
    assert backups[-1].day == "20261031"
    result = plan(backups, daily=30, monthly=12)

    last_thirty_days = {b.day for b in backups[-30:]}
    last_twelve_months = {"202511", "202512", *(f"2026{m:02d}" for m in range(1, 11))}
    newest_of_month = {}
    for b in backups:
        newest_of_month[b.month] = b  # later in the list is later in time
    expected = {b.name for b in backups[-30:]} | {
        newest_of_month[m].name for m in last_twelve_months
    }
    assert {b.day for b in backups[-30:]} == last_thirty_days
    assert set(names(result.keep)) == expected
    assert len(result.keep) == 30 + 11  # October's month-end is one of the thirty nights
    assert set(names(result.delete)) == {b.name for b in backups} - expected
    assert set(names(result.keep)) | set(names(result.delete)) == {b.name for b in backups}
    assert not set(names(result.keep)) & set(names(result.delete))
    assert result.kept_bytes == len(result.keep) * MB
    assert result.over_cap is False


def test_a_month_is_represented_by_its_last_night():
    backups = nightly(at(2026, 1, 1), 150)
    result = plan(backups, daily=3, monthly=12)
    kept_days = {b.day for b in result.keep}
    # The month ends: 31 Jan, 28 Feb, 31 Mar, 30 Apr, and the three newest nights of May 2026.
    assert {"20260131", "20260228", "20260331", "20260430"} <= kept_days
    assert {"20260528", "20260529", "20260530"} <= kept_days  # the daily three
    assert "20260115" not in kept_days


def test_two_archives_in_one_day_keep_the_newer():
    early, late = archive(at(2026, 10, 27, 16, 30)), archive(at(2026, 10, 27, 22, 30))
    result = plan([early, late], daily=30, monthly=12)
    assert names(result.keep) == [late.name]
    assert names(result.delete) == [early.name]


def test_days_count_only_where_there_is_an_archive():
    """A server that was down for a fortnight still keeps 30 archives, not 16."""
    before = nightly(at(2026, 8, 1), 20)
    after = nightly(at(2026, 9, 5), 20)
    result = plan(before + after, daily=30, monthly=0)
    assert len(result.keep) == 30
    assert names(result.keep)[0] == after[-1].name
    assert before[0].name in names(result.delete)  # the 10 oldest of 40 are over the count
    assert len(result.delete) == 10


def test_a_year_of_months_is_not_fifty_two_weeks():
    backups = [archive(at(2025, m, 15)) for m in range(1, 13)] + [archive(at(2026, 1, 15))]
    result = plan(backups, daily=1, monthly=12)
    assert len(result.keep) == 12  # January 2025 is the 13th month back
    assert archive(at(2025, 1, 15)).name in names(result.delete)


def test_the_counts_are_what_they_say_and_zero_means_none():
    backups = nightly(at(2026, 10, 1), 20)
    assert len(plan(backups, daily=5, monthly=0).keep) == 5
    only_latest = plan(backups, daily=0, monthly=0)
    assert names(only_latest.keep) == [backups[-1].name]  # the newest complete archive stays
    negative = plan(backups, daily=-3, monthly=-1)
    assert names(negative.keep) == [backups[-1].name]


def test_the_order_is_newest_first_whatever_order_they_come_in():
    backups = nightly(at(2026, 10, 1), 10)
    shuffled = backups[5:] + backups[:5]
    result = plan(shuffled, daily=4, monthly=0)
    assert names(result.keep) == names(reversed(backups[-4:]))
    assert names(result.delete) == names(reversed(backups[:-4]))


# ---- partial archives -------------------------------------------------------------------------


def test_the_last_good_archive_survives_a_run_of_partial_ones():
    good = archive(at(2026, 8, 1))
    partials = [archive(at(2026, 9, 1) + timedelta(days=n), partial=True) for n in range(45)]
    result = plan([good, *partials], daily=30, monthly=2)
    assert good.name in names(result.keep)
    assert names(result.keep).count(good.name) == 1
    # And it is not kept at the cost of the partial ones inside the counts.
    assert {b.name for b in partials[-30:]} <= set(names(result.keep))


def test_a_day_is_represented_by_its_complete_archive_over_a_later_partial_one():
    complete = archive(at(2026, 10, 20, 16, 30))
    later_partial = archive(at(2026, 10, 20, 20, 0), partial=True)
    next_day = archive(at(2026, 10, 21))
    result = plan([complete, later_partial, next_day], daily=30, monthly=12)
    assert complete.name in names(result.keep)
    assert later_partial.name in names(result.delete)


def test_a_day_with_only_partial_archives_keeps_the_newest_of_them():
    first = archive(at(2026, 10, 20, 16, 30), partial=True)
    second = archive(at(2026, 10, 20, 22, 30), partial=True)
    older_good = archive(at(2026, 10, 19))
    result = plan([first, second, older_good], daily=30, monthly=12)
    assert names(result.keep) == [second.name, older_good.name]
    assert names(result.delete) == [first.name]


def test_a_month_is_represented_by_its_complete_archive_too():
    good = archive(at(2026, 8, 20))
    last_night_of_august = archive(at(2026, 8, 31), partial=True)
    later = archive(at(2026, 10, 1))
    result = plan([good, last_night_of_august, later], daily=1, monthly=12)
    assert good.name in names(result.keep)
    assert last_night_of_august.name in names(result.delete)


def test_the_complete_archive_and_the_partial_one_of_the_same_second_are_told_apart():
    when = at(2026, 10, 27)
    whole, partial = archive(when), archive(when, partial=True)
    result = plan([partial, whole], daily=30, monthly=12)
    assert names(result.keep) == [whole.name]  # the complete one is the newer of the two


# ---- the cap ----------------------------------------------------------------------------------


def test_no_cap_means_no_limit():
    backups = nightly(at(2026, 9, 1), 30, size=10_000 * MB)
    result = plan(backups, daily=30, monthly=12, cap_bytes=0, reserve=10**12)
    assert len(result.keep) == 30 and result.over_cap is False


def test_the_oldest_dailies_go_first_when_the_cap_is_tight():
    backups = nightly(at(2026, 10, 1), 10, size=100)
    result = plan(backups, daily=30, monthly=0, cap_bytes=450, reserve=100)
    # 450 holds the 100 about to be written and three of the ten: the three newest.
    assert names(result.keep) == names(reversed(backups[-3:]))
    assert names(result.delete) == names(reversed(backups[:-3]))
    assert result.kept_bytes == 300
    assert result.over_cap is False


def test_only_as_many_go_as_the_cap_needs_and_they_are_the_oldest():
    backups = nightly(at(2026, 10, 1), 10, size=100)
    result = plan(backups, daily=30, monthly=0, cap_bytes=650, reserve=100)
    # 650 holds the 100 about to be written and five of the ten: the five newest, not any five.
    assert names(result.keep) == names(reversed(backups[-5:]))
    assert result.kept_bytes == 500 and result.over_cap is False


def test_the_cap_is_over_when_the_new_archive_would_not_fit_beside_what_cannot_go():
    backups = nightly(at(2026, 10, 1), 3, size=100)  # all three are among the newest three
    fits = plan(backups, daily=30, monthly=0, cap_bytes=400, reserve=100)
    assert fits.over_cap is False
    does_not = plan(backups, daily=30, monthly=0, cap_bytes=350, reserve=100)
    assert does_not.over_cap is True and does_not.kept_bytes == 300  # 300 kept, 400 with the new


def test_the_reserve_is_what_is_about_to_be_written():
    backups = nightly(at(2026, 10, 1), 6, size=100)
    without = plan(backups, daily=30, monthly=0, cap_bytes=600)
    assert len(without.keep) == 6 and without.over_cap is False
    with_room = plan(backups, daily=30, monthly=0, cap_bytes=600, reserve=100)
    assert len(with_room.keep) == 5  # one night gives way for the new archive
    assert with_room.over_cap is False


def test_the_three_newest_are_never_removed_for_the_cap():
    backups = nightly(at(2026, 10, 1), 10, size=200)
    result = plan(backups, daily=30, monthly=0, cap_bytes=500)
    assert names(result.keep) == names(reversed(backups[-3:]))
    assert result.kept_bytes == 600
    assert result.over_cap is True  # they alone take more than the cap: say so, remove no more


def test_a_monthly_is_never_removed_for_the_cap():
    backups = [archive(at(2026, m, 28), 200) for m in range(1, 11)]
    result = plan(backups, daily=1, monthly=12, cap_bytes=500)
    assert len(result.keep) == 10  # one for every month, though 10 x 200 is four times the cap
    assert result.over_cap is True
    assert result.delete == ()


def test_the_cap_removes_dailies_but_leaves_every_monthly():
    older = [archive(at(2026, m, 28), 100) for m in range(1, 6)]  # five month-ends
    recent = nightly(at(2026, 10, 1), 20, size=100)  # twenty nights of October
    result = plan(older + recent, daily=30, monthly=12, cap_bytes=1000)
    kept = set(names(result.keep))
    assert {b.name for b in older} <= kept  # the five months
    assert recent[-1].name in kept  # October's newest night is also October's month
    assert result.kept_bytes <= 1000 and result.over_cap is False
    assert len(result.keep) == 10


def test_the_last_complete_archive_is_not_removed_for_the_cap():
    good = archive(at(2026, 6, 1), 400)
    partials = [archive(at(2026, 10, 1) + timedelta(days=n), 400, partial=True) for n in range(5)]
    result = plan([good, *partials], daily=30, monthly=0, cap_bytes=1000)
    assert good.name in names(result.keep)
    assert result.over_cap is True  # it and the three newest partials are 1600


def test_a_cap_below_zero_is_no_cap_and_a_zero_reserve_is_nothing():
    backups = nightly(at(2026, 10, 1), 5, size=100)
    assert len(plan(backups, daily=30, monthly=0, cap_bytes=-1).keep) == 5
    assert len(plan(backups, daily=30, monthly=0, cap_bytes=480, reserve=-50).keep) == 4


def test_what_is_kept_and_what_is_removed_are_the_whole_list():
    backups = nightly(at(2025, 11, 1), 300, size=3 * MB)
    for cap in (0, 10_000 * MB, 100 * MB, 40 * MB, 5 * MB):
        result = plan(backups, daily=30, monthly=12, cap_bytes=cap, reserve=3 * MB)
        assert sorted(names(result.keep) + names(result.delete)) == sorted(names(backups))
        assert result.kept_bytes == sum(b.size for b in result.keep)


def test_the_module_imports_nothing_that_could_touch_a_disk_or_a_clock():
    tree = ast.parse(Path(retention.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported == {"__future__", "re", "dataclasses", "datetime"}
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not calls & {"now", "utcnow", "today", "open", "read_text", "write_text"}
