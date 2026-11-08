"""Settings for sudarshan-screener (M9.1, design 6.4), read once at start.

Two places are read, as the other ring services read them. The unit's environment file
(``/etc/sudarshan/screener.env``, root:sudarshan 640) arrives as process environment and holds
the switch ``SUDARSHAN_SCREENER`` (cross-design R3: a ring service reads its own switch from its
own file; the release writes the default when the key is absent and keeps the owner's value).
OpenAlgo's ``.env`` is read for ``API_KEY_PEPPER`` (the seal) and ``DATABASE_URL`` (the master
contract and the watchlists, read-only). A value in the environment wins over ``.env``. Nothing
secret is a setting: the pepper is returned by ``read_pepper`` and kept out of ``Settings``.

| setting | default |
|---|---|
| ``SUDARSHAN_SCREENER`` | ``off`` |
| ``SUDARSHAN_SCREENER_HTTP_PORT`` | ``5584`` (127.0.0.1 only; S10) |
| ``SUDARSHAN_SCREENER_FEED_URL`` | ``http://127.0.0.1:5580`` (loopback only) |
| ``SUDARSHAN_SCREENER_FEED_ZMQ`` | ``tcp://127.0.0.1:5581`` (loopback only) |
| ``SUDARSHAN_SCREENER_OPENALGO_URL`` | ``http://127.0.0.1:5000`` (the internal routes) |
| ``SUDARSHAN_SCREENER_MAX_KEYS`` | ``320`` (at most 700, X10) |
| ``SUDARSHAN_SCREENER_MAX_ACTIVE`` | ``12`` intraday screens |
| ``SUDARSHAN_SCREENER_OPTION_STRIKES`` | ``10`` each side of the money |
| ``SUDARSHAN_SCREENER_STOCK_IV`` | ``on`` |
| ``SUDARSHAN_SCREENER_KEEP_DAYS`` | ``100`` (never under 90) |
| ``SUDARSHAN_SCREENER_REPLAY`` | ``0`` (tests: trust replay health, tick clock) |
| ``SUDARSHAN_NSE_HOLIDAYS_FILE`` / ``SUDARSHAN_NSE_EXTRA_DAYS_FILE`` | the calendar files every service reads |
| ``SUDARSHAN_SCREENER_LOG_LEVEL`` | ``INFO`` |
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

from .contract import DEFAULT_HTTP_PORT, LOCAL_HOST
from .store import KEEP_RUN_DAYS_MIN, STATE_DIR

REPO_ROOT = Path(__file__).resolve().parents[2]
FEED_URL = f"http://{LOCAL_HOST}:5580"
FEED_ZMQ = f"tcp://{LOCAL_HOST}:5581"
OPENALGO_URL = f"http://{LOCAL_HOST}:5000"
#: The most keys this client may claim at the feed now, and the ceiling it may be raised to
#: after one clean session over 600 keys on the socket (cross-design X10, S6).
MAX_KEYS_DEFAULT = 320
MAX_KEYS_CEILING = 700
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_ON = {"1", "on", "true", "yes"}
_OFF = {"0", "off", "false", "no"}


class ConfigError(ValueError):
    """The service cannot run with these settings; the message names the setting and the fix."""


def setup_logging(env: Mapping[str, str] | None = None) -> None:
    """Our format on the journal, at the level the setting names (counts and states only)."""
    name = (env if env is not None else os.environ).get("SUDARSHAN_SCREENER_LOG_LEVEL", "INFO")
    level = getattr(logging, name.strip().upper(), logging.INFO)
    logging.basicConfig(level=level, format=LOG_FORMAT, force=True)
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
    missing or too short (the seal cannot be opened without it)."""
    try:
        return pepper_from(env)
    except VaultKeyError as exc:
        raise ConfigError(f"{exc}. Check API_KEY_PEPPER in OpenAlgo's .env") from exc


@dataclass(frozen=True)
class Settings:
    enabled: bool
    http_port: int = DEFAULT_HTTP_PORT
    feed_url: str = FEED_URL
    feed_zmq: str = FEED_ZMQ
    openalgo_url: str = OPENALGO_URL
    max_keys: int = MAX_KEYS_DEFAULT
    max_active: int = 12
    option_strikes: int = 10
    stock_iv: bool = True
    keep_days: int = 100
    replay: bool = False
    log_level: str = "INFO"
    app_dir: Path = REPO_ROOT
    env_file: Path = REPO_ROOT / ".env"
    state_dir: Path = STATE_DIR
    deployed_tag_file: Path = DEPLOYED_TAG_FILE
    openalgo_db: Path | None = None
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
        state_dir = get("SUDARSHAN_SCREENER_STATE_DIR") or get("STATE_DIRECTORY") or str(STATE_DIR)
        return cls(
            enabled=_switch(env, "SUDARSHAN_SCREENER", False),
            http_port=_number(env, "SUDARSHAN_SCREENER_HTTP_PORT", DEFAULT_HTTP_PORT, 1, 65535),
            feed_url=_loopback_url(env, "SUDARSHAN_SCREENER_FEED_URL", FEED_URL),
            feed_zmq=_loopback_url(env, "SUDARSHAN_SCREENER_FEED_ZMQ", FEED_ZMQ, "tcp"),
            openalgo_url=_loopback_url(env, "SUDARSHAN_SCREENER_OPENALGO_URL", OPENALGO_URL),
            max_keys=_number(
                env, "SUDARSHAN_SCREENER_MAX_KEYS", MAX_KEYS_DEFAULT, 1, MAX_KEYS_CEILING
            ),
            max_active=_number(env, "SUDARSHAN_SCREENER_MAX_ACTIVE", 12, 1, 50),
            option_strikes=_number(env, "SUDARSHAN_SCREENER_OPTION_STRIKES", 10, 1, 25),
            stock_iv=_switch(env, "SUDARSHAN_SCREENER_STOCK_IV", True),
            keep_days=_number(env, "SUDARSHAN_SCREENER_KEEP_DAYS", 100, KEEP_RUN_DAYS_MIN, 3650),
            replay=_switch(env, "SUDARSHAN_SCREENER_REPLAY", False),
            log_level=(get("SUDARSHAN_SCREENER_LOG_LEVEL") or "INFO").strip().upper(),
            app_dir=app_dir,
            env_file=Path(get("SUDARSHAN_OPENALGO_ENV_FILE") or app_dir / ".env"),
            state_dir=Path(state_dir.split(":")[0]),
            deployed_tag_file=Path(get("SUDARSHAN_DEPLOYED_TAG_FILE") or DEPLOYED_TAG_FILE),
            openalgo_db=sqlite_path(get("DATABASE_URL") or "sqlite:///db/openalgo.db", app_dir),
            holidays_file=Path(get("SUDARSHAN_NSE_HOLIDAYS_FILE") or HOLIDAYS_FILE),
            extra_days_file=Path(get("SUDARSHAN_NSE_EXTRA_DAYS_FILE") or EXTRA_TRADING_DAYS_FILE),
        )
