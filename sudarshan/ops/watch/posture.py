"""The watch's slower readings (M19.5, P8 7.1): the server's posture, read once an hour, and
what the System page shows beside the health checks: AI connections and Telegram links counted,
the nightly backup's age, the restore drill's last result, Historify's size.

Like ``readers.py``: each reader reads one thing and returns it plain, or None when it could not
be read; none raises, none changes anything, none starts a process. What is read is SUDARSHAN's
own or OpenAlgo's (the app's .env for four settings, never a key; the modes of the settings
folder and the vault; three counts from OpenAlgo's own tables, read-only; the backup job's
folder), and three things the kernel and the operating system publish for every program: the
listening sockets (only the ring's own ports are looked for), the "restart needed" flag, and the
count of updates waiting.
"""

from __future__ import annotations

import calendar
import json
import os
import re
import stat
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from sudarshan.assistant import locks
from sudarshan.ops import retention
from sudarshan.ops.files import read_regular

from .checks import SERVICES

POSTURE_EVERY_S = 3600.0
ETC_DIR = Path("/etc/sudarshan")
BACKUP_DIR = Path("/var/lib/sudarshan-backup")
DRILL_FILE = "drill.json"
#: The ring's ports: each service's listener, and the feed's tick bus.
RING_PORTS = frozenset(SERVICES.values()) | {5581}
LISTENERS = (Path("/proc/net/tcp"), Path("/proc/net/tcp6"))
REBOOT_FLAG = Path("/run/reboot-required")
UPDATES_FILE = Path("/var/lib/update-notifier/updates-available")
_LOOPBACK_V4 = "0100007F"  # 127.0.0.1 as the kernel prints it
_LOOPBACK_V6 = ("00000000000000000000000001000000", "0000000000000000FFFF00000100007F")
_UPDATES = re.compile(
    r"(\d+) (?:updates? can be applied|of these updates? (?:is|are) (?:a )?standard security)"
)
_MAX_SMALL = 1 << 16
_MAX_TABLE = 4 << 20
_TRUE = ("1", "true", "yes", "on", "t")
_IST_AHEAD_S = 19800.0


def mcp(values: Mapping[str, str]) -> dict:
    """The assistant's switch and locks as OpenAlgo's .env sets them (``assistant/locks.py``)."""
    return {
        "switched_on": locks.switched_on(values),
        "locked": locks.locked(values),
        "approval": locks.approval_required(values),
    }


def debug(values: Mapping[str, str]) -> bool:
    return str(values.get("FLASK_DEBUG", "False")).strip().lower() in _TRUE


def _mode(path: Path) -> int | None:
    try:
        return stat.S_IMODE(os.lstat(path).st_mode)
    except OSError:
        return None


def loose(app_dir: Path, etc_dir: Path = ETC_DIR) -> list[str] | None:
    """Which of the three private places another account could read: "settings" (the settings
    folder is open to others), "vault" (its folder or a file in it is open to the group or to
    others), "env" (OpenAlgo's .env is open to others). None when nothing could be looked at."""
    found, looked = set(), False
    vault = app_dir / "db" / "sudarshan-vault"
    for name, path, mask in (
        ("settings", etc_dir, 0o007),
        ("vault", vault, 0o077),
        ("env", app_dir / ".env", 0o007),
    ):
        mode = _mode(path)
        if mode is None:
            continue
        looked = True
        if mode & mask:
            found.add(name)
    try:
        inside = [Path(entry.path) for entry in os.scandir(vault)]
    except OSError:
        inside = []
    if any((_mode(path) or 0) & 0o077 for path in inside):
        found.add("vault")
    return sorted(found) if looked else None


def open_ports(tables=LISTENERS) -> list[str] | None:
    """The ring's ports that listen on an address other than this machine's own. The kernel's
    table lists every socket; only a line for one of ``RING_PORTS`` is looked at."""
    wide, read = set(), False
    for table in tables:
        raw = read_regular(table, _MAX_TABLE)
        if raw is None:
            continue
        read = True
        for line in raw.decode("ascii", errors="replace").splitlines()[1:]:
            words = line.split()
            if len(words) < 4 or words[3] != "0A" or ":" not in words[1]:  # 0A: listening
                continue
            address, _, port_hex = words[1].rpartition(":")
            try:
                port = int(port_hex, 16)
            except ValueError:
                continue
            if port in RING_PORTS and address.upper() not in (_LOOPBACK_V4, *_LOOPBACK_V6):
                wide.add(str(port))
    return sorted(wide) if read else None


def updates(path: Path = UPDATES_FILE) -> dict | None:
    """Updates waiting, as the operating system's own notice counts them; None where it keeps no
    such notice."""
    raw = read_regular(path, _MAX_SMALL)
    if raw is None:
        return None
    numbers = [int(n) for n in _UPDATES.findall(raw.decode("utf-8", errors="replace"))]
    if not numbers:
        return {"all": 0, "security": 0}
    return {"all": numbers[0], "security": numbers[1] if len(numbers) > 1 else 0}


def read(now: float, app_dir: Path, env_file: Path, known: Mapping | None = None) -> dict | None:
    """The posture, read afresh when ``known`` (the last reading, with its ``at``) is over an
    hour old. Never raises."""
    if isinstance(known, Mapping) and isinstance(known.get("at"), int | float):
        if 0 <= now - known["at"] < POSTURE_EVERY_S:
            return dict(known)
    from sudarshan.alerts.config import read_openalgo_env

    try:
        values = read_openalgo_env(env_file)
    except Exception:
        values = None
    return {
        "at": now,
        "mcp": mcp(values) if values else None,
        "debug": debug(values) if values else None,
        "loose": loose(app_dir),
        "open_ports": open_ports(),
        "reboot": os.path.lexists(REBOOT_FLAG),
        "updates": updates(),
    }


def counts(app_dir: Path, env_file: Path) -> dict | None:
    """Approved AI clients, live AI sign-ins and linked Telegram accounts, from OpenAlgo's own
    tables in one read-only look (opened as ``readers.master_date`` opens it). A table that was
    never made (remote MCP never switched on, the bot never set up) counts as none."""
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from database.engine_factory import create_db_engine
    from sudarshan.alerts.config import read_openalgo_env, sqlite_path
    from sudarshan.alerts.owner import database_urls

    values = read_openalgo_env(env_file)
    db_path = sqlite_path(values.get("DATABASE_URL") or "sqlite:///db/openalgo.db", app_dir)
    if db_path is None or not db_path.is_file():
        return None
    questions = {
        "clients": "SELECT COUNT(*) FROM oauth_clients WHERE approved = 1 AND revoked_at IS NULL",
        "families": (
            "SELECT COUNT(DISTINCT family_id) FROM oauth_refresh_tokens "
            "WHERE revoked_at IS NULL AND expires_at > CURRENT_TIMESTAMP"
        ),
        "links": "SELECT COUNT(*) FROM telegram_users WHERE is_active = 1",
    }
    for url in database_urls(db_path):
        engine = create_db_engine(url)
        try:
            found = {}
            with engine.connect() as connection:
                for name, question in questions.items():
                    try:
                        found[name] = int(connection.execute(text(question)).scalar() or 0)
                    except SQLAlchemyError as exc:
                        if "no such table" not in str(exc).lower():
                            raise
                        found[name] = 0
            return found
        except SQLAlchemyError:
            continue
        finally:
            engine.dispose()
    return None


def backup(now: float, folder: Path = BACKUP_DIR) -> dict | None:
    """The nightly backup as its own folder shows it: the newest complete archive's age and size,
    the newest financial-year archive, and the last run's word on the offsite copy."""
    try:
        entries = [
            (entry.name, entry.stat(follow_symlinks=False))
            for entry in os.scandir(folder / "archive")
        ]
    except FileNotFoundError:
        entries = []
    except OSError:
        return None
    archives = [
        found
        for name, info in entries
        if stat.S_ISREG(info.st_mode) and (found := retention.parse(name, info.st_size))
    ]
    complete = sorted((a for a in archives if not a.partial), key=lambda a: a.at)
    reading: dict = {"age_h": None}
    if complete:
        newest = complete[-1]
        made = calendar.timegm(newest.at.timetuple()) - _IST_AHEAD_S  # the name is India's time
        reading.update(age_h=(now - made) / 3600.0, size_mb=newest.size >> 20)
        yearly = retention.yearlies(archives)
        if yearly:
            label, archive = max(yearly.items())
            reading.update(fy=label, fy_mb=archive.size >> 20)
    status = _json(folder / "status.json")
    if isinstance(status.get("offsite"), str):
        reading["offsite"] = status["offsite"]
    return reading


def _json(path: Path) -> dict:
    raw = read_regular(path, _MAX_SMALL)
    try:
        found = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


def drill(folder: Path = BACKUP_DIR) -> dict | None:
    """The restore drill's last result (``ops/drill.py`` writes it): {} when none was ever done,
    None when the backup's folder cannot be looked into at all."""
    if not folder.is_dir():
        return {}
    if not os.access(folder, os.R_OK | os.X_OK):
        return None
    found = _json(folder / DRILL_FILE)
    try:
        at = datetime.fromisoformat(str(found["at"])).timestamp()
    except (KeyError, ValueError):
        return {}
    ok = found.get("outcome") == "passed"
    return {"at": at, "ok": ok, "item": str(found.get("item") or "")[:40]}


def historify_gb(app_dir: Path) -> float | None:
    try:
        return os.stat(app_dir / "db" / "historify.duckdb").st_size / 1e9
    except FileNotFoundError:
        return 0.0
    except OSError:
        return None
