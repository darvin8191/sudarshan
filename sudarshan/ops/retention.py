"""Which nightly backups to keep (M18.11): 30 daily, 12 monthly and one per financial year for
8 years, inside a size cap.

A decision and nothing else: no files, no clock, no logging. ``backup.py`` lists the archives in
its folder, asks ``plan`` what to remove and removes it.

An archive is named for the moment it was made (IST):

    sudarshan-backup-20261027-163102.tar.gz            everything that was asked for is in it
    sudarshan-backup-20261027-163102-partial.tar.gz    something could not be saved; the rest is

Anything else in the folder is not ours and is never listed here, so never removed.

Rules, in this order:

  Daily    One archive for each of the ``daily`` most recent days that have one: that day's newest
           complete archive, or its newest if every one of them is partial.
  Monthly  The same for each of the ``monthly`` most recent months that have one, so a month is
           represented for a year after its dailies are gone.
  Yearly   A financial year ends on 31 March, but its papers are not whole that evening: the
           journal builds the year's Tax pack on 1 April at 07:00, Upstox's charges for a day
           are read two nights later, and March's own charges row from 3 April. The first
           complete archive made from 4 April 00:00 on is that financial year's archive (one
           made earlier holds no Tax pack and no March charges, and does not belong to the
           year that has ended; a partial one never counts; nor does one made more than 92 days
           later). The archives of the ``yearly`` most recent financial years that have one
           are kept (M18.11, M19.10: 8 years, for the tax records).
  Latest   The newest complete archive is always kept, whatever else is true: a run of partial
           archives must never push the last good one out.
  Cap      When what is kept (plus ``reserve``, the archive about to be written) is over the cap,
           the oldest dailies go first. Never a monthly, never a yearly, never the three newest
           archives, never the latest complete one. If that is not enough the cap is
           ``over_cap``: the caller must say so, because it can no longer be kept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

NAME = re.compile(r"sudarshan-backup-(\d{8})-(\d{6})(-partial)?\.tar\.gz")
STAMP = "%Y%m%d-%H%M%S"
#: The newest archives the cap may not touch: last night's, the one before, and one more.
PROTECT_NEWEST = 3
#: The moment (month, day, hour, IST) from which an archive holds a financial year's papers
#: whole: 4 April 00:00. By then the journal has built the year's Tax pack (1 April 07:00,
#: ``journal/schedule.py`` ``FY_AT``), the charges of the year's last trading days have been
#: read (two nights after each) and March's own charges row has been asked for (from 3 April,
#: ``journal/jobs/charges.py`` ``MONTH_FROM_DAY``). An archive made on the evening of 31 March
#: held none of them (review M3).
FY_FROM = (4, 4, 0)
#: How long after that an archive can still become the year's (to 4 July).
FY_WINDOW_DAYS = 92


@dataclass(frozen=True)
class Backup:
    name: str
    at: datetime  # naive IST, read from the name
    size: int
    partial: bool

    @property
    def day(self) -> str:
        return self.at.strftime("%Y%m%d")

    @property
    def month(self) -> str:
        return self.at.strftime("%Y%m")


@dataclass(frozen=True)
class Plan:
    keep: tuple[Backup, ...]
    delete: tuple[Backup, ...]
    over_cap: bool  # still above the cap after everything that may go has gone
    kept_bytes: int  # what ``keep`` takes, without the reserve


def make_name(at: datetime, *, partial: bool = False) -> str:
    return f"sudarshan-backup-{at.strftime(STAMP)}{'-partial' if partial else ''}.tar.gz"


def parse(name: str, size: int) -> Backup | None:
    """The archive a file name stands for, or None when the name is not one of ours."""
    found = NAME.fullmatch(name)
    if not found:
        return None
    try:
        at = datetime.strptime(f"{found[1]}-{found[2]}", STAMP)
    except ValueError:
        return None
    return Backup(name, at, max(int(size), 0), partial=bool(found[3]))


def fy_label(at: datetime) -> str | None:
    """The financial year an archive made at ``at`` (naive IST) can be the archive of: the one
    whose papers became whole (``FY_FROM``) in the ``FY_WINDOW_DAYS`` before it, else None.
    ``2026-27`` ends on 31 March 2027 and is whole from 4 April 2027. The window keeps an
    archive made months later (a server first installed in November) from being taken for a
    year's archive."""
    month, day, hour = FY_FROM
    end = at.year if at >= datetime(at.year, month, day, hour) else at.year - 1
    if (at - datetime(end, month, day, hour)).days >= FY_WINDOW_DAYS:
        return None
    return f"{end - 1}-{end % 100:02d}"


def yearlies(backups: list[Backup]) -> dict[str, Backup]:
    """Each financial year's archive among ``backups``: the first complete one made after that
    year's papers were whole. By label, ``2026-27``."""
    chosen: dict[str, Backup] = {}
    for backup in sorted(backups, key=lambda b: (b.at, b.name)):
        label = None if backup.partial else fy_label(backup.at)
        if label:
            chosen.setdefault(label, backup)
    return chosen


def yearly_label(backups: list[Backup], name: str) -> str | None:
    """The financial year ``name`` is the archive of, or None when it is not a year's archive."""
    return next((label for label, b in yearlies(backups).items() if b.name == name), None)


def _one_each(newest_first: list[Backup], group) -> list[Backup]:
    """One archive per group (a day, a month), the groups newest first. A group is represented by
    its newest complete archive; only when every archive in it is partial, by its newest."""
    chosen: dict[str, Backup] = {}
    for backup in newest_first:
        held = chosen.get(group(backup))
        if held is None or (held.partial and not backup.partial):
            chosen[group(backup)] = backup
    return list(chosen.values())


def plan(
    backups: list[Backup],
    *,
    daily: int,
    monthly: int,
    yearly: int = 0,
    cap_bytes: int = 0,
    reserve: int = 0,
) -> Plan:
    """What to keep and what to remove. ``cap_bytes`` 0 means no cap; ``yearly`` 0 keeps no
    financial-year archive beyond the other rules."""
    newest_first = sorted(backups, key=lambda b: (b.at, b.name), reverse=True)

    dailies = _one_each(newest_first, lambda b: b.day)
    monthlies = _one_each(newest_first, lambda b: b.month)
    keep_daily = {b.name for b in dailies[: max(daily, 0)]}
    keep_monthly = {b.name for b in monthlies[: max(monthly, 0)]}
    by_year = yearlies(newest_first)
    keep_yearly = {by_year[label].name for label in sorted(by_year, reverse=True)[: max(yearly, 0)]}
    latest = next((b for b in newest_first if not b.partial), None)

    kept = keep_daily | keep_monthly | keep_yearly | ({latest.name} if latest else set())
    untouchable = keep_monthly | keep_yearly | {b.name for b in newest_first[:PROTECT_NEWEST]}
    if latest:
        untouchable.add(latest.name)

    used = sum(b.size for b in newest_first if b.name in kept)
    reserve = max(reserve, 0)
    if cap_bytes > 0:
        for backup in reversed(newest_first):  # oldest first
            if used + reserve <= cap_bytes:
                break
            if backup.name in kept and backup.name not in untouchable:
                kept.discard(backup.name)
                used -= backup.size

    keep = tuple(b for b in newest_first if b.name in kept)
    delete = tuple(b for b in newest_first if b.name not in kept)
    return Plan(
        keep, delete, over_cap=cap_bytes > 0 and used + reserve > cap_bytes, kept_bytes=used
    )
