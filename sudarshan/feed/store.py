"""Last tick per key (persisted atomically) and the gzip JSON-lines tick recorder."""

from __future__ import annotations

import contextlib
import gzip
import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from .window import IST

log = logging.getLogger("sudarshan.feed")
_DAY_FILE = re.compile(r"\d{4}-\d{2}-\d{2}\.jsonl\.gz")


def atomic_write_json(path: Path, obj) -> None:
    """Write to a temp file in the same directory, fsync, then rename over the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, separators=(",", ":"), ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)  # only still there when the write or the rename failed


class SnapshotStore:
    """Last normalised tick per instrument key, kept across restarts and after hours."""

    def __init__(self, path: Path):
        self.path = path
        self.ticks: dict[str, dict] = {}
        self.dirty = False
        self._fresh: set[str] = set()  # keys whose next packet replaces instead of merging

    def load(self) -> int:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return 0
        except (OSError, ValueError) as exc:
            log.warning("snapshot %s unreadable (%s); starting empty", self.path, exc)
            return 0
        ticks = data.get("ticks") if isinstance(data, dict) else None
        if isinstance(ticks, dict):
            self.ticks = {k: v for k, v in ticks.items() if isinstance(v, dict)}
        return len(self.ticks)

    def update(self, key: str, partial: dict, ts_recv: int, replace: bool = False) -> dict:
        """Merge a partial tick into the key's state and return the full state.

        ``replace`` (Upstox's initial_feed after a (re)subscribe) starts the key afresh so fields
        from yesterday or from a previous mode cannot linger; live packets merge, so groups a
        packet left out keep their last values.
        """
        tick = self.ticks.get(key)
        if tick is None or replace or key in self._fresh:
            self._fresh.discard(key)
            tick = self.ticks[key] = {"key": key}
        tick.update(partial)
        tick["ts_recv"] = ts_recv
        self.dirty = True
        return tick

    def reset(self, key: str) -> None:
        self._fresh.add(key)

    def prune(self, keep, cutoff_ms: int) -> int:
        """Forget keys no longer subscribed whose last tick is older than ``cutoff_ms``.

        Keys come and go (option strikes, expiries); without this the snapshot would grow with
        every key ever subscribed.
        """
        stale = [
            k for k, t in self.ticks.items() if k not in keep and t.get("ts_recv", 0) < cutoff_ms
        ]
        for key in stale:
            del self.ticks[key]
        self.dirty = self.dirty or bool(stale)
        return len(stale)

    def save(self) -> None:
        if self.dirty:
            payload = {"version": 1, "saved_at": int(time.time() * 1000), "ticks": self.ticks}
            atomic_write_json(self.path, payload)
            self.dirty = False


class TickRecorder:
    """Appends published ticks to ``<dir>/YYYY-MM-DD.jsonl.gz`` (IST trading date).

    Every flush appends one complete gzip member, so a crash loses at most the unflushed buffer
    and the file stays readable (``gzip`` reads multi-member files). A day stops recording once
    its file reaches ``max_bytes``; files older than ``keep_days`` are deleted at each new day.
    """

    MAX_BUFFER = 200_000  # lines; protects memory if the disk keeps failing

    def __init__(self, directory: Path, max_bytes: int, keep_days: int, clock=time.time):
        self.dir, self.max_bytes, self.keep_days = directory, max_bytes, keep_days
        self.clock = clock
        self._buf: list[bytes] = []
        self._day: str | None = None
        self.capped = False
        self.written = self.dropped = 0
        self.last_error: str | None = None

    def add(self, line: bytes) -> None:
        if len(self._buf) < self.MAX_BUFFER:
            self._buf.append(line)
        else:
            self.dropped += 1

    def path(self) -> Path:
        return self.dir / f"{self._day}.jsonl.gz"

    def flush(self) -> None:
        day = datetime.fromtimestamp(self.clock(), IST).date().isoformat()
        if day != self._day:
            self._day, self.capped = day, False
            self._prune(day)
        lines, self._buf = self._buf, []
        if not lines:
            return
        path = self.path()
        try:
            if not self.capped and path.exists() and path.stat().st_size >= self.max_bytes:
                self.capped = True
                log.warning(
                    "tick recording for %s stopped at the %d MB cap", day, self.max_bytes >> 20
                )
            if self.capped:
                self.dropped += len(lines)
                return
            self.dir.mkdir(parents=True, exist_ok=True)
            with gzip.open(path, "ab", compresslevel=6) as gz:
                gz.write(b"\n".join(lines) + b"\n")
            self.written += len(lines)
            self.last_error = None
        except OSError as exc:
            self.dropped += len(lines)
            if str(exc) != self.last_error:  # once per distinct error, not once per flush
                log.error("tick recorder cannot write %s: %s", path, exc)
            self.last_error = str(exc)

    def _prune(self, today: str) -> None:
        cutoff = (date.fromisoformat(today) - timedelta(days=self.keep_days)).isoformat()
        with contextlib.suppress(OSError):
            for f in self.dir.glob("*.jsonl.gz"):
                if _DAY_FILE.fullmatch(f.name) and f.name[:10] < cutoff:
                    f.unlink()
                    log.info("deleted old tick recording %s", f.name)

    def status(self) -> dict:
        size = None
        if self._day:
            with contextlib.suppress(OSError):
                size = self.path().stat().st_size
        return {
            "file": self.path().name if self._day else None, "bytes_today": size,
            "capped": self.capped, "lines_written": self.written, "lines_dropped": self.dropped,
            "buffered": len(self._buf), "last_error": self.last_error,
        }  # fmt: skip
