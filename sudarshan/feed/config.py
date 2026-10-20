"""Settings for sudarshan-feed, read once from the environment (table in README.md)."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

PKG_DIR = Path(__file__).resolve().parent
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
TOKEN_FILE = "/etc/sudarshan/upstox_analytics_token"
HOLIDAYS_FILE = "/etc/sudarshan/nse_holidays.txt"
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


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.strip().split(":")
    value = int(hours) * 60 + int(minutes)
    if not 0 <= value <= 24 * 60:
        raise ValueError(f"not a time of day: {hhmm!r}")
    return value


def _flag(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    token_file: Path
    keys_file: Path
    state_dir: Path
    holidays_file: Path
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
        start, _, end = get("SUDARSHAN_FEED_WINDOW", "08:58-15:40").partition("-")
        window = (_minutes(start), _minutes(end))
        if window[0] >= window[1]:
            raise ValueError("SUDARSHAN_FEED_WINDOW must be HH:MM-HH:MM with open before close")
        state_dir = get("SUDARSHAN_FEED_STATE_DIR") or get("STATE_DIRECTORY") or STATE_DIR
        return cls(
            token_file=Path(get("SUDARSHAN_UPSTOX_TOKEN_FILE", TOKEN_FILE)),
            keys_file=Path(get("SUDARSHAN_FEED_KEYS") or PKG_DIR / "default_keys.txt"),
            # systemd's StateDirectory= may list several paths separated by ':'; the first is ours.
            state_dir=Path(state_dir.split(":")[0]),
            holidays_file=Path(get("SUDARSHAN_NSE_HOLIDAYS_FILE", HOLIDAYS_FILE)),
            api_base=api_base,
            http_port=int(get("SUDARSHAN_FEED_HTTP_PORT", "5580")),
            zmq_port=int(get("SUDARSHAN_FEED_ZMQ_PORT", "5581")),
            always_on=_flag(get("SUDARSHAN_FEED_ALWAYS_ON", "0")),
            window=window,
            record=_flag(get("SUDARSHAN_FEED_RECORD", "1")),
            record_max_mb=int(get("SUDARSHAN_FEED_RECORD_MAX_MB", "512")),
            record_keep_days=int(get("SUDARSHAN_FEED_RECORD_KEEP_DAYS", "10")),
        )
