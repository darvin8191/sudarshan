"""The recording's fallback at 17:00 (JB6; cross-design Y21).

Live tracking (``feedlink.py``) gives each trip its MAE and MFE at its close from the ticks it
saw. A trip it did not see whole, because it opened before this process started or closed while
the process was down, is finished here from the feed's recording of the day,
``<ticks_dir>/<YYYY-MM-DD>.jsonl.gz`` (the feed's own files, written by the IST date and readable
by this service's user):

* The trips closed today with no excursion, or whose tracked span starts more than a minute
  after their first entry (a restart, not the seconds a claim takes).
* One pass over the file on a thread of its own at the lowest priority: a line's key is found in
  its bytes, and only a line of a wanted key is parsed; for each trip only its running low and
  high inside its span are kept, never the path. A stop of the service ends the pass.
* The span kept at the close (``trip_track``) is merged with what the recording found, and the
  core works the excursion out again from both.
* A file cut off mid-write is read up to where it ends (as ``feed/replay.py``'s reader does).
"""

from __future__ import annotations

import asyncio
import gzip
import logging
import os
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.money import to_date
from ..feedlink import Span, excursion_values, tick_point, track_values
from ..schedule import register_job
from ..store import Scope

log = logging.getLogger("sudarshan.journal")

OWNER = Scope.owner()
KEY_MARK = b'"key":"'
#: A tracked span that starts later than this after the first entry was cut by a restart.
RESTART_GAP_MS = 60_000
#: Lines read between two looks at the stop flag.
STOP_EVERY = 10_000
LOWEST = 19


def key_of(line: bytes) -> bytes | None:
    """The instrument key of a recorded line, from its bytes (no JSON parse)."""
    at = line.find(KEY_MARK)
    if at < 0:
        return None
    start = at + len(KEY_MARK)
    end = line.find(b'"', start)
    return line[start:end] if end > start else None


@dataclass
class Want:
    """One trip the recording is asked about: its key, its span, and the low and high found."""

    trip_id: int
    key: str
    start_ms: int
    end_ms: int
    low: tuple[int, int] | None = None
    high: tuple[int, int] | None = None
    first_ms: int | None = None
    ticks: int = field(default=0)

    def fold(self, ms: int, price: int) -> None:
        if not self.start_ms <= ms <= self.end_ms:
            return
        if self.low is None or price < self.low[1]:
            self.low = (ms, price)
        if self.high is None or price > self.high[1]:
            self.high = (ms, price)
        self.first_ms = ms if self.first_ms is None else min(self.first_ms, ms)
        self.ticks += 1


def scan(path: Path, wants: list[Want], stopping: threading.Event | None = None) -> int | None:
    """One pass over a day's recording for ``wants``; the lines read, or None with no file."""
    by_key: dict[bytes, list[Want]] = {}
    for want in wants:
        by_key.setdefault(want.key.encode(), []).append(want)
    opener = gzip.open if path.suffix == ".gz" else open
    lines = 0
    try:
        with opener(path, "rb") as f:
            try:
                for raw in f:
                    lines += 1
                    if stopping is not None and not lines % STOP_EVERY and stopping.is_set():
                        break
                    group = by_key.get(key_of(raw) or b"")
                    if not group:
                        continue
                    point = tick_point(raw.strip())
                    if point is not None:
                        for want in group:
                            want.fold(*point)
            except (EOFError, gzip.BadGzipFile, zlib.error):
                log.warning("today's recording ends mid-write: read up to there")
    except FileNotFoundError:
        return None
    return lines


def _lower_priority() -> None:
    """The scan thread runs at the lowest priority (on Linux the nice value is per thread)."""
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), LOWEST)
    except (AttributeError, OSError):
        log.debug("the recording's scan could not lower its priority")


class Scanner:
    """The one thread the scan runs on (a part: closed with the service)."""

    def __init__(self) -> None:
        self.stopping = threading.Event()
        self.pool = ThreadPoolExecutor(1, thread_name_prefix="journal-scan",
                                       initializer=_lower_priority)  # fmt: skip

    async def run(self, path: Path, wants: list[Want]) -> int | None:
        return await asyncio.get_running_loop().run_in_executor(
            self.pool, lambda: scan(path, wants, self.stopping)
        )

    async def aclose(self) -> None:
        self.stopping.set()
        self.pool.shutdown(wait=False, cancel_futures=True)


def wanted(trip: dict, track: dict | None) -> bool:
    """Whether a closed trip is one the recording is asked about."""
    if trip["excursion_source"] == "none" or track is None:
        return True
    return int(track["from_ms"]) > int(trip["opened_ms"]) + RESTART_GAP_MS


def merged(want: Want, track: dict | None) -> Span:
    """What the recording found joined with the span the live tracker kept."""
    points = [p for p in (want.low, want.high) if p is not None]
    starts = [want.first_ms] if want.first_ms is not None else []
    ticks = want.ticks
    if track is not None:
        points += [(int(track["low_ms"]), int(track["low_u"])),
                   (int(track["high_ms"]), int(track["high_u"]))]  # fmt: skip
        starts.append(int(track["from_ms"]))
        ticks += int(track["ticks"])
    return Span(points, min(starts) if starts else None, ticks)


async def finish_from_recording(service: Any, today: int, now: int, late: bool) -> str:
    store = service.store

    def plan(tx):
        out = []
        for trip in tx.rows("round_trip", status="closed", close_day=today):
            if not trip["instrument_key"] or trip["closed_ms"] is None:
                continue
            track = tx.trip_track_get(trip["id"])
            if wanted(trip, track):
                out.append((trip, track))
        return out

    loop = asyncio.get_running_loop()
    asked = await loop.run_in_executor(None, lambda: store.read(OWNER, plan))
    if not asked:
        return "no trade needs the recording"
    wants = [Want(t["id"], t["instrument_key"], int(t["opened_ms"]), int(t["closed_ms"]))
             for t, _ in asked]  # fmt: skip
    path = Path(service.settings.ticks_dir) / f"{to_date(today).isoformat()}.jsonl.gz"
    scanner = service.part("scanner", lambda svc: Scanner())
    lines = await scanner.run(path, wants)

    def write(tx):
        found = 0
        for (trip, track), want in zip(asked, wants, strict=True):
            span = merged(want, track)
            found += bool(want.ticks)
            tx.touch("round_trip", trip["id"], excursion_values(trip, span), now)
            kept = track_values(span, int(trip["closed_ms"]))
            if kept is not None:
                tx.trip_track_put(trip["id"], kept, now)
        return found

    found = await loop.run_in_executor(None, lambda: store.write(OWNER, write))
    read = "no recording of today" if lines is None else f"{lines} lines read"
    return f"{len(asked)} trades asked, {found} found in the recording ({read})"


def register_jobs() -> None:
    register_job("excursion", finish_from_recording)
