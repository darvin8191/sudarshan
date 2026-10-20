"""sudarshan-feed: hold ONE Upstox Feed V3 socket and fan it out on 127.0.0.1 (ZMQ + HTTP).

Socket lifecycle (lessons carried over from broker/upstox/streaming/upstox_client.py):
- A fresh authorize for every connect: the authorized URI is single-use and short-lived.
- The token file is re-read for every attempt, so a replaced Analytics Token is picked up.
- 401 from authorize means the token was rejected: back off long (it is valid for a year; never
  hammer) but retry at once when the token file changes.
- A refused upgrade (403) most likely means the per-user connection limit (Basic 2 / Plus 5
  sockets; TRINETRA holds one; a socket left behind by a crash counts until Upstox times it out).
- Every close sends a close frame, and the socket is held only inside the market window.
- The backoff ladder resets only after a socket stayed up STABLE_AFTER_S, so an "accept, then
  drop" loop can never become a tight reconnect storm.
- The socket's age is logged on every drop (open question U9: drops near 600 s with 500+ keys).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import random
import re
import signal
import time
from urllib.parse import urlsplit

import httpx
import websockets
import zmq
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus, InvalidURI

from . import __version__, protocol
from .bus import HEALTH_TOPIC, Publisher
from .config import Settings, is_loopback_url, setup_logging
from .http_api import start_http
from .store import SnapshotStore, TickRecorder
from .subscriptions import Subscriptions, validate
from .window import MarketWindow, iso

log = logging.getLogger("sudarshan.feed")

# Timings are module constants so the tests can shrink them.
BACKOFF_BASE_S, BACKOFF_CAP_S = 2.0, 300.0
STABLE_AFTER_S = 60.0  # a socket that lived this long resets the backoff ladder
REFUSED_FLOOR_S = 60.0  # after a 403: give a stale socket time to expire at Upstox
TOKEN_REJECTED_S = (600.0, 1800.0, 3600.0)  # after a 401 (unless the token file changes)
NO_TOKEN_S = 60.0
TOKEN_POLL_S = 5.0  # how often a backoff checks whether the token file changed
CLOSED_POLL_S = 30.0
WATCH_EVERY_S = 1.0
STALL_S = 120.0  # no message for this long inside 09:15-15:30 forces a reconnect
SNAPSHOT_EVERY_S, FLUSH_EVERY_S, HEALTH_EVERY_S = 30.0, 3.0, 5.0
SNAPSHOT_KEEP_DAYS = 30  # an unsubscribed key's last tick is kept this long
SUB_BATCH, SUB_PAUSE_S = 100, 0.1
AUTHORIZE_PATH = "/v3/feed/market-data-feed/authorize"
WS_OPTIONS = {
    "open_timeout": 10, "close_timeout": 5, "ping_interval": 20, "ping_timeout": 20,
    "max_size": 16 * 2**20, "max_queue": 512,
}  # fmt: skip
WINDOW_CLOSED, STOPPING = "market window closed", "service stopping"
STATS = ("connects", "failed_attempts", "messages", "ticks", "decode_errors")
HINTS = {  # appended to the error in logs and shown as last_error.hint in /health
    "token_rejected": "The Analytics Token in the token file is expired, regenerated or wrong. "
    "Put the current one there: retries are slow (10 min, 30 min, then hourly) but immediate "
    "when the file changes.",
    "upgrade_refused": "Most likely the per-user WebSocket connection limit is reached: Basic "
    "plan 2 sockets, Plus 5; TRINETRA holds one, and a socket left open by a crash counts until "
    "Upstox times it out. Less likely: the token lacks market-data permission.",
    "authorize_refused": "Connection limit, missing market-data permission, or rate limiting.",
    "no_token": "The token file is missing, unreadable, empty or malformed.",
}
_URL_QUERY = re.compile(r"(\w+://[^\s?#'\"]+)\?[^\s'\"]*")


class AuthorizeError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


class Throttle:
    """Logs the first occurrence of each kind, then at most once per ``every`` seconds."""

    def __init__(self, every: float = 300.0):
        self.every, self._seen = every, {}

    def __call__(self, level: int, kind: str, message: str, exc_info: bool = False) -> None:
        now = time.monotonic()
        last, quiet = self._seen.get(kind, (None, 0))
        if last is not None and now - last < self.every:
            self._seen[kind] = (last, quiet + 1)
            return
        suffix = f" ({quiet} similar suppressed)" if quiet else ""
        log.log(level, "%s%s", message, suffix, exc_info=exc_info)
        self._seen[kind] = (now, 0)


def _upstox_reason(resp: httpx.Response) -> str:
    """errorCode and message from an Upstox error body."""
    try:
        err = (resp.json().get("errors") or [{}])[0]
        text = f"{err.get('errorCode', '')} {err.get('message', '')}".strip()
    except (ValueError, AttributeError, LookupError, TypeError):
        text = resp.text.strip()
    return f": {text[:160]}" if text else ""


def _age(start: float | None, now: float) -> float | None:
    return None if start is None else round(now - start, 1)


class FeedService:
    def __init__(self, cfg: Settings, clock=time.time):
        self.cfg, self.clock = cfg, clock
        self.window = MarketWindow(cfg.holidays_file, cfg.window, cfg.always_on)
        self.subs = Subscriptions.load(cfg.keys_file, cfg.state_dir / "subscriptions.json")
        self.store = SnapshotStore(cfg.state_dir / "snapshot.json")
        self.recorder = None
        if cfg.record:
            cap, keep = cfg.record_max_mb << 20, cfg.record_keep_days
            self.recorder = TickRecorder(cfg.state_dir / "ticks", cap, keep, clock)
        self.pub: Publisher | None = None
        self.http: asyncio.Server | None = None
        self._client: httpx.AsyncClient | None = None
        self._stop = asyncio.Event()
        # Subscription changes and the resubscribe after a connect never interleave.
        self._send_lock = asyncio.Lock()
        self._throttle = Throttle()
        self._attempt_task: asyncio.Task | None = None
        self._ws = None
        self._live = False  # the desired set has been sent on the current socket
        self._state, self._detail = "starting", ""
        self._token = ""  # kept only to scrub it out of any text that is logged
        self._token_refusals = 0
        self._connected_at: float | None = None  # monotonic
        self._connected_since: float | None = None  # wall clock
        self._last_msg: float | None = None  # monotonic
        self._last_session_s = 0.0
        self._close_reason: str | None = None
        self._next_retry_at: float | None = None
        self._seen: set[str] = set()  # keys that sent data on the current socket
        self._market_status: dict[str, str] = {}
        self._last_error: dict | None = None
        self._last_disconnect: dict | None = None
        self._stats = dict.fromkeys(STATS, 0)

    # ---- lifecycle -------------------------------------------------------------------------
    async def run(self) -> None:
        """Serve until stop(). A taken port or a bad keys file raises at start."""
        loaded = self.store.load()
        self._prune_snapshot()
        async with contextlib.AsyncExitStack() as stack:
            self.pub = Publisher(self.cfg.zmq_port)
            stack.callback(self.pub.close)
            self.http = await start_http(self, self.cfg.http_port)
            stack.push_async_callback(self._close_http)
            self._client = httpx.AsyncClient(timeout=10.0)
            stack.push_async_callback(self._client.aclose)
            log.info(
                "sudarshan-feed %s: HTTP 127.0.0.1:%d, ZMQ %s, subscriptions %s, %d snapshot ticks",
                __version__, self.http_port, self.pub.endpoint, self.subs.counts(), loaded,
            )  # fmt: skip
            if not self._token_readable():  # say so now, not first at 08:58
                log.warning("token file %s is missing or not readable", self.cfg.token_file)
            jobs = ((SNAPSHOT_EVERY_S, self.store.save), (FLUSH_EVERY_S, self._flush_recorder),
                    (HEALTH_EVERY_S, self._publish_health))  # fmt: skip
            tasks = [asyncio.create_task(self._every(period, fn)) for period, fn in jobs]
            try:
                await self._supervise()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                for fn in (self.store.save, self._flush_recorder):
                    try:
                        fn()
                    except Exception:
                        log.exception("final save failed (%s)", fn.__qualname__)
                self._set_state("stopped")

    def stop(self, reason: str = "stop requested") -> None:
        if not self._stop.is_set():
            log.info("stopping: %s", reason)
            self._stop.set()
        # Mid-authorize or mid-handshake there is no socket to close yet: abandon the attempt.
        # With a socket, the session watchdog sends the close frame.
        if self._ws is None and self._attempt_task is not None:
            self._attempt_task.cancel()

    @property
    def http_port(self) -> int:
        return self.http.sockets[0].getsockname()[1]

    async def _close_http(self) -> None:
        self.http.close()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.http.wait_closed(), 2.0)

    async def _every(self, period: float, fn) -> None:
        while True:
            await asyncio.sleep(period)
            try:
                fn()
            except Exception as exc:
                name = fn.__qualname__
                self._throttle(logging.ERROR, name, f"{name} failed: {exc!r}", exc_info=True)

    def _flush_recorder(self) -> None:
        if self.recorder:
            self.recorder.flush()

    def _prune_snapshot(self) -> None:
        cutoff_ms = int((self.clock() - SNAPSHOT_KEEP_DAYS * 86400) * 1000)
        if n := self.store.prune(self.subs.desired(), cutoff_ms):
            log.info("snapshot: forgot %d unsubscribed keys idle %d days", n, SNAPSHOT_KEEP_DAYS)

    async def _sleep(self, seconds: float) -> None:
        """Sleep, returning early when stop() is called."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), seconds)

    # ---- connection supervisor ----------------------------------------------------------------
    async def _supervise(self) -> None:
        failures = 0
        while not self._stop.is_set():
            now = self.clock()
            is_open, why = self.window.status(now)
            if not is_open:
                failures = 0
                if self._state != "closed":  # once per close (normally once a day)
                    self._prune_snapshot()
                self._set_state("closed", why)
                await self._sleep(min(CLOSED_POLL_S, max(1.0, self.window.next_open(now) - now)))
                continue
            token = self._read_token()
            if token is None:
                self._fail("no_token", f"no usable token in {self.cfg.token_file}")
                await self._wait_retry(NO_TOKEN_S, "no_token", watch_token=True)
                continue
            self._attempt_task = asyncio.create_task(self._attempt(token))
            try:
                outcome = await self._attempt_task
            except asyncio.CancelledError:
                if not self._stop.is_set():
                    raise
                break
            except Exception:  # a bug must cost one attempt, never the service
                log.exception("connection attempt failed unexpectedly")
                outcome = "error"
            finally:
                self._attempt_task = None
            if self._stop.is_set():
                break
            if outcome == "window":
                continue
            failures = 1 if self._last_session_s >= STABLE_AFTER_S else failures + 1
            rejected = outcome == "token_rejected"
            delay = self._retry_delay(outcome, failures)
            await self._wait_retry(delay, "token_rejected" if rejected else "backoff", rejected)

    def _retry_delay(self, outcome: str, failures: int) -> float:
        if outcome == "token_rejected":
            step = min(max(self._token_refusals, 1), len(TOKEN_REJECTED_S))
            return TOKEN_REJECTED_S[step - 1]
        ceiling = min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** min(failures - 1, 16))
        delay = ceiling / 2 + random.uniform(0, ceiling / 2)  # "equal jitter"
        if outcome in ("upgrade_refused", "authorize_refused"):
            delay = max(delay, REFUSED_FLOOR_S)
        return delay

    async def _wait_retry(self, delay: float, state: str, watch_token: bool = False) -> None:
        self._next_retry_at = time.monotonic() + delay
        self._set_state(state, f"next attempt in {delay:.0f}s")
        mtime = self._token_mtime()
        try:
            while not self._stop.is_set():
                left = self._next_retry_at - time.monotonic()
                if left <= 0:
                    return
                await self._sleep(min(left, TOKEN_POLL_S))
                if watch_token and self._token_mtime() != mtime:
                    log.info("token file changed: retrying now")
                    return
        finally:
            self._next_retry_at = None

    def _read_token(self) -> str | None:
        """Re-read for every attempt so a replaced token is used. Never logged or exposed."""
        try:  # utf-8-sig: a file saved by a Windows editor may start with a BOM
            token = self.cfg.token_file.read_text(encoding="utf-8-sig").strip()
        except (OSError, UnicodeDecodeError):
            return None
        if len(token) < 16 or any(c.isspace() for c in token):
            return None
        self._token = token
        return token

    def _token_readable(self) -> bool:
        """Whether the file can be read; its content is never looked at here."""
        return os.access(self.cfg.token_file, os.R_OK) and self.cfg.token_file.is_file()

    def _token_mtime(self) -> int | None:
        try:
            return self.cfg.token_file.stat().st_mtime_ns
        except OSError:
            return None

    async def _authorize(self, token: str) -> str:
        """GET .../authorize -> data.authorized_redirect_uri (fresh for every connect)."""
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        try:
            resp = await self._client.get(self.cfg.api_base + AUTHORIZE_PATH, headers=headers)
        except httpx.HTTPError as exc:
            message = f"authorize failed: {type(exc).__name__}: {exc}"
            raise AuthorizeError("network", message) from None
        status, why = resp.status_code, _upstox_reason(resp)
        if status == 401:
            self._token_refusals += 1
            raise AuthorizeError("token_rejected", f"token rejected by Upstox (HTTP 401{why})")
        if status != 200:
            kind = "authorize_refused" if status in (403, 429) else "authorize_failed"
            raise AuthorizeError(kind, f"authorize answered HTTP {status}{why}")
        try:
            data = resp.json().get("data") or {}
            uri = data.get("authorized_redirect_uri") or data.get("authorizedRedirectUri") or ""
        except (ValueError, AttributeError):
            uri = ""
        if not (uri.startswith("wss://") or (uri.startswith("ws://") and is_loopback_url(uri))):
            raise AuthorizeError("authorize_failed", "authorize answered without a wss:// URI")
        if self._token_refusals:
            log.info("Upstox accepts the token again (after %d refusals)", self._token_refusals)
            self._token_refusals = 0
        return uri

    async def _attempt(self, token: str) -> str:
        """One authorize -> connect -> session cycle; returns the outcome for the backoff."""
        self._last_session_s, self._close_reason = 0.0, None
        try:
            self._set_state("authorizing")
            uri = await self._authorize(token)
            self._set_state("connecting", urlsplit(uri)._replace(query="", fragment="").geturl())
            async with websockets.connect(uri, **WS_OPTIONS) as ws:
                self._ws = ws  # no await since connect returned: stop() sees it atomically
                return await self._session(ws, urlsplit(uri).hostname or "")
        except AuthorizeError as exc:
            return self._fail(exc.kind, str(exc))
        except InvalidStatus as exc:
            code = exc.response.status_code
            kind = "upgrade_refused" if code in (403, 429) else "upgrade_failed"
            return self._fail(kind, f"WebSocket upgrade refused with HTTP {code}")
        except ConnectionClosed as exc:  # no clean closing handshake (no close frame: 1006)
            ours = self._close_reason  # set when we closed and Upstox never answered
            code, reason = (exc.rcvd.code, exc.rcvd.reason) if exc.rcvd else (1006, "")
            self._disconnected(code, reason, ours or "dropped")
            return {WINDOW_CLOSED: "window", STOPPING: "stopped"}.get(ours, "dropped")
        except (OSError, TimeoutError, InvalidHandshake, InvalidURI) as exc:
            return self._fail("network", f"{type(exc).__name__}: {exc}")
        finally:
            self._ws, self._live = None, False
            if self._connected_at is not None:
                self._last_session_s = time.monotonic() - self._connected_at
            self._connected_at = self._connected_since = None

    async def _session(self, ws, host: str) -> str:
        self._connected_at, self._connected_since = time.monotonic(), self.clock()
        self._last_msg, self._close_reason = time.monotonic(), None
        self._seen.clear()
        self._stats["connects"] += 1
        self._set_state("connected", host)
        helpers = [asyncio.create_task(c) for c in (self._watchdog(ws), self._resubscribe(ws))]
        try:
            async for message in ws:
                self._on_message(message)
        finally:
            for task in helpers:
                task.cancel()
            await asyncio.gather(*helpers, return_exceptions=True)
        reason = self._close_reason
        self._disconnected(ws.close_code, ws.close_reason or "", reason or "closed by Upstox")
        return {WINDOW_CLOSED: "window", STOPPING: "stopped"}.get(reason, "closed")

    async def _watchdog(self, ws) -> None:
        """Closes the socket cleanly on stop, at the end of the window, or on a data stall."""
        while True:
            await self._sleep(WATCH_EVERY_S)
            now = self.clock()
            if self._stop.is_set():
                reason = STOPPING
            elif not self.window.status(now)[0]:
                reason = WINDOW_CLOSED
            elif self._stalled(now):
                reason = f"no data for {STALL_S:.0f}s"
            else:
                continue
            self._close_reason = reason
            log.info("closing the feed socket: %s", reason)
            await ws.close(1000, reason)  # waits for Upstox's close frame (close_timeout)
            return

    def _stalled(self, now: float) -> bool:
        if not (self._live and self.subs.desired() and self.window.in_core_session(now)):
            return False  # quiet is normal outside continuous trading or with nothing subscribed
        return time.monotonic() - self._last_msg > STALL_S

    async def _resubscribe(self, ws) -> None:
        """Upstox forgets every subscription when a socket closes: send the whole set again."""
        try:
            async with self._send_lock:
                groups = self.subs.by_mode()
                for mode, keys in groups.items():
                    await self._send(ws, "sub", keys, mode)
                self._live = True
            summary = ", ".join(f"{len(keys)} {mode}" for mode, keys in groups.items())
            log.info("subscribed %s on the new socket", summary or "nothing")
        except ConnectionClosed:
            pass  # the read loop reports the drop
        except Exception:
            log.exception("subscribing on the new socket failed; reconnecting")
            self._close_reason = "resubscribe failed"
            await ws.close(1000, self._close_reason)

    async def _send(self, ws, method: str, keys: list[str], mode: str | None = None) -> None:
        for start in range(0, len(keys), SUB_BATCH):
            if start:
                await asyncio.sleep(SUB_PAUSE_S)
            await ws.send(protocol.request(method, keys[start : start + SUB_BATCH], mode))

    # ---- data path ------------------------------------------------------------------------------
    def _on_message(self, message: bytes | str) -> None:
        self._last_msg = time.monotonic()
        self._stats["messages"] += 1
        if isinstance(message, str):  # Upstox answers a bad request with text
            text = self._scrub(message)[:300]
            self._throttle(logging.WARNING, "upstox-text", f"Upstox says: {text}")
            return
        try:
            kind, ticks, status = protocol.decode(message)
            changed = {s: v for s, v in status.items() if self._market_status.get(s) != v}
            if changed:
                log.info("market status: %s", ", ".join(f"{s} {v}" for s, v in changed.items()))
                self._market_status.update(changed)
            ts_recv, fresh = int(self.clock() * 1000), kind == "initial_feed"
            for key, partial in ticks.items():
                if partial:
                    self._publish(key, self.store.update(key, partial, ts_recv, fresh))
        except Exception as exc:  # one bad frame must not take the socket down
            self._stats["decode_errors"] += 1
            what = f"bad feed frame ({len(message)} bytes): {exc!r}"
            self._throttle(logging.WARNING, "decode", what, exc_info=True)

    def _publish(self, key: str, tick: dict) -> None:
        line = json.dumps(tick, separators=(",", ":"), ensure_ascii=False).encode()
        self.pub.send(key, line)
        if self.recorder:
            self.recorder.add(line)
        self._seen.add(key)
        self._stats["ticks"] += 1

    # ---- state, errors, health --------------------------------------------------------------
    def _set_state(self, state: str, detail: str = "") -> None:
        if (state, detail) != (self._state, self._detail):
            log.info("state %s -> %s%s", self._state, state, f" ({detail})" if detail else "")
            self._state, self._detail = state, detail
            self._publish_health()

    def _scrub(self, text: str) -> str:
        """Remove the token and URL query strings (the authorized URI carries a one-time code)."""
        if self._token:
            text = text.replace(self._token, "<token>")
        return _URL_QUERY.sub(r"\1?<redacted>", text)

    def _fail(self, kind: str, message: str) -> str:
        message, hint = self._scrub(message), HINTS.get(kind)
        self._last_error = {"at": iso(self.clock()), "kind": kind, "message": message, "hint": hint}
        self._stats["failed_attempts"] += 1
        level = logging.ERROR if kind in HINTS else logging.WARNING
        self._throttle(level, kind, message + (f". {hint}" if hint else ""))
        return kind

    def _disconnected(self, code, reason: str, how: str) -> None:
        age = time.monotonic() - self._connected_at if self._connected_at else 0.0
        code = None if code is None else int(code)
        self._last_disconnect = {
            "at": iso(self.clock()), "by": how, "code": code, "reason": self._scrub(reason),
            "socket_age_s": round(age, 1),
        }  # fmt: skip
        level = logging.INFO if how in (WINDOW_CLOSED, STOPPING) else logging.WARNING
        detail = f"code {code}" + (f", {self._scrub(reason)}" if reason else "")
        log.log(level, "feed socket closed after %.0fs: %s (%s)", age, how, detail)

    def _publish_health(self) -> None:
        if self.pub is not None:
            self.pub.send(HEALTH_TOPIC, json.dumps(self.health(), separators=(",", ":")).encode())

    def health(self) -> dict:
        now, mono = self.clock(), time.monotonic()
        is_open, why = self.window.status(now)
        missing = [k for k in self.subs.desired() if k not in self._seen] if self._live else []
        retry = None if self._next_retry_at is None else max(0.0, self._next_retry_at - mono)
        stats = self._stats | {"snapshot_keys": len(self.store.ticks)}
        return {
            "service": "sudarshan-feed",
            "version": __version__,
            "state": self._state,
            "detail": self._detail,
            "connected_since": iso(self._connected_since),
            "socket_age_s": _age(self._connected_at, mono),
            "last_message_age_s": _age(self._last_msg, mono),
            "next_retry_in_s": None if retry is None else round(retry, 1),
            "token_file_readable": self._token_readable(),
            "subscribed": self.subs.counts(),
            "subscriptions_live": self._live,
            "keys_without_data": {"count": len(missing), "sample": missing[:10]},
            "reconnects": max(0, self._stats["connects"] - 1),
            "last_error": self._last_error,
            "last_disconnect": self._last_disconnect,
            "market_window": {
                "open": is_open,
                "reason": why,
                "holidays": self.window.note,
                "next_open": None if is_open else iso(self.window.next_open(now)),
            },
            "market_status": self._market_status,
            "stats": stats | {"zmq_dropped": self.pub.dropped if self.pub else 0},
            "recorder": self.recorder.status() if self.recorder else None,
            "zmq": self.pub.endpoint if self.pub else None,
        }

    # ---- local API ------------------------------------------------------------------------------
    def snapshot(self, keys: list[str] | None) -> dict:
        if keys is None:
            return self.store.ticks
        return {key: self.store.ticks.get(key) for key in keys}

    def subscriptions(self) -> dict:
        return {"by_mode": self.subs.by_mode(), "counts": self.subs.counts()}

    async def subscribe(self, mode, keys) -> dict:
        keys = validate(mode, keys)
        async with self._send_lock:
            new, moved = self.subs.subscribe(mode, keys)  # CapError leaves everything unchanged
            for key in moved:
                self.store.reset(key)  # do not carry the old mode's fields into the new one
            live = await self._send_live((("sub", new), ("change_mode", moved)), mode)
        log.info("API subscribe %s: %d new, %d moved (live: %s)", mode, len(new), len(moved), live)
        counts = self.subs.counts()
        return {"ok": True, "new": len(new), "mode_changed": len(moved), "applied_live": live,
                "subscribed": counts}  # fmt: skip

    async def unsubscribe(self, keys) -> dict:
        keys = validate(None, keys, need_mode=False)
        async with self._send_lock:
            gone = self.subs.unsubscribe(keys)
            live = await self._send_live((("unsub", gone),), None)
        log.info("API unsubscribe: %d removed (live: %s)", len(gone), live)
        counts = self.subs.counts()
        return {"ok": True, "removed": len(gone), "applied_live": live, "subscribed": counts}

    async def _send_live(self, batches, mode: str | None) -> bool:
        """Send changes on the live socket; False means they apply at the next connect."""
        ws = self._ws
        if ws is None or not self._live:
            return False
        try:
            for method, keys in batches:
                if keys:
                    await self._send(ws, method, keys, mode)
        except ConnectionClosed:
            return False
        return True


async def _serve(cfg: Settings) -> None:
    service = FeedService(cfg)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, service.stop, f"signal {sig.name}")
    await service.run()


def main() -> int:
    setup_logging()
    try:
        asyncio.run(_serve(Settings.from_env()))
    except (OSError, ValueError, zmq.ZMQError) as exc:  # taken port, bad keys file or settings
        log.error("sudarshan-feed cannot run: %s", exc)
        return 2
    return 0
