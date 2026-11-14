"""F10: the outbox (sent rows keep no text, Y44; quiet hours hold or drop with an audit line; sign-in
notices never quiet; the back-off; Telegram through the bot), the test send, the day's summary
(six lines through the mask, the journal's line per Y16, once a day, a missed one until 21:00 per
Y42) and the channels through the running service, with fd-audit's measurements."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import date

import pytest
from cryptography.fernet import Fernet
from family_botkit import FakeTelegram, Weekdays, fake_service, make_bot, new_token, ready, settle
from family_net import ask, fast_hasher, keys, running, settings
from family_planekit import CHILD, MONDAY, VIEWER, Clock, ist
from family_pushkit import ENDPOINT, FakePushService, PushServer, subscription, write_vapid
from family_testkit import add_member, close, open_store

from sudarshan.alerts.market import sessions as market_sessions
from sudarshan.autologin.handoff import NonceBook, open_sealed
from sudarshan.common.schedule import Schedule
from sudarshan.family import bot as bot_module
from sudarshan.family import bot_words as words
from sudarshan.family import digest as digest_module
from sudarshan.family import push as push_module
from sudarshan.family import sessions
from sudarshan.family.bot_words import PAGE, Refused
from sudarshan.family.contract import COOKIE, CSRF_COOKIE, CSRF_HEADER
from sudarshan.family.listener import Response
from sudarshan.family.outbox import DIGEST_TTL_S, Outbox, message
from sudarshan.family.quiet import MarketHours
from sudarshan.family.service import FamilyService

MARKET = MarketHours(Weekdays())
TUESDAY = MONDAY.replace(day=17)
CANARY = "SYNTHCANARY-8f3a"


def run(coro):
    try:
        return asyncio.run(coro)
    finally:
        close(None)


def signed(opened, now: int, **values):
    member = add_member(opened, status="active", **values)

    def make(tx):
        device = sessions.Device("Android 15 · Chrome 130", "Android 15 · Chrome 130")
        session_id, _ = sessions.mint(tx, member["id"], device, now)
        return tx.row("viewer_session", id_hash=sessions.id_hash(session_id))

    row = opened.write_now(make)
    return member, sessions.Session(row["id_hash"], member, row)


class BotStub:
    """The bot as the outbox sees it: the linked chats and a deliver that answers ``answer``."""

    def __init__(self):
        self.chats: dict[int, int] = {}
        self.sent: list[tuple[int, str, str]] = []
        self.answer = "sent"

    async def deliver(self, member_id: int, text: str, lane: str = "alert") -> str:
        self.sent.append((member_id, text, lane))
        return self.answer


async def _outbox(opened, tmp_path, clock, fake=None, bot=None):
    service = fake_service(opened, clock, tmp_path / "etc")
    push = push_module.Push(service, write_vapid(tmp_path / "etc"))
    http = (fake or FakePushService()).http()
    push.start(http)
    box = Outbox(service, push=push, bot=bot or BotStub(), market=MARKET)
    return service, push, box, http


def digest_row(member_id: int, now: int, text: str = CANARY, channel: str = "push",
               category: str = "digest", ttl_s: int = DIGEST_TTL_S) -> dict:  # fmt: skip
    payload = message("digest", words.HEADER, text, url="/family", tag="digest-20261116", at=now,
                      language="en")  # fmt: skip
    return Outbox.row(member_id, channel, category, payload, urgency="low", topic="digest-20261116",
                      ttl_s=ttl_s, now=now)  # fmt: skip


async def _rows(opened):
    return await opened.read(lambda tx: tx.rows("outbox", "id"))


# ---- the outbox -----------------------------------------------------------------------------------
def test_a_summary_is_written_without_its_text_and_a_sent_row_keeps_none(tmp_path):
    """Y44: a summary's row carries no text; its lines (with the owner's figures, and here a
    canary) are made when it is sent: they reach the phone and the chat, never family.db, not
    even a free page or the write-ahead log. A sign-in notice's text is cleared when it is sent."""

    async def go():
        opened = open_store(tmp_path)
        try:
            clock, fake = Clock(ist(MONDAY, 16, 5)), FakePushService()
            member, phone = signed(opened, clock(), privacy=VIEWER)
            canary = {**MARKET_VIEW, "indices": [{**MARKET_VIEW["indices"][0], "name": CANARY}]}
            made, bot = await _digest(opened, tmp_path, clock, routes=Routes(market=canary))
            bot.chats[member["id"]] = 900_000_001
            box = made.outbox
            box.push.start(fake.http())
            await box.push.route_push_subscribe(None, subscription(1), phone)
            assert await made.step(clock()) == "sent"
            rows = await _rows(opened)
            assert all("body" not in json.loads(r["payload"]) for r in rows)
            assert all(json.loads(r["payload"])["day"] == 20261116 for r in rows)
            await box.step()
            rows = await _rows(opened)
            assert [r["result"] for r in rows] == ["sent to 1 of 1 phones", "sent"]
            assert all(r["payload"] is None for r in rows)
            assert CANARY in made.telegram.texts()[-1]  # the chat got the made text
            sent = fake.headers()[-1]
            assert (sent["urgency"], sent["topic"], sent["ttl"]) == (
                "low",
                "digest-20261116",
                "21600",
            )
            lines = await opened.read(lambda tx: tx.rows("audit", kind="digest_sent"))
            assert sorted(ln["detail"]["channel"] for ln in lines) == ["push", "telegram"]
            await box.put([digest_row(member["id"], clock(), text="a sign-in notice")])
            await box.step()
            assert (await _rows(opened))[-1]["payload"] is None
            await settle(bot)
            stored = b"".join(p.read_bytes() for p in tmp_path.glob("family.db*"))
            assert CANARY.encode() not in stored
        finally:
            close(opened)

    run(go())


def test_quiet_hours_hold_a_summary_their_window_ends_inside_its_life(tmp_path):
    async def go():
        opened = open_store(tmp_path)
        try:
            clock = Clock(ist(MONDAY, 16, 5))
            quiet = {"from": "15:00", "to": "17:00", "digest": True, "alerts": True}
            member, phone = signed(opened, clock(), quiet=quiet)
            service, push, box, http = await _outbox(opened, tmp_path, clock)
            await push.route_push_subscribe(None, subscription(1), phone)
            await box.put([digest_row(member["id"], clock())])
            await box.step()
            row = (await _rows(opened))[0]
            assert row["sent_at"] is None and row["due_at"] == ist(MONDAY, 17) and row["payload"]
            clock.at = ist(MONDAY, 16, 59)
            assert await box.step() == 0
            clock.at = ist(MONDAY, 17)
            await box.step()
            row = (await _rows(opened))[0]
            assert row["sent_at"] == clock() and row["result"].startswith("sent")
            await http.aclose()
        finally:
            close(opened)

    run(go())


def test_a_summary_quiet_hours_outlast_is_dropped_with_an_audit_line(tmp_path):
    """A child's 16:05 summary: quiet until Tuesday 09:15, past its six hours: dropped."""

    async def go():
        opened = open_store(tmp_path)
        try:
            clock = Clock(ist(MONDAY, 16, 5))
            child, phone = signed(opened, clock(), role="child", privacy=CHILD)
            service, push, box, http = await _outbox(opened, tmp_path, clock)
            await push.route_push_subscribe(None, subscription(1), phone)
            await box.put([digest_row(child["id"], clock())])
            await box.step()
            row = (await _rows(opened))[0]
            assert row["payload"] is None and row["result"].startswith("dropped: quiet hours")
            lines = await opened.read(lambda tx: tx.rows("audit", kind="notice_dropped"))
            assert [ln["detail"] for ln in lines] == [
                {"channel": "push", "category": "digest", "why": "quiet"}]  # fmt: skip
            await http.aclose()
        finally:
            close(opened)

    run(go())


def test_a_sign_in_notice_is_never_quiet_and_goes_urgent(tmp_path):
    """Mutation target 21, at the outbox: a child's lock notice at 23:00 goes at once, high."""

    async def go():
        opened = open_store(tmp_path)
        try:
            clock, fake = Clock(ist(MONDAY, 23)), FakePushService()
            child, phone = signed(opened, clock(), role="child", privacy=CHILD)
            bot = BotStub()
            bot.chats[child["id"]] = 900_000_001
            service, push, box, http = await _outbox(opened, tmp_path, clock, fake, bot)
            await push.route_push_subscribe(None, subscription(1), phone)
            until = (clock() + 15 * 60_000) // 1000
            box.on_notice("lockout", child, attempts="5", until=str(until), device="Android 15")
            for _ in range(100):
                if box.counts["put"]:
                    break
                await asyncio.sleep(0)
            await box.step()
            rows = await _rows(opened)
            assert [r["result"] for r in rows] == ["sent to 1 of 1 phones", "sent"]
            assert all(r["payload"] is None for r in rows)
            sent = fake.headers()[-1]
            assert (sent["urgency"], sent["topic"]) == ("high", "security-lockout")
            text = bot.sent[-1][1]
            assert "locked after 5 wrong attempts" in text and "23:15" in text
            assert bot.sent[-1][2] == "alert"
            await http.aclose()
        finally:
            close(opened)

    run(go())


def test_backoff_holds_a_row_and_expiry_drops_it(tmp_path):
    async def go():
        opened = open_store(tmp_path)
        try:
            clock, fake = Clock(ist(MONDAY, 16, 5)), FakePushService()
            member, phone = signed(opened, clock())
            service, push, box, http = await _outbox(opened, tmp_path, clock, fake)
            await push.route_push_subscribe(None, subscription(1), phone)
            fake.queue(ENDPOINT.format(n=1), 503)
            await box.put([digest_row(member["id"], clock())])
            await box.step()
            row = (await _rows(opened))[0]
            assert row["sent_at"] is None and row["due_at"] == clock() + 600_000
            clock.at += 600_000
            await box.step()
            assert (await _rows(opened))[0]["result"] == "sent to 1 of 1 phones"
            # a row past its life is dropped, audited, never sent
            await box.put([digest_row(member["id"], clock(), ttl_s=60)])
            clock.at += 61_000
            await box.step()
            row = (await _rows(opened))[-1]
            assert row["result"] == "dropped: its time to live ran out" and row["payload"] is None
            lines = await opened.read(lambda tx: tx.rows("audit", kind="notice_dropped"))
            assert lines[-1]["detail"]["why"] == "expired" and len(fake.seen) == 2
            await http.aclose()
        finally:
            close(opened)

    run(go())


def test_telegram_rows_go_through_the_bot(tmp_path):
    async def go():
        opened = open_store(tmp_path)
        try:
            clock, bot = Clock(ist(MONDAY, 16, 5)), BotStub()
            member, _ = signed(opened, clock())
            other, _ = signed(opened, clock(), display_name="Member B")
            paused, _ = signed(opened, clock(), display_name="Member C")
            await opened.write(lambda tx: tx.update("member", {"id": paused["id"]},
                                                    {"status": "suspended"}))  # fmt: skip
            service, push, box, http = await _outbox(opened, tmp_path, clock, bot=bot)
            await box.put([digest_row(member["id"], clock(), channel="telegram")])
            await box.step()
            assert bot.sent[-1] == (member["id"], f"{words.HEADER}\n{CANARY}", "digest")
            assert (await _rows(opened))[-1]["result"] == "sent"
            bot.answer = "not_running"
            await box.put([digest_row(other["id"], clock(), channel="telegram")])
            await box.step()
            row = (await _rows(opened))[-1]
            assert row["sent_at"] is None and row["due_at"] == clock() + 60_000
            bot.answer = "not_linked"
            clock.at += 60_000
            await box.step()
            assert (await _rows(opened))[-1]["result"] == "Telegram is not linked"
            await box.put([digest_row(paused["id"], clock(), channel="telegram")])
            await box.step()
            assert (await _rows(opened))[-1]["result"] == "dropped: the member is not active"
            await http.aclose()
        finally:
            close(opened)

    run(go())


def test_rows_sent_a_week_ago_are_forgotten(tmp_path):
    async def go():
        opened = open_store(tmp_path)
        try:
            clock = Clock(ist(MONDAY, 16, 5))
            member, _ = signed(opened, clock())
            service, push, box, http = await _outbox(opened, tmp_path, clock)
            await box.put([digest_row(member["id"], clock())])
            await box.step()  # no phone: finished at once
            assert len(await _rows(opened)) == 1
            clock.at += 7 * 86_400_000 + 3_600_000
            await box.step()
            assert await _rows(opened) == []
            await http.aclose()
        finally:
            close(opened)

    run(go())


# ---- the test send --------------------------------------------------------------------------------
def test_the_test_send_says_what_happened_on_each_channel(tmp_path):
    async def go():
        opened = open_store(tmp_path)
        try:
            clock, fake, bot = Clock(ist(MONDAY, 18, 42, 6)), FakePushService(), BotStub()
            member, phone = signed(opened, clock())
            service, push, box, http = await _outbox(opened, tmp_path, clock, fake, bot)
            bot.answer = "not_linked"
            found = (await box.route_test_send(None, {}, phone)).payload["data"]
            assert found == [
                {"channel": "push", "sent": False, "at": None, "words": PAGE["test_push_off"]},
                {"channel": "telegram", "sent": False, "at": None, "words": "Not linked"},
            ]
            with pytest.raises(Refused) as refused:
                await box.route_test_send(None, {}, phone)
            assert refused.value.code == "busy"
            await push.route_push_subscribe(None, subscription(1), phone)
            bot.answer = "sent"
            clock.at += 10_000
            found = (await box.route_test_send(None, {}, phone)).payload["data"]
            assert found[0] == {"channel": "push", "sent": True, "at": clock(),
                                "words": "Sent 18:42:16"}  # fmt: skip
            assert found[1]["words"] == "Sent 18:42:16" and found[1]["sent"] is True
            assert fake.headers()[-1]["urgency"] == "normal" and fake.headers()[-1]["ttl"] == "60"
            fake.queue(ENDPOINT.format(n=1), 503)
            bot.answer = "not_running"
            clock.at += 10_000
            found = (await box.route_test_send(None, {}, phone)).payload["data"]
            assert found[0]["words"] == (
                "Not sent: Google's notification service is having trouble; try again later"
            )
            assert found[1]["words"] == PAGE["test_bot_down"]
            push.stop()
            clock.at += 10_000
            found = (await box.route_test_send(None, {}, phone)).payload["data"]
            assert found[0]["words"] == PAGE["test_push_no_server"]
            await http.aclose()
        finally:
            close(opened)

    run(go())


# ---- the day's summary: its lines -----------------------------------------------------------------
MARKET_VIEW = {"feed": "closed", "since": 1, "indices": [
    {"symbol": "Nifty 50", "name": "NIFTY 50", "ltp": 24310.55, "change_percent": 0.42},
    {"symbol": "Nifty Bank", "name": "NIFTY BANK", "ltp": 52100.2, "change_percent": -0.1}]}  # fmt: skip
SUMMARY = {"state": "closed", "read_at": 1, "words": None, "hidden": [],
           "positions": {"open": 3, "long": 2, "short": 1}, "pnl": {"pnl_paise": 123_450}}  # fmt: skip
HOLDINGS = {"part": "holdings", "state": "closed", "read_at": 1, "hidden": [], "rows": [
    {"symbol": "SYNTH1", "qty_u": 10_000_000, "last_price_u": 10_100_000,
     "close_price_u": 10_000_000},
    {"symbol": "SYNTH2", "qty_u": 5_000_000, "last_price_u": 9_950_000,
     "close_price_u": 10_000_000}]}  # fmt: skip
COUNTS = {"trades": 6, "won": 3, "lost": 2}


def test_the_summary_is_six_lines_at_most_through_the_mask():
    viewer = {"language": "en", "privacy": VIEWER}
    lines = digest_module.lines_for(viewer, MONDAY, market=MARKET_VIEW, summary=SUMMARY,
                                    holdings=HOLDINGS, counts=COUNTS, next_day=TUESDAY)  # fmt: skip
    assert lines == [
        "Today's summary · Mon 16 Nov",
        "NIFTY 50 24,310.55 +0.42% · NIFTY BANK 52,100.20 −0.10%",
        "Positions: 3 open · 2 long · 1 short · P&L +₹1,234.50",
        "Holdings today: +₹75.00",  # 10 x ₹10 up, 5 x ₹5 down
        "Trades today: 6 · won 3 · lost 2",
        "Next trading day: Tue 17 Nov",
    ]
    child = {"language": "en", "privacy": CHILD}
    hidden = {
        **SUMMARY,
        "hidden": ["pnl", "qty", "funds", "orders"],
        "positions": None,
        "pnl": None,
    }
    lines = digest_module.lines_for(child, MONDAY, market=MARKET_VIEW, summary=hidden,
                                    holdings=HOLDINGS, counts=COUNTS, next_day=TUESDAY)  # fmt: skip
    assert lines == ["Today's summary · Mon 16 Nov", lines[1], "Next trading day: Tue 17 Nov"]
    # orders shown, P&L hidden (Y16): the count, not won and lost; no holdings move
    counted = {"language": "en", "privacy": {**VIEWER, "pnl": False}}
    lines = digest_module.lines_for(counted, MONDAY, market=None, summary=SUMMARY, holdings=HOLDINGS,
                                    counts=COUNTS, next_day=None)  # fmt: skip
    assert lines == ["Today's summary · Mon 16 Nov", "Positions: 3 open · 2 long · 1 short",
                     "Trades today: 6"]  # fmt: skip
    # figures that are not current are left out, not shown old
    stale = {**SUMMARY, "state": "budget"}
    lines = digest_module.lines_for(viewer, MONDAY, market=None, summary=stale,
                                    holdings={**HOLDINGS, "state": "unreachable"}, counts=None,
                                    next_day=None)  # fmt: skip
    assert lines == ["Today's summary · Mon 16 Nov"]
    gu = digest_module.lines_for({"language": "gu", "privacy": VIEWER}, MONDAY, market=None,
                                 summary=SUMMARY, holdings=None, counts=None, next_day=TUESDAY)  # fmt: skip
    assert gu[0] == "આજનો સારાંશ · સોમ 16 નવે" and gu[-1] == "આગળનો ટ્રેડિંગ દિવસ: મંગળ 17 નવે"


def test_the_holdings_move_is_rounded_as_the_portfolio_rounds():
    rows = [{"qty_u": 3_000_000, "last_price_u": 10_000_005, "close_price_u": 10_000_000}]
    assert digest_module.holdings_today(rows) == 0  # 3 x 0.0005 = 0.0015 rupees, 0.15 paise
    rows = [{"qty_u": 1_000_000, "last_price_u": 10_000_050, "close_price_u": 10_000_000}]
    assert digest_module.holdings_today(rows) == 1  # 0.5 paise, half away from zero
    assert digest_module.holdings_today([{"qty_u": 1}]) is None


# ---- the day's summary: when, once, Y42 ------------------------------------------------------------
class Routes:
    """The viewer routes the summary reads, for every member alike."""

    def __init__(self, market: dict | None = None):
        self.market = market or MARKET_VIEW

    async def _summary(self, request, args, session):
        return Response.ok(SUMMARY)

    async def _book(self, request, args, session):
        return Response.ok(HOLDINGS)

    async def _market(self, request, args, session):
        return Response.ok(self.market)


class SpecialSunday(Weekdays):
    """Weekdays, and one Sunday with a window of its own (as a Muhurat session)."""

    def __init__(self, day: date, window: tuple[int, int]):
        self.day, self.window = day, window

    def is_trading_day(self, day) -> bool:
        return day == self.day or super().is_trading_day(day)

    def day_window(self, day):
        return self.window if day == self.day else None


async def _digest(opened, tmp_path, clock, calendar=None, journal=None, routes=None):
    fake = FakeTelegram(new_token())
    service = fake_service(opened, clock, tmp_path / "etc", routes or Routes())
    bot, _ = make_bot(service, fake, tmp_path / "etc")
    await ready(bot)
    market = MarketHours(calendar or Weekdays())
    push = push_module.Push(service, write_vapid(tmp_path / "etc"))
    box = Outbox(service, push=push, bot=bot, market=market)
    made = digest_module.Digest(service, outbox=box, bot=bot, market=market,
                                schedule=Schedule(calendar or Weekdays(), eod_after_min=25),
                                journal=journal)  # fmt: skip
    box.render = made.render
    made.telegram = fake  # the test reads what reached the chat
    return made, bot


def test_the_summary_is_made_at_the_last_close_plus_25_minutes_once(tmp_path):
    """Mutation target 20, for the summary: its rows carry urgency low, its topic and TTL 6 h."""

    async def go():
        opened = open_store(tmp_path)
        try:
            clock = Clock(ist(MONDAY, 16, 4))
            member = add_member(opened, status="active", privacy=VIEWER)
            add_member(opened, status="active", display_name="Member B",
                       notify={"digest_push": False, "digest_telegram": True})  # fmt: skip
            made, bot = await _digest(opened, tmp_path, clock)
            bot.chats[member["id"]] = 900_000_001
            assert await made.step(clock()) is None and await _rows(opened) == []
            clock.at = ist(MONDAY, 16, 5)
            assert await made.step(clock()) == "sent"
            rows = await _rows(opened)
            assert [(r["member_id"], r["channel"]) for r in rows] == [
                (member["id"], "push"),
                (member["id"], "telegram"),
            ]  # B: Telegram, not linked
            assert {(r["urgency"], r["ttl_s"], r["category"]) for r in rows} == {
                ("low", 21_600, "digest")}  # fmt: skip
            assert rows[0]["topic"] == "digest-20261116" and rows[1]["topic"] is None
            stored = json.loads(rows[0]["payload"])
            assert stored["title"] == words.HEADER and stored["url"] == "/family"
            assert "body" not in stored  # Y44: made when it is sent
            body = (await made.render(member, stored))["body"]
            assert body.splitlines()[0] == "Today's summary · Mon 16 Nov"
            assert "P&L +₹1,234.50" in body and len(body.splitlines()) <= 6
            job = await opened.read(lambda tx: tx.job("digest", 20261116))
            assert job["status"] == "ok" and job["detail"] == "2 messages"
            clock.at = ist(MONDAY, 18)
            assert await made.step(clock()) is None and len(await _rows(opened)) == 2
            await settle(bot)
        finally:
            close(opened)

    run(go())


def test_a_missed_summary_runs_until_21_00_and_is_then_skipped(tmp_path):
    """Y42: a late start runs the day's summary up to 21:00; after that the day is skipped."""

    async def go():
        opened = open_store(tmp_path)
        try:
            add_member(opened, status="active", privacy=VIEWER)
            clock = Clock(ist(MONDAY, 20, 59))
            made, bot = await _digest(opened, tmp_path, clock)
            assert await made.step(clock()) == "sent"
            clock.at = ist(TUESDAY, 21)
            assert await made.step(clock()) == "skipped"
            job = await opened.read(lambda tx: tx.job("digest", 20261117))
            assert job["status"] == "skipped" and "21:00" in job["detail"]
            assert len(await _rows(opened)) == 1  # Monday's only
            clock.at = ist(MONDAY.replace(day=21), 16, 5)  # a Saturday: no summary
            assert await made.step(clock()) is None
            await settle(bot)
        finally:
            close(opened)

    run(go())


def test_a_special_day_has_its_own_summary_time(tmp_path):
    async def go():
        opened = open_store(tmp_path)
        try:
            add_member(opened, status="active", privacy=VIEWER)
            sunday = MONDAY.replace(day=22)
            calendar = SpecialSunday(sunday, (13 * 60, 14 * 60 + 30))
            clock = Clock(ist(sunday, 14, 54))
            made, bot = await _digest(opened, tmp_path, clock, calendar)
            assert await made.step(clock()) is None
            clock.at = ist(sunday, 14, 55)  # the window's F&O close 14:30, plus 25 minutes
            assert await made.step(clock()) == "sent"
            await settle(bot)
        finally:
            close(opened)

    run(go())


class FakeJournal:
    """The journal's sealed door for ``family_day`` (counts only), on 127.0.0.1."""

    def __init__(self, fernet: Fernet):
        self.fernet, self.nonces, self.asked = fernet, NonceBook(64), []
        self.server = None

    async def start(self) -> str:
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        return f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"

    async def close(self) -> None:
        self.server.close()
        await self.server.wait_closed()

    async def _serve(self, reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            length = next(int(v) for k, _, v in (ln.partition(":") for ln in lines[1:])
                          if k.strip().lower() == "content-length")  # fmt: skip
            message_ = open_sealed(self.fernet, await reader.readexactly(length), op="family_day",
                                   seen=self.nonces)  # fmt: skip
            self.asked.append((lines[0].split(" ")[1], message_["principal"], message_["args"]))
            data = json.dumps({"status": "success", "data": {"day": message_["args"]["day"],
                                                             **COUNTS}}).encode()  # fmt: skip
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                         + str(len(data)).encode() + b"\r\nConnection: close\r\n\r\n" + data)  # fmt: skip
            await writer.drain()
        finally:
            writer.close()


def test_the_journals_counts_make_the_trades_line_and_its_absence_none(tmp_path):
    async def go():
        opened = open_store(tmp_path)
        fernet = Fernet(Fernet.generate_key())
        journal = FakeJournal(fernet)
        url = await journal.start()
        http = digest_module.make_http(url)
        try:
            add_member(opened, status="active", privacy=VIEWER)
            clock = Clock(ist(MONDAY, 16, 5))
            client = digest_module.Journal(url, fernet, http)
            made, bot = await _digest(opened, tmp_path, clock, journal=client)
            await made.step(clock())
            assert journal.asked == []  # asked when a row is sent, not when it is put
            member = (await opened.read(lambda tx: tx.members_live()))[0]
            body = (await made.render(member, json.loads((await _rows(opened))[0]["payload"])))[
                "body"]  # fmt: skip
            assert "Trades today: 6 · won 3 · lost 2" in body
            assert journal.asked == [("/v1/family_day", "sudarshan-family", {"day": 20261116})]
            await made.render(member, json.loads((await _rows(opened))[0]["payload"]))
            assert len(journal.asked) == 1  # asked once a day, whatever the rows
            # the client reaches the journal's port and nothing else
            with pytest.raises(Exception, match="may not reach"):
                await http.get("http://127.0.0.1:5000/")
            down = digest_module.Journal("http://127.0.0.1:9", fernet,
                                         digest_module.make_http("http://127.0.0.1:9"))  # fmt: skip
            assert await down.counts(20261116) is None
            await down.http.aclose()
            await settle(bot)
        finally:
            await http.aclose()
            await journal.close()
            close(opened)

    run(go())


# ---- through the running service ------------------------------------------------------------------
async def _wait(check, rounds: int = 600):
    for _ in range(rounds):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("not reached in time")


def _headers(cookie: str) -> dict[str, str]:
    csrf = "c" * 43
    return {"Cookie": f"{cookie}; {CSRF_COOKIE}={csrf}", CSRF_HEADER: csrf,
            "Content-Type": "application/json"}  # fmt: skip


def test_every_channel_route_is_seated_and_a_new_phone_notice_reaches_the_other_phone(
    tmp_path, monkeypatch
):
    async def go():
        fake_push, fake_telegram = FakePushService(), FakeTelegram(new_token())
        monkeypatch.setattr(push_module, "make_http", fake_push.http)
        monkeypatch.setattr(bot_module, "make_http", fake_telegram.http)
        etc = tmp_path / "etc"
        write_vapid(etc)
        (etc / bot_module.TOKEN_FILE).write_text(fake_telegram.token + "\n")
        clock = Clock(ist(MONDAY, 12))
        service = FamilyService(settings(tmp_path), keys(), hasher=fast_hasher(), clock=clock,
                                jobs={})  # fmt: skip
        async with running(service):
            port = service.listener.port
            await _wait(lambda: service.channels.bot.ready)

            def make(tx):
                member = tx.member_add({"display_name": "Member A", "role": "viewer",
                                        "status": "active", "privacy": VIEWER, "language": "en"},
                                       clock())  # fmt: skip
                device = sessions.Device("Android 15 · Chrome 130", "Android 15 · Chrome 130")
                session_id, _ = sessions.mint(tx, member["id"], device, clock())
                return member, session_id

            member, session_id = await service.store.write(make)
            head = _headers(f"{COOKIE}={session_id}")
            say = lambda method, path, body=None: ask(  # noqa: E731
                port, method, f"/family/api{path}", headers=head,
                body=json.dumps(body).encode() if body is not None else b"")  # fmt: skip
            status, _, answer = await say("GET", "/settings")
            assert status == 200 and answer["data"]["telegram_linked"] is False
            status, _, answer = await say("PUT", "/settings", {
                "version": answer["data"]["version"], "notify": {"digest_push": True,
                                                                 "digest_telegram": False}})  # fmt: skip
            assert status == 200, answer
            status, _, answer = await say("GET", "/push/key")
            assert status == 200 and len(answer["data"]["key"]) == 87
            status, _, answer = await say("POST", "/push/subscriptions", subscription(1))
            assert status == 200, answer
            status, _, answer = await say("POST", "/notify/test", {})
            assert status == 200 and answer["data"][0]["sent"] is True
            status, _, answer = await say("POST", "/telegram/link", {})
            assert status == 200 and answer["data"]["url"].startswith("https://t.me/")
            status, _, answer = await say("POST", "/telegram/unlink", {})
            assert status == 200
            status, _, answer = await say("POST", "/push/subscriptions/remove",
                                          {"endpoint": "https://fcm.googleapis.com/fcm/send/none"})  # fmt: skip
            assert status == 200
            # a sign-in from a new phone: the member's other phone is told, urgently
            service.channels.outbox.on_notice(
                "new_device", member, device="Android 16 · Chrome 131"
            )
            await _wait(lambda: service.channels.outbox.counts["put"] >= 1)
            service.channels.outbox.wake()
            await _wait(lambda: any(h.get("topic") == "security-new_device"
                                    for h in fake_push.headers()))  # fmt: skip
            assert fake_push.headers()[-1]["urgency"] == "high"
            status, _, health = await ask(port, "GET", "/health")
            channels = health["family_state"]["channels"]
            assert channels["push"]["keys"] is True and channels["outbox"]["notices"] == 1
            assert "SYNTH" not in json.dumps(health) and "endpoint" not in json.dumps(health)

    run(go())


def test_pushes_over_real_sockets_leave_no_descriptor_and_a_stop_leaves_nothing(
    tmp_path, monkeypatch
):
    """fd-audit for F10: a hundred pushes through the outbox to a local push service (a real
    connection each) keep the descriptor count flat; after the stop, nothing opened since the
    start is open and no task is left."""
    from sudarshan.tests.fdwatch import FdWatch

    def count() -> int:
        return len(os.listdir("/proc/self/fd"))

    async def go():
        fake = FakePushService()
        server = PushServer(fake)
        await server.start()
        monkeypatch.setattr(push_module, "make_http", server.http)
        etc = tmp_path / "etc"
        write_vapid(etc)
        clock = Clock(ist(MONDAY, 12))
        service = FamilyService(settings(tmp_path), keys(), hasher=fast_hasher(), clock=clock,
                                jobs={})  # fmt: skip
        watch = FdWatch()
        try:
            async with running(service):
                member, phone = await service.store.write(lambda tx: _member_and_phone(tx, clock()))
                await service.channels.push.route_push_subscribe(None, subscription(1), phone)
                box = service.channels.outbox

                async def batch(n: int) -> None:
                    goal = len(fake.seen) + n
                    for _ in range(n):
                        await box.put([digest_row(member["id"], clock())])
                    await _wait(lambda: len(fake.seen) >= goal, rounds=3000)

                await batch(5)
                await asyncio.sleep(0.2)
                before = count()
                await batch(100)
                await asyncio.sleep(0.2)
                assert count() <= before, (before, count())
            await watch.aassert_clean()
            assert [t for t in asyncio.all_tasks() if t is not asyncio.current_task()] == []
        finally:
            await server.close()

    run(go())


def _member_and_phone(tx, now: int):
    member = tx.member_add({"display_name": "Member A", "role": "viewer", "status": "active",
                            "privacy": VIEWER, "language": "en"}, now)  # fmt: skip
    device = sessions.Device("Android 15 · Chrome 130", "Android 15 · Chrome 130")
    session_id, _ = sessions.mint(tx, member["id"], device, now)
    row = tx.row("viewer_session", id_hash=sessions.id_hash(session_id))
    return member, sessions.Session(row["id_hash"], member, row)


def test_market_sessions_give_the_summary_time_the_design_names():
    """16:05 on a normal day before 7 Dec 2026 (F&O closes 15:40), 16:10 after (15:45)."""
    schedule = Schedule(Weekdays(), eod_after_min=25)
    assert schedule.eod_at(MONDAY) == ist(MONDAY, 16, 5)
    december = date(2026, 12, 7)
    assert schedule.eod_at(december) == ist(december, 16, 10)
    assert market_sessions.day_key(MONDAY) == 20261116


def test_the_hourly_tidy_forgets_signed_out_phones_of_a_member_sent_nothing(tmp_path):
    """fd-audit, the store: a member who is sent nothing keeps no signed-out phone's
    subscription past the next tidy."""

    async def go():
        opened = open_store(tmp_path)
        try:
            clock = Clock(ist(MONDAY, 12))
            member, phone = signed(opened, clock())
            service, push, box, http = await _outbox(opened, tmp_path, clock)
            await push.route_push_subscribe(None, subscription(1), phone)
            await opened.write(lambda tx: sessions.revoke(tx, phone.id_hash, clock(), "logout"))
            await box.step()  # the first step tidies
            assert await opened.read(lambda tx: tx.rows("push_subscription")) == []
            await http.aclose()
        finally:
            close(opened)

    run(go())
