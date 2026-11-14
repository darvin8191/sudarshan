"""The weekly server summary (M19.5, M19.13; P8 7.1): one message on Sunday evening.

Every check in a line's worth of counts, the sizes of what SUDARSHAN and OpenAlgo keep on the
disk, updates waiting, the last backup and restore drill, and how busy the server's processor
and memory were over the week (the tenancy's idle rule is about those two), with "nothing needs
you" when that is true. The watch records it as one event, ``watch_summary``; the notice desk
words it.

The week's use is counted from what each run already reads: the kernel's processor counters
(the share of time not idle since the run before) and its memory figure, each into twenty
5-percent buckets kept in the watch's state. The figure told is the level the server stayed
under for 95 percent of the runs. These are the whole server's figures, as the kernel gives
them to every program; nothing of another system is read.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from . import checks

SUNDAY, FROM_MIN = 6, 18 * 60  # Sunday, 18:00 in India
BUCKETS = 20
MAX_ENTRIES = 50_000  # one summary's walks give up past this many files, together
#: SUDARSHAN's own folders under the system's state folder all start with this.
RING_PREFIX = "/var/lib/sudarshan-"
BACKUP_FOLDER = "sudarshan-backup"
_IST = timezone(timedelta(hours=5, minutes=30))


def week_of(now: float) -> str:
    """The week a moment belongs to, as India's calendar counts it: ``2026-W46``."""
    year, week, _ = datetime.fromtimestamp(now, UTC).astimezone(_IST).isocalendar()
    return f"{year}-W{week:02d}"


def due(now: float, last_week: str | None) -> bool:
    """Sunday from 18:00 in India, once per week."""
    moment = datetime.fromtimestamp(now, UTC).astimezone(_IST)
    late_enough = moment.weekday() == SUNDAY and moment.hour * 60 + moment.minute >= FROM_MIN
    return late_enough and week_of(now) != last_week


def _bucket(percent: float) -> int:
    return min(BUCKETS - 1, max(0, int(percent // (100 / BUCKETS))))


def _counts(found) -> list[int]:
    ok = isinstance(found, list) and len(found) == BUCKETS
    ok = ok and all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in found)
    return list(found) if ok else [0] * BUCKETS


def _pair(found) -> bool:
    return (
        isinstance(found, list | tuple)
        and len(found) == 2
        and all(isinstance(n, int) and not isinstance(n, bool) for n in found)
    )


def sample(usage: Mapping | None, readings: Mapping) -> dict:
    """The week's use with this run counted in. Pure. ``usage`` is last run's (or None)."""
    usage = usage if isinstance(usage, Mapping) else {}
    cpu, mem = _counts(usage.get("cpu")), _counts(usage.get("mem"))
    now_cpu, was = readings.get("cpu"), usage.get("cpu_prev")
    if _pair(now_cpu) and _pair(was) and now_cpu[1] > was[1] and now_cpu[0] >= was[0]:
        cpu[_bucket(100.0 * (now_cpu[0] - was[0]) / (now_cpu[1] - was[1]))] += 1
    free, total = readings.get("mem_available_mb"), readings.get("mem_total_mb")
    if all(isinstance(n, int | float) and not isinstance(n, bool) for n in (free, total)):
        if total > 0:
            mem[_bucket(100.0 * (1 - free / total))] += 1
    return {"cpu": cpu, "mem": mem, "cpu_prev": list(now_cpu) if _pair(now_cpu) else None}


def under_for_95(counts: list[int]) -> int | None:
    """The percent the figure stayed under for 95 percent of the runs counted (a bucket's upper
    edge), or None when nothing was counted."""
    total = sum(counts)
    if total <= 0:
        return None
    running = 0
    for index, count in enumerate(counts):
        running += count
        if running >= 0.95 * total:
            return (index + 1) * (100 // BUCKETS)
    return 100


def folder_mb(path: Path, budget: list[int] | None = None) -> int | None:
    """What a folder's files take, in MB, links not followed. None when it cannot be listed or
    holds more files than a walk is allowed; 0 when it is not there."""
    budget = [MAX_ENTRIES] if budget is None else budget
    total, stack = 0, [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    budget[0] -= 1
                    if budget[0] < 0:
                        return None
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
        except FileNotFoundError:
            continue
        except OSError:
            return None
    return total >> 20


def sizes(app_dir: Path, ring_prefix: str = RING_PREFIX) -> dict[str, int]:
    """The sizes of the retention table (README), in MB; a folder that could not be measured is
    left out. Only the app's ``db`` and ``log`` folders and SUDARSHAN's own state folders."""
    budget = [MAX_ENTRIES]  # shared: a run has a minute, and the disk is the market's too
    found: dict[str, int | None] = {
        "db": folder_mb(app_dir / "db", budget),
        "log": folder_mb(app_dir / "log", budget),
    }
    try:
        historify = os.stat(app_dir / "db" / "historify.duckdb").st_size >> 20
    except OSError:
        historify = 0
    found["hist"] = historify
    if found["db"] is not None:
        found["db"] = max(0, found["db"] - historify)
    base, stem = os.path.split(ring_prefix)
    ring, backups = 0, None
    try:
        names = sorted(name for name in os.listdir(base) if name.startswith(stem))
    except OSError:
        names, ring = [], None
    for name in names:
        if os.path.islink(os.path.join(base, name)):
            continue  # a link is not SUDARSHAN's folder, wherever it points
        size = folder_mb(Path(base) / name, budget)
        if name == BACKUP_FOLDER:
            backups = size
        elif size is not None and ring is not None:
            ring += size
    found.update(ring=ring, bak=backups)
    return {name: size for name, size in found.items() if size is not None}


def facts(results: list[checks.Result], usage: Mapping, measured: Mapping[str, int]) -> dict:
    """The summary's event facts: short strings the notice desk words. Pure."""
    by_level: dict[str, list[str]] = {level: [] for level in (checks.FAILING, checks.WARNING)}
    counted = dict.fromkeys((checks.FINE, checks.UNREAD), 0)
    by_name = {result.name: result for result in results}
    for result in results:
        if result.level in by_level:
            by_level[result.level].append(result.name)
        elif result.level in counted:
            counted[result.level] += 1
    out = {
        "failing": ",".join(by_level[checks.FAILING])[:190],
        "warning": ",".join(by_level[checks.WARNING])[:190],
        "fine": str(counted[checks.FINE]),
        "unread": str(counted[checks.UNREAD]),
        "sizes": ",".join(f"{name}={size}" for name, size in sorted(measured.items())),
    }
    for check, names in (
        ("disk", ("free_gb",)),
        ("posture_updates", ("all", "security")),
        ("backup", ("age_h",)),
        ("drill", ("days", "cause")),
    ):
        for name in names:
            value = (by_name[check].facts if check in by_name else {}).get(name)
            if value is not None:
                out[f"{check.removeprefix('posture_')}_{name}"] = str(value)
    if "posture_reboot" in by_name and by_name["posture_reboot"].level != checks.UNREAD:
        out["reboot"] = "yes" if by_name["posture_reboot"].level == checks.WARNING else "no"
    if "drill" in by_name and by_name["drill"].level == checks.FINE:
        out["drill_cause"] = "passed"
    for name in ("cpu", "mem"):
        level = under_for_95(_counts(usage.get(name)))
        if level is not None:
            out[f"{name}95"] = str(level)
    return out
