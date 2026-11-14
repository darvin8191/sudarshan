"""The watch's readings: what one run reads from the server (M19.5, P8 7.1).

Each reader reads one thing and returns it plain, or None when it could not be read; none of
them raises and none of them changes anything. Everything read is SUDARSHAN's own or OpenAlgo's:
the ring's ``/health`` pages and OpenAlgo on 127.0.0.1, SUDARSHAN's unit links, the app's disk,
the kernel's memory figure, the certificate nginx serves on this machine, and the instrument
list's date from OpenAlgo's own status table (read-only). Nothing of another system on the
server is named here, and nothing outside the server is called.
"""

from __future__ import annotations

import fcntl
import ipaddress
import json
import os
import socket
import ssl
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from sudarshan.ops.files import read_regular

from . import posture
from .checks import SERVICES

IST = timezone(timedelta(hours=5, minutes=30), "IST")  # India keeps no daylight saving
TIMEOUT_S = 2.0
MAX_ANSWER = 1 << 20
OPENALGO_URL = "http://127.0.0.1:5000/"
#: Where systemd links a unit that is enabled (each SUDARSHAN service is WantedBy multi-user).
WANTS_DIR = Path("/etc/systemd/system/multi-user.target.wants")
MEMINFO = Path("/proc/meminfo")
CPU_STAT = Path("/proc/stat")
#: The deploy tool holds this for the whole of an install (sudarshan/ops/deploy/sudarshan-deploy).
DEPLOY_LOCK = Path("/var/lib/sudarshan/deploy.lock")
CERT_ADDRESS = ("127.0.0.1", 443)
#: In /etc/sudarshan/watch.env: the app's own address, when OpenAlgo's HOST_SERVER does not name
#: it. nginx may serve more than one site on this server, so the certificate is asked for by
#: SUDARSHAN's own name and checked to be that name's; another site's is never judged.
CERT_HOST_ENV = "SUDARSHAN_WATCH_CERT_HOST"
#: What the certificate reading says in place of hours when it did not judge one: no name for
#: SUDARSHAN's site is known, or the certificate nginx gave is another site's.
CERT_NO_NAME, CERT_OTHER_SITE = "no_name", "other_site"
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


def installing(lock: Path = DEPLOY_LOCK) -> bool:
    """Whether the deploy tool is installing a release right now: its lock is held. A lock that is
    not there or cannot be opened is no install (a server the tool never deployed, a test)."""
    try:
        fd = os.open(lock, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return False
    finally:
        os.close(fd)  # closing gives the shared lock back
    return False


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


def mem_total_mb(path: Path = MEMINFO) -> int | None:
    raw = read_regular(path, 8192)
    for line in (raw or b"").decode("ascii", errors="replace").splitlines():
        words = line.split()
        if len(words) >= 2 and words[0] == "MemTotal:" and words[1].isdigit():
            return int(words[1]) // 1024
    return None


def cpu(path: Path = CPU_STAT) -> list[int] | None:
    """The kernel's processor counters since boot as [busy, total] ticks, all processors
    together. Two runs' difference is the share of time the server was not idle."""
    raw = read_regular(path, 8192)
    for line in (raw or b"").decode("ascii", errors="replace").splitlines():
        words = line.split()
        if len(words) >= 6 and words[0] == "cpu" and all(w.isdigit() for w in words[1:]):
            ticks = [int(w) for w in words[1:]]
            total = sum(ticks[:8])
            return [total - ticks[3] - ticks[4], total]  # less idle and waiting on the disk
    return None


def site_name(env_file: Path | None, environ: Mapping[str, str] | None = None) -> str | None:
    """SUDARSHAN's own address, to ask nginx for its certificate by: ``CERT_HOST_ENV`` from the
    watch's settings, else the host of ``HOST_SERVER`` in OpenAlgo's .env (the address the owner
    opens the app at). None when neither names one; this server's own loopback is no name."""
    environ = os.environ if environ is None else environ
    named = (environ.get(CERT_HOST_ENV) or "").strip()
    if not named and env_file is not None:
        from sudarshan.alerts.config import read_openalgo_env

        try:
            named = read_openalgo_env(env_file).get("HOST_SERVER") or ""
        except Exception:
            named = ""
    if "//" in named:
        try:
            named = urlsplit(named).hostname or ""
        except ValueError:
            named = ""
    named = named.strip().strip("[].").lower()
    if not named or named == "localhost" or any(c.isspace() or c == "/" for c in named):
        return None
    address = _address(named)
    if address is not None and (address.is_loopback or address.is_unspecified):
        return None
    return named


def _address(name: str):
    try:
        return ipaddress.ip_address(name)
    except ValueError:
        return None


def _covers(certificate, name: str) -> bool:
    """Whether a certificate is issued for ``name`` (a DNS name, one leading ``*`` label allowed,
    or an address). It is not otherwise verified: this only tells whose certificate was given."""
    from cryptography import x509

    try:
        names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        common = certificate.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
        return any(str(part.value).lower() == name for part in common)
    address = _address(name)
    if address is not None:
        return address in names.get_values_for_type(x509.IPAddress)
    for pattern in names.get_values_for_type(x509.DNSName):
        pattern = pattern.lower().rstrip(".")
        if pattern == name or (pattern.startswith("*.") and name.split(".", 1)[-1] == pattern[2:]):
            return True
    return False


def cert_hours_left(
    now: float,
    address=CERT_ADDRESS,
    timeout: float = TIMEOUT_S,
    name: str | None = None,
    env_file: Path | None = None,
) -> float | str | None:
    """Hours until SUDARSHAN's own certificate, as nginx serves it at ``address``, runs out.

    nginx is asked by ``name`` (else ``site_name``), and the certificate it gives must be that
    name's own. With no name known the answer is ``CERT_NO_NAME`` and nothing is asked: on a
    server with more than one site the certificate nginx offers first may be another site's.
    When the one given is not SUDARSHAN's (nginx has no site of that name on this address), the
    answer is ``CERT_OTHER_SITE``. An address for a name cannot be sent with the request, so
    nginx gives its first certificate, and it is judged only when it is issued for that address.
    None when the certificate could not be read. Its issuer and dates are read, never judged."""
    from cryptography import x509

    name = (name or "").strip().lower() or site_name(env_file)
    if name is None:
        return CERT_NO_NAME
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    asked = None if _address(name) is not None else name
    try:
        with (
            socket.create_connection(address, timeout=timeout) as plain,
            context.wrap_socket(plain, server_hostname=asked) as secure,
        ):
            der = secure.getpeercert(binary_form=True)
        certificate = x509.load_der_x509_certificate(der)
        if not _covers(certificate, name):
            return CERT_OTHER_SITE
        ends = certificate.not_valid_after_utc
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


def gather(
    now: float,
    app_dir: Path,
    env_file: Path,
    known_master_date: str | None = None,
    known_posture: Mapping | None = None,
) -> dict:
    """One run's readings, in the shape ``checks.py`` documents. Never raises.

    ``known_master_date`` is the instrument list's date an earlier run today already read: when it
    is today's, OpenAlgo's database is not opened again for it. ``known_posture`` is the last
    posture reading: it is read afresh once an hour.
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
        "mem_total_mb": _or_none(mem_total_mb),
        "cpu": _or_none(cpu),
        "cert_hours_left": _or_none(lambda: cert_hours_left(now, env_file=env_file)),
        "posture": _or_none(posture.read, now, app_dir, env_file, known_posture),
        "counts": _or_none(posture.counts, app_dir, env_file),
        "backup": _or_none(posture.backup, now),
        "drill": _or_none(posture.drill),
        "historify_gb": _or_none(posture.historify_gb, app_dir),
    }
