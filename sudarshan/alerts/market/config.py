"""Settings for market alerts (M10.1), read once from the environment at start.

The unit reads ``/etc/sudarshan/alerts.env`` (cross-design R3: the release ships the default the
deploy writes when the key is absent; the owner's value is kept). Market alerts are off unless
``SUDARSHAN_MARKET_ALERTS=on``; the process exits only when they and order alerts are both off.

| setting | default |
|---|---|
| ``SUDARSHAN_MARKET_ALERTS`` | ``off`` |
| ``SUDARSHAN_ALERTS_HTTP_PORT`` | ``5582`` (the sealed local API, 127.0.0.1 only) |
| ``SUDARSHAN_ALERTS_FEED_URL`` | ``http://127.0.0.1:5580`` (loopback only) |
| ``SUDARSHAN_ALERTS_FEED_ZMQ`` | ``tcp://127.0.0.1:5581`` (loopback only) |
| ``SUDARSHAN_MARKET_ALERTS_MAX_INSTRUMENTS`` | ``200`` (at most 300, the feed's quota) |
| ``SUDARSHAN_MARKET_ALERTS_MAX_ACTIVE`` | ``500`` |
| ``SUDARSHAN_MARKET_ALERTS_KEEP_DAYS`` | ``100`` (never under 90) |
| ``SUDARSHAN_MARKET_ALERTS_REPLAY`` | ``0`` (tests only: trust a replay's health) |
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from sudarshan.alerts.config import ConfigError, _switch, is_loopback_url

FEED_URL = "http://127.0.0.1:5580"
FEED_ZMQ = "tcp://127.0.0.1:5581"
HTTP_PORT = 5582
#: The feed's quota for this client (cross-design R1); the service keeps under its own ceiling.
FEED_QUOTA = 300


def market_on(env: Mapping[str, str] | None = None) -> bool:
    """The switch alone, read before anything else (ConfigError for a value that is neither)."""
    return _switch(os.environ if env is None else env, "SUDARSHAN_MARKET_ALERTS", False)


def feed_url_of(env: Mapping[str, str] | None = None) -> str:
    """The feed's address with market alerts off, where nothing else is checked: the setting
    when it points at this server, else the default."""
    url = ((os.environ if env is None else env).get("SUDARSHAN_ALERTS_FEED_URL") or "").rstrip("/")
    return url if url.startswith("http://") and is_loopback_url(url) else FEED_URL


def _number(env: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number from {low} to {high}") from None
    if not low <= value <= high:
        raise ConfigError(f"{name} must be a whole number from {low} to {high}")
    return value


@dataclass(frozen=True)
class MarketSettings:
    enabled: bool
    http_port: int = HTTP_PORT
    feed_url: str = FEED_URL
    feed_zmq: str = FEED_ZMQ
    max_instruments: int = 200
    max_active: int = 500
    keep_days: int = 100
    replay: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> MarketSettings:
        env = os.environ if env is None else env
        feed_url = (env.get("SUDARSHAN_ALERTS_FEED_URL") or FEED_URL).rstrip("/")
        if not (feed_url.startswith("http://") and is_loopback_url(feed_url)):
            raise ConfigError(
                "SUDARSHAN_ALERTS_FEED_URL must be http:// on this server (127.0.0.1)"
            )
        feed_zmq = (env.get("SUDARSHAN_ALERTS_FEED_ZMQ") or FEED_ZMQ).strip()
        zmq = urlsplit(feed_zmq)
        if zmq.scheme != "tcp" or zmq.hostname != "127.0.0.1" or not zmq.port:
            raise ConfigError("SUDARSHAN_ALERTS_FEED_ZMQ must be tcp://127.0.0.1:<port>")
        return cls(
            enabled=market_on(env),
            http_port=_number(env, "SUDARSHAN_ALERTS_HTTP_PORT", HTTP_PORT, 1, 65535),
            feed_url=feed_url,
            feed_zmq=feed_zmq,
            max_instruments=_number(
                env, "SUDARSHAN_MARKET_ALERTS_MAX_INSTRUMENTS", 200, 1, FEED_QUOTA
            ),
            max_active=_number(env, "SUDARSHAN_MARKET_ALERTS_MAX_ACTIVE", 500, 1, 2000),
            keep_days=_number(env, "SUDARSHAN_MARKET_ALERTS_KEEP_DAYS", 100, 90, 3650),
            replay=_switch(env, "SUDARSHAN_MARKET_ALERTS_REPLAY", False),
        )
