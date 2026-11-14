"""The watch's readings: what one run reads from the server (M19.5, P8 7.1).

Each reader reads one thing and returns it plain, or None when it could not be read; none of
them raises and none of them changes anything. Everything read is SUDARSHAN's own or OpenAlgo's:
the ring's ``/health`` pages and OpenAlgo on 127.0.0.1, SUDARSHAN's unit links, the app's disk,
the kernel's memory figure, the certificate nginx serves on this machine, and the instrument
list's date from OpenAlgo's own status table (read-only). Nothing of another system on the
server is named here, and nothing outside the server is called.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from sudarshan.ops.files import read_regular

from .checks import SERVICES

IST = timezone(timedelta(hours=5, minutes=30), "IST")  # India keeps no daylight saving
TIMEOUT_S = 2.0
MAX_ANSWER = 1 << 20
OPENALGO_URL = "http://127.0.0.1:5000/"
#: Where systemd links a unit that is enabled (each SUDARSHAN service is WantedBy multi-user).
WANTS_DIR = Path("/etc/systemd/system/multi-user.target.wants")
MEMINFO = Path("/proc/meminfo")
CERT_ADDRESS = ("127.0.0.1", 443)
#: Loopback only, whatever proxy the environment names.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_get(url: str, timeout: float = TIMEOUT_S) -> tuple[int, bytes] | None:
    """One GET on this machine: (status, body), or None when nothing answered in time."""
    try:
        with _OPENER.open(url, timeout=timeout) as answer:
            return answer.status, answer.read(MAX_ANSWER)
    except urllib.error.HTTPError as refused:
        refused.close()
        return refused.code, b""
    except (OSError, ValueError):
        return None


def health(port: int, get=http_get) -> dict | None:
    """A ring service's ``/health`` document, or None when it did not answer with one."""
    answer = get(f"http://127.0.0.1:{port}/health")
    if answer is None or answer[0] != 200:
        return None
    try:
        document = json.loads(answer[1])
    except ValueError:
        return None
    return document if isinstance(document, dict) else None


def openalgo(get=http_get) -> bool:
    """Whether OpenAlgo answered its front page (200, or 302 to the login: the deploy's check)."""
    answer = get(OPENALGO_URL)
    return answer is not None and answer[0] in (200, 302)


def enabled(name: str, wants_dir: Path = WANTS_DIR) -> bool | None:
    """Whether ``sudarshan-<name>.service`` is enabled here; None when that cannot be told."""
    if not wants_dir.is_dir():
        return None
    return os.path.lexists(wants_dir / f"sudarshan-{name}.service")


def disk(folder: Path) -> dict | None:
    """Free space (GB) and free file slots (percent) of the disk that holds ``folder``."""
    try:
        seen = os.statvfs(folder)
    except OSError:
        return None
    slots = 100.0 if seen.f_files == 0 else 100.0 * seen.f_favail / seen.f_files
    return {"free_gb": seen.f_bavail * seen.f_frsize / 1e9, "inodes_free_pct": slots}


def mem_available_mb(path: Path = MEMINFO) -> int | None:
    raw = read_regular(path, 8192)
    for line in (raw or b"").decode("ascii", errors="replace").splitlines():
        words = line.split()
        if len(words) >= 2 and words[0] == "MemAvailable:" and words[1].isdigit():
            return int(words[1]) // 1024
    return None


def cert_hours_left(now: float, address=CERT_ADDRESS, timeout: float = TIMEOUT_S) -> float | None:
    """Hours until the certificate served at ``address`` runs out. It is read, not judged: an
    address-only certificate is as good as a named one here."""
    from cryptography import x509

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with (
            socket.create_connection(address, timeout=timeout) as plain,
            context.wrap_socket(plain) as secure,
        ):
            der = secure.getpeercert(binary_form=True)
        ends = x509.load_der_x509_certificate(der).not_valid_after_utc
    except (OSError, ValueError, TypeError):
        return None
    return (ends.timestamp() - now) / 3600.0


def master_date(app_dir: Path, env_file: Path) -> str | None:
    """The date in India of the last instrument list download, "" when there was none, None when
    it could not be read. One read-only question to OpenAlgo's own status table, opened as the
    journal opens OpenAlgo's records (``journal/openalgo_ro.py``): an engine made for it and
    disposed after it. OpenAlgo's .env is read for the database's place only."""
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from database.engine_factory import create_db_engine
    from sudarshan.alerts.config import read_openalgo_env, sqlite_path
    from sudarshan.alerts.owner import database_urls

    values = read_openalgo_env(env_file)
    db_path = sqlite_path(values.get("DATABASE_URL") or "sqlite:///db/openalgo.db", app_dir)
    if db_path is None or not db_path.is_file():
        return None
    question = text("SELECT MAX(last_download_time) FROM master_contract_status")
    for url in database_urls(db_path):
        engine = create_db_engine(url)
        try:
            with engine.connect() as connection:
                last = connection.execute(question).scalar()
            break
        except SQLAlchemyError:
            continue
        finally:
            engine.dispose()
    else:
        return None
    if not last:
        return ""
    # OpenAlgo stores the server's local time without a zone; astimezone() reads it as local.
    return datetime.fromisoformat(str(last)).astimezone(IST).date().isoformat()


def _or_none(read, *args):
    try:
        return read(*args)
    except Exception:
        return None


def gather(now: float, app_dir: Path, env_file: Path, known_master_date: str | None = None) -> dict:
    """One run's readings, in the shape ``checks.py`` documents. Never raises.

    ``known_master_date`` is the instrument list's date an earlier run today already read: when it
    is today's, OpenAlgo's database is not opened again.
    """
    moment = datetime.fromtimestamp(now, UTC).astimezone(IST)
    today = moment.date().isoformat()
    answers = {name: _or_none(health, port) for name, port in SERVICES.items()}
    window = (answers.get("feed") or {}).get("market_window")
    window_open = window.get("open") if isinstance(window, Mapping) else None
    minute = moment.hour * 60 + moment.minute
    if known_master_date == today:
        master = today
    elif window_open and minute >= 9 * 60:
        master = _or_none(master_date, app_dir, env_file)
    else:
        master = known_master_date
    return {
        "now": now,
        "ist_minute": minute,
        "ist_date": today,
        "window_open": window_open if isinstance(window_open, bool) else None,
        "health": answers,
        "enabled": {name: _or_none(enabled, name) for name in SERVICES},
        "openalgo": _or_none(openalgo),
        "master_date": master,
        "disk": _or_none(disk, app_dir),
        "mem_available_mb": _or_none(mem_available_mb),
        "cert_hours_left": _or_none(cert_hours_left, now),
    }
