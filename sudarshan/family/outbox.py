"""The family's outbox: every message to a member's phones and Telegram goes through it (M10.6,
M10.9, M20.4; design 2.6, 4.2; cross-design Y44).

A message is one row per member and channel (``push`` or ``telegram``), with what it is (``digest``
or ``security``), its urgency, its push topic, when it is due and how long it may live. The text in
the row is already in the member's language and through their mask. The sender (``run``, every five
seconds and at once when a message is put) takes the rows that are due, in order:

* quiet hours (``quiet.until``): a row due in them waits until they end when its time to live still
  covers that, and is otherwise dropped with an audit line (``notice_dropped``); a sign-in notice is
  never quiet;
* push: to each phone of the member (``push.Push.to_member``), with the time left to live as TTL; a
  phone that asked for a pause, or failed, is tried again 10 minutes then an hour later, as long as
  the message still lives;
* Telegram: through the family bot's pacer; a bot that is not running yet is tried again a minute
  later, while the message lives.

Y44: a summary's row holds no text at all, only what to say (the day and the language): its lines
are made from the books when it is sent (``render``), so no figure of the owner's is ever written
to family.db, not even to a free page or the write-ahead log. Any other row's text (a sign-in
notice: a device and a time) is cleared in the same write that records its result (a CHECK in the
schema holds it), and rows are forgotten seven days after.

The sign-in notices to the member (a new phone, a locked sign-in) come from the accounts' notice
hook (``on_notice``), urgent and never quiet. The test send (``POST /family/api/notify/test``) goes
straight to this phone and to the member's chat, and says what happened on each.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import and_, delete, select

from sudarshan.push import Outcome
from sudarshan.tasks import cancel_and_wait

from . import audit
from . import bot_words as words
from .bot_words import PAGE, Refused
from .listener import Response
from .quiet import MarketHours, day_of
from .quiet import until as quiet_until

log = logging.getLogger("sudarshan.family")

SEND_EVERY_S = 5.0
BATCH = 50
#: Rows sent or dropped are forgotten this long after.
KEEP_SENT_MS = 7 * 86_400_000
TIDY_EVERY_MS = 3_600_000
DIGEST_TTL_S = 6 * 3600  # design 2.6: "low with TTL 21600"
NOTICE_TTL_S = 24 * 3600
TELEGRAM_RETRY_MS = 60_000
TEST_EVERY_MS = 10_000
#: The accounts' notices a member is told of too (by every channel they have, never quiet).
NOTICE_KINDS = ("new_device", "lockout")
RESULT_MAX = 200


def message(kind: str, title: str, body: str, *, url: str, tag: str, at: int,
            language: str | None) -> dict[str, Any]:  # fmt: skip
    """What a phone's service worker reads (design 2.6): ``{v, kind, title, body, url, tag, at,
    lang}``; the same text goes to Telegram."""
    return {"v": 1, "kind": kind, "title": title, "body": body, "url": url, "tag": tag, "at": at,
            "lang": language or "en"}  # fmt: skip


def push_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def telegram_text(payload: dict[str, Any]) -> str:
    return f"{payload['title']}\n{payload['body']}"


def when_words(at_ms: int, now_ms: int, language: str | None) -> str:
    """19:05, or 19:05 Tue 17 Nov when it is another day."""
    text = words.minutes(at_ms)
    day = day_of(at_ms)
    return text if day == day_of(now_ms) else f"{text} {words.day_words(day, language)}"


class Outbox:
    """The rows waiting to go, and the one task that sends them."""

    def __init__(self, service: Any, *, push: Any, bot: Any, market: MarketHours):
        self.service, self.push, self.bot, self.market = service, push, bot, market
        self.clock: Callable[[], int] = service.clock
        self.counts = {"put": 0, "sent": 0, "held": 0, "dropped": 0, "finished": 0, "notices": 0,
                       "tests": 0}  # fmt: skip
        self._wake = asyncio.Event()
        self._tested: dict[int, int] = {}
        self._tasks: set[asyncio.Task] = set()
        self._tidied = 0
        self._closed = False
        #: Makes the text of a row that carries none (a summary) at send time: (member, payload)
        #: -> payload with ``body``. ``timers.install`` sets it to the summary's own.
        self.render: (
            Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]] | None
        ) = None

    def health(self) -> dict[str, int]:
        return dict(self.counts)

    def wake(self) -> None:
        self._wake.set()

    # ---- putting rows ------------------------------------------------------------------------
    @staticmethod
    def row(member_id: int, channel: str, category: str, payload: dict[str, Any], *, urgency: str,
            topic: str | None, ttl_s: int, now: int) -> dict[str, Any]:  # fmt: skip
        return {"member_id": member_id, "channel": channel, "category": category,
                "payload": json.dumps(payload, ensure_ascii=False), "urgency": urgency,
                "topic": topic if channel == "push" else None, "ttl_s": ttl_s, "due_at": now,
                "created_at": now}  # fmt: skip

    async def put(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0

        def insert(tx) -> int:
            for row in rows:
                tx.insert("outbox", row)
            return len(rows)

        done = await self.service.store.write(insert)
        self.counts["put"] += done
        self.wake()
        return done

    # ---- sending -----------------------------------------------------------------------------
    async def run(self) -> None:
        try:
            while True:
                self._wake.clear()
                await self.step()
                try:
                    await asyncio.wait_for(self._wake.wait(), SEND_EVERY_S)
                except TimeoutError:
                    pass
        finally:
            self._closed = True
            await cancel_and_wait(*self._tasks)

    async def step(self) -> int:
        """Send, hold or drop every row that is due; how many were taken."""
        now = self.clock()

        def due(tx) -> list[dict[str, Any]]:
            t = tx.t("outbox")
            query = (select(t).where(and_(t.c.sent_at.is_(None), t.c.due_at <= now))
                     .order_by(t.c.due_at, t.c.id).limit(BATCH))  # fmt: skip
            return [dict(r) for r in tx.conn.execute(query).mappings()]

        rows = await self.service.store.read(due)
        for row in rows:
            await self._one(row)
        if now - self._tidied >= TIDY_EVERY_MS:
            self._tidied = now
            await self._tidy(now)
        return len(rows)

    async def _one(self, row: dict[str, Any]) -> None:
        now, row_id = self.clock(), row["id"]
        member = await self.service.store.read(lambda tx: tx.member(row["member_id"]))
        if member is None or member["status"] != "active":
            await self._finish(row, "dropped: the member is not active", why="member")
            return
        expiry = row["created_at"] + row["ttl_s"] * 1000
        if now >= expiry:
            await self._finish(row, "dropped: its time to live ran out", why="expired")
            return
        held = quiet_until(member, row["category"], now, self.market)
        if held is not None:
            if held < expiry:
                await self._hold(row_id, held)
                return
            await self._finish(row, "dropped: quiet hours outlast its time to live", why="quiet")
            return
        payload = json.loads(row["payload"])
        if not isinstance(payload.get("body"), str):  # a summary: its lines are made now (Y44)
            if self.render is None:
                await self._finish(row, "dropped: nothing makes its text", why="refused")
                return
            payload = await self.render(member, payload)
        if row["channel"] == "push":
            done = await self.push.to_member(member["id"], push_body(payload), expiry=expiry,
                                             urgency=row["urgency"], topic=row["topic"])  # fmt: skip
            if done.state == "sent":
                await self._finish(row, f"sent to {done.sent} of {done.phones} phones", sent=True)
            elif done.state == "none":
                await self._finish(row, "no phone of the member has notifications on")
            elif done.state == "later" and done.retry_at is not None and done.retry_at < expiry:
                await self._hold(row_id, done.retry_at)
            elif done.state == "later":
                await self._finish(row, "dropped: no phone took it in time", why="expired")
            else:
                await self._finish(row, "dropped: no phone took it", why="refused")
            return
        lane = "digest" if row["category"] == "digest" else "alert"
        found = await self.bot.deliver(member["id"], telegram_text(payload), lane)
        if found == "sent":
            await self._finish(row, "sent", sent=True)
        elif found in ("not_running", "later") and now + TELEGRAM_RETRY_MS < expiry:
            await self._hold(row_id, now + TELEGRAM_RETRY_MS)
        elif found == "not_linked":
            await self._finish(row, "Telegram is not linked")
        elif found == "blocked":
            await self._finish(row, "dropped: the member blocked the bot", why="blocked")
        else:
            await self._finish(row, "dropped: Telegram did not take it", why="refused")

    async def _finish(self, row: dict[str, Any], result: str, *, sent: bool = False,
                      why: str | None = None) -> None:  # fmt: skip
        """Y44: the text goes in the same write that records the result."""
        now = self.clock()

        def finish(tx) -> None:
            tx.update("outbox", {"id": row["id"]},
                      {"sent_at": now, "payload": None, "result": result[:RESULT_MAX]})  # fmt: skip
            facts = {"channel": row["channel"], "category": row["category"]}
            if sent and row["category"] == "digest":
                audit.record(tx, at=now, actor=audit.SYSTEM, kind="digest_sent",
                             member_id=row["member_id"], detail=facts)  # fmt: skip
            elif why is not None:
                audit.record(tx, at=now, actor=audit.SYSTEM, kind="notice_dropped",
                             member_id=row["member_id"], detail={**facts, "why": why})  # fmt: skip

        await self.service.store.write(finish)
        self.counts["sent" if sent else "dropped" if why else "finished"] += 1

    async def _hold(self, row_id: int, due: int) -> None:
        await self.service.store.write(
            lambda tx: tx.update("outbox", {"id": row_id}, {"due_at": int(due)})
        )
        self.counts["held"] += 1

    async def _tidy(self, now: int) -> None:
        """Forget rows sent or dropped a week ago, and the subscriptions of signed-out phones."""

        def tidy(tx) -> int:
            t = tx.t("outbox")
            gone = and_(t.c.sent_at.is_not(None), t.c.sent_at < now - KEEP_SENT_MS)
            return int(tx.conn.execute(delete(t).where(gone)).rowcount)

        await self.service.store.write(tidy)
        await self.push.forget_ended()

    # ---- the member's sign-in notices (the accounts' notice hook) ----------------------------
    def on_notice(self, kind: str, member: Any = None, **facts: Any) -> None:
        if kind in NOTICE_KINDS and isinstance(member, dict) and member.get("id") is not None:
            self._spawn(self._notice(kind, member, facts))

    async def _notice(self, kind: str, member: dict[str, Any], facts: dict[str, Any]) -> None:
        now, language = self.clock(), member.get("language")
        if kind == "new_device":
            body = words.say("notice_new_device", language, device=facts.get("device") or "-",
                             time=words.minutes(now))  # fmt: skip
        else:
            until = int(facts.get("until") or 0) * 1000 or now
            body = words.say("notice_lockout", language, attempts=facts.get("attempts") or "5",
                             time=when_words(until, now, language))  # fmt: skip
        payload = message(kind, words.HEADER, body, url="/family/settings", tag=f"security-{kind}",
                          at=now, language=language)  # fmt: skip
        how = {"urgency": "high", "topic": f"security-{kind}", "ttl_s": NOTICE_TTL_S, "now": now}
        rows = [self.row(member["id"], "push", "security", payload, **how)]
        if member["id"] in self.bot.chats:
            rows.append(self.row(member["id"], "telegram", "security", payload, **how))
        self.counts["notices"] += 1
        await self.put(rows)

    def _spawn(self, work: Awaitable[Any]) -> None:
        if self._closed:
            if asyncio.iscoroutine(work):
                work.close()
            return
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ---- the test send (the viewer API) ------------------------------------------------------
    async def route_test_send(self, request: Any, args: dict, session: Any) -> Response:
        member_id, now = session.member_id, self.clock()
        if now - self._tested.get(member_id, -TEST_EVERY_MS) < TEST_EVERY_MS:
            raise Refused("busy", PAGE["test_busy"])
        self._tested = {k: at for k, at in self._tested.items() if now - at < TEST_EVERY_MS}
        self._tested[member_id] = now
        self.counts["tests"] += 1
        language = session.member.get("language")
        return Response.ok([await self._test_push(session, language),
                            await self._test_telegram(session, language)])  # fmt: skip

    async def _test_push(self, session: Any, language: str | None) -> dict[str, Any]:
        now = self.clock()
        payload = message("test", words.HEADER, words.say("test_push", language),
                          url="/family/settings", tag="test", at=now, language=language)  # fmt: skip
        found = await self.push.to_session(session.member_id, session.id_hash, push_body(payload))
        out: dict[str, Any] = {"channel": "push", "sent": False, "at": None}
        if found == "no_server":
            return {**out, "words": PAGE["test_push_no_server"]}
        if found == "off":
            return {**out, "words": PAGE["test_push_off"]}
        if found.outcome == Outcome.SENT:
            return {**out, "sent": True, "at": now,
                    "words": PAGE["test_sent"].format(time=words.clock(now))}  # fmt: skip
        return {**out, "words": PAGE["test_failed"].format(reason=found.reason.rstrip("."))}

    async def _test_telegram(self, session: Any, language: str | None) -> dict[str, Any]:
        now = self.clock()
        text = f"{words.HEADER}\n{words.say('test_telegram', language)}"
        found = await self.bot.deliver(session.member_id, text)
        out: dict[str, Any] = {"channel": "telegram", "sent": False, "at": None}
        if found == "sent":
            return {**out, "sent": True, "at": now,
                    "words": PAGE["test_sent"].format(time=words.clock(now))}  # fmt: skip
        key = {"not_linked": "test_not_linked", "not_running": "test_bot_down",
               "blocked": "test_blocked"}.get(found, "test_later")  # fmt: skip
        return {**out, "words": PAGE[key]}
