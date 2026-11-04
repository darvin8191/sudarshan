"""Settings for sudarshan-portfolio (M7.18, design 7.1), read once at start.

Two places are read, as the alerts service reads them. The unit's environment file
(``/etc/sudarshan/portfolio.env``, optional, owner-readable) arrives as process environment; the
switch ``SUDARSHAN_PORTFOLIO`` is set by the release in OpenAlgo's ``.env`` (ops/deploy), which the
service reads for it and for ``API_KEY_PEPPER``. A value in the environment wins over the same
name in ``.env``. Nothing secret is a setting: the Upstox token is a file (``token_file``) that
later parts read, and the pepper is returned by ``read_pepper`` and kept out of ``Settings``.

| setting | default |
|---|---|
| ``SUDARSHAN_PORTFOLIO`` | ``off`` (the release sets it) |
| ``SUDARSHAN_PORTFOLIO_HTTP_PORT`` | ``5583`` (127.0.0.1 only) |
| ``SUDARSHAN_PORTFOLIO_FEED_URL`` | ``http://127.0.0.1:5580`` (loopback only) |
| ``SUDARSHAN_PORTFOLIO_MAX_CLAIMS`` | ``200`` (the feed's ceiling for this client, at most 200) |
| ``SUDARSHAN_PORTFOLIO_AMFI`` | ``on`` |
| ``SUDARSHAN_PORTFOLIO_EOD_AFTER_MIN`` | ``15`` (minutes after the close; the record is at 15:55 on a normal day) |
| ``SUDARSHAN_PORTFOLIO_SETTLE_AT`` | ``09:20`` (IST) |
| ``SUDARSHAN_UPSTOX_TOKEN_FILE`` | ``/etc/sudarshan/upstox_analytics_token`` |
| ``SUDARSHAN_PORTFOLIO_UPSTOX_URL`` | ``https://api.upstox.com`` (a test points it at a fake on this server) |
| ``SUDARSHAN_PORTFOLIO_AMFI_URL`` | AMFI's NAVAll.txt (a test points it at a fake on this server) |
| ``SUDARSHAN_NSE_HOLIDAYS_FILE`` / ``SUDARSHAN_NSE_EXTRA_DAYS_FILE`` | the calendar files the other services read |
| ``SUDARSHAN_PORTFOLIO_LOG_LEVEL`` | ``INFO`` |
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

from .core.kinds import DEFAULT_HTTP_PORT, LOCAL_HOST

REPO_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = "/var/lib/sudarshan-portfolio"
FEED_URL = f"http://{LOCAL_HOST}:5580"
TOKEN_FILE = "/etc/sudarshan/upstox_analytics_token"
UPSTOX_URL = "https://api.upstox.com"
AMFI_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"
#: The most keys this client may claim at the feed (cross-design X10).
MAX_CLAIMS_CEILING = 200
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_ON = {"1", "on", "true", "yes"}
_OFF = {"0", "off", "false", "no"}


class ConfigError(ValueError):
    """The service cannot run with these settings; the message names the setting and the fix."""


def setup_logging(env: Mapping[str, str] | None = None) -> None:
    """Our format on the journal, at the level the setting names (counts and states only)."""
    name = (env if env is not None else os.environ).get("SUDARSHAN_PORTFOLIO_LOG_LEVEL", "INFO")
    level = getattr(logging, name.strip().upper(), logging.INFO)
    logging.basicConfig(level=level, format=LOG_FORMAT, force=True)
    # Library debug output can carry URLs and headers; the Upstox token travels in one of them.
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


def _clock(env: Mapping[str, str], name: str, default: str) -> int:
    """HH:MM as minutes after midnight."""
    raw = (env.get(name) or default).strip()
    hours, sep, minutes = raw.partition(":")
    if not (sep and hours.isdigit() and minutes.isdigit() and len(minutes) == 2):
        raise ConfigError(f"{name} must be a time such as {default}")
    if not (int(hours) < 24 and int(minutes) < 60):
        raise ConfigError(f"{name} must be a time such as {default}")
    return int(hours) * 60 + int(minutes)


def _loopback_url(env: Mapping[str, str], name: str, default: str) -> str:
    url = (env.get(name) or default).strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme != "http" or (parts.hostname or "") not in LOOPBACK_HOSTS:
        raise ConfigError(f"{name} must be http:// on this server (127.0.0.1)")
    return url


def _upstox_url(env: Mapping[str, str], name: str) -> str:
    """Upstox itself, or http on this server (a fake in a test): the token goes nowhere else."""
    url = (env.get(name) or UPSTOX_URL).strip().rstrip("/")
    parts = urlsplit(url)
    if url == UPSTOX_URL or (parts.scheme == "http" and (parts.hostname or "") in LOOPBACK_HOSTS):
        return url
    raise ConfigError(f"{name} must be {UPSTOX_URL} or http:// on this server")


def _amfi_url(env: Mapping[str, str], name: str) -> str:
    """AMFI's file, or http on this server (a fake in a test)."""
    url = (env.get(name) or AMFI_URL).strip()
    parts = urlsplit(url)
    if url == AMFI_URL or (parts.scheme == "http" and (parts.hostname or "") in LOOPBACK_HOSTS):
        return url
    raise ConfigError(f"{name} must be {AMFI_URL} or http:// on this server")


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
    max_claims: int = MAX_CLAIMS_CEILING
    amfi: bool = True
    eod_after_min: int = 15
    settle_at_min: int = 9 * 60 + 20
    token_file: Path = Path(TOKEN_FILE)
    upstox_url: str = UPSTOX_URL
    amfi_url: str = AMFI_URL
    log_level: str = "INFO"
    app_dir: Path = REPO_ROOT
    env_file: Path = REPO_ROOT / ".env"
    state_dir: Path = Path(STATE_DIR)
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
        state_dir = get("SUDARSHAN_PORTFOLIO_STATE_DIR") or get("STATE_DIRECTORY") or STATE_DIR
        return cls(
            enabled=_switch(env, "SUDARSHAN_PORTFOLIO", False),
            http_port=_number(env, "SUDARSHAN_PORTFOLIO_HTTP_PORT", DEFAULT_HTTP_PORT, 1, 65535),
            feed_url=_loopback_url(env, "SUDARSHAN_PORTFOLIO_FEED_URL", FEED_URL),
            max_claims=_number(
                env, "SUDARSHAN_PORTFOLIO_MAX_CLAIMS", MAX_CLAIMS_CEILING, 1, MAX_CLAIMS_CEILING
            ),
            amfi=_switch(env, "SUDARSHAN_PORTFOLIO_AMFI", True),
            eod_after_min=_number(env, "SUDARSHAN_PORTFOLIO_EOD_AFTER_MIN", 15, 0, 120),
            settle_at_min=_clock(env, "SUDARSHAN_PORTFOLIO_SETTLE_AT", "09:20"),
            token_file=Path(get("SUDARSHAN_UPSTOX_TOKEN_FILE") or TOKEN_FILE),
            upstox_url=_upstox_url(env, "SUDARSHAN_PORTFOLIO_UPSTOX_URL"),
            amfi_url=_amfi_url(env, "SUDARSHAN_PORTFOLIO_AMFI_URL"),
            log_level=(get("SUDARSHAN_PORTFOLIO_LOG_LEVEL") or "INFO").strip().upper(),
            app_dir=app_dir,
            env_file=Path(get("SUDARSHAN_OPENALGO_ENV_FILE") or app_dir / ".env"),
            state_dir=Path(state_dir.split(":")[0]),
            deployed_tag_file=Path(get("SUDARSHAN_DEPLOYED_TAG_FILE") or DEPLOYED_TAG_FILE),
            openalgo_db=sqlite_path(get("DATABASE_URL") or "sqlite:///db/openalgo.db", app_dir),
            holidays_file=Path(get("SUDARSHAN_NSE_HOLIDAYS_FILE") or HOLIDAYS_FILE),
            extra_days_file=Path(get("SUDARSHAN_NSE_EXTRA_DAYS_FILE") or EXTRA_TRADING_DAYS_FILE),
        )
