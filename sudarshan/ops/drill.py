"""The restore drill (M19.8, P8 7.3): proof that a nightly archive can be read back whole.

    python -m sudarshan.ops.drill            the newest complete archive (the monthly timer)
    python -m sudarshan.ops.drill ARCHIVE    this file (the owner's quarterly drill, with a copy
                                             downloaded from the offsite bucket and decrypted)

A backup that has never been restored is not a backup. The drill restores nothing over live
files and stops no service: it unpacks the archive into a private scratch folder inside the
backup's own state folder, looks, and removes the folder again.

  1. The archive reads back against the record it carries (``backup --verify``), and is complete.
  2. It is unpacked into ``<state>/drill/run-*`` (mode 700): regular files and folders only,
     under their own names, never outside the folder.
  3. Every database opens read-only, passes SQLite's quick check, holds at least one table, and
     a store this release migrates is at a schema version this release knows.
  4. ``openalgo.db`` holds exactly one admin user, an API key and a broker login.
  5. The archive's ``.env`` has the keys the app cannot start without (never printed).
  6. The vault opens with the key derived from that same ``.env``: one sealed record of each
     key the vault names is decrypted (never printed). This is the step that proves the pair
     restores together. A vault that holds no sealed record proves nothing of the kind: the
     drill then says "the vault holds nothing sealed yet", never that it opened (review L3).
  7. The scratch folder is removed and seen gone.

The result is ``drill.json`` in the backup's state folder (the watch reads it for the System
page) and one event, ``drill_passed`` or ``drill_failed`` with the first failing item, in the
backup's notice file, which ``sudarshan-alerts`` words for the owner.

When it steps aside: the timer's run does nothing on a trading day (the calendar's word, a
listed special session included), and no run starts in the hours the nightly backup keeps out
of. It proves an archive can be read back into a working set of files; it does not prove that a
new server boots (README, "If the server is lost").

Exit status: 0 passed or stepped aside, 1 failed, 2 the file named is not there.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import logging
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from sudarshan.autologin.schedule import IST, TradingCalendar

from . import backup
from .events import record
from .release import installed_release

log = logging.getLogger("sudarshan.drill")

RESULT_FILE = "drill.json"
SCRATCH = "drill"
PASSED, FAILED, SKIPPED = "passed", "failed", "skipped"
GIB = 1 << 30
CHUNK = 1 << 20
ENV_MEMBER = "app/openalgo.env"
OPENALGO_DB = "db/openalgo.db"
VAULT_DB = "db/sudarshan-vault/sudarshan_vault.db"
#: Without these the app does not start, or cannot open what it stored.
ENV_KEYS = ("APP_KEY", "API_KEY_PEPPER", "DATABASE_URL", "BROKER_API_KEY", "BROKER_API_SECRET")
#: The ring's stores that carry a schema version, and where this release keeps its newest.
VERSIONED = {
    "journal.db": "sudarshan.journal.migrations",
    "portfolio.db": "sudarshan.portfolio.migrations",
    "screener.db": "sudarshan.screener.migrations",
    "market.db": "sudarshan.alerts.market.migrations",
    "family-copy.db": "sudarshan.family.migrations",
}
#: What OpenAlgo's own database must hold for the owner to sign in and trade after a restore.
OPENALGO_ROWS = (
    ("admin", "SELECT COUNT(*) FROM users WHERE is_admin = 1", lambda n: n == 1),
    ("api_key", "SELECT COUNT(*) FROM api_keys", lambda n: n >= 1),
    ("broker", "SELECT COUNT(*) FROM auth", lambda n: n >= 1),
)
#: The first failing item, in the owner's words (``alerts/notices.py`` says the same).
WORDS = {
    "none": "There is no complete backup on the server to test yet.",
    "archive": "The archive does not read back as it was saved.",
    "partial": "The archive is incomplete: something could not be saved that night.",
    "space": "There is not enough free disk space to unpack the archive.",
    "unpack": "The archive could not be unpacked.",
    "database": "A database in the archive is damaged or empty.",
    "version": "A database in the archive is newer than this release understands.",
    "openalgo": "OpenAlgo's database in the archive has no owner account, API key or Upstox login.",
    "env": "OpenAlgo's settings file in the archive is missing, or misses a key the app needs.",
    "vault": "The vault of sealed sign-in details is not in the archive.",
    "vault_key": "The vault in the archive does not open with the keys in the same archive.",
    "cleanup": "The test's scratch folder could not be removed.",
    "other": "The test stopped unexpectedly.",
}


VAULT_OPENED, VAULT_EMPTY = "opened", "empty"
#: What a passed drill says of the vault: only what it tried.
VAULT_SAID = {
    VAULT_OPENED: "the vault opens with the archive's own keys",
    VAULT_EMPTY: "the vault holds nothing sealed yet, so no record was opened",
    "": "the vault was not checked",
}


@dataclass(frozen=True)
class Outcome:
    outcome: str  # passed, failed, skipped
    item: str = ""  # the first failing item (a key of WORDS), or why it stepped aside
    name: str = ""  # the database or the key the item is about: a name, never a value
    archive: str = ""
    databases: int = 0
    #: What the vault step did on a pass: ``opened`` (a sealed record of every key was
    #: decrypted with the archive's own keys) or ``empty`` (the vault holds no sealed record,
    #: so nothing was opened). "" when the drill did not get that far.
    vault: str = ""

    @property
    def exit_code(self) -> int:
        return 1 if self.outcome == FAILED else 0


class _Fail(Exception):
    def __init__(self, item: str, name: str = ""):
        super().__init__(item)
        self.item, self.name = item, name


# ---- the steps --------------------------------------------------------------------------------


def newest_complete(folder: Path) -> Path | None:
    complete = [b for b in backup.list_archives(folder) if not b.partial]
    return folder / max(complete, key=lambda b: (b.at, b.name)).name if complete else None


def _safe(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts and "\\" not in name


def unpack(archive: Path, scratch: Path) -> None:
    """Every regular file of the archive under ``scratch``, each private (600, folders 700).
    A member that is not a file or a folder, or whose name leaves the folder, fails the drill."""
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar:
                if member.isdir():
                    continue
                if not member.isreg() or not _safe(member.name):
                    raise _Fail("unpack", member.name[:60])
                target = scratch / member.name
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source = tar.extractfile(member)
                if source is None:
                    raise _Fail("unpack", member.name[:60])
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as out:
                    shutil.copyfileobj(source, out, CHUNK)
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise _Fail("unpack") from exc


def _open_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{quote(str(path))}?mode=ro&immutable=1", uri=True)


def _known_version(file_name: str) -> int | None:
    module = VERSIONED.get(file_name)
    return int(importlib.import_module(module).LATEST) if module else None


def check_database(path: Path, arc: str) -> None:
    """Opens read-only, passes the quick check, has a table, is of a version this release knows."""
    try:
        with contextlib.closing(_open_ro(path)) as db:
            sound = [row[0] for row in db.execute("PRAGMA quick_check")] == ["ok"]
            tables = db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
    except sqlite3.Error as exc:
        raise _Fail("database", arc) from exc
    if not sound or not tables or tables[0] < 1:
        raise _Fail("database", arc)
    known = _known_version(PurePosixPath(arc).name)
    if known is not None and version > known:
        raise _Fail("version", arc)


def check_openalgo(path: Path) -> None:
    """Exactly one admin user, an API key row and a broker login row."""
    try:
        with contextlib.closing(_open_ro(path)) as db:
            for name, question, enough in OPENALGO_ROWS:
                if not enough(int(db.execute(question).fetchone()[0])):
                    raise _Fail("openalgo", name)
    except (sqlite3.Error, OSError) as exc:
        raise _Fail("openalgo") from exc


def check_env(path: Path) -> Mapping[str, str]:
    """The archive's .env has every key the app needs. Returns its values, for the vault step
    only: nothing of it is logged, recorded or printed."""
    from sudarshan.alerts.config import ConfigError, read_openalgo_env

    try:
        values = read_openalgo_env(path)
    except ConfigError as exc:
        raise _Fail("env") from exc
    for key in ENV_KEYS:
        if not values.get(key):
            raise _Fail("env", key)
    return values


def check_vault(path: Path, env: Mapping[str, str]) -> bool:
    """One sealed record of each key the vault names opens with the key derived from the
    archive's own .env. True when records were opened, False when the vault holds none (nothing
    was ever sealed, so nothing was tried)."""
    from cryptography.fernet import InvalidToken

    from sudarshan.vault.keys import (
        VAULT_INFO,
        VaultKeyError,
        fernet_for,
        key_fingerprint,
        pepper_from,
    )

    if not path.is_file():
        raise _Fail("vault")
    try:
        with contextlib.closing(_open_ro(path)) as db:
            rows = db.execute(
                "SELECT ciphertext, key_id FROM secrets GROUP BY key_id ORDER BY key_id"
            ).fetchall()
    except sqlite3.Error as exc:
        raise _Fail("vault") from exc
    if not rows:
        return False
    try:
        pepper = pepper_from(env)
        mine, fernet = key_fingerprint(pepper, VAULT_INFO), fernet_for(pepper, VAULT_INFO)
        for ciphertext, key_id in rows:
            # A record sealed with any other key would not open on the restored server.
            if key_id != mine:
                raise _Fail("vault_key")
            fernet.decrypt(str(ciphertext).encode("ascii"))
    except (VaultKeyError, InvalidToken, ValueError) as exc:
        raise _Fail("vault_key") from exc
    return True


# ---- one drill --------------------------------------------------------------------------------


def drill(archive: Path, state_dir: Path, free_bytes: Callable[[Path], int]) -> Outcome:
    """Steps 1 to 7 on one archive. Never raises; the scratch folder is gone on every path."""
    problems, manifest = backup.check_archive(archive)
    if problems or not manifest:
        return Outcome(FAILED, "archive", archive=archive.name)
    if manifest.get("partial"):
        return Outcome(FAILED, "partial", archive=archive.name)
    members = manifest["members"]
    need = sum(int(member["size"]) for member in members) + GIB
    root = state_dir / SCRATCH
    scratch = None
    found = Outcome(FAILED, "other", archive=archive.name)
    try:
        shutil.rmtree(root, ignore_errors=True)  # what a run that was killed left behind
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if free_bytes(root) < need:
            raise _Fail("space")
        scratch = Path(tempfile.mkdtemp(dir=root, prefix="run-"))
        unpack(archive, scratch)
        databases = [m["name"] for m in members if m.get("kind") == "sqlite"]
        for arc in databases:
            check_database(scratch / arc, arc)
        if OPENALGO_DB not in databases:
            raise _Fail("openalgo")
        check_openalgo(scratch / OPENALGO_DB)
        if not (scratch / ENV_MEMBER).is_file():
            raise _Fail("env")
        env = check_env(scratch / ENV_MEMBER)
        opened = check_vault(scratch / VAULT_DB, env)
        found = Outcome(PASSED, archive=archive.name, databases=len(databases),
                        vault=VAULT_OPENED if opened else VAULT_EMPTY)  # fmt: skip
    except _Fail as failed:
        found = Outcome(FAILED, failed.item, failed.name, archive.name)
    except Exception:
        log.exception("the restore drill stopped unexpectedly")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    if root.exists():
        return Outcome(FAILED, "cleanup", archive=archive.name)
    return found


def write_result(state_dir: Path, at: datetime, found: Outcome, release: str | None) -> None:
    document = {
        "at": at.astimezone(IST).isoformat(timespec="seconds"),
        "outcome": found.outcome,
        "item": found.item,
        "name": found.name,
        "words": WORDS.get(found.item, "") if found.outcome == FAILED else "",
        "archive": found.archive,
        "databases": found.databases,
        "vault": found.vault,
        "release": release,
    }
    target = state_dir / RESULT_FILE
    temp = target.with_name(f".{target.name}.new")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    os.replace(temp, target)


def tell(settings: backup.Settings, at: datetime, found: Outcome) -> bool:
    """One event for the owner's Telegram, in the backup's notice file."""
    stamp = at.astimezone(IST).strftime("%Y%m%d-%H%M%S")
    return record(
        settings.spool,
        kind="drill_passed" if found.outcome == PASSED else "drill_failed",
        event_id=f"drill-{found.outcome}-{stamp}",
        at=at.timestamp(),
        run=f"drill-{stamp}",
        src="backup",
        item=found.item,
        name=PurePosixPath(found.name).name,
        archive=found.archive,
        databases=str(found.databases),
        vault=found.vault,
    )


def run(
    settings: backup.Settings,
    archive: Path | None = None,
    *,
    clock: Callable[[], datetime] | None = None,
    free_bytes: Callable[[Path], int] = backup.free_bytes,
) -> Outcome:
    """One drill: the newest complete archive, or ``archive``. The result is written and told
    unless the run stepped aside (the market, a trading day for the timer's run, another run)."""
    at = (clock or (lambda: datetime.now(IST)))()
    calendar = TradingCalendar(settings.holidays_file, settings.extra_days_file)
    if backup.market_blocks(calendar, at):
        return Outcome(SKIPPED, "market")
    if archive is None and calendar.is_trading_day(at.astimezone(IST).date()):
        return Outcome(SKIPPED, "trading_day")
    settings.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with backup.single_run(settings.state_dir / "drill.lock") as alone:
        if not alone:
            return Outcome(SKIPPED, "busy")
        target = archive or newest_complete(settings.archive_dir)
        if target is None:
            found = Outcome(FAILED, "none")
        else:
            found = drill(target, settings.state_dir, free_bytes)
        release = installed_release(settings.deployed_tag_file)
        try:
            write_result(settings.state_dir, at, found, release)
        except OSError:
            log.warning("drill.json could not be written")
        tell(settings, at, found)
        return found


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sudarshan.ops.drill")
    parser.add_argument("archive", nargs="?", help="an archive file; the newest one when left out")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format=backup.LOG_FORMAT, force=True)
    with contextlib.suppress(OSError):
        os.nice(19)
    archive = Path(args.archive) if args.archive else None
    if archive is not None and not archive.is_file():
        sys.stdout.write(f"{archive} is not a file\n")
        return 2
    try:
        settings = backup.Settings.from_env(os.environ if env is None else env)
    except backup.ConfigError as exc:
        sys.stdout.write(f"the backup settings cannot be used: {exc}\n")
        return 2
    found = run(settings, archive)
    if found.outcome == SKIPPED:
        why = {
            "market": "not started in or just before market hours",
            "trading_day": "today is a trading day: the monthly test waits for a day without one",
            "busy": "another restore test is running",
        }[found.item]
        sys.stdout.write(f"restore drill: nothing done ({why})\n")
    elif found.outcome == PASSED:
        sys.stdout.write(
            f"restore drill: passed ({found.archive}, {found.databases} databases, "
            f"{VAULT_SAID[found.vault]})\n"
        )
    else:
        about = f" ({PurePosixPath(found.name).name})" if found.name else ""
        sys.stdout.write(f"restore drill: FAILED: {WORDS.get(found.item, found.item)}{about}\n")
    return found.exit_code


if __name__ == "__main__":
    sys.exit(main())
