"""The family bot's HTTP client: Telegram's Bot API, and no other place on the internet (M16.4;
design 2.6, 5).

Every request the client makes passes ``AllowList`` first: its scheme, host and port must be one
of ``ALLOWED`` (Telegram's API, and the two loopback services the design names: the portfolio on
5583 and the price feed on 5580), or it raises ``HostNotAllowed`` before a byte leaves this
process. A leaked bot token or a bug in the bot therefore cannot make this client reach OpenAlgo
(5000), its market-data bus (5555), its WebSocket proxy (8765) or any other host. Redirects are
never followed and the environment's proxy settings are never read.

The bot token is part of every request's path (Telegram's own scheme), so nothing here ever logs a
request's address, its parameters or an exception's text: a failure is logged by its kind only,
and the caller gets an ``Answer`` naming what happened (``OK``, ``REFUSED_TOKEN``, ``CONFLICT``,
``SLOW_DOWN``, ``BLOCKED``, ``BAD_REQUEST``, ``FAILING``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from . import safe_http

log = logging.getLogger("sudarshan.family")

API = "https://api.telegram.org"
#: The only places this client may reach: (scheme, host, port).
ALLOWED = frozenset(
    {("https", "api.telegram.org", 443), ("http", "127.0.0.1", 5583), ("http", "127.0.0.1", 5580)}
)
#: A token as BotFather shows it: the bot's number, a colon, and its secret.
TOKEN_RE = re.compile(r"[0-9]{5,16}:[A-Za-z0-9_-]{30,64}")
#: A bot's user name (the ``t.me/<name>`` of its link).
USERNAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{3,31}")
#: How long one getUpdates waits for a message (design 2.6: 50 s, one request in flight).
POLL_S = 50
MAX_TEXT = 4096
DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=10.0, pool=10.0)
POLL_TIMEOUT = httpx.Timeout(POLL_S + 15.0, connect=10.0, pool=10.0)

# What became of one call.
OK = "ok"
REFUSED_TOKEN = "refused_token"  # 401 or 404: Telegram does not know this token
CONFLICT = "conflict"  # 409: another program polls with this token, or a webhook is set
SLOW_DOWN = "slow_down"  # 429, with retry_after
BLOCKED = "blocked"  # 403: the member blocked the bot or deleted the chat
BAD_REQUEST = "bad_request"  # 400: the message was refused (a chat that is gone, say)
FAILING = "failing"  # 5xx, a timeout, no connection, an answer that is not Telegram's


class HideToken(logging.Filter):
    """httpx logs each request's address at INFO, and Telegram's addresses carry the token: this
    filter, on httpx's own logger, writes ``/bot<token>`` in its place, whatever level anyone sets
    that logger to while looking for a fault."""

    PATTERN = re.compile(r"/bot[0-9]{5,16}:[A-Za-z0-9_-]{20,}")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            text = record.getMessage()
        except Exception:  # a record that cannot be formatted is not ours to judge
            return True
        if self.PATTERN.search(text):
            record.msg, record.args = self.PATTERN.sub("/bot<token>", text), ()
        return True


def hide_tokens() -> None:
    """Put ``HideToken`` on httpx's logger once (the import of this module does it)."""
    target = logging.getLogger("httpx")
    if not any(isinstance(f, HideToken) for f in target.filters):
        target.addFilter(HideToken())


hide_tokens()


class HostNotAllowed(Exception):
    """A request to a place the family bot's client may not reach. It is raised before anything
    is sent, and it is not an httpx error, so no caller mistakes it for a network failure."""


class AllowList(httpx.AsyncBaseTransport):
    """A transport that sends a request only to one of ``allowed`` (scheme, host, port)."""

    def __init__(self, allowed: frozenset[tuple[str, str, int]],
                 inner: httpx.AsyncBaseTransport | None = None):  # fmt: skip
        self.allowed = frozenset(allowed)
        self.inner = inner if inner is not None else safe_http.transport(retries=0)

    def permits(self, url: httpx.URL) -> bool:
        port = url.port if url.port is not None else {"https": 443, "http": 80}.get(url.scheme)
        return (url.scheme, url.host, port) in self.allowed and not url.userinfo

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not self.permits(request.url):
            raise HostNotAllowed("the family bot may not reach that address")
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()


def make_http(inner: httpx.AsyncBaseTransport | None = None,
              allowed: frozenset[tuple[str, str, int]] = ALLOWED) -> httpx.AsyncClient:  # fmt: skip
    """The bot's client: the allow-list in front of every request, no redirect, no proxy from the
    environment. ``inner`` is the real transport (a test gives a fake Telegram)."""
    return httpx.AsyncClient(transport=AllowList(allowed, inner), trust_env=False,
                             follow_redirects=False, timeout=DEFAULT_TIMEOUT)  # fmt: skip


def token_ok(text: str | None) -> bool:
    return isinstance(text, str) and TOKEN_RE.fullmatch(text) is not None


@dataclass(frozen=True)
class Answer:
    """One call's outcome: ``result`` is Telegram's ``result`` on OK; ``retry_after`` the seconds
    Telegram asked for (SLOW_DOWN); ``status`` the HTTP status, for the log (None: no answer)."""

    outcome: str
    result: Any = None
    retry_after: int | None = None
    status: int | None = None


def judge(status: int, body: Any) -> Answer:
    """Telegram's answer as an ``Answer``."""
    envelope = body if isinstance(body, dict) else {}
    if status == 200 and envelope.get("ok") is True:
        return Answer(OK, envelope.get("result"), status=status)
    code = envelope.get("error_code") if isinstance(envelope.get("error_code"), int) else status
    if code in (401, 404):
        return Answer(REFUSED_TOKEN, status=status)
    if code == 409:
        return Answer(CONFLICT, status=status)
    if code == 429:
        params = envelope.get("parameters") if isinstance(envelope.get("parameters"), dict) else {}
        wait = params.get("retry_after")
        wait = wait if isinstance(wait, int) and not isinstance(wait, bool) else 5
        return Answer(SLOW_DOWN, retry_after=max(1, min(wait, 3600)), status=status)
    if code == 403:
        return Answer(BLOCKED, status=status)
    if code == 400:
        return Answer(BAD_REQUEST, status=status)
    return Answer(FAILING, status=status)


class TelegramClient:
    """The three Bot API calls the family bot makes. The token is passed to each call and kept
    only in the request it builds."""

    def __init__(self, http: httpx.AsyncClient):
        self.http = http
        self.counts = {"calls": 0, "failed": 0}

    async def call(self, token: str, method: str, params: dict[str, Any],
                   timeout: httpx.Timeout = DEFAULT_TIMEOUT) -> Answer:  # fmt: skip
        if not token_ok(token):
            return Answer(REFUSED_TOKEN)
        self.counts["calls"] += 1
        try:
            answer = await self.http.post(f"{API}/bot{token}/{method}", json=params,
                                          timeout=timeout)  # fmt: skip
        except HostNotAllowed:
            raise
        except (httpx.HTTPError, OSError) as exc:  # its text can carry the address: its kind only
            self.counts["failed"] += 1
            log.debug("the family bot's %s call failed (%s)", method, type(exc).__name__)
            return Answer(FAILING)
        try:
            body = answer.json()
        except ValueError:
            body = None
        verdict = judge(answer.status_code, body)
        if verdict.outcome != OK:
            self.counts["failed"] += 1
            log.debug("the family bot's %s call: %s (answer %d)", method, verdict.outcome,
                      answer.status_code)  # fmt: skip
        return verdict

    async def get_me(self, token: str) -> Answer:
        return await self.call(token, "getMe", {})

    async def get_updates(self, token: str, offset: int, wait_s: int = POLL_S) -> Answer:
        params = {"offset": offset, "timeout": wait_s, "allowed_updates": ["message"]}
        return await self.call(token, "getUpdates", params, timeout=POLL_TIMEOUT)

    async def send_message(self, token: str, chat_id: int, text: str) -> Answer:
        params = {"chat_id": chat_id, "text": text[:MAX_TEXT], "disable_web_page_preview": True}
        return await self.call(token, "sendMessage", params)
