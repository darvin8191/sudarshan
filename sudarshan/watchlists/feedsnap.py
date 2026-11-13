"""The feed's last ticks, for a follow row whose page has no price (M8.10, W15).

``GET /snapshot?keys=`` of ``sudarshan-feed`` on the loopback, one call for every key a request
needs, through the shared httpx client with an explicit 0.5 s read timeout and no retry. Anything
but a clean answer (feed down, slow, wrong shape) is an empty answer: the row then waits or says
its last price, and the request is never held past the timeout. Request-driven; no thread, timer
or loop. The failure is logged once a minute, never per request.
"""

from __future__ import annotations

import math
import os

import httpx

from utils.httpx_client import get_httpx_client
from utils.logging import get_logger
from utils.thread_safe_cache import MISSING, LockedTTLCache

logger = get_logger(__name__)

PORT_ENV = "SUDARSHAN_FEED_HTTP_PORT"
DEFAULT_PORT = 5580
TIMEOUT_S = 0.5
MAX_KEYS = 100
_TIMEOUT = httpx.Timeout(TIMEOUT_S, connect=0.25, pool=0.25)
_said = LockedTTLCache(maxsize=4, ttl=60)


def _url() -> str:
    raw = (os.environ.get(PORT_ENV) or "").strip()
    port = int(raw) if raw.isascii() and raw.isdigit() and 0 < int(raw) < 65536 else DEFAULT_PORT
    return f"http://127.0.0.1:{port}/snapshot"


def _warn_once(kind: str, message: str) -> None:
    with _said.lock:
        first = _said.get(kind, MISSING) is MISSING
        if first:
            _said[kind] = True
    if first:
        logger.warning(message)


def read(keys: list[str]) -> dict[str, tuple[float, int | None]]:
    """``{instrument key: (last price, epoch ms)}`` for the keys the feed has a tick for."""
    keys = list(dict.fromkeys(k for k in keys if k))[:MAX_KEYS]
    if not keys:
        return {}
    try:
        answer = get_httpx_client().get(
            _url(), params={"keys": ",".join(keys)}, timeout=_TIMEOUT, follow_redirects=False
        )
        if answer.status_code != 200:
            _warn_once("status", "Watchlists: the feed did not give its last ticks")
            return {}
        ticks = answer.json().get("ticks")
    except (httpx.HTTPError, ValueError, AttributeError):
        _warn_once("call", "Watchlists: the feed did not answer for the last ticks")
        return {}
    out: dict[str, tuple[float, int | None]] = {}
    for key, tick in (ticks or {}).items() if isinstance(ticks, dict) else ():
        if not isinstance(tick, dict):
            continue
        last = tick.get("ltp")
        if isinstance(last, bool) or not isinstance(last, int | float):
            continue
        if not math.isfinite(last) or last <= 0:
            continue
        at = tick.get("ltt") or tick.get("ts_recv")
        out[key] = (float(last), int(at) if isinstance(at, int | float) and at > 0 else None)
    return out
