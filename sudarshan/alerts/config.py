"""Settings for sudarshan-alerts, read once from the environment (table in README.md).

The unit reads ``/etc/sudarshan/alerts.env`` if it exists; a missing file means the defaults.
Nothing secret is set here: the API key is read from OpenAlgo's database (owner.py).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from dotenv import dotenv_values

from sudarshan.autologin.schedule import EXTRA_TRADING_DAYS_FILE, HOLIDAYS_FILE, parse_window

REPO_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = "/var/lib/sudarshan-alerts"
#: Trading days only, 15 minutes before the 09:00 pre-open to 20 minutes after the 15:40 F&O
#: close (IST); a special session's own window gets the same margins (window.py).
DEFAULT_WINDOW = "08:45-16:00"
PROXY_URL = "ws://127.0.0.1:8765"
OPENALGO_URL = "http://127.0.0.1:5000"
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
#: Which cancellations are sent: every one, or only those of an order that was part filled.
CANCELLED_CHOICES = ("all", "part-filled")
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_ON = {"1", "on", "true", "yes"}
_OFF = {"0", "off", "false", "no"}


class ConfigError(ValueError):
    """The service cannot run with these settings."""


def setup_logging() -> None:
    """Our format on the journal. Replaces the handlers OpenAlgo's logging module installs."""
    level = os.environ.get("SUDARSHAN_ALERTS_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=getattr(logging, level, logging.INFO), format=LOG_FORMAT, force=True)
    # Library debug output can carry request bodies or URLs; the API key travels in both.
    for name in ("websockets", "httpx", "httpcore", "sqlalchemy"):
        logging.getLogger(name).setLevel(logging.WARNING)


def is_loopback_url(url: str) -> bool:
    return (urlsplit(url).hostname or "") in LOOPBACK_HOSTS


def _switch(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = (env.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in _ON:
        return True
    if raw in _OFF:
        return False
    raise ConfigError(f"{name} must be on or off")


def switched_off(env: Mapping[str, str] | None = None) -> bool:
    """The kill switch, read before anything else: off means no other setting matters."""
    return not _switch(os.environ if env is None else env, "SUDARSHAN_ORDER_ALERTS", True)


def sqlite_path(database_url: str, app_dir: Path) -> Path | None:
    """The file behind a ``sqlite:///`` URL (relative to OpenAlgo's directory), else None."""
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return None
    raw = unquote(database_url[len(prefix) :].split("?", 1)[0])
    if raw.startswith("file:"):
        raw = raw[len("file:") :]
    path = Path(raw)
    return path if path.is_absolute() else app_dir / path


@dataclass(frozen=True)
class Settings:
    enabled: bool
    cancelled: str
    sandbox: bool
    app_dir: Path
    env_file: Path
    state_dir: Path
    holidays_file: Path
    extra_days_file: Path
    proxy_url: str
    openalgo_url: str
    window: tuple[int, int]  # IST minutes after midnight: [open, close)
    always_on: bool

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        get = env.get
        cancelled = (get("SUDARSHAN_ORDER_ALERTS_CANCELLED") or "all").strip().lower()
        if cancelled not in CANCELLED_CHOICES:
            raise ConfigError("SUDARSHAN_ORDER_ALERTS_CANCELLED must be all or part-filled")
        # The API key travels on both: plain ws:// and http:// only to this server.
        proxy_url = (get("SUDARSHAN_OPENALGO_WS_URL") or PROXY_URL).rstrip("/")
        openalgo_url = (get("SUDARSHAN_OPENALGO_URL") or OPENALGO_URL).rstrip("/")
        for name, url in (
            ("SUDARSHAN_OPENALGO_WS_URL", proxy_url),
            ("SUDARSHAN_OPENALGO_URL", openalgo_url),
        ):
            if not is_loopback_url(url):
                raise ConfigError(f"{name} must point at this server (127.0.0.1)")
        app_dir = Path(get("SUDARSHAN_OPENALGO_DIR") or REPO_ROOT)
        # systemd's StateDirectory= may list several paths separated by ':'; the first is ours.
        state_dir = get("SUDARSHAN_ALERTS_STATE_DIR") or get("STATE_DIRECTORY") or STATE_DIR
        try:
            window = parse_window(get("SUDARSHAN_ALERTS_WINDOW") or DEFAULT_WINDOW)
        except ValueError as exc:
            raise ConfigError(f"SUDARSHAN_ALERTS_WINDOW: {exc}") from None
        return cls(
            enabled=_switch(env, "SUDARSHAN_ORDER_ALERTS", True),
            cancelled=cancelled,
            sandbox=_switch(env, "SUDARSHAN_ORDER_ALERTS_SANDBOX", False),
            app_dir=app_dir,
            env_file=Path(get("SUDARSHAN_OPENALGO_ENV_FILE") or app_dir / ".env"),
            state_dir=Path(state_dir.split(":")[0]),
            holidays_file=Path(get("SUDARSHAN_NSE_HOLIDAYS_FILE") or HOLIDAYS_FILE),
            extra_days_file=Path(get("SUDARSHAN_NSE_EXTRA_DAYS_FILE") or EXTRA_TRADING_DAYS_FILE),
            proxy_url=proxy_url,
            openalgo_url=openalgo_url,
            window=window,
            always_on=_switch(env, "SUDARSHAN_ALERTS_ALWAYS_ON", False),
        )


def read_openalgo_env(path: Path) -> dict[str, str]:
    """OpenAlgo's .env as a dict (values unquoted). Raises ConfigError if it cannot be read."""
    try:
        with path.open(encoding="utf-8") as handle:
            values = dotenv_values(stream=handle)
    except OSError as exc:
        raise ConfigError(f"cannot read {path}") from exc
    return {key: (value or "").strip() for key, value in values.items() if key}
