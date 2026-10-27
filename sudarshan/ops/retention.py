"""Which nightly backups to keep (M18.11): 30 daily and 12 monthly, inside a size cap.

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
  Latest   The newest complete archive is always kept, whatever else is true: a run of partial
           archives must never push the last good one out.
  Cap      When what is kept (plus ``reserve``, the archive about to be written) is over the cap,
           the oldest dailies go first. Never a monthly, never the three newest archives, never
           the latest complete one. If that is not enough the cap is ``over_cap``: the caller
           must say so, because it can no longer be kept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

NAME = re.compile(r"sudarshan-backup-(\d{8})-(\d{6})(-partial)?\.tar\.gz")
STAMP = "%Y%m%d-%H%M%S"
#: The newest archives the cap may not touch: last night's, the one before, and one more.
PROTECT_NEWEST = 3


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
    cap_bytes: int = 0,
    reserve: int = 0,
) -> Plan:
    """What to keep and what to remove. ``cap_bytes`` 0 means no cap."""
    newest_first = sorted(backups, key=lambda b: (b.at, b.name), reverse=True)

    dailies = _one_each(newest_first, lambda b: b.day)
    monthlies = _one_each(newest_first, lambda b: b.month)
    keep_daily = {b.name for b in dailies[: max(daily, 0)]}
    keep_monthly = {b.name for b in monthlies[: max(monthly, 0)]}
    latest = next((b for b in newest_first if not b.partial), None)

    kept = keep_daily | keep_monthly | ({latest.name} if latest else set())
    untouchable = keep_monthly | {b.name for b in newest_first[:PROTECT_NEWEST]}
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
