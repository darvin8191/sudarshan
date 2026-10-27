"""Nightly local backups (M18.11, part 1): one archive a day of what the server cannot rebuild.

    sudarshan-backup.timer (16:30 and 22:30 IST) -> sudarshan-backup.service -> this module
      -> /var/lib/sudarshan-backup/archive/sudarshan-backup-YYYYMMDD-HHMMSS.tar.gz   (mode 600)

What goes in, of what exists (a source that is there and cannot be read makes a partial archive):

  db/*.db                    OpenAlgo's databases (openalgo.db, sandbox.db, logs.db, ...)
  db/sudarshan-*/            SUDARSHAN's own folders under db/ as they are (the sealed vault: the
                             folder 700, its files 600, kept as such inside the archive)
  /etc/sudarshan/            the machine keys and settings, the NSE day files
  /var/lib/sudarshan-*/*.db  the ring's databases (alerts.db, market.db, ...)
  the app's .env             without it a restored openalgo.db and the vault cannot be decrypted
                             (the archive therefore holds the keys that protect it: keep it as
                             private as the .env itself, and encrypt it before it leaves the server)

Not in it: historify.duckdb (market history that is fetched again; DuckDB has no online backup),
nginx and the certificates (root's, and re-issued in minutes).

How a database is copied: SQLite's backup, from a read-only connection, into a private work folder;
the copy is checked (``PRAGMA quick_check``) and only then packed. Never a plain copy of a live
file: that can catch a database half written. Rows still in the write-ahead log are in the copy.
Any other file is copied to the work folder first and packed from there, so a file that changes
while it is read can never leave a damaged archive.

When it runs, and when it refuses to:

  Never between 08:30 and 16:00 on a trading day, nor in a listed day's own session (the same
        hours the deploy tool installs outside of). The timer fires after the close; a run that
        a boot or a missed timer starts earlier stops quietly, and one that was still going when
        the market opened is cleaned up and left for the next time.
  Once a night. An archive less than ``min_gap_s`` old (a complete one) ends the run at once, so
        the second timer of the day, and the run the deploy tool starts when it restarts the
        service after an install, cost nothing.
  Not with the deploy tool. It holds a lock for the whole of an install; the job takes the same
        lock shared and waits for it, so a migration never meets an open copy of the database it
        is changing, and the tool never stops the job half way.

Keeping them: ``retention.py`` (30 daily and 12 monthly archives, then the size cap). Space is
checked before anything is written: an archive that does not fit is not started, and the owner
is told why.

Failures reach the owner's Telegram through item 1's path: ``ops.events.record`` writes the fact
to ``events.jsonl`` in this job's state folder and ``sudarshan-alerts`` words and sends it once
(``alerts/notices.py``). ``status.json`` beside it says how the last run went.

Offsite (rclone to OCI Object Storage) needs the owner's keys, which do not exist yet. The hook
for it is ``SUDARSHAN_BACKUP_OFFSITE_CMD``: a command line (run without a shell) with ``{archive}``
(the file's full path), ``{name}`` and ``{dir}`` (the folder of archives) in it, run after each
good archive. It must name ``{archive}`` or ``{dir}``. A command that copies the whole folder
(``rclone copy {dir} ...`` skips what is already there) also sends the archive an earlier failed
or stopped upload left behind; one that copies ``{archive}`` sends that file only. Left empty,
nothing happens.

Exit status: 0 done or nothing to do, 1 something failed (the archive that could be made is made),
2 a setting that cannot be used.

Other commands: ``--check`` says what a run would do and changes nothing; ``--force`` makes an
archive although a recent one exists; ``--verify FILE`` reads an archive back against the record it
carries (0 sound, 1 something wrong, 2 not an archive) and touches nothing else.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import grp
import hashlib
import json
import logging
import math
import os
import pwd
import re
import shlex
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from sudarshan.autologin.schedule import (
    EXTRA_TRADING_DAYS_FILE,
    HOLIDAYS_FILE,
    IST,
    TradingCalendar,
    minute_of_day,
    minutes,
)

from . import retention
from .events import MAX_VALUE, record
from .release import DEPLOYED_TAG_FILE, installed_release

log = logging.getLogger("sudarshan.backup")

REPO_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = "/var/lib/sudarshan-backup"
ETC_DIR = "/etc/sudarshan"
RING_VAR = "/var/lib"
DEPLOY_LOCK = "/var/lib/sudarshan/deploy.lock"
#: No run in these hours of a trading day (the deploy tool's install window is the rest).
BLOCKED = ("08:30", "16:00")
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

GIB = 1 << 30
MANIFEST = "MANIFEST.json"
FORMAT = 1
DB_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.db")
FILE_NAME = re.compile(r"[A-Za-z0-9._-][A-Za-z0-9._-]{0,127}")
OWN_DIR = re.compile(r"sudarshan-[a-z0-9-]{1,60}")
SIDE_FILES = (".db-wal", ".db-shm", ".db-journal")  # a database's own, never packed apart from it
MAX_PLAIN_FILE = 32 << 20  # a plain file bigger than this is not a key or a setting
MAX_PLAIN_TOTAL = 64 << 20
MAX_ENV_FILE = 1 << 20
MAX_DEPTH = 4
#: A notice names at most this many files (alerts/notices.py takes the same number).
NOTICE_NAMES = 10
#: An archive is guessed to take this much of the databases' size, for the free-space check.
COMPRESSED_GUESS = 0.6
SNAPSHOT_TRIES = 3
SNAPSHOT_PAUSE_S = 10.0
LOCK_POLL_S = 10.0
OFFSITE_LOG_BYTES = 4096

OK, PARTIAL, FAILED, SKIPPED = "ok", "partial", "failed", "skipped"

#: Why a run failed, as the notice (alerts/notices.py) names it.
SPACE, CAP, WRITE, SOURCE, INTEGRITY, OFFSITE, SETTINGS, OTHER = (
    "space", "cap", "write", "source", "integrity", "offsite", "settings", "other",
)  # fmt: skip


class ConfigError(ValueError):
    """A setting the job cannot use."""


class Deferred(Exception):  # noqa: N818 - not an error: the market opened, the run steps aside
    """The market opened while the job was running."""


class WriteFailed(Exception):  # noqa: N818
    """The archive could not be written; ``space`` says whether the disk is full."""

    def __init__(self, space: bool):
        super().__init__("disk full" if space else "write failed")
        self.space = space


@dataclass(frozen=True)
class Settings:
    app_dir: Path
    etc_dir: Path
    ring_var: Path
    state_dir: Path
    deploy_lock: Path
    holidays_file: Path
    extra_days_file: Path
    deployed_tag_file: Path
    keep_daily: int = 30
    keep_monthly: int = 12
    max_bytes: int = 10 * GIB
    min_free_bytes: int = 2 * GIB
    min_gap_s: float = 8 * 3600.0
    deploy_wait_s: float = 20 * 60.0
    offsite_cmd: tuple[str, ...] = ()
    offsite_timeout_s: float = 3600.0

    @property
    def archive_dir(self) -> Path:
        return self.state_dir / "archive"

    @property
    def work_dir(self) -> Path:
        return self.state_dir / "work"

    @property
    def spool(self) -> Path:
        return self.state_dir / "events.jsonl"

    @property
    def status_file(self) -> Path:
        return self.state_dir / "status.json"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        get = env.get
        app_dir = Path(get("SUDARSHAN_OPENALGO_DIR") or REPO_ROOT)
        return cls(
            app_dir=app_dir,
            etc_dir=Path(get("SUDARSHAN_ETC_DIR") or ETC_DIR),
            ring_var=Path(get("SUDARSHAN_RING_VAR") or RING_VAR),
            state_dir=state_dir_from(env),
            deploy_lock=Path(get("SUDARSHAN_DEPLOY_LOCK") or DEPLOY_LOCK),
            holidays_file=Path(get("SUDARSHAN_NSE_HOLIDAYS_FILE") or HOLIDAYS_FILE),
            extra_days_file=Path(get("SUDARSHAN_NSE_EXTRA_DAYS_FILE") or EXTRA_TRADING_DAYS_FILE),
            deployed_tag_file=Path(get("SUDARSHAN_DEPLOYED_TAG_FILE") or DEPLOYED_TAG_FILE),
            keep_daily=int(_number(env, "SUDARSHAN_BACKUP_KEEP_DAILY", 30, 1, 366)),
            keep_monthly=int(_number(env, "SUDARSHAN_BACKUP_KEEP_MONTHLY", 12, 0, 120)),
            max_bytes=int(_number(env, "SUDARSHAN_BACKUP_MAX_GB", 10, 0.01, 1e6) * GIB),
            min_free_bytes=int(_number(env, "SUDARSHAN_BACKUP_MIN_FREE_GB", 2, 0, 1e7) * GIB),
            min_gap_s=_number(env, "SUDARSHAN_BACKUP_MIN_GAP_HOURS", 8, 0, 72) * 3600.0,
            deploy_wait_s=_number(env, "SUDARSHAN_BACKUP_DEPLOY_WAIT_MIN", 20, 0, 600) * 60.0,
            offsite_cmd=_command(get("SUDARSHAN_BACKUP_OFFSITE_CMD")),
            offsite_timeout_s=_number(env, "SUDARSHAN_BACKUP_OFFSITE_TIMEOUT_MIN", 60, 1, 720)
            * 60.0,
        )


def state_dir_from(env: Mapping[str, str]) -> Path:
    """This job's state folder. systemd's StateDirectory= may list several, separated by ':'."""
    chosen = env.get("SUDARSHAN_BACKUP_STATE_DIR") or env.get("STATE_DIRECTORY") or STATE_DIR
    return Path(chosen.split(":")[0])


def _number(env: Mapping[str, str], name: str, default: float, low: float, high: float) -> float:
    raw = (env.get(name) or "").strip()
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number") from None
    if not math.isfinite(value) or not low <= value <= high:
        raise ConfigError(f"{name} must be between {low:g} and {high:g}")
    return value


def _command(raw: str | None) -> tuple[str, ...]:
    """The offsite command as words; empty is off. It must name what to send."""
    text = (raw or "").strip()
    if not text:
        return ()
    try:
        words = tuple(shlex.split(text))
    except ValueError:
        raise ConfigError("SUDARSHAN_BACKUP_OFFSITE_CMD has an unclosed quote") from None
    if not any("{archive}" in word or "{dir}" in word for word in words):
        raise ConfigError("SUDARSHAN_BACKUP_OFFSITE_CMD must contain {archive} or {dir}")
    return words


# ---- the hours --------------------------------------------------------------------------------


def market_blocks(calendar: TradingCalendar, at: datetime) -> bool:
    """True from 08:30 to 16:00 on a trading day, and in a listed day's own session."""
    local = at.astimezone(IST)
    day = local.date()
    if not calendar.is_trading_day(day):
        return False
    spans = calendar.market_spans(day, (minutes(BLOCKED[0]), minutes(BLOCKED[1])))
    now = minute_of_day(local)
    return any(start <= now < end for start, end in spans)


# ---- what to back up --------------------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    arc: str  # the name inside the archive
    path: Path
    kind: str  # "sqlite" or "file"
    size: int  # on disk now: a database's file plus its log
    mode: int
    uid: int
    gid: int


@dataclass(frozen=True)
class Folder:
    arc: str
    mode: int
    uid: int
    gid: int


@dataclass
class Found:
    sources: list[Source] = field(default_factory=list)
    folders: dict[str, Folder] = field(default_factory=dict)
    #: Files that are there but were not taken (too big, a name the archive does not take): the
    #: archive's record names them and the owner is told, as for a file that could not be copied.
    skipped: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(s.size for s in self.sources)

    @property
    def largest_database(self) -> int:
        return max((s.size for s in self.sources if s.kind == "sqlite"), default=0)


def _entries(path: Path) -> list[os.DirEntry]:
    try:
        with os.scandir(path) as scan:
            return sorted(scan, key=lambda e: e.name)
    except OSError:
        return []


def _stat(entry: os.DirEntry | Path) -> os.stat_result | None:
    try:
        return os.lstat(entry.path if isinstance(entry, os.DirEntry) else entry)
    except OSError:
        return None


def _is_file(info: os.stat_result | None) -> bool:
    return info is not None and stat.S_ISREG(info.st_mode)


def _is_dir(info: os.stat_result | None) -> bool:
    return info is not None and stat.S_ISDIR(info.st_mode)


def _add_database(found: Found, arc: str, path: Path, info: os.stat_result) -> None:
    log_info = _stat(Path(f"{path}-wal"))
    size = info.st_size + (log_info.st_size if _is_file(log_info) else 0)
    found.sources.append(Source(arc, path, "sqlite", size, info.st_mode, info.st_uid, info.st_gid))


def _add_folder(found: Found, arc: str, info: os.stat_result) -> None:
    found.folders.setdefault(arc, Folder(arc, info.st_mode, info.st_uid, info.st_gid))


def discover(settings: Settings) -> Found:
    """Everything that exists to be backed up. Links are not followed or taken. In the places
    taken whole (the sealed folders, ``/etc/sudarshan``, the ``.env``) and for a database, what is
    there and not taken goes in ``skipped`` to be told, never left out quietly."""
    found = Found()
    db = settings.app_dir / "db"
    for entry in _entries(db):
        info = _stat(entry)
        if _is_file(info) and DB_NAME.fullmatch(entry.name):
            _add_database(found, f"db/{entry.name}", Path(entry.path), info)
        elif _is_file(info) and entry.name.endswith(".db"):
            found.skipped.append(f"db/{entry.name}")
        elif _is_dir(info) and OWN_DIR.fullmatch(entry.name):
            _add_sealed(found, entry, info)
    _walk_plain(found, settings.etc_dir, "etc/sudarshan", depth=0, budget=[MAX_PLAIN_TOTAL])
    _add_ring(found, settings)
    env_file = settings.app_dir / ".env"
    info = _stat(env_file)
    if _is_file(info) and info.st_size <= MAX_ENV_FILE:
        _add_plain(found, "app/openalgo.env", env_file, info)
    elif _is_file(info):
        found.skipped.append("app/openalgo.env")
    return found


def _add_sealed(found: Found, folder: os.DirEntry, info: os.stat_result) -> None:
    """One of SUDARSHAN's own folders under ``db/`` (the owner vault): every file in it, whole."""
    _add_folder(found, f"db/{folder.name}", info)
    for inner in _entries(Path(folder.path)):
        inner_info = _stat(inner)
        arc = f"db/{folder.name}/{inner.name}"
        if inner.name.endswith(SIDE_FILES) or not (_is_file(inner_info) or _is_dir(inner_info)):
            continue  # a database's side files go with it; links and the like are not sources
        if not _is_file(inner_info) or not FILE_NAME.fullmatch(inner.name):
            found.skipped.append(arc)  # a folder inside, or a name the archive does not take
        elif DB_NAME.fullmatch(inner.name):
            _add_database(found, arc, Path(inner.path), inner_info)
        elif inner.name.endswith(".db") or inner_info.st_size > MAX_PLAIN_FILE:
            found.skipped.append(arc)  # a database is never copied as a plain file
        else:
            _add_plain(found, arc, Path(inner.path), inner_info)


def _add_ring(found: Found, settings: Settings) -> None:
    """The databases in the state folders of the ring's services (not this job's own)."""
    own_state = settings.state_dir.resolve()
    for entry in _entries(settings.ring_var):
        info = _stat(entry)
        if not _is_dir(info) or not OWN_DIR.fullmatch(entry.name):
            continue
        if Path(entry.path).resolve() == own_state:
            continue
        for inner in _entries(Path(entry.path)):
            inner_info = _stat(inner)
            if not _is_file(inner_info):
                continue
            if DB_NAME.fullmatch(inner.name):
                _add_folder(found, f"ring/{entry.name}", info)
                _add_database(
                    found, f"ring/{entry.name}/{inner.name}", Path(inner.path), inner_info
                )
            elif inner.name.endswith(".db"):
                found.skipped.append(f"ring/{entry.name}/{inner.name}")


def _add_plain(found: Found, arc: str, path: Path, info: os.stat_result) -> None:
    found.sources.append(
        Source(arc, path, "file", info.st_size, info.st_mode, info.st_uid, info.st_gid)
    )


def _walk_plain(found: Found, folder: Path, arc: str, *, depth: int, budget: list[int]) -> None:
    """The regular files under ``folder`` (no links), with a size budget. What is a file or a
    folder and is not taken (a name or a size the archive does not take) is named in
    ``found.skipped``; links and the like are not sources and are passed over."""
    info = _stat(folder)
    if not _is_dir(info):
        return
    if depth > MAX_DEPTH:
        found.skipped.append(arc)
        return
    _add_folder(found, arc, info)
    for entry in _entries(folder):
        inner = _stat(entry)
        here = f"{arc}/{entry.name}"
        if not (_is_dir(inner) or _is_file(inner)):
            continue
        if not FILE_NAME.fullmatch(entry.name):
            found.skipped.append(here)
        elif _is_dir(inner):
            _walk_plain(found, Path(entry.path), here, depth=depth + 1, budget=budget)
        elif inner.st_size <= MAX_PLAIN_FILE and budget[0] >= inner.st_size:
            budget[0] -= inner.st_size
            _add_plain(found, here, Path(entry.path), inner)
        else:
            found.skipped.append(here)


# ---- copies -----------------------------------------------------------------------------------


class SourceError(Exception):
    """One source could not be copied; ``why`` is SOURCE."""


def _uri(path: Path, query: str) -> str:
    return f"file:{quote(str(path))}?{query}"


def snapshot(source: Path, dest: Path, *, sleep: Callable[[float], None] = time.sleep) -> None:
    """SQLite's online backup of ``source`` into the new file ``dest``; raises SourceError.

    The source is opened read-only, so nothing here can change it. A database that is busy is
    tried again a few times.
    """
    last: Exception | None = None
    for attempt in range(SNAPSHOT_TRIES):
        try:
            reader = sqlite3.connect(_uri(source, "mode=ro"), uri=True, timeout=30)
            try:
                writer = sqlite3.connect(dest)
                try:
                    reader.backup(writer)
                finally:
                    writer.close()
            finally:
                reader.close()
            return
        except (sqlite3.Error, OSError, MemoryError) as exc:
            # MemoryError: what Python raises when SQLite reports a damaged page as "out of memory".
            last = exc
            with contextlib.suppress(OSError):
                dest.unlink()
            if attempt + 1 < SNAPSHOT_TRIES:
                sleep(SNAPSHOT_PAUSE_S)
    raise SourceError(str(last) or type(last).__name__)


def quick_check(copy: Path) -> bool:
    """True when SQLite finds the copy sound (it reads the whole file)."""
    try:
        con = sqlite3.connect(_uri(copy, "mode=ro&immutable=1"), uri=True)
        try:
            return con.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        finally:
            con.close()
    except (sqlite3.Error, MemoryError):
        return False


def copy_stable(source: Path, dest: Path) -> None:
    """A plain file copied whole into ``dest``; raises SourceError if it cannot be read, or if it
    changed size while it was read (a file being written is not a copy worth keeping)."""
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(source, flags)
    except OSError as exc:
        raise SourceError(exc.strerror or "cannot be opened") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise SourceError("not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as reader, dest.open("wb") as writer:
            shutil.copyfileobj(reader, writer, 1 << 20)
        after = os.fstat(fd)
        if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            raise SourceError("changed while it was copied")
    except OSError as exc:
        raise SourceError(exc.strerror or "cannot be read") from exc
    finally:
        os.close(fd)


# ---- the archive ------------------------------------------------------------------------------


@dataclass
class Member:
    name: str
    kind: str
    size: int
    sha256: str
    integrity: str = ""  # databases: ok / failed


@dataclass
class Failure:
    why: str
    names: tuple[str, ...] = ()
    facts: dict[str, str] = field(default_factory=dict)


class _Hashing:
    """A file object that sha256s what is read from it (tarfile reads it once, in order)."""

    def __init__(self, handle):
        self._handle = handle
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        data = self._handle.read(size)
        self.digest.update(data)
        return data


def _names(uid: int, gid: int) -> tuple[str, str]:
    try:
        user = pwd.getpwuid(uid).pw_name
    except KeyError:
        user = ""
    try:
        group = grp.getgrgid(gid).gr_name
    except KeyError:
        group = ""
    return user, group


def _info(arc: str, *, mode: int, uid: int, gid: int, mtime: float, size: int = 0, folder=False):
    item = tarfile.TarInfo(arc)
    item.type = tarfile.DIRTYPE if folder else tarfile.REGTYPE
    item.mode = stat.S_IMODE(mode)
    item.uid, item.gid = uid, gid
    item.uname, item.gname = _names(uid, gid)
    item.mtime = int(mtime)
    item.size = 0 if folder else size
    return item


def _parents(arc: str) -> Iterator[str]:
    parts = arc.split("/")[:-1]
    for end in range(1, len(parts) + 1):
        yield "/".join(parts[:end])


@dataclass
class Built:
    members: list[Member]
    failures: list[Failure]
    manifest_sha: str


def write_archive(
    settings: Settings,
    found: Found,
    work: Path,
    target: Path,
    started: datetime,
    *,
    still_open: Callable[[], bool],
    sleep: Callable[[float], None] = time.sleep,
) -> Built:
    """Pack every source into ``target`` (a new file). Raises Deferred when the market opens and
    WriteFailed when the archive itself cannot be written; a source that cannot be copied is a
    Failure and the rest goes on."""
    members: list[Member] = []
    unreadable: list[str] = list(found.skipped)
    unsound: list[str] = []
    made = started.timestamp()
    folders_done: set[str] = set()

    def folder_entry(tar: tarfile.TarFile, arc: str) -> None:
        for parent in _parents(arc):
            if parent in folders_done:
                continue
            folders_done.add(parent)
            known = found.folders.get(parent)
            tar.addfile(
                _info(
                    parent,
                    mode=known.mode if known else 0o755,
                    uid=known.uid if known else os.getuid(),
                    gid=known.gid if known else os.getgid(),
                    mtime=made,
                    folder=True,
                )
            )

    def stage(source: Source, staged: Path) -> str:
        """Copy the source to a stable file; returns the integrity verdict ("" for a file).
        Whatever goes wrong with one source is that source's failure, never the run's."""
        try:
            if source.kind == "sqlite":
                snapshot(source.path, staged, sleep=sleep)
                return "ok" if quick_check(staged) else "failed"
            copy_stable(source.path, staged)
            return ""
        except SourceError:
            raise
        except Exception as exc:
            log.exception("%s could not be copied", source.arc)
            raise SourceError(type(exc).__name__) from exc

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(target, flags, 0o600)
    except OSError as exc:
        raise WriteFailed(exc.errno == errno.ENOSPC) from exc
    try:
        with os.fdopen(fd, "wb") as raw:
            with tarfile.open(fileobj=raw, mode="w:gz", compresslevel=6) as tar:
                for number, source in enumerate(found.sources):
                    if not still_open():
                        raise Deferred
                    staged = work / f"stage-{number}"
                    try:
                        try:
                            integrity = stage(source, staged)
                        except SourceError as exc:
                            log.warning("%s could not be copied: %s", source.arc, exc)
                            unreadable.append(source.arc)
                            continue
                        if integrity == "failed":
                            log.error("%s failed SQLite's own check", source.arc)
                            unsound.append(source.arc)
                        folder_entry(tar, source.arc)
                        item = _info(
                            source.arc,
                            mode=source.mode,
                            uid=source.uid,
                            gid=source.gid,
                            mtime=made,
                            size=staged.stat().st_size,
                        )
                        with staged.open("rb") as handle:
                            hashed = _Hashing(handle)
                            tar.addfile(item, hashed)
                        members.append(
                            Member(
                                source.arc,
                                source.kind,
                                item.size,
                                hashed.digest.hexdigest(),
                                integrity,
                            )
                        )
                    finally:
                        staged.unlink(missing_ok=True)
                failures = _failures(unreadable, unsound)
                body = _manifest(started, settings, members, failures)
                tar.addfile(
                    _info(
                        MANIFEST,
                        mode=0o600,
                        uid=os.getuid(),
                        gid=os.getgid(),
                        mtime=made,
                        size=len(body),
                    ),
                    _Bytes(body),
                )
            raw.flush()
            os.fsync(raw.fileno())
    except OSError as exc:
        raise WriteFailed(exc.errno == errno.ENOSPC) from exc
    return Built(members, failures, hashlib.sha256(body).hexdigest())


class _Bytes:
    def __init__(self, data: bytes):
        self._data = data
        self._at = 0

    def read(self, size: int = -1) -> bytes:
        end = len(self._data) if size < 0 else self._at + size
        chunk = self._data[self._at : end]
        self._at += len(chunk)
        return chunk


def _failures(unreadable: list[str], unsound: list[str]) -> list[Failure]:
    out = []
    if unreadable:
        out.append(Failure(SOURCE, tuple(unreadable)))
    if unsound:
        out.append(Failure(INTEGRITY, tuple(unsound)))
    return out


def _manifest(
    started: datetime, settings: Settings, members: list[Member], failures: list[Failure]
) -> bytes:
    document = {
        "format": FORMAT,
        "created": started.astimezone(IST).isoformat(timespec="seconds"),
        "release": installed_release(settings.deployed_tag_file),
        "partial": bool(failures),
        "members": [
            {
                "name": m.name,
                "kind": m.kind,
                "size": m.size,
                "sha256": m.sha256,
                **({"integrity": m.integrity} if m.integrity else {}),
            }
            for m in members
        ],
        "not_saved": [{"name": n, "why": f.why} for f in failures for n in f.names],
    }
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()


def _read_back(path: Path) -> tuple[dict[str, tuple[int, str]], bytes] | None:
    """Every regular member of the archive as (size, sha256), and the manifest's bytes; None when
    the archive cannot be read to its end."""
    seen: dict[str, tuple[int, str]] = {}
    manifest = b""
    try:
        with tarfile.open(path, "r:gz") as tar:
            for item in tar:
                if not item.isreg():
                    continue
                handle = tar.extractfile(item)
                if handle is None:
                    return None
                digest, size, kept = hashlib.sha256(), 0, []
                for chunk in iter(lambda h=handle: h.read(1 << 20), b""):
                    digest.update(chunk)
                    size += len(chunk)
                    if item.name == MANIFEST and size <= MAX_PLAIN_FILE:
                        kept.append(chunk)
                seen[item.name] = (size, digest.hexdigest())
                if item.name == MANIFEST:
                    manifest = b"".join(kept)
    except (tarfile.TarError, OSError, EOFError):
        return None
    return seen, manifest


def verify(path: Path, built: Built) -> bool:
    """True when the archive reads back whole: every member there with the hash it was packed
    with, and the manifest as written."""
    expected = {m.name: m.sha256 for m in built.members}
    expected[MANIFEST] = built.manifest_sha
    read = _read_back(path)
    return read is not None and {name: found[1] for name, found in read[0].items()} == expected


def check_archive(path: Path) -> tuple[list[str], dict]:
    """Read an archive back against the record it carries. Returns the problems found, in plain
    words (none: the archive is sound), and the manifest. Reads the file and nothing else."""
    read = _read_back(path)
    if read is None:
        return ["the file cannot be read as a backup archive"], {}
    seen, raw = read
    try:
        manifest = json.loads(raw)
        listed = {m["name"]: (int(m["size"]), str(m["sha256"])) for m in manifest["members"]}
        if manifest["format"] != FORMAT:
            return [
                f"the archive is of a format this program does not know ({manifest['format']})"
            ], {}
    except (ValueError, KeyError, TypeError):
        return ["the archive has no readable record of what it should hold"], {}
    problems = []
    for name, packed in sorted(listed.items()):
        if name not in seen:
            problems.append(f"{name} is missing")
        elif seen[name] != packed:
            problems.append(f"{name} is not what was saved")
    problems += [
        f"{name} is in the archive but not in its record"
        for name in sorted(set(seen) - set(listed) - {MANIFEST})
    ]
    return problems, manifest


# ---- the folder -------------------------------------------------------------------------------


def list_archives(folder: Path) -> list[retention.Backup]:
    found = []
    for entry in _entries(folder):
        info = _stat(entry)
        if not _is_file(info):
            continue
        backup = retention.parse(entry.name, info.st_size)
        if backup:
            found.append(backup)
    return found


def remove(folder: Path, doomed: tuple[retention.Backup, ...]) -> None:
    for backup in doomed:
        try:
            (folder / backup.name).unlink()
            log.info("removed %s (outside what is kept)", backup.name)
        except OSError:
            log.warning("could not remove %s", backup.name)


def free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def clean_leftovers(settings: Settings) -> None:
    """What a run that was killed left: it held the lock, so these are nobody's now."""
    for entry in _entries(settings.archive_dir):
        if entry.name.endswith(".partial"):
            with contextlib.suppress(OSError):
                os.unlink(entry.path)
    shutil.rmtree(settings.work_dir, ignore_errors=True)


# ---- locks ------------------------------------------------------------------------------------


@contextlib.contextmanager
def single_run(path: Path) -> Iterator[bool]:
    """True when no other run of the job holds the lock (it is held until the block ends)."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0), 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            got = True
        except BlockingIOError:
            got = False
        yield got
    finally:
        os.close(fd)


@contextlib.contextmanager
def with_deploy_tool(
    path: Path,
    wait_s: float,
    *,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> Iterator[str]:
    """Take the deploy tool's lock shared, waiting up to ``wait_s`` for an install to end.

    Yields ``held``, ``absent`` (no such file: a machine the tool never ran on, or one that
    cannot be opened, in which case the job goes on without) or ``busy`` (an install is still
    running after the wait; the job steps aside).
    """
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        log.info("the deploy tool's lock is not there to wait for; going on without it")
        yield "absent"
        return
    try:
        state = "busy"
        deadline = monotonic() + wait_s
        told = False
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                state = "held"
                break
            except BlockingIOError:
                if monotonic() >= deadline:
                    break
                if not told:
                    log.info("an install is running; waiting for it to finish")
                    told = True
                sleep(min(LOCK_POLL_S, max(deadline - monotonic(), 0.05)))
        yield state
    finally:
        os.close(fd)


# ---- offsite ----------------------------------------------------------------------------------


def send_offsite(settings: Settings, archive: Path) -> str:
    """Run the owner's offsite command for ``archive``: "off" (none set), "ok" or "failed"."""
    if not settings.offsite_cmd:
        return "off"
    words = [
        w.replace("{archive}", str(archive))
        .replace("{name}", archive.name)
        .replace("{dir}", str(archive.parent))
        for w in settings.offsite_cmd
    ]
    out = settings.state_dir / "offsite.log"
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(settings.state_dir),
        "LANG": "C.UTF-8",
    }
    try:
        fd = os.open(
            out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_CLOEXEC", 0), 0o600
        )
        with os.fdopen(fd, "wb") as sink:
            # The owner's own command, run without a shell.
            done = subprocess.run(
                words,
                stdin=subprocess.DEVNULL,
                stdout=sink,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=settings.state_dir,
                timeout=settings.offsite_timeout_s,
                check=False,
            )
    except subprocess.TimeoutExpired:
        log.warning("the offsite command did not finish in time; see %s", out)
        return "failed"
    except OSError as exc:
        log.warning(
            "the offsite command could not be started: %s", exc.strerror or "unknown reason"
        )
        return "failed"
    if done.returncode != 0:
        log.warning("the offsite command failed with status %d; see %s", done.returncode, out)
        return "failed"
    log.info("offsite: %s sent", archive.name)
    return "ok"


# ---- one run ----------------------------------------------------------------------------------


@dataclass
class Result:
    outcome: str  # OK, PARTIAL, FAILED, SKIPPED
    reason: str = ""  # SKIPPED: market, fresh, busy, deploy, nothing
    archive: str = ""
    size: int = 0
    members: int = 0
    failures: list[Failure] = field(default_factory=list)
    offsite: str = "off"
    kept: int = 0
    used_bytes: int = 0

    @property
    def exit_code(self) -> int:
        if self.outcome in (PARTIAL, FAILED) or self.failures:
            return 1
        return 0


def _gb(value: float) -> str:
    """Gigabytes for a notice: 0.05, 1.37, 12.5 (the notice reads at most two decimals)."""
    return f"{value / GIB:.2f}" if value < 10 * GIB else f"{value / GIB:.1f}"


def run(
    settings: Settings,
    *,
    clock: Callable[[], datetime] | None = None,
    force: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> Result:
    """One nightly run. Never raises for anything about the data; the result says what happened."""
    clock = clock or (lambda: datetime.now(IST))
    started = clock()
    calendar = TradingCalendar(settings.holidays_file, settings.extra_days_file)

    def still_open() -> bool:
        return not market_blocks(calendar, clock())

    if not still_open():
        log.info("not during market hours; the next scheduled run will do it")
        return Result(SKIPPED, "market")
    settings.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with single_run(settings.state_dir / "backup.lock") as mine:
        if not mine:
            log.info("another backup run is going; this one stops")
            return Result(SKIPPED, "busy")
        with with_deploy_tool(
            settings.deploy_lock, settings.deploy_wait_s, sleep=sleep, monotonic=monotonic
        ) as deploy:
            if deploy == "busy":
                log.info("an install is still running; the next scheduled run will do it")
                return Result(SKIPPED, "deploy")
            result = _run_locked(settings, started, still_open, force, sleep)
        # The install lock is let go: sending the file touches no database, and may take long.
        if result.archive and result.outcome in (OK, PARTIAL):
            result.offsite = send_offsite(settings, settings.archive_dir / result.archive)
            if result.offsite == "failed":
                result.failures.append(Failure(OFFSITE))
    _write_status(settings, started, result)
    _tell(settings, started, result)
    return result


def _run_locked(
    settings: Settings,
    started: datetime,
    still_open: Callable[[], bool],
    force: bool,
    sleep: Callable[[float], None],
) -> Result:
    archive_dir = settings.archive_dir
    archive_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    clean_leftovers(settings)
    existing = list_archives(archive_dir)
    local_now = started.astimezone(IST).replace(tzinfo=None)
    newest_complete = max((b.at for b in existing if not b.partial), default=None)
    if (
        not force
        and newest_complete is not None
        and 0 <= (local_now - newest_complete).total_seconds() < settings.min_gap_s
    ):
        log.info("an archive from %s is recent enough; nothing to do", newest_complete)
        return Result(SKIPPED, "fresh", kept=len(existing))

    found = discover(settings)
    if not found.sources:
        log.error("nothing to back up was found under %s", settings.app_dir)
        return Result(FAILED, failures=[Failure(OTHER)])

    # What lies outside the counts goes first: it is never wanted, and it is space given back.
    by_count = retention.plan(existing, daily=settings.keep_daily, monthly=settings.keep_monthly)
    remove(archive_dir, by_count.delete)
    # Then the cap, for the archive about to be written. If it cannot fit even with the oldest
    # dailies gone, nothing more is removed: they are worth more than a promise to fit.
    guess = int(found.total * COMPRESSED_GUESS)
    settled = retention.plan(
        list(by_count.keep),
        daily=settings.keep_daily,
        monthly=settings.keep_monthly,
        cap_bytes=settings.max_bytes,
        reserve=guess,
    )
    if settled.over_cap:
        return Result(
            FAILED,
            failures=[
                Failure(
                    CAP,
                    facts={
                        "cap_gb": _gb(settings.max_bytes),
                        "used_gb": _gb(by_count.kept_bytes + guess),
                    },
                )
            ],
            kept=len(by_count.keep),
            used_bytes=by_count.kept_bytes,
        )
    remove(archive_dir, settled.delete)
    need = guess + found.largest_database + settings.min_free_bytes
    free = free_bytes(archive_dir)
    if free < need:
        log.error("not enough free space: %d bytes free, %d needed", free, need)
        return Result(
            FAILED,
            failures=[Failure(SPACE, facts={"free_gb": _gb(free), "need_gb": _gb(need)})],
            kept=len(settled.keep),
            used_bytes=settled.kept_bytes,
        )
    return _write(settings, found, started, still_open, sleep)


def _write(
    settings: Settings,
    found: Found,
    started: datetime,
    still_open: Callable[[], bool],
    sleep: Callable[[float], None],
) -> Result:
    archive_dir = settings.archive_dir
    settings.work_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(dir=settings.work_dir, prefix="run-"))
    at = started.astimezone(IST).replace(tzinfo=None)
    target = archive_dir / (retention.make_name(at) + ".partial")
    try:
        try:
            built = write_archive(
                settings, found, work, target, started, still_open=still_open, sleep=sleep
            )
        except Deferred:
            log.info("the market opened; the run stops and leaves the backup for the next time")
            return Result(SKIPPED, "market")
        except WriteFailed as exc:
            log.error("the archive could not be written (%s)", exc)
            return Result(FAILED, failures=[Failure(SPACE if exc.space else WRITE)])
        if not verify(target, built):
            log.error("the archive did not read back whole; it is removed")
            return Result(FAILED, failures=[Failure(WRITE)])
        complete = not built.failures
        final = archive_dir / retention.make_name(at, partial=not complete)
        os.replace(target, final)
        _sync_dir(archive_dir)
        size = final.stat().st_size
        settled = retention.plan(
            list_archives(archive_dir),
            daily=settings.keep_daily,
            monthly=settings.keep_monthly,
            cap_bytes=settings.max_bytes,
        )
        remove(archive_dir, settled.delete)
        failures = list(built.failures)
        if settled.over_cap:
            facts = {"cap_gb": _gb(settings.max_bytes), "used_gb": _gb(settled.kept_bytes)}
            failures.append(Failure(CAP, facts=facts))
        log.info(
            "backup %s: %d files, %s GB, %d archives kept",
            final.name, len(built.members), _gb(size), len(settled.keep),
        )  # fmt: skip
        return Result(
            OK if complete else PARTIAL,
            archive=final.name,
            size=size,
            members=len(built.members),
            failures=failures,
            kept=len(settled.keep),
            used_bytes=settled.kept_bytes,
        )
    finally:
        target.unlink(missing_ok=True)
        shutil.rmtree(work, ignore_errors=True)


def _sync_dir(folder: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(folder, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


# ---- telling ----------------------------------------------------------------------------------


def _write_status(settings: Settings, started: datetime, result: Result) -> None:
    """status.json: how the last run that did something went (a skipped run changes nothing)."""
    if result.outcome == SKIPPED:
        return
    document = {
        "at": started.astimezone(IST).isoformat(timespec="seconds"),
        "outcome": result.outcome,
        "archive": result.archive,
        "size": result.size,
        "files": result.members,
        "kept": result.kept,
        "used_bytes": result.used_bytes,
        "offsite": result.offsite,
        "failures": [{"why": f.why, "names": list(f.names)} for f in result.failures],
    }
    target = settings.status_file
    try:
        temp = target.with_name(f".{target.name}.new")
        fd = os.open(
            temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_CLOEXEC", 0), 0o600
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp, target)
    except OSError:
        log.warning("status.json could not be written")


def _names_fact(names: tuple[str, ...]) -> str:
    """The files of a failure as a notice names them; "" when they do not all fit. A list cut
    short reads as the whole of it, and a name cut in half names a file that is not there: the
    archive's own record (``--verify``, ``status.json``) always has every one."""
    plain = ", ".join(name.rsplit("/", 1)[-1] for name in names)
    return plain if 0 < len(names) <= NOTICE_NAMES and len(plain) <= MAX_VALUE else ""


def _tell(settings: Settings, started: datetime, result: Result) -> None:
    """One event per failure, for sudarshan-alerts to word and send once."""
    day = started.astimezone(IST).strftime("%Y%m%d")
    run_id = f"backup-{started.astimezone(IST).strftime('%Y%m%d-%H%M%S')}"
    for failure in result.failures:
        facts = dict(failure.facts)
        if names := _names_fact(failure.names):
            facts["names"] = names
        recorded = record(
            settings.spool,
            kind="backup_failed",
            event_id=f"backup-failed-{day}-{failure.why}",
            at=started.timestamp(),
            run=run_id,
            src="backup",
            why=failure.why,
            **facts,
        )
        if not recorded:
            log.error("the notice for a failed backup (%s) could not be recorded", failure.why)


def _tell_settings(env: Mapping[str, str], message: str, now: datetime) -> None:
    """A setting that cannot be used still gets its notice, in the default state folder."""
    record(
        state_dir_from(env) / "events.jsonl",
        kind="backup_failed",
        event_id=f"backup-failed-{now.astimezone(IST).strftime('%Y%m%d')}-{SETTINGS}",
        at=now.timestamp(),
        run=f"backup-{now.astimezone(IST).strftime('%Y%m%d-%H%M%S')}",
        src="backup",
        why=SETTINGS,
    )
    log.error("%s", message)


# ---- the command ------------------------------------------------------------------------------


def check(settings: Settings, now: datetime) -> str:
    """What a run would do now, in words. Changes nothing."""
    calendar = TradingCalendar(settings.holidays_file, settings.extra_days_file)
    found = discover(settings)
    existing = list_archives(settings.archive_dir)
    settled = retention.plan(
        existing,
        daily=settings.keep_daily,
        monthly=settings.keep_monthly,
        cap_bytes=settings.max_bytes,
        reserve=int(found.total * COMPRESSED_GUESS),
    )
    lines = [
        f"hours: {'blocked (market)' if market_blocks(calendar, now) else 'open for a backup'}",
        f"sources: {len(found.sources)} files, {_gb(found.total)} GB",
        *([f"not taken: {', '.join(found.skipped)}"] if found.skipped else []),
        f"archives: {len(existing)} now, {len(settled.keep)} kept, {len(settled.delete)} to remove",
        f"cap: {_gb(settings.max_bytes)} GB, over: {'yes' if settled.over_cap else 'no'}",
        f"offsite: {'set' if settings.offsite_cmd else 'not set'}",
    ]
    return "\n".join(lines) + "\n"


def _verify_command(path: Path) -> int:
    """``--verify``: 0 sound, 1 something wrong with the archive, 2 not an archive at all."""
    problems, manifest = check_archive(path)
    out = [f"archive: {path.name}"]
    if not manifest:
        out += [f"problem: {text}" for text in problems]
        sys.stdout.write("\n".join(out) + "\n")
        return 2
    not_saved = [f"{n['name']} ({n['why']})" for n in manifest.get("not_saved", [])]
    out += [
        f"made: {manifest.get('created', '?')}",
        f"release: {manifest.get('release') or 'not recorded'}",
        f"files: {len(manifest['members'])}",
        "complete: " + ("no, not saved: " + ", ".join(not_saved) if not_saved else "yes"),
        "check: " + ("ok" if not problems else f"{len(problems)} problem(s)"),
    ]
    out += [f"problem: {text}" for text in problems]
    sys.stdout.write("\n".join(out) + "\n")
    return 1 if problems else 0


class _Terminated(SystemExit):
    pass


def _on_term(_signum, _frame) -> None:
    raise _Terminated(143)  # lets the cleanup in ``finally`` run


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sudarshan.ops.backup")
    parser.add_argument(
        "--check", action="store_true", help="say what a run would do, change nothing"
    )
    parser.add_argument(
        "--force", action="store_true", help="make an archive even if one is recent"
    )
    parser.add_argument(
        "--verify", metavar="ARCHIVE", help="read an archive back against its own record"
    )
    args = parser.parse_args(argv)
    if args.verify:
        return _verify_command(Path(args.verify))
    env = os.environ if env is None else env
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, force=True)
    with contextlib.suppress(OSError):
        os.nice(19)
    now = datetime.now(IST)
    try:
        settings = Settings.from_env(env)
    except ConfigError as exc:
        _tell_settings(env, f"the backup settings cannot be used: {exc}", now)
        return 2
    if args.check:
        sys.stdout.write(check(settings, now))
        return 0
    signal.signal(signal.SIGTERM, _on_term)
    try:
        return run(settings, force=args.force).exit_code
    except _Terminated:
        log.warning("stopped by the system; the next scheduled run will do it")
        return 1
    except Exception:
        log.exception("the nightly backup failed unexpectedly")
        record(
            settings.spool,
            kind="backup_failed",
            event_id=f"backup-failed-{now.strftime('%Y%m%d')}-{OTHER}",
            at=now.timestamp(),
            run=f"backup-{now.strftime('%Y%m%d-%H%M%S')}",
            src="backup",
            why=OTHER,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
