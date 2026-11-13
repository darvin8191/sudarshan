"""The family store's copy for the nightly backup (P6 4.4; cross-design Y9): ``family-copy.db``.

The nightly backup runs as ``sudarshan`` at 16:30 and 22:30 IST and cannot open this service's own
folder (0700, another account). So at 16:20 and 22:20, ten minutes before, this service leaves a
copy of its store in the export folder it shares by group with the ring, and the backup takes that
file like any ring database:

* SQLite's online backup of family.db, made on the store thread (no write lands halfway through);
* ``PRAGMA journal_mode=DELETE`` on the copy, so that a reader needs no ``-wal`` or ``-shm`` file
  beside it (the backup's account cannot write in that folder);
* ``PRAGMA quick_check`` on the copy before it counts;
* the copy made as a new file (0640, whatever the umask: ``fchmod``), synced and renamed over the
  last one, so a reader only ever sees a whole copy. The folder is setgid ``sudarshan``, which
  gives the file its group.

A slot runs once a day (the ``job_run`` row ``copy-1620`` or ``copy-2220`` of the day), and the
latest slot due is caught up at a start that missed it. A copy that fails is logged in words and
tried again at the next check; the copy left by the last good run stays in place.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
import sqlite3
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import store as store_module
from .errors import StoreError

log = logging.getLogger("sudarshan.family")

COPY_NAME = "family-copy.db"
COPY_MODE = 0o640
#: The slots, IST: ten minutes before the nightly backup's 16:30 and 22:30.
SLOTS = ((16, 20), (22, 20))
CHECK_EVERY_S = 30.0
IST = timezone(timedelta(hours=5, minutes=30))


def due(now_ms: int) -> tuple[str, int]:
    """The latest slot at or before ``now``: its job name and its day (YYYYMMDD, IST)."""
    now = datetime.fromtimestamp(now_ms / 1000, UTC).astimezone(IST)
    for day in (now.date(), now.date() - timedelta(days=1)):
        for hour, minute in reversed(SLOTS):
            at = datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)
            if at <= now:
                return f"copy-{hour:02d}{minute:02d}", _day(day)
    raise AssertionError("a slot is always due within a day")  # pragma: no cover


def _day(day: date) -> int:
    return day.year * 10000 + day.month * 100 + day.day


def make_copy(source: Path, folder: Path) -> int:
    """Copy the store into ``folder/family-copy.db`` as described above; its size in bytes."""
    if not folder.is_dir():
        raise FileNotFoundError(f"{folder} is not there; the family set-up makes it")
    temp = folder / f".{COPY_NAME}.{secrets.token_hex(4)}"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    os.close(os.open(temp, flags, COPY_MODE))
    try:
        reader = sqlite3.connect(f"file:{quote(str(source))}?mode=ro", uri=True)
        try:
            writer = sqlite3.connect(temp)
            try:
                reader.backup(writer)
                writer.execute("PRAGMA journal_mode=DELETE")
                sound = writer.execute("PRAGMA quick_check").fetchall() == [("ok",)]
            finally:
                writer.close()
        finally:
            reader.close()
        if not sound:
            raise sqlite3.DatabaseError("the copy did not pass its check")
        fd = os.open(temp, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fchmod(fd, COPY_MODE)  # the unit's UMask=0077 took the group's bits at creation
            os.fsync(fd)
            size = os.fstat(fd).st_size
        finally:
            os.close(fd)
        os.replace(temp, folder / COPY_NAME)
    except BaseException:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise
    with contextlib.suppress(OSError):
        dir_fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    return size


async def run_due(service: Any) -> bool | None:
    """Make the copy of the latest slot if it has not been made; True made, False failed, None
    nothing to do (or no store)."""
    opened = service.store
    if opened is None:
        return None
    job, day = due(service.clock())
    if await opened.read(lambda tx: tx.job(job, day)) is not None:
        return None
    source, folder = opened.path, service.settings.export_dir
    try:
        size = await store_module.on_writer_async(lambda: make_copy(source, folder))
    except (OSError, sqlite3.Error) as exc:
        log.error("the family store's copy for the nightly backup was not made (%s); the last "
                  "copy stays, and it is tried again in a minute", exc)  # fmt: skip
        service.copy_state = {"at": service.clock(), "ok": False}
        return False
    try:
        await opened.write(lambda tx: tx.job_put(job, day, "ok", f"{size} bytes", service.clock()))
    except StoreError as exc:
        log.error("the copy was made but its run could not be recorded (%s)", exc.cause)
    service.copy_state = {"at": service.clock(), "ok": True}
    log.info("made the family store's copy for the nightly backup (%s, %d bytes)", job, size)
    return True


async def copy_job(service: Any) -> None:
    """Check every 30 seconds whether a slot's copy is due."""
    while True:
        await run_due(service)
        await asyncio.sleep(CHECK_EVERY_S)
