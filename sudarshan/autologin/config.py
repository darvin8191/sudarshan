"""Settings for the auto-login worker (environment table in README.md).

Secrets are never read from the environment: the TOTP key and PIN come from the vault, and the
Upstox API key plus the pepper come from OpenAlgo's own ``.env`` (read here with python-dotenv,
because its ``KEY = 'value'`` lines are not systemd EnvironmentFile syntax).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

from sudarshan.vault.store import DB_FILENAME, VAULT_DIR

from .schedule import DEFAULT_WINDOW, EXTRA_TRADING_DAYS_FILE, HOLIDAYS_FILE, parse_window
from .upstox import UPSTOX_API_BASE

REPO_ROOT = Path(__file__).resolve().parents[2]
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging() -> None:
    level = os.environ.get("SUDARSHAN_AUTOLOGIN_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=getattr(logging, level, logging.INFO), format=LOG_FORMAT)
    # Library debug output can carry request headers (the x-api-key) or bodies.
    for name in ("httpx", "httpcore", "hpack", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)


def is_loopback_url(url: str) -> bool:
    return (urlsplit(url).hostname or "") in LOOPBACK_HOSTS


class ConfigError(ValueError):
    """The worker cannot run with these settings (bad URL, unreadable .env, ...)."""


@dataclass(frozen=True)
class Settings:
    app_dir: Path
    env_file: Path
    vault_path: Path
    holidays_file: Path
    extra_days_file: Path
    openalgo_url: str
    upstox_base: str
    window: tuple[int, int]

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        get = env.get
        app_dir = Path(get("SUDARSHAN_OPENALGO_DIR") or REPO_ROOT)
        upstox_base = get("SUDARSHAN_UPSTOX_API_BASE", UPSTOX_API_BASE).rstrip("/")
        # The PIN and TOTP travel to this base URL: plain http only to a local test server.
        if not upstox_base.startswith("https://") and not is_loopback_url(upstox_base):
            raise ConfigError("SUDARSHAN_UPSTOX_API_BASE must use https://")
        openalgo_url = get("SUDARSHAN_OPENALGO_URL", "http://127.0.0.1:5000").rstrip("/")
        if not is_loopback_url(openalgo_url):
            raise ConfigError("SUDARSHAN_OPENALGO_URL must point at this server (127.0.0.1)")
        return cls(
            app_dir=app_dir,
            env_file=Path(get("SUDARSHAN_OPENALGO_ENV_FILE") or app_dir / ".env"),
            vault_path=Path(get("SUDARSHAN_VAULT_DB") or app_dir / "db" / VAULT_DIR / DB_FILENAME),
            holidays_file=Path(get("SUDARSHAN_NSE_HOLIDAYS_FILE") or HOLIDAYS_FILE),
            extra_days_file=Path(get("SUDARSHAN_NSE_EXTRA_DAYS_FILE") or EXTRA_TRADING_DAYS_FILE),
            openalgo_url=openalgo_url,
            upstox_base=upstox_base,
            window=parse_window(get("SUDARSHAN_AUTOLOGIN_WINDOW") or DEFAULT_WINDOW),
        )


def read_openalgo_env(path: Path) -> dict[str, str]:
    """OpenAlgo's .env as a dict (values unquoted). Raises ConfigError if it cannot be read."""
    try:
        with path.open(encoding="utf-8") as handle:
            values = dotenv_values(stream=handle)
    except OSError as exc:
        raise ConfigError(f"cannot read {path}") from exc
    return {key: (value or "").strip() for key, value in values.items() if key}
