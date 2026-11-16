"""Settings for sudarshan-signals (T5, design 3.5), read once at start.

The switch ``SUDARSHAN_SIGNALS`` is set by the release in OpenAlgo's ``.env``
(``ops/deploy/openalgo-env.conf``), which the service reads for it, for any setting below and for
``API_KEY_PEPPER`` (its seal keys). A value in the process environment wins over the same name in
``.env``; the unit names no environment file of its own. Nothing secret is a setting: the pepper is returned by
``read_pepper`` and kept out of ``Settings``. The publisher's account number is not a setting either:
it is the set-up's file (``/etc/sudarshan/signals.env``), read again while it is missing, so a set-up
run after the service started needs no restart.

| setting | default |
|---|---|
| ``SUDARSHAN_SIGNALS`` | ``off`` (the release sets it) |
| ``SUDARSHAN_SIGNALS_HTTP_PORT`` | ``5588`` (127.0.0.1 only) |
| ``SUDARSHAN_SIGNALS_LETTERBOX`` | ``/var/spool/sudarshan-signals/in`` |
| ``SUDARSHAN_SIGNALS_SETUP_FILE`` | ``/etc/sudarshan/signals.env`` |
| ``SUDARSHAN_SIGNALS_OPENALGO_URL`` | ``http://127.0.0.1:5000`` (the push; loopback only) |
| ``SUDARSHAN_SIGNALS_ALERTS_URL`` | ``http://127.0.0.1:5582`` (the relay; loopback only) |
| ``SUDARSHAN_SIGNALS_FEED_URL`` | ``http://127.0.0.1:5580`` (the index marks; loopback only) |
| ``SUDARSHAN_NSE_HOLIDAYS_FILE`` / ``SUDARSHAN_NSE_EXTRA_DAYS_FILE`` | the calendar files the other services read |
| ``SUDARSHAN_SIGNALS_LOG_LEVEL`` | ``INFO`` |
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

from sudarshan.autologin.schedule import EXTRA_TRADING_DAYS_FILE, HOLIDAYS_FILE
from sudarshan.ops.release import DEPLOYED_TAG_FILE
from sudarshan.signals import contract as c
from sudarshan.vault import VaultKeyError, pepper_from

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_HOST = "127.0.0.1"
OPENALGO_URL = f"http://{LOCAL_HOST}:5000"
ALERTS_URL = f"http://{LOCAL_HOST}:{c.ALERTS_PORT}"
FEED_URL = f"http://{LOCAL_HOST}:5580"
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_ON = {"1", "on", "true", "yes"}
_OFF = {"0", "off", "false", "no"}


class ConfigError(ValueError):
    """The service cannot run with these settings; the message names the setting and the fix."""


def setup_logging(env: Mapping[str, str] | None = None) -> None:
    """Our format on the system journal, at the level the setting names (states and counts only:
    never TRINETRA's text, a key or a token)."""
    name = (env if env is not None else os.environ).get("SUDARSHAN_SIGNALS_LOG_LEVEL", "INFO")
    level = getattr(logging, name.strip().upper(), logging.INFO)
    logging.basicConfig(level=level, format=LOG_FORMAT, force=True)
    for library in ("httpx", "httpcore", "sqlalchemy"):
        logging.getLogger(library).setLevel(logging.WARNING)


def switch(env: Mapping[str, str], name: str, default: bool) -> bool:
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


def _loopback_url(env: Mapping[str, str], name: str, default: str) -> str:
    url = (env.get(name) or default).strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme != "http" or (parts.hostname or "") not in LOOPBACK_HOSTS:
        raise ConfigError(f"{name} must be http:// on this server (127.0.0.1)")
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
    """OpenAlgo's ``API_KEY_PEPPER``; ConfigError in words if it is missing or too short."""
    try:
        return pepper_from(env)
    except VaultKeyError as exc:
        raise ConfigError(f"{exc}. Check API_KEY_PEPPER in OpenAlgo's .env") from exc


@dataclass(frozen=True)
class Settings:
    enabled: bool
    http_port: int = c.PORT
    letterbox: Path = Path(c.LETTERBOX)
    setup_file: Path = Path(c.SETUP_FILE)
    state_dir: Path = Path(c.STATE_DIR)
    openalgo_url: str = OPENALGO_URL
    alerts_url: str = ALERTS_URL
    feed_url: str = FEED_URL
    log_level: str = "INFO"
    app_dir: Path = REPO_ROOT
    env_file: Path = REPO_ROOT / ".env"
    deployed_tag_file: Path = DEPLOYED_TAG_FILE
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
        state_dir = get("SUDARSHAN_SIGNALS_STATE_DIR") or get("STATE_DIRECTORY") or c.STATE_DIR
        return cls(
            enabled=switch(env, c.SWITCH_ENV, False),
            http_port=_number(env, "SUDARSHAN_SIGNALS_HTTP_PORT", c.PORT, 1, 65535),
            letterbox=Path(get("SUDARSHAN_SIGNALS_LETTERBOX") or c.LETTERBOX),
            setup_file=Path(get("SUDARSHAN_SIGNALS_SETUP_FILE") or c.SETUP_FILE),
            state_dir=Path(state_dir.split(":")[0]),
            openalgo_url=_loopback_url(env, "SUDARSHAN_SIGNALS_OPENALGO_URL", OPENALGO_URL),
            alerts_url=_loopback_url(env, "SUDARSHAN_SIGNALS_ALERTS_URL", ALERTS_URL),
            feed_url=_loopback_url(env, "SUDARSHAN_SIGNALS_FEED_URL", FEED_URL),
            log_level=(get("SUDARSHAN_SIGNALS_LOG_LEVEL") or "INFO").strip().upper(),
            app_dir=app_dir,
            env_file=Path(get("SUDARSHAN_OPENALGO_ENV_FILE") or app_dir / ".env"),
            deployed_tag_file=Path(get("SUDARSHAN_DEPLOYED_TAG_FILE") or DEPLOYED_TAG_FILE),
            holidays_file=Path(get("SUDARSHAN_NSE_HOLIDAYS_FILE") or HOLIDAYS_FILE),
            extra_days_file=Path(get("SUDARSHAN_NSE_EXTRA_DAYS_FILE") or EXTRA_TRADING_DAYS_FILE),
        )
