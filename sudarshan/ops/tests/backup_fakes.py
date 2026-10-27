"""A pretend server for the nightly backup tests: real folders, real SQLite files, real locks.

Nothing is faked about the data. The app's ``db/`` folder, ``/etc/sudarshan``, the ring's folders in
``/var/lib`` and the deploy tool's lock all live under one temporary folder; ``Server.env()`` points
the job at them the way systemd's environment points the real one at the real paths.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import io
import json
import os
import sqlite3
import tarfile
from datetime import datetime
from pathlib import Path

from sudarshan.autologin.schedule import IST
from sudarshan.ops import backup
from sudarshan.ops.backup import Settings
from sudarshan.ops.events import Event, read_events

#: A Tuesday evening, after the close: the time the timer fires.
NIGHT = datetime(2026, 10, 27, 16, 31, 2, tzinfo=IST)


def ist(year: int, month: int, day: int, hour: int = 16, minute: int = 30, second: int = 0):
    return datetime(year, month, day, hour, minute, second, tzinfo=IST)


def make_database(
    path: Path, rows: int = 3, *, wal: bool = False, keep_open: bool = False, blob: int = 0
):
    """A SQLite file with ``rows`` rows. With ``keep_open`` the writer is returned still connected
    (its rows in the write-ahead log, as in a running app); otherwise it is closed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS t (k INTEGER PRIMARY KEY, v TEXT, b BLOB)")
    con.executemany(
        "INSERT INTO t (v, b) VALUES (?, ?)",
        [(f"{path.stem}-{n}", os.urandom(blob) if blob else None) for n in range(rows)],
    )
    con.commit()
    if keep_open:
        return con
    con.close()
    return None


def damage(path: Path) -> None:
    """Overwrite the first byte of the second page (a b-tree's type byte): SQLite's own check
    finds the file unsound, yet the file can still be copied page for page."""
    with open(path, "r+b") as handle:
        handle.seek(4096)
        handle.write(b"\xff")


def count_rows(content: bytes, scratch: Path) -> int:
    """The rows of table ``t`` in a database held as bytes."""
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_bytes(content)
    con = sqlite3.connect(f"file:{scratch}?mode=ro&immutable=1", uri=True)
    try:
        return con.execute("SELECT count(*) FROM t").fetchone()[0]
    finally:
        con.close()


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class Archive:
    """What is inside one archive: members by name, their bytes, the manifest."""

    def __init__(self, path: Path):
        self.path = path
        self.info: dict[str, tarfile.TarInfo] = {}
        self.data: dict[str, bytes] = {}
        with tarfile.open(path, "r:gz") as tar:
            for member in tar.getmembers():
                self.info[member.name] = member
                if member.isreg():
                    self.data[member.name] = tar.extractfile(member).read()

    @property
    def files(self) -> set[str]:
        return set(self.data)

    @property
    def folders(self) -> set[str]:
        return {name for name, member in self.info.items() if member.isdir()}

    @property
    def manifest(self) -> dict:
        return json.loads(self.data[backup.MANIFEST])


class Server:
    """A pretend server under ``root``."""

    def __init__(self, root: Path):
        self.root = root
        self.app = root / "openalgo"
        self.etc = root / "etc-sudarshan"
        self.var = root / "var-lib"
        self.state = self.var / "sudarshan-backup"
        self.lock = root / "deploy.lock"
        self.holidays = self.etc / "nse_holidays.txt"
        self.extra = self.etc / "nse_extra_trading_days.txt"
        self.tag = root / "deployed_tag"
        for folder in (self.app / "db", self.etc, self.var):
            folder.mkdir(parents=True)
        self.lock.write_text("")
        self.holidays.write_text("2026-11-09\n")
        self.extra.write_text("")
        self.tag.write_text("v0.3.4\n")
        self.writers: list[sqlite3.Connection] = []

    # -- settings -----------------------------------------------------------------------------

    def env(self, **more: str) -> dict[str, str]:
        env = {
            "SUDARSHAN_OPENALGO_DIR": str(self.app),
            "SUDARSHAN_ETC_DIR": str(self.etc),
            "SUDARSHAN_RING_VAR": str(self.var),
            "SUDARSHAN_BACKUP_STATE_DIR": str(self.state),
            "SUDARSHAN_DEPLOY_LOCK": str(self.lock),
            "SUDARSHAN_NSE_HOLIDAYS_FILE": str(self.holidays),
            "SUDARSHAN_NSE_EXTRA_DAYS_FILE": str(self.extra),
            "SUDARSHAN_DEPLOYED_TAG_FILE": str(self.tag),
            "SUDARSHAN_BACKUP_DEPLOY_WAIT_MIN": "0",
        }
        env.update(more)
        return env

    def settings(self, env: dict[str, str] | None = None, **fields) -> Settings:
        """Settings from this server's environment, with any fields replaced."""
        return dataclasses.replace(Settings.from_env(env or self.env()), **fields)

    # -- what is on it ------------------------------------------------------------------------

    def database(self, relative: str, rows: int = 3, **kwargs):
        con = make_database(self.app / relative, rows, **kwargs)
        if con is not None:
            self.writers.append(con)
        return con

    def plain(self, path: Path, text: str, mode: int = 0o640) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(mode)
        return path

    def populate(self) -> dict[str, bytes]:
        """A server with every kind of source (and a few things that are not sources). Returns
        the bytes of the plain files by the name they have in the archive."""
        db = self.app / "db"
        self.database("db/openalgo.db", rows=40, wal=True, keep_open=True)
        self.database("db/sandbox.db", rows=5)
        self.database("db/logs.db", rows=7)
        (db / "historify.duckdb").write_bytes(b"duck" * 100)  # market history: not taken
        (db / "notes.txt").write_text("not a database")
        vault = db / "sudarshan-vault"
        vault.mkdir()
        self.database("db/sudarshan-vault/sudarshan_vault.db", rows=2)
        (vault / "sudarshan_vault.db").chmod(0o600)
        self.plain(vault / "key.salt", "salt-bytes", 0o600)
        # A running app leaves a database's side files beside it; they are never packed apart
        # from the database they belong to.
        self.plain(vault / "sudarshan_vault.db-wal", "side file", 0o600)
        self.plain(vault / "sudarshan_vault.db-shm", "side file", 0o600)
        vault.chmod(0o700)
        books = db / "sudarshan-books"
        books.mkdir()
        books.chmod(0o755)
        self.database("db/sudarshan-books/books.db", rows=4)
        (db / "not-ours").mkdir()
        make_database(db / "not-ours" / "x.db")
        plain = {
            "app/openalgo.env": self.plain(self.app / ".env", "APP_KEY=not-a-real-key\n", 0o600),
            "etc/sudarshan/feed.env": self.plain(self.etc / "feed.env", "FEED=1\n"),
            "etc/sudarshan/nse_holidays.txt": self.holidays,
            "etc/sudarshan/nse_extra_trading_days.txt": self.extra,
            "etc/sudarshan/sub/inner.txt": self.plain(self.etc / "sub" / "inner.txt", "inner\n"),
            "db/sudarshan-vault/key.salt": vault / "key.salt",
        }
        for service, names in {
            "sudarshan-alerts": ("alerts.db", "market.db"),
            "sudarshan-feed": ("feed.db",),
        }.items():
            for name in names:
                make_database(self.var / service / name, rows=6)
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)  # systemd's StateDirectory
        self.state.chmod(0o700)
        make_database(self.state / "own.db")  # the job's own folder is never a source
        make_database(self.var / "other-system" / "x.db")  # not ours
        (self.var / "sudarshan-link").symlink_to(
            self.var / "other-system"
        )  # links are not followed
        return {name: path.read_bytes() for name, path in plain.items()}

    def close(self) -> None:
        for con in self.writers:
            try:
                con.close()
            except sqlite3.Error:
                pass
        self.writers.clear()

    # -- what it left -------------------------------------------------------------------------

    @property
    def archive_dir(self) -> Path:
        return self.state / "archive"

    def archives(self) -> list[str]:
        if not self.archive_dir.is_dir():
            return []
        return sorted(p.name for p in self.archive_dir.iterdir())

    def events(self) -> list[Event]:
        return read_events(self.state / "events.jsonl")

    def status(self) -> dict:
        return json.loads((self.state / "status.json").read_text())

    def seed(self, when: datetime, size: int = 100, *, partial: bool = False) -> Path:
        """A pretend earlier archive: a file with a real name and ``size`` bytes."""
        from sudarshan.ops import retention

        self.archive_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self.archive_dir / retention.make_name(
            when.astimezone(IST).replace(tzinfo=None), partial=partial
        )
        path.write_bytes(b"x" * size)
        return path


def open_files() -> list[str]:
    """What this process holds open, by target (the listing's own descriptor is there both times)."""
    found = []
    for number in os.listdir("/proc/self/fd"):
        with contextlib.suppress(OSError):
            found.append(os.readlink(f"/proc/self/fd/{number}"))
    return sorted(found)


def no_sleep(_seconds: float) -> None:
    return None


class Sleeps:
    """A sleep that only writes down how long it was asked to."""

    def __init__(self):
        self.asked: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.asked.append(seconds)


def go(server: Server, when: datetime = NIGHT, *, settings: Settings | None = None, **kwargs):
    """One run of the job at ``when`` that never really waits."""
    kwargs.setdefault("sleep", no_sleep)
    return backup.run(settings or server.settings(), clock=lambda: when, **kwargs)


def tar_bytes(members: dict[str, bytes]) -> bytes:
    """A gzip tar holding the given regular files (to build archives that are wrong)."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name, content in members.items():
            item = tarfile.TarInfo(name)
            item.size = len(content)
            tar.addfile(item, io.BytesIO(content))
    return out.getvalue()
