"""F8: the family bot. Its words in both languages, link codes (hashed, ten minutes, one use), the
command table and the refusal, an unlinked chat told only how to link, answers through the
member's mask (end to end through the data plane), a pacer per chat, the token (missing, refused,
changed, never logged), a removed member's last line, the owner's state answer, and a stop that
leaves nothing behind."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from family_botkit import (
    USERNAME,
    FakeTelegram,
    TelegramServer,
    fake_service,
    make_bot,
    new_token,
    ready,
    settle,
    update,
)
from family_net import ask, fast_hasher, keys, running, sealed, settings
from family_planekit import (
    CHILD,
    MONDAY,
    VIEWER,
    Clock,
    FakePortfolio,
    books_answer,
    ist,
    position,
    signed_in,
)
from family_testkit import add_member, close, open_store

from sudarshan.family import bot as bot_module
from sudarshan.family import bot_words as words
from sudarshan.family.books import PlaneKeys
from sudarshan.family.contract import COOKIE, CSRF_COOKIE, CSRF_HEADER
from sudarshan.family.listener import Response
from sudarshan.family.service import FamilyService

CHAT_A, CHAT_B = 900_000_001, 900_000_002
REFUSAL = words.say("refusal")


def run(coro):
    try:
        return asyncio.run(coro)
    finally:
        close(None)


def asker(member_id: int) -> SimpleNamespace:
    return SimpleNamespace(member_id=member_id)


# ---- the words ------------------------------------------------------------------------------------
def _facts(text: str) -> set[str]:
    import string

    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_every_line_has_both_languages_and_the_same_facts():
    assert words.LANGUAGES == ("en", "gu")
    for line, columns in words.LINES.items():
        assert isinstance(columns, tuple) and len(columns) == 2, line
        english, gujarati = columns
        assert english.strip() and gujarati.strip(), line
        assert _facts(english) == _facts(gujarati), line
        assert any("઀" <= ch <= "૿" for ch in gujarati), f"{line} is not in Gujarati"
    assert words.say("refusal") == (
        "This bot only shows information. It cannot place, change or cancel an order."
    )
    assert words.HEADER == "सुदर्शन / SUDARSHAN · Family"


#: Symbols a line may carry besides letters, marks, digits, punctuation and spaces.
ALLOWED_SYMBOLS = {"₹", words.MINUS, "+", "&", "<", ">", "="}


def _icon(ch: str) -> bool:
    category = unicodedata.category(ch)
    if ch in ALLOWED_SYMBOLS or category[0] in "LMNPZ":
        return ch in "‍︎️" or "︀" <= ch <= "️"
    return True


def test_no_line_carries_an_icon_or_emoji():
    texts = [*words.HEADER, *(t for pair in words.LINES.values() for t in pair)]
    texts += [*words.DAYS[0], *words.DAYS[1], *words.MONTHS[0], *words.MONTHS[1]]
    for text in texts:
        found = [ch for ch in text if _icon(ch) and ch != "\n"]
        assert not found, (text, found)
    assert _icon(chr(0x1F642)) and _icon("✅") and _icon("️")  # the check finds them


def test_numbers_are_written_the_indian_way_with_a_true_minus():
    assert words.grouped(1234567) == "12,34,567" and words.grouped(999) == "999"
    assert words.money(123_450) == "+₹1,234.50"
    assert words.money(-10_000_000_05) == "−₹1,00,00,000.05"
    assert words.money(0) == "₹0.00"
    assert words.price(10_100_500) == "₹1,010.05" and words.price(10_100_549) == "₹1,010.05"
    assert words.price(10_100_550) == "₹1,010.06"
    assert words.quantity(10_000_000) == "10" and words.quantity(109_459_000) == "109.459"
    assert words.quantity(-50_000_000) == "50"
    assert words.percent(0.4234) == "+0.42%" and words.percent(-1.05) == "−1.05%"
    assert words.day_words(MONDAY) == "Mon 16 Nov" and words.day_words(MONDAY, "gu") == "સોમ 16 નવે"


def test_parse_reads_commands_and_nothing_else():
    parse = bot_module.parse
    assert parse("/positions") == ("positions", "")
    assert parse("  /Quiet ON ") == ("quiet", "ON")
    assert parse(f"/status@{USERNAME}", USERNAME) == ("status", "")
    assert parse("/status@SomeOtherBot", USERNAME) == (None, "")
    for text in (
        "positions",
        "",
        "/",
        "/ positions",
        "//x",
        None,
        5,
        "/pos-itions",
        "/" + "x" * 600,
    ):
        assert parse(text, USERNAME) == (None, ""), text


# ---- linking --------------------------------------------------------------------------------------
def test_a_link_code_is_hashed_lasts_ten_minutes_and_works_once(tmp_path):
    async def go():
        opened = open_store(tmp_path)
        try:
            clock = Clock(ist(MONDAY, 18))
            member = add_member(opened, status="active")
            other = add_member(opened, status="active", display_name="Member B")
            fake = FakeTelegram(new_token())
            made, _ = make_bot(
                fake_service(opened, clock, tmp_path / "etc"), fake, tmp_path / "etc"
            )
            with pytest.raises(bot_module.Refused) as refused:
                await made.route_telegram_link(None, {}, asker(member["id"]))
            assert refused.value.code == "not_ready" and "not set up" in str(refused.value)
            await ready(made)
            answer = await made.route_telegram_link(None, {}, asker(member["id"]))
            link = answer.payload["data"]
            assert link["expires_at"] == clock() + 600_000
            assert link["url"].startswith(f"https://t.me/{USERNAME}?start=")
            code = link["url"].split("start=", 1)[1]
            assert bot_module.LINK_CODE_RE.fullmatch(code)
            row = await opened.read(lambda tx: tx.row("telegram_link", member_id=member["id"]))
            assert row["link_code_hash"] == hashlib.sha256(code.encode()).hexdigest()
            assert row["chat_id_enc"] is None
            # the code from a chat: linked, and the chat id is kept sealed
            await made.handle(update(f"/start {code}", CHAT_A, date=clock() // 1000))
            assert made.chats == {member["id"]: CHAT_A}
            assert fake.texts(CHAT_A)[-1].startswith(words.say("linked"))
            row = await opened.read(lambda tx: tx.row("telegram_link", member_id=member["id"]))
            assert row["link_code_hash"] is None and row["chat_id_enc"]
            assert str(CHAT_A) not in row["chat_id_enc"]
            stored = b"".join(p.read_bytes() for p in tmp_path.glob("family.db*"))
            assert code.encode() not in stored and str(CHAT_A).encode() not in stored
            lines = await opened.read(lambda tx: tx.rows("audit", kind="telegram_linked"))
            assert [line["member_id"] for line in lines] == [member["id"]]
            # used once: the same code from another chat links nothing
            await made.handle(update(f"/start {code}", CHAT_B, date=clock() // 1000))
            assert made.chats == {member["id"]: CHAT_A}
            assert fake.texts(CHAT_B) == [words.both("link_first")]
            # ten minutes: a code made now and used after its end links nothing
            answer = await made.route_telegram_link(None, {}, asker(other["id"]))
            late = answer.payload["data"]["url"].split("start=", 1)[1]
            clock.at += 600_000
            made._told.clear()
            await made.handle(update(f"/start {late}", CHAT_B, date=clock() // 1000))
            assert other["id"] not in made.chats and len(fake.texts(CHAT_B)) == 2
            await settle(made)
        finally:
            close(opened)

    run(go())


def test_an_unlinked_chat_is_told_only_how_to_link_and_at_most_once_a_minute(tmp_path):
    async def go():
        opened = open_store(tmp_path)
        try:
            clock = Clock(ist(MONDAY, 11))
            fake = FakeTelegram(new_token())
            made, ticks = make_bot(fake_service(opened, clock, tmp_path / "etc"), fake,
                                   tmp_path / "etc")  # fmt: skip
            await ready(made)
            for text in ("/positions", "/status", "/help", "hello", "/orders", None, "/stop"):
                await made.handle(update(text, CHAT_A, date=clock() // 1000))
            assert fake.sent == []  # nothing at all for a chat nobody linked
            await made.handle(update("/start", CHAT_A, date=clock() // 1000))
            await made.handle(update("/start wrong-code-0000000000", CHAT_A, date=clock() // 1000))
            assert fake.texts(CHAT_A) == [words.both("link_first")]
            ticks.now += bot_module.TELL_EVERY_S
            await made.handle(update("/start", CHAT_A, date=clock() // 1000))
            assert len(fake.texts(CHAT_A)) == 2
            # a group is never answered, linked or not
            group = update("/start", CHAT_B, date=clock() // 1000)
            group["message"]["chat"]["type"] = "group"
            await made.handle(group)
            assert fake.texts(CHAT_B) == []
            await settle(made)
        finally:
            close(opened)

    run(go())


# ---- the command table ----------------------------------------------------------------------------
class Viewer:
    """The viewer routes the bot reads, answering a fixed book, portfolio and market."""

    def __init__(self, hidden: tuple[str, ...] = ()):
        self.hidden = list(hidden)
        self.asked: list[tuple[str, dict]] = []

    async def _book(self, request, args, session):
        self.asked.append(("book", args))
        part = args["part"]
        if part == "orders" and "orders" in self.hidden:
            return Response.ok({"part": part, "state": "not_shared", "read_at": None,
                                "words": "Not shared with you.", "hidden": self.hidden,
                                "rows": []})  # fmt: skip
        row = {"symbol": "SYNTH1", "exchange": "NSE", "last_price_u": 10_100_000,
               "transaction_type": "BUY", "status": "complete", "side": "long"}  # fmt: skip
        if "qty" not in self.hidden:
            row |= {"qty_u": 2_000_000, "price_u": 10_000_000}
        if "pnl" not in self.hidden:
            row["pnl_paise"] = 20_000
        return Response.ok({"part": part, "state": "ok", "read_at": ist(MONDAY, 11),
                            "words": None, "hidden": self.hidden, "rows": [row]})  # fmt: skip

    async def _portfolio(self, request, args, session):
        self.asked.append(("portfolio", args))
        return Response.ok({"read_at": 1, "state": "ok", "words": None, "hidden": self.hidden,
                            "tabs": [{"id": 3, "name": "Long term", "kind": "custom",
                                      "level": "view", "expires_at": None}],
                            "summary": {"entries": [{"tab": {"id": 3}, "live": {"totals": {
                                "value_paise": 1_00_00_000, "today": {
                                    "state": "ok", "paise": 1_234}}}}]}})  # fmt: skip


@pytest.mark.parametrize("language", ["en", "gu"])
def test_every_command_of_the_table_answers_and_anything_else_is_refused(tmp_path, language):
    async def go():
        opened = open_store(tmp_path)
        try:
            clock = Clock(ist(MONDAY, 11))
            member = add_member(opened, status="active", language=language)
            fake = FakeTelegram(new_token())
            made, _ = make_bot(fake_service(opened, clock, tmp_path / "etc", Viewer()), fake,
                               tmp_path / "etc")  # fmt: skip
            await ready(made)
            made.chats[member["id"]] = CHAT_A
            refusal = words.say("refusal", language)
            asks = {"quiet": "/quiet on", "language": f"/language {language}"}
            for command in bot_module.COMMANDS:
                if command == "stop":
                    continue
                before = len(fake.sent)
                await made.handle(update(asks.get(command, f"/{command}"), CHAT_A,
                                         date=clock() // 1000))  # fmt: skip
                assert len(fake.sent) == before + 1, command
                answer = fake.sent[-1]["text"]
                assert answer != refusal, command
                if command != "help":  # help lists the refusal at its end, as a reminder
                    assert refusal not in answer, command
            for text in ("/buy SYNTH1", "/sell", "/exit", "/cancel 1", "/placeorder", "/modify",
                         "/closeall", "buy 10 SYNTH1", "hello", "/positionsnow", None):  # fmt: skip
                await made.handle(update(text, CHAT_A, date=clock() // 1000))
                assert fake.sent[-1]["text"] == refusal, text
            await made.handle(update("/stop", CHAT_A, date=clock() // 1000))
            assert fake.sent[-1]["text"] == words.say("unlinked", language) and not made.chats
            await settle(made)
        finally:
            close(opened)

    run(go())


def test_book_answers_say_what_is_hidden_by_leaving_it_out(tmp_path):
    async def go():
        opened = open_store(tmp_path)
        try:
            clock = Clock(ist(MONDAY, 11))
            child = add_member(opened, status="active", role="child")
            fake = FakeTelegram(new_token())
            hidden = ("pnl", "qty", "funds", "orders")
            made, _ = make_bot(fake_service(opened, clock, tmp_path / "etc", Viewer(hidden)),
                               fake, tmp_path / "etc")  # fmt: skip
            await ready(made)
            made.chats[child["id"]] = CHAT_A
            await made.handle(update("/positions", CHAT_A, date=clock() // 1000))
            text = fake.sent[-1]["text"]
            assert "SYNTH1" in text and "₹1,010.00" in text and "Figures from 11:00:00." in text
            assert "P&L" not in text and "Qty" not in text
            assert "NSE · Long · ₹1,010.00" in text  # the direction is never hidden (design 3.1)
            await made.handle(update("/orders", CHAT_A, date=clock() // 1000))
            assert fake.sent[-1]["text"] == words.say("orders_hidden")
            await made.handle(update("/portfolio", CHAT_A, date=clock() // 1000))
            text = fake.sent[-1]["text"]
            assert "Long term" in text and "Value" not in text and "Today" not in text
            await settle(made)
        finally:
            close(opened)

    run(go())


def test_quiet_and_language_change_the_member_and_are_audited(tmp_path):
    async def go():
        opened = open_store(tmp_path)
        try:
            clock = Clock(ist(MONDAY, 11))
            member = add_member(opened, status="active")
            fake = FakeTelegram(new_token())
            made, _ = make_bot(
                fake_service(opened, clock, tmp_path / "etc"), fake, tmp_path / "etc"
            )
            await ready(made)
            made.chats[member["id"]] = CHAT_A
            say = lambda text: made.handle(update(text, CHAT_A, date=clock() // 1000))  # noqa: E731
            await say("/quiet off")
            row = await opened.read(lambda tx: tx.member(member["id"]))
            assert row["quiet"] == {
                "from": "21:00",
                "to": "08:00",
                "digest": False,
                "alerts": False,
            }
            assert fake.sent[-1]["text"] == words.say("quiet_off")
            await say("/quiet maybe")
            assert fake.sent[-1]["text"] == words.say("quiet_usage")
            await say("/language gu")
            row = await opened.read(lambda tx: tx.member(member["id"]))
            assert row["language"] == "gu" and fake.sent[-1]["text"] == words.say(
                "language_set", "gu"
            )
            await say("/quiet on")
            assert fake.sent[-1]["text"] == words.say("quiet_on", "gu", start="21:00", end="08:00")
            lines = await opened.read(lambda tx: tx.rows("audit", kind="member_updated"))
            assert [ln["detail"]["fields"] for ln in lines] == [["quiet"], ["language"], ["quiet"]]
            assert all(ln["actor"] == f"member:{member['id']}" for ln in lines)
            await settle(made)
        finally:
            close(opened)

    run(go())


def test_a_paused_member_is_told_so_and_can_still_unlink(tmp_path):
    async def go():
        opened = open_store(tmp_path)
        try:
            clock = Clock(ist(MONDAY, 11))
            member = add_member(opened, status="suspended")
            fake = FakeTelegram(new_token())
            made, _ = make_bot(fake_service(opened, clock, tmp_path / "etc", Viewer()), fake,
                               tmp_path / "etc")  # fmt: skip
            await ready(made)
            made.chats[member["id"]] = CHAT_A
            await made.handle(update("/positions", CHAT_A, date=clock() // 1000))
            assert fake.sent[-1]["text"] == words.say("paused")
            await made.handle(update("/stop", CHAT_A, date=clock() // 1000))
            assert fake.sent[-1]["text"] == words.say("unlinked") and not made.chats
            # a member whose sign-in is being reset is not told it was paused
            reset = add_member(opened, status="invited", display_name="Member B")
            made.chats[reset["id"]] = CHAT_B
            await made.handle(update("/positions", CHAT_B, date=clock() // 1000))
            assert fake.sent[-1]["text"] == words.say("not_active")
            await settle(made)
        finally:
            close(opened)

    run(go())


def test_a_message_sent_while_the_bot_was_away_is_not_answered(tmp_path):
    async def go():
        opened = open_store(tmp_path)
        try:
            clock = Clock(ist(MONDAY, 11))
            member = add_member(opened, status="active")
            fake = FakeTelegram(new_token())
            made, _ = make_bot(
                fake_service(opened, clock, tmp_path / "etc"), fake, tmp_path / "etc"
            )
            await ready(made)
            made.chats[member["id"]] = CHAT_A
            old = clock() // 1000 - bot_module.STALE_S - 1
            await made.handle(update("/help", CHAT_A, date=old))
            assert fake.sent == []
            await settle(made)
        finally:
            close(opened)

    run(go())


# ---- pacing ---------------------------------------------------------------------------------------
def test_one_pacer_per_chat_at_one_message_a_second(tmp_path):
    async def go():
        fake = FakeTelegram(new_token())
        service = fake_service(None, Clock(ist(MONDAY, 11)), tmp_path / "etc")
        made, ticks = make_bot(service, fake, tmp_path / "etc")
        await ready(made)
        stamps: list[tuple[int, float]] = []
        real_send = made.client.send_message

        async def send(token, chat, text):
            stamps.append((chat, ticks.now))
            return await real_send(token, chat, text)

        made.client.send_message = send
        for _ in range(3):
            await made.send(CHAT_A, "a")
        await made.send(CHAT_B, "b")
        a = [at for chat, at in stamps if chat == CHAT_A]
        assert [round(y - x, 3) for x, y in zip(a, a[1:], strict=False)] == [1.0, 1.0]
        assert stamps[-1] == (CHAT_B, a[-1])  # another chat does not wait for this one
        # a digest gives way while an answer for that chat waits
        made._alerts_waiting[CHAT_A] = 1
        task = asyncio.ensure_future(made.send(CHAT_A, "digest", "digest"))
        for _ in range(5):
            await asyncio.sleep(0)
        assert not task.done()
        made._alerts_waiting[CHAT_A] = 0
        assert (await task).outcome == "ok"
        await settle(made)

    run(go())


def test_a_slow_down_is_waited_out_and_the_message_sent_again(tmp_path):
    async def go():
        fake = FakeTelegram(new_token())
        made, ticks = make_bot(fake_service(None, Clock(1), tmp_path / "etc"), fake,
                               tmp_path / "etc")  # fmt: skip
        await ready(made)
        fake.slow = 1
        answer = await made.send(CHAT_A, "x")
        assert answer.outcome == "ok" and fake.texts() == ["x"] and 2 in ticks.slept
        fake.slow = 5
        assert (await made.send(CHAT_A, "y")).outcome == "slow_down"  # three tries, then later
        fake.slow, fake.block = 0, True
        made.chats[7] = CHAT_A
        assert await made.deliver(7, "z") == "blocked"
        assert await made.deliver(8, "z") == "not_linked"
        await settle(made)

    run(go())


# ---- the token ------------------------------------------------------------------------------------
def test_the_token_missing_refused_and_changed_and_never_logged(tmp_path, caplog):
    async def go():
        etc = tmp_path / "etc"
        fake = FakeTelegram(new_token())
        service = fake_service(None, Clock(1), etc)
        made, ticks = make_bot(service, fake, etc)
        token_file = etc / bot_module.TOKEN_FILE
        token_file.unlink()
        poll = asyncio.ensure_future(made.poll())

        async def until(check):
            for _ in range(400):
                if check():
                    return
                await asyncio.sleep(0)
            raise AssertionError("not reached")

        await until(lambda: ticks.slept)
        assert made.state == "token_missing" and made.report() == {
            "state": "token_missing", "last_message_at": None}  # fmt: skip
        token_file.write_text("not a token\n", encoding="ascii")
        ticks.slept.clear()
        await until(lambda: ticks.slept)
        assert made.state == "token_missing"
        fake.refuse = True
        token_file.write_text(fake.token + "\n", encoding="ascii")
        await until(lambda: made.state == "token_refused")
        assert service.notices.seen == [("bot_token_rejected", {
            "key": hashlib.sha256(fake.token.encode()).hexdigest()[:12]})]  # fmt: skip
        for _ in range(5):  # still refused: told once, not at every look
            ticks.slept.clear()
            await until(lambda: ticks.slept)
        assert len(service.notices.seen) == 1
        fake.refuse = False
        fresh = new_token()
        fake.token = fresh
        token_file.write_text(fresh + "\n", encoding="ascii")
        await until(lambda: made.state == "polling")
        assert made.username == USERNAME
        from sudarshan.tasks import cancel_and_wait

        await cancel_and_wait(poll)
        await made.client.http.aclose()
        text = caplog.text
        assert fresh not in text and fake.token not in text and "Telegram refused" in text
        assert fresh.split(":")[1] not in text

    with caplog.at_level(logging.DEBUG):
        run(go())


def test_another_program_polling_the_token_is_said_in_words_once(tmp_path, caplog):
    async def go():
        fake = FakeTelegram(new_token())
        made, ticks = make_bot(fake_service(None, Clock(1), tmp_path / "etc"), fake,
                               tmp_path / "etc")  # fmt: skip
        fake.conflict = True
        poll = asyncio.ensure_future(made.poll())
        for _ in range(400):
            if made.counts["conflicts"] >= 3:
                break
            await asyncio.sleep(0)
        from sudarshan.tasks import cancel_and_wait

        await cancel_and_wait(poll)
        await made.client.http.aclose()
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert len([w for w in warnings if "another program" in w]) == 1
        assert bot_module.CONFLICT_WAIT_S in ticks.slept

    with caplog.at_level(logging.INFO, logger="sudarshan.family"):
        run(go())


# ---- through the running service ------------------------------------------------------------------
async def _wait(check, rounds: int = 600):
    for _ in range(rounds):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("not reached in time")


async def _link(service, fake: FakeTelegram, cookie: str, chat: int, now_s: int) -> None:
    """Link a member's chat the way a phone and Telegram do: the route, then /start <code>."""
    csrf = "c" * 43
    status, _, answer = await ask(
        service.listener.port, "POST", "/family/api/telegram/link", body=b"{}",
        headers={"Cookie": f"{cookie}; {CSRF_COOKIE}={csrf}", CSRF_HEADER: csrf,
                 "Content-Type": "application/json"},
    )  # fmt: skip
    assert status == 200, answer
    code = answer["data"]["url"].split("start=", 1)[1]
    fake.push(f"/start {code}", chat=chat, date=now_s)
    await _wait(lambda: chat in service.channels.bot.chats.values())


def test_answers_come_through_the_members_mask_end_to_end(tmp_path, monkeypatch):
    """A child's /positions through the real books: the portfolio leaks quantity and P&L, the
    phone's mask removes them, and so does the bot's answer; a viewer's shows both."""

    async def go():
        clock, fernet = Clock(ist(MONDAY, 10)), Fernet(Fernet.generate_key())
        rows = {"positions": [position(qty_u=2_000_000, pnl_paise=987_654_321)]}
        portfolio = FakePortfolio(fernet, books_answer(rows, clock))
        url = await portfolio.start()
        fake = FakeTelegram(new_token())
        monkeypatch.setattr(bot_module, "make_http", fake.http)
        (tmp_path / "etc").mkdir()
        (tmp_path / "etc" / bot_module.TOKEN_FILE).write_text(fake.token + "\n")
        service = FamilyService(settings(tmp_path, portfolio_url=url), keys(),
                                hasher=fast_hasher(), clock=clock, jobs={},
                                data_plane=PlaneKeys(fernet))  # fmt: skip
        try:
            async with running(service):
                await _wait(lambda: service.channels.bot.ready)
                child, child_cookie = await signed_in(service, CHILD, clock(), role="child")
                viewer, viewer_cookie = await signed_in(service, VIEWER, clock())
                await _link(service, fake, child_cookie, CHAT_A, clock() // 1000)
                await _link(service, fake, viewer_cookie, CHAT_B, clock() // 1000)
                for chat in (CHAT_A, CHAT_B):
                    fake.push("/positions", chat=chat, date=clock() // 1000)
                await _wait(lambda: len(fake.texts(CHAT_A)) >= 2 and len(fake.texts(CHAT_B)) >= 2)
                child_text, viewer_text = fake.texts(CHAT_A)[-1], fake.texts(CHAT_B)[-1]
                assert "SYNTH1" in child_text and "₹101.00" in child_text
                assert "9,87,65,432" not in child_text and "P&L" not in child_text
                assert "Qty" not in child_text and " 2" not in child_text.split("₹")[0]
                assert "P&L +₹98,76,543.21" in viewer_text and "Long 2" in viewer_text
                assert all(call[0] == "upstox_book" for call in portfolio.calls)
        finally:
            await portfolio.close()

    run(go())


def test_a_removed_members_chat_gets_one_last_line(tmp_path, monkeypatch):
    async def go():
        clock = Clock(ist(MONDAY, 18))
        fake = FakeTelegram(new_token())
        monkeypatch.setattr(bot_module, "make_http", fake.http)
        (tmp_path / "etc").mkdir()
        (tmp_path / "etc" / bot_module.TOKEN_FILE).write_text(fake.token + "\n")
        service = FamilyService(settings(tmp_path), keys(), hasher=fast_hasher(), clock=clock,
                                jobs={})  # fmt: skip
        async with running(service):
            await _wait(lambda: service.channels.bot.ready)
            member, cookie = await signed_in(service, VIEWER, clock())
            await _link(service, fake, cookie, CHAT_A, clock() // 1000)
            await service.accounts.member_remove({"id": member["id"], "version": member["version"]})
            await _wait(lambda: fake.texts(CHAT_A)[-1] == words.say("unlinked"))
            assert service.channels.bot.chats == {}
            assert await service.store.read(lambda tx: tx.rows("telegram_link")) == []

    run(go())


def test_the_owners_state_answer_carries_the_bot_and_the_install_check(tmp_path, monkeypatch):
    async def go():
        fake = FakeTelegram(new_token())
        monkeypatch.setattr(bot_module, "make_http", fake.http)
        monkeypatch.setattr(bot_module, "TOKEN_RECHECK_S", 0.05)  # look again soon, not in 30 s
        etc = tmp_path / "etc"
        etc.mkdir()
        service = FamilyService(settings(tmp_path), keys(), hasher=fast_hasher(), jobs={})
        async with running(service):
            port = service.listener.port

            async def state():
                status, _, answer = await ask(port, "POST", "/v1/admin/state",
                                              body=sealed(service.keys.admin, "state"))  # fmt: skip
                assert status == 200, answer
                return answer["data"]

            found = await state()
            assert found["bot"] == {"state": "token_missing", "last_message_at": None}
            assert found["check"] == {"ok": False, "at": None}
            (etc / "provisioned").write_text("the family check passed\n")
            (etc / bot_module.TOKEN_FILE).write_text(fake.token + "\n")
            await _wait(lambda: service.channels.bot.ready)
            found = await state()
            assert found["bot"]["state"] == "polling"
            at = int((etc / "provisioned").stat().st_mtime * 1000)
            assert found["check"] == {"ok": True, "at": at}
            status, _, health = await ask(port, "GET", "/health")
            channels = health["family_state"]["channels"]
            assert channels["bot"]["state"] == "polling" and fake.token not in json.dumps(health)

    run(go())


def test_the_bot_leaves_no_descriptor_or_task_behind(tmp_path, monkeypatch):
    """fd-audit: fifty answers over real sockets (a local Telegram, one connection per request)
    keep the descriptor count flat (the long poll in flight holds two sockets at any instant: its
    own and the local Telegram's), and after the stop nothing opened since the start is open."""
    import os

    from sudarshan.tests.fdwatch import FdWatch

    def count() -> int:
        return len(os.listdir("/proc/self/fd"))

    async def go():
        clock = Clock(ist(MONDAY, 11))
        fake = FakeTelegram(new_token())
        server = TelegramServer(fake)
        await server.start()
        monkeypatch.setattr(bot_module, "make_http", server.http)
        monkeypatch.setattr(bot_module, "CHAT_GAP_S", 0.01)  # fifty answers, not fifty seconds
        (tmp_path / "etc").mkdir()
        (tmp_path / "etc" / bot_module.TOKEN_FILE).write_text(fake.token + "\n")
        service = FamilyService(settings(tmp_path), keys(), hasher=fast_hasher(), clock=clock,
                                jobs={})  # fmt: skip
        watch = FdWatch()
        try:
            async with running(service):
                await _wait(lambda: service.channels.bot.ready)
                member, cookie = await signed_in(service, VIEWER, clock())
                await _link(service, fake, cookie, CHAT_A, clock() // 1000)
                for _ in range(5):
                    fake.push("/help", chat=CHAT_A, date=clock() // 1000)
                await _wait(lambda: len(fake.texts(CHAT_A)) >= 6)
                await asyncio.sleep(0.2)
                before = count()
                for _ in range(50):
                    fake.push("/help", chat=CHAT_A, date=clock() // 1000)
                await _wait(lambda: len(fake.texts(CHAT_A)) >= 56, rounds=3000)
                await asyncio.sleep(0.2)
                assert count() <= before + 2, (before, count())
            await watch.aassert_clean()
            leftover = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            assert leftover == []
        finally:
            await server.close()

    run(go())


def test_httpx_own_log_never_carries_the_token(tmp_path, caplog):
    """httpx logs each request's address; the bot's token is in that address. With httpx's
    logger turned all the way up, its lines say /bot<token> instead."""

    async def go():
        fake = FakeTelegram(new_token())
        made, _ = make_bot(fake_service(None, Clock(1), tmp_path / "etc"), fake, tmp_path / "etc")
        await ready(made)
        await made.send(CHAT_A, "x")
        await settle(made)

    with caplog.at_level(logging.DEBUG, logger="httpx"):
        run(go())
    lines = [r.getMessage() for r in caplog.records if r.name == "httpx"]
    assert lines and all("/bot<token>/" in line for line in lines), lines
    secret = [r for r in caplog.records if "api.telegram.org/bot1" in r.getMessage()]
    assert secret == []


def test_the_bot_module_reaches_no_order_path():
    """The bot's files import nothing that can place an order and name no order route."""
    import ast

    family = Path(bot_module.__file__).parent
    for name in ("bot.py", "bot_words.py", "telegram_client.py"):
        source = (family / name).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import | ast.ImportFrom):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [
                    node.module or ""]  # fmt: skip
                for found in names:
                    assert not found.startswith(("sudarshan.books", "services", "broker",
                                                 "restx_api", "openalgo")), (name, found)  # fmt: skip
        for word in ("placeorder", "/api/v1", "modifyorder", "cancelorder", "closeposition"):
            assert word not in source.lower(), (name, word)


def test_strangers_chats_do_not_grow_the_bots_tables_past_their_bound(tmp_path):
    """fd-audit, memory: two hundred chats nobody linked, each sending /start, leave at most
    KEEP_CHATS pacers, locks and counters (the linked chats and the one in use are always kept)."""

    async def go():
        opened = open_store(tmp_path)
        try:
            clock = Clock(ist(MONDAY, 11))
            member = add_member(opened, status="active")
            fake = FakeTelegram(new_token())
            made, _ = make_bot(
                fake_service(opened, clock, tmp_path / "etc"), fake, tmp_path / "etc"
            )
            await ready(made)
            made.chats[member["id"]] = CHAT_A
            await made.send(CHAT_A, "kept")
            for n in range(200):
                await made.handle(update("/start", 910_000_000 + n, date=clock() // 1000))
            assert len(fake.texts()) == 201
            bound = bot_module.KEEP_CHATS + 1
            for table in (made._pacers, made._locks, made._used, made._told, made._alerts_waiting):
                assert len(table) <= bound, len(table)
            assert CHAT_A in made._pacers  # a linked chat's pacer is never dropped
            await settle(made)
        finally:
            close(opened)

    run(go())


def test_cost_figures_follow_pnl_not_quantities(tmp_path):
    """Family review H1 (privacy_fields.json version 2): with P&L hidden and quantities shown, an
    order's price and a tab's value are not shown, whatever the row carries."""

    class Leaky(Viewer):
        async def _book(self, request, args, session):
            answer = await super()._book(request, args, session)
            answer.payload["data"]["rows"][0] |= {"avg_price_u": 10_000_000, "price_u": 10_000_000}
            return answer

    async def go():
        opened = open_store(tmp_path)
        try:
            clock = Clock(ist(MONDAY, 11))
            privacy = {"pnl": False, "qty": True, "funds": False, "orders": True}
            member = add_member(opened, status="active", privacy=privacy)
            fake = FakeTelegram(new_token())
            routes = Leaky(hidden=("pnl", "funds"))
            made, _ = make_bot(fake_service(opened, clock, tmp_path / "etc", routes), fake,
                               tmp_path / "etc")  # fmt: skip
            await ready(made)
            made.chats[member["id"]] = CHAT_A
            await made.handle(update("/orders", CHAT_A, date=clock() // 1000))
            text = fake.sent[-1]["text"]
            assert "Qty 2" in text and "₹1,000.00" not in text
            await made.handle(update("/portfolio", CHAT_A, date=clock() // 1000))
            assert "Value" not in fake.sent[-1]["text"]
            await settle(made)
        finally:
            close(opened)

    run(go())


def test_status_and_the_summary_read_the_family_views_own_market_route(tmp_path):
    """/status and the summary's index line through F6's market route (``viewer_ws.Dialect``),
    not a stand-in: the feed's state in words, only the indices that have a price, a price's time
    whenever ticks are not arriving, and the next trading day once the market has closed."""
    from datetime import timedelta

    from sudarshan.family import digest
    from sudarshan.family.symbol_map import SymbolMap
    from sudarshan.family.viewer_ws import Dialect

    class Feed:
        def __init__(self, state, frames):
            self.state, self.since, self.frames = state, 1, frames

        async def snapshot(self, keys):
            return {key: self.frames[key] for key in keys if key in self.frames}

    async def judge(hashed):
        return None

    async def go():
        opened = open_store(tmp_path)
        try:
            clock = Clock(ist(MONDAY, 11))
            member = add_member(opened, status="active")
            dialect = Dialect(judge=judge, symbols=SymbolMap(), books=None)
            routes = SimpleNamespace(_market=dialect.route_market)
            fake = FakeTelegram(new_token())
            made, _ = make_bot(fake_service(opened, clock, tmp_path / "etc", routes), fake,
                               tmp_path / "etc")  # fmt: skip
            await ready(made)
            made.chats[member["id"]] = CHAT_A
            at = ist(MONDAY, 10)
            frames = {"NSE_INDEX|Nifty 50": {"ltp": 24310.55, "cp": 24209.0, "ltt": at},
                      "NSE_INDEX|Nifty Bank": {"ltp": 52100.2, "cp": 52152.3, "ltt": at}}  # fmt: skip

            async def status():
                await made.handle(update("/status", CHAT_A, date=clock() // 1000))
                return fake.sent[-1]["text"].split("\n")

            dialect.feed = Feed("realtime", frames)
            assert await status() == [words.HEADER, words.say("realtime"),
                                      "NIFTY 50 24,310.55 +0.42%",
                                      "NIFTY BANK 52,100.20 −0.10%"]  # fmt: skip
            lines = digest.lines_for(member, MONDAY, market=await made.ask("market", member),
                                     summary=None, holdings=None, counts=None, next_day=None)  # fmt: skip
            assert lines[1] == "NIFTY 50 24,310.55 +0.42% · NIFTY BANK 52,100.20 −0.10%"
            dialect.feed = Feed("reconnecting", frames)
            assert (await status())[1:3] == [words.say("reconnecting"),
                                             "NIFTY 50 24,310.55 +0.42% (10:00:00)"]  # fmt: skip
            dialect.feed = Feed("reconnecting", {})
            assert (await status())[2] == words.say("indices_unavailable")
            clock.at = ist(MONDAY, 17)
            dialect.feed = Feed("closed", frames)
            closed = await status()
            assert closed[1] == words.say("market_closed")
            assert closed[2] == "NIFTY 50 24,310.55 +0.42% (10:00:00)"
            tuesday = words.day_words(MONDAY + timedelta(days=1))
            assert closed[-1] == words.say("next_day", day=tuesday)
            await settle(made)
        finally:
            close(opened)

    run(go())
