"""The family Telegram bot (M16.4, M1.15; design 2.6, 5; cross-design Y18, Y43).

A second bot the owner makes in BotFather, held only by this service. Its token is the file
``/etc/sudarshan-family/telegram_bot_token`` (root:sudarshan-family 640; the owner puts it there,
``README.md`` "Channels"), read again whenever the file changes and never logged. The bot
long-polls ``getUpdates`` (50 s, one request in flight) and answers with ``sendMessage``, paced by
a ``sudarshan.common.pacer.Pacer`` per chat at Telegram's own limit of one message a second to a
chat (Y43); the owner's bot and its pace are untouched.

**Linking.** A member taps Link Telegram in Settings: ``POST /family/api/telegram/link`` makes a
code (16 random bytes, kept only as its SHA-256, 10 minutes, one use) and answers
``https://t.me/<bot>?start=<code>``. Telegram then sends ``/start <code>`` from the member's chat;
the chat id is kept sealed under ``store.key`` and the code is gone. A ``/start`` without a code
that works is answered with how to link, in English and Gujarati; a chat that is not linked gets
nothing else.

**Commands**: the fixed table ``COMMANDS`` and nothing else. Every figure comes from the viewer
API's own routes for that member (the books, the portfolio, the market), so a chat shows exactly
what the member's phone shows, under the same mask, read again at every command (a privacy change
applies to the next one). Anything else is answered "This bot only shows information. It cannot
place, change or cancel an order." There is no path from here to an order: the module imports
nothing that can place one, and its HTTP client reaches only Telegram's API and the two loopback
services the design names (``telegram_client.ALLOWED``).

**The token.** Missing, or not a token: the owner's page says "Token missing". Refused by Telegram
(401 or 404): "Telegram refused the token", and one notice for the owner (``bot_token_rejected``)
per token. Another program polling with the same token (409) is logged in words, once in ten
minutes, and tried again.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import re
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from sudarshan.autologin.schedule import TradingCalendar
from sudarshan.common.pacer import Lane, Pacer
from sudarshan.tasks import cancel_and_wait

from . import audit, sessions
from . import bot_words as words
from .bot_words import Refused
from .errors import FamilyError
from .listener import Response
from .quiet import MarketHours, day_of, quiet_of
from .telegram_client import (
    BAD_REQUEST,
    BLOCKED,
    CONFLICT,
    FAILING,
    OK,
    REFUSED_TOKEN,
    SLOW_DOWN,
    USERNAME_RE,
    Answer,
    TelegramClient,
    make_http,
    token_ok,
)

log = logging.getLogger("sudarshan.family")

TOKEN_FILE = "telegram_bot_token"
TOKEN_FILE_MAX = 256
#: The commands of the table (design 2.6). ``/start`` is the link step, not a command.
COMMANDS = ("status", "positions", "holdings", "orders", "portfolio", "quiet", "language",
            "help", "stop")  # fmt: skip
BOOK_COMMANDS = {"positions": "positions", "holdings": "holdings", "orders": "orders"}
LINK_MS = 10 * 60_000
LINK_CODE_BYTES = 16
LINK_CODE_RE = re.compile(r"[A-Za-z0-9_-]{16,64}")
#: A message older than this when it reaches the bot (a restart, a long outage) is not answered.
STALE_S = 300
#: How often a missing or refused token is looked at again.
TOKEN_RECHECK_S = 30.0
CONFLICT_WAIT_S = 30.0
MAX_BACKOFF_S = 60.0
#: How many rows of a book one answer lists.
MAX_ROWS = 20
#: A chat that is not linked is told how to link at most this often.
TELL_EVERY_S = 60.0
#: Chats whose pacer and lock are kept (the linked ones are always kept).
KEEP_CHATS = 64
#: One ``bot_command`` audit line per command per member in this long (design 4.2: as ``view``).
AUDIT_EVERY_MS = 10 * 60_000
WARN_EVERY_S = 600.0
SEND_TRIES = 3
#: Telegram's own limit: one message a second to a chat (Y43).
CHAT_GAP_S = 1.0

Sleep = Callable[[float], Awaitable[None]]


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode("ascii")).hexdigest()


def _clean(value: Any, limit: int = 40) -> str:
    """Text from a row, as one line: printable, cut to ``limit``."""
    text = value if isinstance(value, str) else ""
    return "".join(ch for ch in text if ch.isprintable())[:limit]


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse(text: Any, username: str | None = None) -> tuple[str | None, str]:
    """A message's command (lower case, without ``@<this bot>``) and the rest of it. A message that
    is not a command, or names another bot, gives ``(None, "")``."""
    if not isinstance(text, str):
        return None, ""
    text = text.strip()
    if not text.startswith("/") or len(text) > 512:
        return None, ""
    head, _, rest = text.partition(" ")
    name, at, bot = head[1:].partition("@")
    if at and (username is None or bot.lower() != username.lower()):
        return None, ""
    if not name or not name.isascii() or not name.replace("_", "").isalnum():
        return None, ""
    return name.lower(), rest.strip()


class Bot:
    """The bot of one service: its token, its chats, its poll and its sends. Used from the
    service's one event loop."""

    def __init__(
        self,
        service: Any,
        client: TelegramClient | None,
        *,
        token_path: Path,
        market: MarketHours,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ):
        self.service, self.client, self.token_path = service, client, Path(token_path)
        self.market, self.monotonic, self.sleep = market, monotonic, sleep
        self.clock: Callable[[], int] = service.clock
        self.state = "token_missing"
        self.username: str | None = None
        self.last_message_at: int | None = None
        self.offset = 0
        #: member id -> chat id: the linked chats, from the store at start, kept as links change.
        self.chats: dict[int, int] = {}
        self.counts = {"updates": 0, "answered": 0, "sent": 0, "send_failed": 0, "conflicts": 0,
                       "refused_tokens": 0}  # fmt: skip
        self._token: str | None = None
        self._stamp: tuple[int, int, int] | None = None
        self._refused: str | None = None
        self._pacers: dict[int, Pacer] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._used: dict[int, float] = {}
        self._alerts_waiting: dict[int, int] = {}
        self._told: dict[int, float] = {}
        self._audited: dict[tuple[int, str], int] = {}
        self._warned: dict[str, float] = {}
        self._tasks: set[asyncio.Task] = set()
        #: Set when ``run`` has ended: nothing more is started or sent.
        self._closed = False

    # ---- what the owner's page and /health read ----------------------------------------------
    def report(self) -> dict[str, Any]:
        """The admin ``state`` answer's ``bot`` block."""
        return {"state": self.state, "last_message_at": self.last_message_at}

    def health(self) -> dict[str, Any]:
        """Counts and states only: no chat, no member, no token."""
        calls = dict(self.client.counts) if self.client is not None else {}
        return {"state": self.state, "linked": len(self.chats), **self.counts, "calls": calls}

    @property
    def ready(self) -> bool:
        return self.state == "polling" and self.username is not None

    # ---- the token ---------------------------------------------------------------------------
    def read_token(self) -> str | None:
        """The token in the file, read again when the file changed; None when there is none."""
        try:
            info = os.stat(self.token_path)
        except OSError:
            if self._stamp is not None or self._token is not None:
                log.warning("the family bot's token file is gone; the bot stops until it is back")
            self._stamp, self._token = None, None
            return None
        stamp = (info.st_ino, info.st_size, info.st_mtime_ns)
        if stamp == self._stamp:
            return self._token
        self._stamp = stamp
        try:
            with open(self.token_path, "rb") as handle:
                raw = handle.read(TOKEN_FILE_MAX + 1)
        except OSError:
            raw = b""
        text = raw.decode("ascii", "replace").strip() if len(raw) <= TOKEN_FILE_MAX else ""
        found = text if token_ok(text) else None
        if found is None:
            log.warning("the family bot's token file does not hold a bot token as BotFather shows "
                        "it; see sudarshan/family/README.md, Channels")  # fmt: skip
        if found != self._token:
            if self._token is not None:
                log.info("the family bot's token changed; the bot starts again with it")
            self._token, self.username, self.offset = found, None, 0
        return self._token

    def _refuse(self, token: str) -> None:
        self._refused = token
        self.state, self.username = "token_refused", None
        self.counts["refused_tokens"] += 1
        log.error("Telegram refused the family bot's token: the bot is stopped until the owner "
                  "puts a new token in the file (sudarshan/family/README.md, Channels)")  # fmt: skip
        # One notice per token: the key is a short hash of it, which gives nothing back.
        self.service.notices("bot_token_rejected", key=_hash(token)[:12])

    # ---- the chats ---------------------------------------------------------------------------
    async def load(self) -> None:
        """The linked chats, from the store."""
        store = self.service.store

        def read(tx) -> dict[int, int]:
            found = {}
            for row in tx.rows("telegram_link"):
                if not row.get("chat_id_enc"):
                    continue
                try:
                    found[int(row["member_id"])] = int(tx.secrets.open(row["chat_id_enc"]))
                except (FamilyError, ValueError):
                    log.error("a linked Telegram chat could not be opened with this store.key; "
                              "that member links Telegram again from Settings")  # fmt: skip
            return found

        self.chats = await store.read(read) if store is not None else {}

    def member_of(self, chat: int) -> int | None:
        return next((member for member, linked in self.chats.items() if linked == chat), None)

    # ---- the poll ----------------------------------------------------------------------------
    async def run(self) -> None:
        """Load the chats, then poll until cancelled (a fault is logged and the bot starts again
        in 5 s); the sends it started end with it."""
        try:
            while True:
                try:
                    await self.load()
                    await self.poll()
                    return
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("the family bot stopped on a fault; it starts again in 5 s")
                    await self.sleep(5.0)
        finally:
            self._closed = True
            await cancel_and_wait(*self._tasks)

    async def poll(self) -> None:
        wait = 1.0
        while True:
            token = self.read_token()
            if token is None:
                self.state = "token_missing"
                await self.sleep(TOKEN_RECHECK_S)
                continue
            if token == self._refused:
                self.state = "token_refused"
                await self.sleep(TOKEN_RECHECK_S)
                continue
            if self.username is None:
                answer = await self.client.get_me(token)
                if answer.outcome == REFUSED_TOKEN:
                    self._refuse(token)
                    continue
                found = answer.result.get("username") if isinstance(answer.result, dict) else None
                if answer.outcome != OK or not (isinstance(found, str) and
                                                USERNAME_RE.fullmatch(found)):  # fmt: skip
                    await self.sleep(wait)
                    wait = min(wait * 2, MAX_BACKOFF_S)
                    continue
                self.username, self.state = found, "polling"
                log.info("the family bot's token works; the bot is polling Telegram")
            answer = await self.client.get_updates(token, self.offset)
            if answer.outcome == REFUSED_TOKEN:
                self._refuse(token)
                continue
            if answer.outcome == CONFLICT:
                self.counts["conflicts"] += 1
                self._warn("conflict", "another program is using the family bot's token (or a "
                           "webhook is set for it), so Telegram gives this service no messages; "
                           "it tries again every 30 seconds")  # fmt: skip
                await self.sleep(CONFLICT_WAIT_S)
                continue
            if answer.outcome == SLOW_DOWN:
                await self.sleep(float(answer.retry_after or 5))
                continue
            if answer.outcome != OK or not isinstance(answer.result, list):
                await self.sleep(wait)
                wait = min(wait * 2, MAX_BACKOFF_S)
                continue
            wait = 1.0
            for update in answer.result:
                update_id = _int(update.get("update_id")) if isinstance(update, dict) else None
                if update_id is None:
                    continue
                self.offset = max(self.offset, update_id + 1)
                self.counts["updates"] += 1
                try:
                    await self.handle(update)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("the family bot could not answer a message")

    # ---- one message -------------------------------------------------------------------------
    async def handle(self, update: Mapping[str, Any]) -> None:
        message = update.get("message")
        if not isinstance(message, Mapping):
            return
        chat = message.get("chat") if isinstance(message.get("chat"), Mapping) else {}
        chat_id = _int(chat.get("id"))
        if chat_id is None or chat.get("type") != "private":
            return  # groups and channels are never linked
        sent_at = _int(message.get("date"))
        if sent_at is not None and self.clock() // 1000 - sent_at > STALE_S:
            return  # sent while the bot was away: not answered late
        self.last_message_at = self.clock()
        command, rest = parse(message.get("text"), self.username)
        member_id = self.member_of(chat_id)
        if member_id is None:
            if command == "start":
                await self.start(chat_id, rest)
            return  # a chat that is not linked gets nothing else
        member = await self.service.store.read(lambda tx: tx.member(member_id))
        if member is None or member["status"] == "removed":
            self.chats.pop(member_id, None)
            return
        language = member.get("language")
        if command == "start":
            await self.send(chat_id, words.say("already_linked", language))
        elif command == "stop":
            await self.unlink(member_id, audit.member_actor(member_id), tell=True)
        elif member["status"] != "active":  # paused by the owner, or a sign-in being reset
            line = "paused" if member["status"] == "suspended" else "not_active"
            await self.send(chat_id, words.say(line, language))
        elif command in COMMANDS:
            self.counts["answered"] += 1
            await self.send(chat_id, await self.answer(member, command, rest))
        else:
            await self.send(chat_id, words.say("refusal", language))

    async def start(self, chat_id: int, code: str) -> None:
        """``/start <code>`` from a chat that is not linked: link it, or say how."""
        now = self.clock()
        member = None
        if LINK_CODE_RE.fullmatch(code):
            hashed = _hash(code)

            def bind(tx) -> dict[str, Any] | None:
                row = tx.row("telegram_link", link_code_hash=hashed)
                if row is None or not row.get("link_expires_at") or row["link_expires_at"] <= now:
                    return None
                found = tx.member(row["member_id"])
                if found is None or found["status"] != "active":
                    return None
                tx.update("telegram_link", {"member_id": found["id"]},
                          {"chat_id_enc": tx.secrets.seal(str(chat_id)), "linked_at": now,
                           "link_code_hash": None, "link_expires_at": None})  # fmt: skip
                audit.record(tx, at=now, actor=audit.member_actor(found["id"]),
                             kind="telegram_linked", member_id=found["id"])  # fmt: skip
                return found

            member = await self.service.store.write(bind)
        if member is None:
            if self.monotonic() - self._told.get(chat_id, -1e9) >= TELL_EVERY_S:
                self._told[chat_id] = self.monotonic()
                await self.send(chat_id, words.both("link_first"))
            return
        before = self.chats.get(member["id"])
        self.chats[member["id"]] = chat_id
        language = member.get("language")
        if before is not None and before != chat_id:
            await self.send(before, words.say("unlinked", language))
        await self.send(
            chat_id, words.say("linked", language) + "\n\n" + words.say("help", language)
        )

    async def unlink(self, member_id: int, actor: str, *, tell: bool) -> bool:
        """Forget the member's chat (and any code waiting); True when a chat was linked."""
        now = self.clock()

        def drop(tx) -> bool:
            row = tx.row("telegram_link", member_id=member_id)
            if row is None:
                return False
            tx.delete("telegram_link", member_id=member_id)
            if not row.get("chat_id_enc"):
                return False
            audit.record(tx, at=now, actor=actor, kind="telegram_unlinked", member_id=member_id)
            return True

        linked = await self.service.store.write(drop)
        chat = self.chats.pop(member_id, None)
        if tell and chat is not None:
            language = await self._language(member_id)
            await self.send(chat, words.say("unlinked", language))
        return linked

    async def _language(self, member_id: int) -> str:
        member = await self.service.store.read(lambda tx: tx.member(member_id))
        return (member or {}).get("language") or "en"

    def member_removed(self, member: Mapping[str, Any]) -> None:
        """The owner removed a member (the notice hook): their chat gets one last line. The link
        itself went in the removal's own transaction."""
        chat = self.chats.pop(int(member["id"]), None)
        if chat is not None:
            self._spawn(self.send(chat, words.say("unlinked", member.get("language"))))

    def _spawn(self, work: Awaitable[Any]) -> None:
        if self._closed:  # the service is stopping: nothing new is started
            if asyncio.iscoroutine(work):
                work.close()
            return
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ---- the answers -------------------------------------------------------------------------
    async def answer(self, member: dict[str, Any], command: str, rest: str) -> str:
        """The answer to a command of the table, in the member's language."""
        language = member.get("language")
        if command == "help":
            return words.say("help", language) + "\n\n" + words.say("refusal", language)
        if command == "quiet":
            return await self.set_quiet(member, rest.lower())
        if command == "language":
            return await self.set_language(member, rest.lower())
        self._audit_command(member, command)
        if command == "status":
            return await self.status(member)
        if command in BOOK_COMMANDS:
            return await self.book(member, BOOK_COMMANDS[command])
        return await self.portfolio(member)

    async def ask(self, op: str, member: Mapping[str, Any], **args: Any) -> dict[str, Any] | None:
        """The viewer route ``op`` answered for this member, as their phone would get it; None
        when that part of the family view is not there or did not answer."""
        handler = getattr(self.service.viewer, f"_{op}", None)
        if handler is None:
            return None
        asker = sessions.Session("", dict(member), {})
        try:
            response = await handler(None, dict(args), asker)
        except FamilyError:
            return None
        payload = response.payload if isinstance(response, Response) else None
        if not isinstance(payload, dict) or payload.get("status") != "success":
            return None
        data = payload.get("data")
        return data if isinstance(data, dict) else None

    async def status(self, member: Mapping[str, Any]) -> str:
        language, now = member.get("language"), self.clock()
        lines = [words.HEADER]
        market = await self.ask("market", member)
        is_open = self.market.is_open(now)
        feed = market.get("feed") if market else None
        if feed == "realtime":
            lines.append(words.say("realtime", language))
        elif feed == "reconnecting":
            lines.append(words.say("reconnecting", language))
        else:
            lines.append(words.say("market_open" if is_open and feed is None else "market_closed",
                                   language))  # fmt: skip
        quotes = [q for q in (market or {}).get("indices") or () if isinstance(q, Mapping)]
        for quote in quotes[:3]:
            lines.append(self._quote_line(quote, feed == "realtime"))
        if not quotes:
            lines.append(words.say("indices_unavailable", language))
        if not is_open:
            following = self.market.next_day(day_of(now))
            if following is not None:
                lines.append(words.say("next_day", language,
                                       day=words.day_words(following, language)))  # fmt: skip
        return "\n".join(lines)

    @staticmethod
    def _quote_line(quote: Mapping[str, Any], live: bool) -> str:
        bits = [_clean(quote.get("name")) or _clean(quote.get("symbol"))]
        ltp, pct = quote.get("ltp"), quote.get("change_percent")
        if isinstance(ltp, int | float) and not isinstance(ltp, bool):
            bits.append(words.level(ltp))
        if isinstance(pct, int | float) and not isinstance(pct, bool):
            bits.append(words.percent(pct))
        at = _int(quote.get("at"))
        if not live and at is not None:
            bits.append(f"({words.clock(at)})")
        return " ".join(bit for bit in bits if bit)

    @staticmethod
    def when(book: Mapping[str, Any], language: str | None) -> str | None:
        """When a book's figures are from, said as rule 7 wants: never as current."""
        state, at = book.get("state"), _int(book.get("read_at"))
        stamp = words.clock(at) if at is not None else None
        if state == "ok":
            return words.say("figures_from", language, time=stamp) if stamp else None
        if state == "closed":
            return (words.say("closed_from", language, time=stamp) if stamp
                    else words.say("market_closed", language))  # fmt: skip
        if state == "budget":
            return (words.say("budget_from", language, time=stamp) if stamp
                    else words.say("no_figures", language))  # fmt: skip
        if state == "unreachable":
            text = words.say("unreachable", language)
            return text + (" " + words.say("figures_from", language, time=stamp) if stamp else "")
        if state == "portfolio_off":
            return words.say("portfolio_off", language)
        return words.say("not_shared", language)

    async def book(self, member: Mapping[str, Any], part: str) -> str:
        language = member.get("language")
        found = await self.ask("book", member, part=part)
        if found is None:
            return words.say("not_ready", language)
        if found.get("state") == "not_shared":
            return words.say("orders_hidden" if part == "orders" else "not_shared", language)
        hidden = set(found.get("hidden") or ())
        rows = [r for r in found.get("rows") or () if isinstance(r, Mapping)]
        lines = [f"{words.HEADER}\n{words.say(part, language)}"]
        when = self.when(found, language)
        if when:
            lines.append(when)
        if not rows and found.get("state") in ("ok", "closed"):
            lines.append(words.say(f"{part}_none", language))
        line_of = {"positions": self._position, "holdings": self._holding, "orders": self._order}
        for row in rows[:MAX_ROWS]:
            lines.append(line_of[part](row, hidden, language))
        if len(rows) > MAX_ROWS:
            lines.append(words.say("more_rows", language, count=len(rows) - MAX_ROWS))
        return "\n".join(lines)

    @staticmethod
    def _figures(row: Mapping[str, Any], hidden: set[str], language: str | None,
                 *, price_key: str = "last_price_u") -> list[str]:  # fmt: skip
        out = []
        price = _int(row.get(price_key))
        if price is not None:
            out.append(words.price(price))
        pnl = _int(row.get("pnl_paise"))
        if pnl is not None and "pnl" not in hidden:
            out.append(words.say("pnl", language, amount=words.money(pnl)))
        return out

    def _position(self, row: Mapping[str, Any], hidden: set[str], language: str | None) -> str:
        bits = [_clean(row.get("symbol")) or "-", _clean(row.get("exchange"), 12)]
        side = row.get("side")
        if side in ("long", "short", "closed"):
            qty = _int(row.get("qty_u"))
            shown = qty is not None and "qty" not in hidden and side != "closed"
            bits.append(words.say(side, language) + (f" {words.quantity(qty)}" if shown else ""))
        bits += self._figures(row, hidden, language)
        return words.SEP.join(bit for bit in bits if bit)

    def _holding(self, row: Mapping[str, Any], hidden: set[str], language: str | None) -> str:
        bits = [_clean(row.get("symbol")) or "-"]
        qty = _int(row.get("qty_u"))
        if qty is not None and "qty" not in hidden:
            bits.append(words.say("qty", language, qty=words.quantity(qty)))
        bits += self._figures(row, hidden, language)
        return words.SEP.join(bit for bit in bits if bit)

    def _order(self, row: Mapping[str, Any], hidden: set[str], language: str | None) -> str:
        bits = [_clean(row.get("symbol")) or "-", _clean(row.get("exchange"), 12)]
        side = row.get("transaction_type")
        if side in ("BUY", "SELL"):
            bits.append(words.say(side.lower(), language))
        status = _clean(row.get("status"), 30).upper()
        if status:
            bits.append(status)
        qty = _int(row.get("qty_u"))
        if qty is not None and "qty" not in hidden:
            bits.append(words.say("qty", language, qty=words.quantity(qty)))
        price = _int(row.get("avg_price_u")) or _int(row.get("price_u"))
        if price and "pnl" not in hidden:  # a price paid is a cost figure: shown with P&L (H1)
            bits.append(words.price(price))
        return words.SEP.join(bit for bit in bits if bit)

    async def portfolio(self, member: Mapping[str, Any]) -> str:
        language = member.get("language")
        found = await self.ask("portfolio", member)
        if found is None:
            return words.say("not_ready", language)
        if found.get("state") not in (None, "ok"):
            return self.when(found, language) or words.say("unreachable", language)
        tabs = [t for t in found.get("tabs") or () if isinstance(t, Mapping)]
        if not tabs:
            return words.say("portfolio_none", language)
        hidden = set(found.get("hidden") or ())
        summary = found.get("summary") if isinstance(found.get("summary"), Mapping) else {}
        entries = {
            e["tab"].get("id"): e for e in summary.get("entries") or ()
            if isinstance(e, Mapping) and isinstance(e.get("tab"), Mapping)
        }  # fmt: skip
        lines = [f"{words.HEADER}\n{words.say('portfolio', language)}"]
        for tab in tabs[:MAX_ROWS]:
            bits = [_clean(tab.get("name")) or "-"]
            live = (entries.get(tab.get("id")) or {}).get("live")
            totals = live.get("totals") if isinstance(live, Mapping) else None
            if isinstance(totals, Mapping):
                value = _int(totals.get("value_paise"))
                if value is not None and "pnl" not in hidden:  # a cost figure (H1)
                    bits.append(words.say("value", language, amount=words.money(value)))
                today = totals.get("today") if isinstance(totals.get("today"), Mapping) else {}
                paise = _int(today.get("paise"))
                if today.get("state") == "ok" and paise is not None and "pnl" not in hidden:
                    bits.append(words.say("today", language, amount=words.money(paise)))
            lines.append(words.SEP.join(bits))
        return "\n".join(lines)

    async def set_quiet(self, member: Mapping[str, Any], choice: str) -> str:
        language = member.get("language")
        if choice not in ("on", "off"):
            return words.say("quiet_usage", language)
        on = choice == "on"

        def change(tx) -> dict[str, Any]:
            now, row = self.clock(), tx.member(member["id"])
            quiet = {**quiet_of(row), "digest": on, "alerts": on}
            tx.member_change(row["id"], row["version"], {"quiet": quiet}, now)
            audit.record(tx, at=now, actor=audit.member_actor(row["id"]), kind="member_updated",
                         member_id=row["id"], detail={"fields": ["quiet"], "via": "telegram"})  # fmt: skip
            return quiet

        quiet = await self.service.store.write(change)
        if on:
            return words.say("quiet_on", language, start=quiet["from"], end=quiet["to"])
        return words.say("quiet_off", language)

    async def set_language(self, member: Mapping[str, Any], choice: str) -> str:
        if choice not in words.LANGUAGES:
            return words.say("language_usage", member.get("language"))

        def change(tx) -> None:
            now, row = self.clock(), tx.member(member["id"])
            if row["language"] != choice:
                tx.member_change(row["id"], row["version"], {"language": choice}, now)
                audit.record(tx, at=now, actor=audit.member_actor(row["id"]),
                             kind="member_updated", member_id=row["id"],
                             detail={"fields": ["language"], "via": "telegram"})  # fmt: skip

        await self.service.store.write(change)
        return words.say("language_set", choice)

    def _audit_command(self, member: Mapping[str, Any], command: str) -> None:
        now, key = self.clock(), (int(member["id"]), command)
        if now - self._audited.get(key, -AUDIT_EVERY_MS) < AUDIT_EVERY_MS:
            return
        self._audited = {k: at for k, at in self._audited.items() if now - at < AUDIT_EVERY_MS}
        self._audited[key] = now

        async def write() -> None:
            await self.service.store.write(lambda tx: audit.record(
                tx, at=now, actor=audit.member_actor(key[0]), kind="bot_command",
                member_id=key[0], detail={"command": command}))  # fmt: skip

        self._spawn(write())

    # ---- sending -----------------------------------------------------------------------------
    def _pacer(self, chat: int) -> Pacer:
        pacer = self._pacers.get(chat)
        if pacer is None:
            pacer = Pacer((Lane("alert"), Lane("digest", yields_to=("alert",))),
                          gap_s=lambda: CHAT_GAP_S, clock=self.monotonic)  # fmt: skip
            pacer.set_waiting("alert", lambda chat=chat: self._alerts_waiting.get(chat, 0) > 0)
            self._pacers[chat] = pacer
            self._locks[chat] = asyncio.Lock()
            self._used[chat] = self.monotonic()
            self._prune(chat)
        self._used[chat] = self.monotonic()
        return pacer

    def _prune(self, current: int) -> None:
        """Keep the pacers of the linked chats, of ``current`` and of the chats used last."""
        if len(self._pacers) <= KEEP_CHATS:
            return
        keep = {*self.chats.values(), current}
        spare = sorted((c for c in self._pacers if c not in keep and not self._locks[c].locked()),
                       key=lambda c: self._used.get(c, 0.0))  # fmt: skip
        for chat in spare[: len(self._pacers) - KEEP_CHATS]:
            for table in (self._pacers, self._locks, self._used, self._told, self._alerts_waiting):
                table.pop(chat, None)

    async def send(self, chat: int, text: str, lane: str = "alert") -> Answer:
        """One message to a chat, paced; a slow-down from Telegram is waited out and tried again
        (three tries in all). A digest gives way, outside the chat's lock, while answers and
        notices for that chat wait."""
        token = self._token
        if token is None or token == self._refused:
            return Answer(REFUSED_TOKEN)
        if self._closed or self.client is None:
            return Answer(FAILING)
        pacer = self._pacer(chat)
        if lane == "alert":
            self._alerts_waiting[chat] = self._alerts_waiting.get(chat, 0) + 1
        try:
            while lane != "alert" and pacer.blocked(lane):
                await self.sleep(0.2)
            async with self._locks[chat]:
                answer = Answer(SLOW_DOWN)
                for _ in range(SEND_TRIES):
                    wait = pacer.allowed_at(lane) - self.monotonic()
                    if wait > 0:
                        await self.sleep(wait)
                    pacer.took(lane)
                    answer = await self.client.send_message(token, chat, text)
                    if answer.outcome != SLOW_DOWN:
                        break
                    await self.sleep(float(answer.retry_after or 1))
        finally:
            if lane == "alert":
                self._alerts_waiting[chat] = max(0, self._alerts_waiting.get(chat, 1) - 1)
        if answer.outcome == OK:
            self.counts["sent"] += 1
            self.last_message_at = self.clock()
        else:
            self.counts["send_failed"] += 1
        return answer

    async def deliver(self, member_id: int, text: str, lane: str = "alert") -> str:
        """A message to a member's chat (the outbox and the test send): ``sent``, ``not_linked``,
        ``not_running``, ``blocked`` (the member blocked the bot), ``later`` (try again) or
        ``refused``."""
        chat = self.chats.get(member_id)
        if chat is None:
            return "not_linked"
        if not self.ready:
            return "not_running"
        answer = await self.send(chat, text, lane)
        if answer.outcome == OK:
            return "sent"
        if answer.outcome == BLOCKED:
            return "blocked"
        if answer.outcome in (BAD_REQUEST, REFUSED_TOKEN):
            return "refused"
        return "later"

    # ---- the viewer routes (Settings, Telegram) ----------------------------------------------
    async def route_telegram_link(self, request: Any, args: dict, session: Any) -> Response:
        """A one-time link for the member's Telegram: ``TelegramLink``."""
        if not self.ready:
            raise Refused("not_ready", words.PAGE["bot_not_set_up"])
        code, now = secrets.token_urlsafe(LINK_CODE_BYTES), self.clock()
        ends, member_id = now + LINK_MS, session.member_id

        def put(tx) -> None:
            values = {"link_code_hash": _hash(code), "link_expires_at": ends}
            if tx.row("telegram_link", member_id=member_id) is None:
                tx.insert("telegram_link", {"member_id": member_id, **values})
            else:
                tx.update("telegram_link", {"member_id": member_id}, values)

        await self.service.store.write(put)
        return Response.ok({"url": f"https://t.me/{self.username}?start={code}",
                            "expires_at": ends})  # fmt: skip

    async def route_telegram_unlink(self, request: Any, args: dict, session: Any) -> Response:
        member_id = session.member_id
        await self.unlink(member_id, audit.member_actor(member_id), tell=True)
        return Response.ok({"ok": True, "words": None})

    def _warn(self, kind: str, text: str) -> None:
        now = self.monotonic()
        if now - self._warned.get(kind, -1e9) >= WARN_EVERY_S:
            self._warned[kind] = now
            log.warning(text)


def install_check(etc_dir: Path) -> dict[str, Any]:
    """The admin ``state`` answer's ``check`` block: the deploy writes ``provisioned`` only after
    the family check passed as this account, and removes it after a check that failed."""
    with contextlib.suppress(OSError):
        return {"ok": True, "at": int(os.stat(etc_dir / "provisioned").st_mtime * 1000)}
    return {"ok": False, "at": None}


async def finish(steps: list[Callable[[], Awaitable[Any]]]) -> None:
    """Run every closing step even when the job is cancelled again while it closes (the service
    cancels a job that has not ended every 0.2 s): a cancel is kept and raised at the end."""
    interrupted: asyncio.CancelledError | None = None
    for step in steps:
        try:
            await step()
        except asyncio.CancelledError as exc:
            interrupted = exc
        except Exception:
            log.exception("a part of the family channels did not close cleanly")
    if interrupted is not None:
        raise interrupted


def build(service: Any) -> Bot:
    """The bot of a service, from its settings: the token file and the calendar copy it keeps.
    Its HTTP client is made by the job that runs it (``install``), and closed when that ends."""
    settings = service.settings
    calendar = TradingCalendar(settings.holidays_file, settings.extra_days_file, logger=log)
    return Bot(service, None, token_path=settings.etc_dir / TOKEN_FILE,
               market=MarketHours(calendar))  # fmt: skip


def hook_notices(service: Any, on_notice: Callable[..., None]) -> None:
    """Hear the accounts' notices too: ``on_notice(kind, member, **facts)`` after the owner's."""
    first = service.accounts.notify

    def both(kind: str, member: Any = None, **facts: Any) -> None:
        first(kind, member, **facts)
        try:
            on_notice(kind, member, **facts)
        except Exception:
            log.exception("a family notice could not be passed to the channels")

    service.accounts.notify = both
