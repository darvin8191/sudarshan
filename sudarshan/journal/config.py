"""Settings for sudarshan-journal (JB2, design 7.1 as amended), read once at start.

Two places are read, as the portfolio reads them. The unit's environment file
(``/etc/sudarshan/journal.env``, optional, owner-readable) arrives as process environment; the
switch ``SUDARSHAN_JOURNAL`` is set by the release in OpenAlgo's ``.env`` (ops/deploy), which the
service reads for it, for ``API_KEY_PEPPER`` and for the paths of OpenAlgo's databases. A value in
the environment wins over the same name in ``.env``. Nothing secret is a setting: the pepper is
returned by ``read_pepper`` and kept out of ``Settings``. The journal never reads the Upstox token
(cross-design Y4): every account read goes through the portfolio's journal purpose.

| setting | default |
|---|---|
| ``SUDARSHAN_JOURNAL`` | ``off`` (the release sets it) |
| ``SUDARSHAN_JOURNAL_HTTP_PORT`` | ``5586`` (127.0.0.1 only) |
| ``SUDARSHAN_JOURNAL_FEED_URL`` | ``http://127.0.0.1:5580`` (loopback only) |
| ``SUDARSHAN_JOURNAL_FEED_ZMQ`` | ``tcp://127.0.0.1:5581`` (loopback only) |
| ``SUDARSHAN_JOURNAL_PORTFOLIO_URL`` | ``http://127.0.0.1:5583`` (loopback only) |
| ``SUDARSHAN_JOURNAL_MAX_CLAIMS`` | ``50`` (the feed's quota for this client, at most 50; Y12) |
| ``SUDARSHAN_JOURNAL_EOD_AFTER_MIN`` | ``5`` (the day report: the day's last close plus this) |
| ``SUDARSHAN_JOURNAL_CHECK_AFTER_MIN`` | ``5`` (the close check: the cash post-close session's end plus this; Y22) |
| ``SUDARSHAN_JOURNAL_BRIEF_BEFORE_MIN`` | ``30`` (the morning brief: the day's first session start minus this) |
| ``SUDARSHAN_JOURNAL_WEEKLY_AT`` | ``SUN 18:00`` |
| ``SUDARSHAN_JOURNAL_MONTHLY_AT`` | ``1 07:00`` (day of the month, then the time) |
| ``SUDARSHAN_JOURNAL_TICKS_DIR`` | ``/var/lib/sudarshan-feed/ticks`` (the feed's recording, read only) |
| ``SUDARSHAN_NSE_HOLIDAYS_FILE`` / ``SUDARSHAN_NSE_EXTRA_DAYS_FILE`` | the calendar files the other services read |
| ``SUDARSHAN_JOURNAL_LOG_LEVEL`` | ``INFO`` |

From OpenAlgo's ``.env``: ``DATABASE_URL`` (openalgo.db), ``LATENCY_DATABASE_URL``,
``SANDBOX_DATABASE_URL`` and ``SUDARSHAN_RISK_DB``, each opened read-only, never written.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

from sudarshan.alerts.config import sqlite_path
from sudarshan.autologin.schedule import EXTRA_TRADING_DAYS_FILE, HOLIDAYS_FILE
from sudarshan.ops.release import DEPLOYED_TAG_FILE
from sudarshan.vault import VaultKeyError, pepper_from

from .core.kinds import LIMITS, PORT

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_HOST = "127.0.0.1"
STATE_DIR = "/var/lib/sudarshan-journal"
FEED_URL = f"http://{LOCAL_HOST}:5580"
FEED_ZMQ = f"tcp://{LOCAL_HOST}:5581"
PORTFOLIO_URL = f"http://{LOCAL_HOST}:5583"
#: The alerts service, whose report desk delivers the reports (its /health says it runs).
ALERTS_URL = f"http://{LOCAL_HOST}:5582"
TICKS_DIR = "/var/lib/sudarshan-feed/ticks"
RISK_DB = "db/sudarshan_risk.db"
#: The most keys this client may claim at the feed (cross-design Y12: the journal's quota).
MAX_CLAIMS_CEILING = LIMITS["claims"]
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_ON = {"1", "on", "true", "yes"}
_OFF = {"0", "off", "false", "no"}


class ConfigError(ValueError):
    """The service cannot run with these settings; the message names the setting and the fix."""


def setup_logging(env: Mapping[str, str] | None = None) -> None:
    """Our format on the system journal, at the level the setting names (counts and states
    only: never a key, a price or a quantity at INFO)."""
    name = (env if env is not None else os.environ).get("SUDARSHAN_JOURNAL_LOG_LEVEL", "INFO")
    level = getattr(logging, name.strip().upper(), logging.INFO)
    logging.basicConfig(level=level, format=LOG_FORMAT, force=True)
    # Library debug output can carry URLs and headers; nothing of theirs belongs in our log.
    for library in ("httpx", "httpcore", "sqlalchemy"):
        logging.getLogger(library).setLevel(logging.WARNING)


def _switch(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = (env.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in _ON:
        return True
    if raw in _OFF:
        return False
    raise ConfigError(f"{name} must be on or off")


def _number(env: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    if not raw.isdigit() or not low <= int(raw) <= high:
        raise ConfigError(f"{name} must be a whole number from {low} to {high}")
    return int(raw)


def _clock(raw: str, name: str, example: str) -> int:
    """HH:MM as minutes after midnight."""
    hours, sep, minutes = raw.strip().partition(":")
    if not (sep and hours.isdigit() and minutes.isdigit() and len(minutes) == 2):
        raise ConfigError(f"{name} must name a time such as {example}")
    if not (int(hours) < 24 and int(minutes) < 60):
        raise ConfigError(f"{name} must name a time such as {example}")
    return int(hours) * 60 + int(minutes)


def _weekly(env: Mapping[str, str], name: str, default: str) -> tuple[int, int]:
    """``SUN 18:00`` as (weekday, minutes after midnight); Monday is 0."""
    day, _, at = (env.get(name) or default).strip().upper().partition(" ")
    if day not in WEEKDAYS:
        raise ConfigError(f"{name} must be a day and a time such as {default}")
    return WEEKDAYS.index(day), _clock(at, name, default)


def _monthly(env: Mapping[str, str], name: str, default: str) -> tuple[int, int]:
    """``1 07:00`` as (day of the month, minutes after midnight); the day is 1 to 28."""
    day, _, at = (env.get(name) or default).strip().partition(" ")
    if not day.isdigit() or not 1 <= int(day) <= 28:
        raise ConfigError(f"{name} must be a day from 1 to 28 and a time, such as {default}")
    return int(day), _clock(at, name, default)


def _loopback_url(env: Mapping[str, str], name: str, default: str, scheme: str = "http") -> str:
    url = (env.get(name) or default).strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme != scheme or (parts.hostname or "") not in LOOPBACK_HOSTS:
        raise ConfigError(f"{name} must be {scheme}:// on this server (127.0.0.1)")
    return url


def read_openalgo_env(path: Path) -> dict[str, str]:
    """OpenAlgo's .env as a dict (values unquoted). Raises ConfigError if it cannot be read."""
    try:
        with path.open(encoding="utf-8") as handle:
            values = dotenv_values(stream=handle)
    except OSError as exc:
        raise ConfigError(f"cannot read {path}") from exc
    return {key: (value or "").strip() for key, value in values.items() if key}


def merged_env(
    env: Mapping[str, str] | None = None, openalgo: Mapping[str, str] | None = None
) -> dict[str, str]:
    """OpenAlgo's .env under the process environment: a name set in both takes the environment's."""
    return {
        **(openalgo or {}),
        **{k: v for k, v in (os.environ if env is None else env).items() if v != ""},
    }


def read_pepper(env: Mapping[str, str]) -> str:
    """OpenAlgo's ``API_KEY_PEPPER`` from the merged environment; ConfigError in words if it is
    missing or too short (no seal can be opened or made without it)."""
    try:
        return pepper_from(env)
    except VaultKeyError as exc:
        raise ConfigError(f"{exc}. Check API_KEY_PEPPER in OpenAlgo's .env") from exc


def _app_path(raw: str, app_dir: Path) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else app_dir / path


@dataclass(frozen=True)
class Settings:
    enabled: bool
    http_port: int = PORT
    feed_url: str = FEED_URL
    feed_zmq: str = FEED_ZMQ
    portfolio_url: str = PORTFOLIO_URL
    alerts_url: str = ALERTS_URL
    max_claims: int = MAX_CLAIMS_CEILING
    eod_after_min: int = 5
    check_after_min: int = 5
    brief_before_min: int = 30
    weekly_at: tuple[int, int] = (6, 18 * 60)
    monthly_at: tuple[int, int] = (1, 7 * 60)
    ticks_dir: Path = Path(TICKS_DIR)
    log_level: str = "INFO"
    app_dir: Path = REPO_ROOT
    env_file: Path = REPO_ROOT / ".env"
    state_dir: Path = Path(STATE_DIR)
    deployed_tag_file: Path = DEPLOYED_TAG_FILE
    openalgo_db: Path | None = None
    latency_db: Path | None = None
    sandbox_db: Path | None = None
    risk_db: Path | None = None
    holidays_file: Path = Path(HOLIDAYS_FILE)
    extra_days_file: Path = Path(EXTRA_TRADING_DAYS_FILE)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """The settings in ``env`` (default: the process environment merged over OpenAlgo's .env
        when that file can be read)."""
        if env is None:
            base = dict(os.environ)
            app_dir = Path(base.get("SUDARSHAN_OPENALGO_DIR") or REPO_ROOT)
            env_file = Path(base.get("SUDARSHAN_OPENALGO_ENV_FILE") or app_dir / ".env")
            env = merged_env(base, read_openalgo_env(env_file) if env_file.exists() else {})
        get = env.get
        app_dir = Path(get("SUDARSHAN_OPENALGO_DIR") or REPO_ROOT)
        # systemd's StateDirectory= may list several paths separated by ':'; the first is ours.
        state_dir = get("SUDARSHAN_JOURNAL_STATE_DIR") or get("STATE_DIRECTORY") or STATE_DIR
        return cls(
            enabled=_switch(env, "SUDARSHAN_JOURNAL", False),
            http_port=_number(env, "SUDARSHAN_JOURNAL_HTTP_PORT", PORT, 1, 65535),
            feed_url=_loopback_url(env, "SUDARSHAN_JOURNAL_FEED_URL", FEED_URL),
            feed_zmq=_loopback_url(env, "SUDARSHAN_JOURNAL_FEED_ZMQ", FEED_ZMQ, "tcp"),
            portfolio_url=_loopback_url(env, "SUDARSHAN_JOURNAL_PORTFOLIO_URL", PORTFOLIO_URL),
            alerts_url=_loopback_url(env, "SUDARSHAN_JOURNAL_ALERTS_URL", ALERTS_URL),
            max_claims=_number(
                env, "SUDARSHAN_JOURNAL_MAX_CLAIMS", MAX_CLAIMS_CEILING, 1, MAX_CLAIMS_CEILING
            ),
            eod_after_min=_number(env, "SUDARSHAN_JOURNAL_EOD_AFTER_MIN", 5, 0, 120),
            check_after_min=_number(env, "SUDARSHAN_JOURNAL_CHECK_AFTER_MIN", 5, 0, 120),
            brief_before_min=_number(env, "SUDARSHAN_JOURNAL_BRIEF_BEFORE_MIN", 30, 0, 180),
            weekly_at=_weekly(env, "SUDARSHAN_JOURNAL_WEEKLY_AT", "SUN 18:00"),
            monthly_at=_monthly(env, "SUDARSHAN_JOURNAL_MONTHLY_AT", "1 07:00"),
            ticks_dir=Path(get("SUDARSHAN_JOURNAL_TICKS_DIR") or TICKS_DIR),
            log_level=(get("SUDARSHAN_JOURNAL_LOG_LEVEL") or "INFO").strip().upper(),
            app_dir=app_dir,
            env_file=Path(get("SUDARSHAN_OPENALGO_ENV_FILE") or app_dir / ".env"),
            state_dir=Path(state_dir.split(":")[0]),
            deployed_tag_file=Path(get("SUDARSHAN_DEPLOYED_TAG_FILE") or DEPLOYED_TAG_FILE),
            openalgo_db=sqlite_path(get("DATABASE_URL") or "sqlite:///db/openalgo.db", app_dir),
            latency_db=sqlite_path(
                get("LATENCY_DATABASE_URL") or "sqlite:///db/latency.db", app_dir
            ),
            sandbox_db=sqlite_path(
                get("SANDBOX_DATABASE_URL") or "sqlite:///db/sandbox.db", app_dir
            ),
            risk_db=_app_path(get("SUDARSHAN_RISK_DB") or RISK_DB, app_dir),
            holidays_file=Path(get("SUDARSHAN_NSE_HOLIDAYS_FILE") or HOLIDAYS_FILE),
            extra_days_file=Path(get("SUDARSHAN_NSE_EXTRA_DAYS_FILE") or EXTRA_TRADING_DAYS_FILE),
        )
