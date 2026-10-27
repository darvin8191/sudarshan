"""Settings for sudarshan-feed, read once from the environment (table in README.md)."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from sudarshan.autologin.schedule import EXTRA_TRADING_DAYS_FILE, parse_window

PKG_DIR = Path(__file__).resolve().parent
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
TOKEN_FILE = "/etc/sudarshan/upstox_analytics_token"
HOLIDAYS_FILE = "/etc/sudarshan/nse_holidays.txt"
#: 2 minutes before the 09:00 pre-open to 10 minutes after the 15:40 F&O close (window.py).
DEFAULT_WINDOW = "08:58-15:50"
STATE_DIR = "/var/lib/sudarshan-feed"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging() -> None:
    level = os.environ.get("SUDARSHAN_FEED_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=getattr(logging, level, logging.INFO), format=LOG_FORMAT)
    # Library debug output can carry the authorized URI (one-time code) or request headers.
    for name in ("websockets", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def is_loopback_url(url: str) -> bool:
    return (urlsplit(url).hostname or "") in LOOPBACK_HOSTS


def _flag(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    token_file: Path
    keys_file: Path
    state_dir: Path
    holidays_file: Path
    extra_days_file: Path  # sessions on otherwise closed days, as auto-login and the alerts read
    api_base: str
    http_port: int
    zmq_port: int
    always_on: bool
    window: tuple[int, int]  # IST minutes after midnight: [open, close)
    record: bool
    record_max_mb: int
    record_keep_days: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        get = env.get
        api_base = get("SUDARSHAN_UPSTOX_API_BASE", "https://api.upstox.com").rstrip("/")
        # The bearer token travels to this base URL: plain http only to a local test server.
        if not api_base.startswith("https://") and not is_loopback_url(api_base):
            raise ValueError("SUDARSHAN_UPSTOX_API_BASE must use https://")
        try:  # the calendar files' own reading of HH:MM-HH:MM
            window = parse_window(get("SUDARSHAN_FEED_WINDOW", DEFAULT_WINDOW))
        except ValueError:
            raise ValueError(
                "SUDARSHAN_FEED_WINDOW must be HH:MM-HH:MM with open before close"
            ) from None
        state_dir = get("SUDARSHAN_FEED_STATE_DIR") or get("STATE_DIRECTORY") or STATE_DIR
        return cls(
            token_file=Path(get("SUDARSHAN_UPSTOX_TOKEN_FILE", TOKEN_FILE)),
            keys_file=Path(get("SUDARSHAN_FEED_KEYS") or PKG_DIR / "default_keys.txt"),
            # systemd's StateDirectory= may list several paths separated by ':'; the first is ours.
            state_dir=Path(state_dir.split(":")[0]),
            holidays_file=Path(get("SUDARSHAN_NSE_HOLIDAYS_FILE", HOLIDAYS_FILE)),
            extra_days_file=Path(get("SUDARSHAN_NSE_EXTRA_DAYS_FILE") or EXTRA_TRADING_DAYS_FILE),
            api_base=api_base,
            http_port=int(get("SUDARSHAN_FEED_HTTP_PORT", "5580")),
            zmq_port=int(get("SUDARSHAN_FEED_ZMQ_PORT", "5581")),
            always_on=_flag(get("SUDARSHAN_FEED_ALWAYS_ON", "0")),
            window=window,
            record=_flag(get("SUDARSHAN_FEED_RECORD", "1")),
            record_max_mb=int(get("SUDARSHAN_FEED_RECORD_MAX_MB", "512")),
            record_keep_days=int(get("SUDARSHAN_FEED_RECORD_KEEP_DAYS", "10")),
        )
