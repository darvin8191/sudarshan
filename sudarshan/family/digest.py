"""The day's summary for each family member (M16.4, M10.6, M20.4; design 2.6; cross-design Y16,
Y42).

**When.** On a trading day, at the day's last close plus ``SUDARSHAN_FAMILY_DIGEST_AFTER_MIN``
(25: 16:05, 16:10 from 7 Dec 2026, a special day from its own window, through
``sudarshan.common.schedule``). A summary the service missed (stopped over that time, a late
install) runs at its next start up to 21:00, when quiet hours begin; after that the day is recorded
as skipped (``job_run``, Y42). One record per day: the rows and the record are written together,
so a restart never sends a day twice.

**What.** Six lines at most, in the member's language, every figure through their mask (read from
the viewer API's own routes, as their phone reads them):

    Today's summary · Mon 16 Nov
    NIFTY 50 24,310.55 +0.42% · NIFTY BANK 52,100.20 −0.10%     when the market view answers
    Positions: 3 open · 2 long · 1 short · P&L +₹1,234.50       P&L only when shown
    Holdings today: +₹2,345.00                                  only when P&L is shown
    Trades today: 6 · won 3 · lost 2                            Y16: orders shown; won and lost
                                                                with P&L shown too
    Next trading day: Tue 17 Nov

A line whose figures are not there, or not current, is left out rather than shown old. The trades
line is the journal's count (``family_day`` on 127.0.0.1:5586, sealed under the journal's family
purpose; counts only, no money): no answer, no line.

**To whom.** Each active member, by push when they chose it (urgency low, TTL 6 h, topic
``digest-<day>`` so a waiting one is replaced) and by Telegram when they chose it and linked a chat.

**Y44.** A summary's row carries no text: only its day, the member's language and where it opens.
The outbox asks ``render`` for the lines when it sends the row, so the owner's figures are read
from the books and leave for the phone without ever being written to family.db.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from datetime import date
from typing import Any

import httpx
from cryptography.fernet import Fernet

from sudarshan.alerts.market import sessions
from sudarshan.autologin.handoff import seal
from sudarshan.common.schedule import Schedule

from . import bot_words as words
from .outbox import DIGEST_TTL_S, Outbox, message
from .quiet import MarketHours, at_minute, day_of, notify_of
from .telegram_client import AllowList

log = logging.getLogger("sudarshan.family")

JOB = "digest"
#: Y42: a missed summary runs up to this minute of its day (21:00, when quiet hours begin).
CUTOFF_MIN = 21 * 60
CHECK_EVERY_S = 30.0
MAX_LINES = 6
JOURNAL_PRINCIPAL = "sudarshan-family"
WARN_EVERY_S = 600.0


def make_http(url: str, inner: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """A client that reaches the journal's port on 127.0.0.1 and nothing else."""
    parsed = httpx.URL(url)
    allowed = frozenset({(parsed.scheme, parsed.host, parsed.port or 80)})
    return httpx.AsyncClient(transport=AllowList(allowed, inner), trust_env=False,
                             follow_redirects=False)  # fmt: skip


class Journal:
    """The journal's counts-only family line (Y16)."""

    def __init__(self, url: str, fernet: Fernet, http: httpx.AsyncClient,
                 monotonic: Callable[[], float] | None = None):  # fmt: skip
        self.url, self.fernet, self.http = url.rstrip("/"), fernet, http
        self.monotonic = monotonic or time.monotonic
        self._warned = -1e9

    async def counts(self, day: int) -> dict[str, int] | None:
        """``{trades, won, lost}`` of the live trades closed on ``day``; None when the journal
        does not answer, is switched off, or answers something else."""
        body = seal(self.fernet, "family_day", principal=JOURNAL_PRINCIPAL, args={"day": day})
        try:
            answer = await self.http.post(f"{self.url}/v1/family_day", content=body,
                                          headers={"Content-Type": "application/octet-stream"},
                                          timeout=httpx.Timeout(5.0, connect=1.0))  # fmt: skip
            found = answer.json() if answer.status_code == 200 else None
        except (httpx.HTTPError, ValueError) as exc:
            self._warn(type(exc).__name__)
            return None
        data = found.get("data") if isinstance(found, dict) else None
        if not isinstance(data, dict) or data.get("enabled") is False:
            return None
        picked = {k: data.get(k) for k in ("trades", "won", "lost")}
        if not all(
            isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in picked.values()
        ):
            return None
        return picked  # type: ignore[return-value]

    def _warn(self, why: str) -> None:
        if self.monotonic() - self._warned >= WARN_EVERY_S:
            self._warned = self.monotonic()
            log.warning("the journal did not answer the family summary's trades line (%s); the "
                        "summary goes without it", why)  # fmt: skip


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _half_away(numerator: int, denominator: int) -> int:
    whole, rest = divmod(abs(numerator), denominator)
    if 2 * rest >= denominator:
        whole += 1
    return whole if numerator >= 0 else -whole


def holdings_today(rows: list[Any]) -> int | None:
    """The holdings' move today in paise: each row's quantity times (last price - previous close),
    rounded as the portfolio rounds (``qty_u x price_u / 10^8``); None when a row lacks one."""
    total = 0
    for row in rows:
        if not isinstance(row, Mapping):
            return None
        qty, last, close = (_int(row.get(k)) for k in ("qty_u", "last_price_u", "close_price_u"))
        if qty is None or last is None or close is None:
            return None
        total += _half_away(qty * (last - close), 10**8)
    return total


def _current(view: Mapping[str, Any] | None) -> bool:
    return isinstance(view, Mapping) and view.get("state") in ("ok", "closed")


def lines_for(member: Mapping[str, Any], day: date, *, market: Mapping[str, Any] | None,
              summary: Mapping[str, Any] | None, holdings: Mapping[str, Any] | None,
              counts: Mapping[str, int] | None, next_day: date | None) -> list[str]:  # fmt: skip
    """The summary's lines for one member (pure: the reads are the caller's)."""
    language = member.get("language")
    hidden = set((summary or holdings or {}).get("hidden") or ())
    privacy = member.get("privacy") if isinstance(member.get("privacy"), Mapping) else {}
    hidden |= {c for c in ("pnl", "qty", "funds", "orders") if privacy.get(c) is not True}
    if "qty" in hidden:
        hidden.add("pnl")  # P&L is shown only with quantities (Y24)
    lines = [words.say("digest_title", language, day=words.day_words(day, language))]
    quotes = [q for q in (market or {}).get("indices") or () if isinstance(q, Mapping)]
    shown = []
    for quote in quotes[:3]:
        ltp, pct = quote.get("ltp"), quote.get("change_percent")
        if isinstance(ltp, int | float) and not isinstance(ltp, bool):
            name = str(quote.get("name") or quote.get("symbol") or "")[:20]
            bits = [name, words.level(ltp)]
            if isinstance(pct, int | float) and not isinstance(pct, bool):
                bits.append(words.percent(pct))
            shown.append(" ".join(bits))
    if shown:
        lines.append(words.SEP.join(shown))
    count = (summary or {}).get("positions") if _current(summary) else None
    if isinstance(count, Mapping) and all(_int(count.get(k)) is not None for k in ("open", "long",
                                                                                     "short")):  # fmt: skip
        line = (words.say("digest_positions", language, open=count["open"], long=count["long"],
                          short=count["short"]) if count["open"]
                else words.say("digest_positions_none", language))  # fmt: skip
        pnl = (summary or {}).get("pnl")
        paise = _int(pnl.get("pnl_paise")) if isinstance(pnl, Mapping) else None
        if paise is not None and "pnl" not in hidden:
            line += words.SEP + words.say("pnl", language, amount=words.money(paise))
        lines.append(line)
    held = list((holdings or {}).get("rows") or ())
    if _current(holdings) and "pnl" not in hidden and held:
        moved = holdings_today(held)
        if moved is not None:
            lines.append(words.say("digest_holdings", language, amount=words.money(moved)))
    if counts is not None and "orders" not in hidden:
        line = words.say("digest_trades", language, count=counts["trades"])
        if "pnl" not in hidden and counts["trades"]:
            line += words.SEP + words.say("digest_won_lost", language, won=counts["won"],
                                          lost=counts["lost"])  # fmt: skip
        lines.append(line)
    if next_day is not None:
        lines.append(words.say("next_day", language, day=words.day_words(next_day, language)))
    return lines[:MAX_LINES]


class Digest:
    """The summary's timer and its rows. ``ask`` is the bot's reader of the viewer routes."""

    def __init__(self, service: Any, *, outbox: Outbox, bot: Any, market: MarketHours,
                 schedule: Schedule, journal: Journal | None = None):  # fmt: skip
        self.service, self.outbox, self.bot = service, outbox, bot
        self.market, self.schedule, self.journal = market, schedule, journal
        self.clock: Callable[[], int] = service.clock
        self.last: dict[str, Any] = {"day": None, "result": None}
        self._counts: tuple[int, dict[str, int] | None] | None = None

    def health(self) -> dict[str, Any]:
        return dict(self.last)

    async def run(self) -> None:
        while True:
            try:
                await self.step(self.clock())
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("the family summary could not be made this time")
            await asyncio.sleep(CHECK_EVERY_S)

    async def step(self, now: int) -> str | None:
        """Make today's summary when it is due and not made: ``sent``, ``skipped`` or None."""
        day = day_of(now)
        due = self.schedule.eod_at(day)
        if due is None or now < due:
            return None
        key = sessions.day_key(day)
        if await self.service.store.read(lambda tx: tx.job(JOB, key)) is not None:
            return None
        if now >= at_minute(day, CUTOFF_MIN):
            why = ("the service was not running at the summary's time, and a summary after 21:00 "
                   "would arrive in quiet hours")  # fmt: skip
            await self.service.store.write(lambda tx: tx.job_put(JOB, key, "skipped", why, now))
            log.info("today's family summary was skipped: %s", why)
            self.last = {"day": key, "result": "skipped"}
            return "skipped"
        made = await self.run_day(day, now)
        self.last = {"day": key, "result": f"{made} messages"}
        return "sent"

    async def counts(self, key: int) -> dict[str, int] | None:
        """The journal's counts for a day, asked once a day (every member's rows share them)."""
        if self._counts is None or self._counts[0] != key:
            self._counts = (key, await self.journal.counts(key) if self.journal else None)
        return self._counts[1]

    async def render(self, member: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        """A summary row's text, made now from the books for this member (the outbox's
        ``render``): six lines at most, through their mask, in their language."""
        key = int(payload["day"])
        day = sessions.key_day(key)
        lines = lines_for(member, day, market=await self.bot.ask("market", member),
                          summary=await self.bot.ask("summary", member),
                          holdings=await self.bot.ask("book", member, part="holdings"),
                          counts=await self.counts(key), next_day=self.market.next_day(day))  # fmt: skip
        return {**payload, "body": "\n".join(lines), "lang": member.get("language") or "en"}

    async def run_day(self, day: date, now: int) -> int:
        """Every member's rows for ``day``, written with the day's record in one transaction.
        A row carries no text (Y44): ``render`` makes it when the row is sent."""
        members = await self.service.store.read(lambda tx: tx.members_live())
        rows: list[dict[str, Any]] = []
        for member in members:
            if member["status"] != "active":
                continue
            choice = notify_of(member)
            telegram = choice["digest_telegram"] and member["id"] in self.bot.chats
            if not (choice["digest_push"] or telegram):
                continue
            topic = f"digest-{sessions.day_key(day)}"
            payload = message("digest", words.HEADER, "", url="/family", tag=topic, at=now,
                              language=member.get("language"))  # fmt: skip
            del payload["body"]  # made at send time
            payload["day"] = sessions.day_key(day)
            how = {"ttl_s": DIGEST_TTL_S, "now": now, "topic": topic}
            if choice["digest_push"]:
                rows.append(
                    Outbox.row(member["id"], "push", "digest", payload, urgency="low", **how)
                )
            if telegram:
                rows.append(Outbox.row(member["id"], "telegram", "digest", payload, urgency="low",
                                       **how))  # fmt: skip
        key = sessions.day_key(day)

        def write(tx) -> int:
            if tx.job(JOB, key) is not None:
                return 0  # made meanwhile: never twice
            for row in rows:
                tx.insert("outbox", row)
            tx.job_put(JOB, key, "ok", f"{len(rows)} messages", now)
            return len(rows)

        made = await self.service.store.write(write)
        if made:
            self.outbox.counts["put"] += made
            self.outbox.wake()
        return made
