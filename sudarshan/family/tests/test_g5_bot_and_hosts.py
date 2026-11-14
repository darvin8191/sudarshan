"""G5 (P6 T2): the family bot can answer only its read-only table, and the family's HTTP clients
can reach only the places the design names.

* The command allow-list: the table is exactly design 2.6's; every command in it answers; every
  other message from a linked chat (every order verb OpenAlgo and the owner's bot know, another
  bot's command, plain text, odd characters) is answered with the one refusal sentence, in either
  language; a chat nobody linked is told only how to link, and only for /start.
* The host allow-lists: the bot's client reaches exactly api.telegram.org on 443 and 127.0.0.1 on
  5583 and 5580, and any fourth address raises before anything is sent; the push sender takes an
  endpoint only at one of the four push services (https, 443, no numeric address, no user name),
  and the push client refuses anything else even when asked directly; the summary's journal client
  reaches its one port. The bot's client calls three Telegram methods, none of which acts.

(The nginx map cases of T2 belong to the nginx lane, ``sudarshan/ops/nginx``.)
"""

from __future__ import annotations

import ast
import asyncio
import random
import string
from pathlib import Path

import httpx
import pytest
from family_botkit import FakeTelegram, fake_service, make_bot, new_token, ready, settle, update
from family_planekit import MONDAY, Clock, ist
from family_pushkit import subscription
from family_testkit import add_member, close, open_store

from sudarshan.family import bot as bot_module
from sudarshan.family import bot_words as words
from sudarshan.family import digest as digest_module
from sudarshan.family import push as push_module
from sudarshan.family import telegram_client
from sudarshan.family.listener import Response
from sudarshan.family.telegram_client import AllowList, HostNotAllowed
from sudarshan.push import PUSH_SERVICES, EndpointRefused, InvalidSubscription, Subscription

FAMILY = Path(bot_module.__file__).resolve().parent
CHAT = 900_000_001
#: Design 2.6: the table, and nothing else.
TABLE = ("status", "positions", "holdings", "orders", "portfolio", "quiet", "language", "help",
         "stop")  # fmt: skip
#: Order verbs from OpenAlgo's API, its Telegram bot, the owner's surfaces and common slang.
ORDER_WORDS = (
    "placeorder", "placesmartorder", "basketorder", "splitorder", "modifyorder", "cancelorder",
    "cancelallorder", "closeposition", "closeall", "exit", "exitall", "squareoff", "square_off",
    "buy", "sell", "short", "cover", "order", "orders_place", "gtt", "oco", "bracket", "convert",
    "killswitch", "kill", "panic", "flatten", "hedge", "trade", "execute", "confirm", "yes",
    "admin", "apikey", "token", "login", "logout", "unlock", "reset", "invite", "start_trading",
    "analyzer", "live", "sandbox", "toggle", "mode", "settings", "privacy", "share", "unshare",
)  # fmt: skip


def run(coro):
    try:
        return asyncio.run(coro)
    finally:
        close(None)


class ReadOnlyRoutes:
    """The viewer routes a command reads, answering a fixed book and portfolio."""

    async def _book(self, request, args, session):
        row = {"symbol": "SYNTH1", "exchange": "NSE", "last_price_u": 10_100_000, "side": "long",
               "transaction_type": "BUY", "status": "complete"}  # fmt: skip
        return Response.ok({"part": args["part"], "state": "ok", "read_at": ist(MONDAY, 11),
                            "words": None, "hidden": [], "rows": [row]})  # fmt: skip

    async def _portfolio(self, request, args, session):
        return Response.ok({"read_at": 1, "state": "ok", "words": None, "hidden": [], "tabs": [],
                            "summary": None})  # fmt: skip


def others(seed: int = 6) -> list[object]:
    """Messages that are not a command of the table."""
    found: list[object] = [f"/{w}" for w in ORDER_WORDS]
    found += [f"/{w} SYNTH1 10" for w in ORDER_WORDS[:12]]
    found += [f"/{c}@SomeOtherBot" for c in TABLE]  # another bot's command, in a group's form
    found += ["buy 10 SYNTH1", "sell everything", "exit all", "hello", "?", "/", "//", "/ status",
              "status", "positions", "/status_", "/statusx", "/positions2", "/st atus",
              "/स्टेटस", "‍/status", " ", "", None,
              chr(0x1F680) + " /buy", "/" + "a" * 600, "/start", "/start SYNTH"]  # fmt: skip
    rng = random.Random(seed)
    alphabet = string.ascii_lowercase + string.digits + "_"
    while len(found) < 260:
        word = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 14)))
        if word not in TABLE:
            found.append(f"/{word}")
    return found


def test_the_table_is_the_designs_and_nothing_else():
    assert bot_module.COMMANDS == TABLE
    assert set(bot_module.BOOK_COMMANDS) == {"positions", "holdings", "orders"}


@pytest.mark.parametrize("language", ["en", "gu"])
def test_every_command_answers_and_anything_else_gets_only_the_refusal(tmp_path, language):
    async def go():
        opened = open_store(tmp_path)
        try:
            clock = Clock(ist(MONDAY, 11))
            member = add_member(opened, status="active", language=language)
            fake = FakeTelegram(new_token())
            made, _ = make_bot(fake_service(opened, clock, tmp_path / "etc", ReadOnlyRoutes()),
                               fake, tmp_path / "etc")  # fmt: skip
            await ready(made)
            made.chats[member["id"]] = CHAT
            refusal = words.say("refusal", language)
            told = lambda text: made.handle(update(text, CHAT, date=clock() // 1000))  # noqa: E731
            for command in TABLE:
                if command == "stop":
                    continue
                text = {"quiet": "/quiet off", "language": f"/language {language}"}.get(
                    command, f"/{command}")  # fmt: skip
                before = len(fake.sent)
                await told(text)
                assert len(fake.sent) == before + 1 and fake.sent[-1]["text"] != refusal, command
            for message in others():
                if message == "/start":
                    continue  # the link step: "already linked", never a figure
                before = len(fake.sent)
                await told(message)
                answers = [m["text"] for m in fake.sent[before:]]
                if bot_module.parse(message, made.username)[0] == "start":
                    assert answers == [words.say("already_linked", language)], message
                else:
                    assert answers == [refusal], message
            await told(
                "/stop"
            )  # the table's last command: unlinked, then nothing but the link step
            assert fake.sent[-1]["text"] == words.say("unlinked", language)
            before = len(fake.sent)
            for message in [f"/{c}" for c in TABLE] + others()[:40]:
                await told(message)
            answers = {m["text"] for m in fake.sent[before:]}
            assert answers <= {words.both("link_first")}, answers
            await settle(made)
        finally:
            close(opened)

    run(go())


def test_the_bots_dispatch_has_one_way_out_for_anything_unknown():
    """Read from the source: ``handle`` answers a known command through ``answer`` and anything
    else with the refusal line; ``answer`` reaches only the read-only helpers and the two
    settings the member owns."""
    tree = ast.parse((FAMILY / "bot.py").read_text(encoding="utf-8"))
    bot = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Bot")
    methods = {f.name: f for f in bot.body if isinstance(f, ast.AsyncFunctionDef | ast.FunctionDef)}
    called = {node.func.attr for node in ast.walk(methods["answer"])
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}  # fmt: skip
    assert called <= {"say", "get", "lower", "set_quiet", "set_language", "_audit_command",
                      "status", "book", "portfolio"}, called  # fmt: skip
    lines = [node.value for node in ast.walk(methods["handle"])
             if isinstance(node, ast.Constant) and isinstance(node.value, str)]  # fmt: skip
    assert "refusal" in lines


# ---- the host allow-lists -------------------------------------------------------------------------
REFUSED_FOR_THE_BOT = (
    "http://127.0.0.1:5000/api/v1/placeorder",  # OpenAlgo
    "http://127.0.0.1:5555/",  # its market-data bus
    "http://127.0.0.1:8765/",  # its WebSocket proxy
    "http://127.0.0.1:5582/",  # the alerts
    "http://127.0.0.1:5585/v1/admin/state",  # this service's own admin door
    "http://127.0.0.1:5586/v1/family_day",  # the journal: the summary's client, not the bot's
    "http://localhost:5583/v1/state",
    "http://[::1]:5583/v1/state",
    "https://127.0.0.1:5583/v1/state",
    "http://api.telegram.org/bot1/getMe",
    "https://api.telegram.org:8443/bot1/getMe",
    "https://api.telegram.org.evil.example/bot1/getMe",
    "https://evil.example/",
    "https://149.154.167.220/bot1/getMe",
    "https://user@api.telegram.org/bot1/getMe",
    "https://fcm.googleapis.com/fcm/send/x",
)


def test_the_bots_client_reaches_exactly_the_three_places_the_design_names():
    """Mutation target 18: the HTTP host allow-list widened."""
    assert telegram_client.ALLOWED == frozenset({("https", "api.telegram.org", 443),
                                                 ("http", "127.0.0.1", 5583),
                                                 ("http", "127.0.0.1", 5580)})  # fmt: skip

    async def go():
        seen: list[str] = []

        def answer(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json={"ok": True, "result": {}})

        http = telegram_client.make_http(httpx.MockTransport(answer))
        for url in ("https://api.telegram.org/bot1/getMe", "http://127.0.0.1:5583/v1/state",
                    "http://127.0.0.1:5580/health"):  # fmt: skip
            assert (await http.post(url)).status_code == 200
        for url in REFUSED_FOR_THE_BOT:
            with pytest.raises(HostNotAllowed):
                await http.post(url)
        assert len(seen) == 3  # nothing refused ever reached a transport
        await http.aclose()

    run(go())


def test_a_redirect_from_telegram_is_never_followed_anywhere():
    async def go():
        def answer(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                302, headers={"Location": "http://127.0.0.1:5000/api/v1/placeorder"}
            )

        http = telegram_client.make_http(httpx.MockTransport(answer))
        found = await http.post("https://api.telegram.org/bot1/getMe")
        assert found.status_code == 302 and found.next_request is not None
        with pytest.raises(HostNotAllowed):
            await http.send(found.next_request)  # even if something tried to follow it
        await http.aclose()

    run(go())


def test_the_bot_calls_three_telegram_methods_none_of_which_acts():
    source = (FAMILY / "telegram_client.py").read_text(encoding="utf-8")
    calls = [node for node in ast.walk(ast.parse(source))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr == "call"]  # fmt: skip
    methods = {c.args[1].value for c in calls if isinstance(c.args[1], ast.Constant)}
    assert methods == {"getMe", "getUpdates", "sendMessage"}


REFUSED_ENDPOINTS = (
    "https://127.0.0.1/fcm/send/x",
    "https://[::1]/fcm/send/x",
    "https://2130706433/fcm/send/x",
    "http://fcm.googleapis.com/fcm/send/x",
    "https://fcm.googleapis.com:8443/fcm/send/x",
    "https://user@fcm.googleapis.com/fcm/send/x",
    "https://fcm.googleapis.com.evil.example/fcm/send/x",
    "https://api.telegram.org/bot1/sendMessage",
    "https://evil.example/fcm/send/x",
    "https://localhost/fcm/send/x",
    "https://a.b.push.apple.com/x",
    "https://notify.windows.com/x",
)


def test_the_push_sender_takes_endpoints_only_at_the_four_push_services():
    assert [(s.name, s.host, s.wildcard) for s in PUSH_SERVICES] == [
        ("Google", "fcm.googleapis.com", False),
        ("Mozilla", "updates.push.services.mozilla.com", False),
        ("Apple", "push.apple.com", True),
        ("Microsoft", "notify.windows.com", True),
    ]
    keys = subscription(1)["keys"]  # a phone's keys, made for this run
    for endpoint in REFUSED_ENDPOINTS:
        with pytest.raises(EndpointRefused):
            Subscription.from_json({"endpoint": endpoint, "keys": keys})
    for endpoint in ("https://fcm.googleapis.com/fcm/send/SYNTH",
                     "https://updates.push.services.mozilla.com/wpush/v2/SYNTH",
                     "https://web.push.apple.com/SYNTH",
                     "https://wns2-bl2p.notify.windows.com/w/?token=SYNTH"):  # fmt: skip
        assert Subscription.from_json({"endpoint": endpoint, "keys": keys}).endpoint == endpoint
    with pytest.raises(InvalidSubscription):
        Subscription.from_json({"endpoint": "https://fcm.googleapis.com/x", "keys": {}})


def test_the_push_client_refuses_anything_but_a_push_service_even_when_asked_directly():
    """Mutation target 18, for the push client."""

    async def go():
        seen: list[str] = []
        http = push_module.make_http(httpx.MockTransport(
            lambda r: (seen.append(str(r.url)), httpx.Response(201))[1]))  # fmt: skip
        await http.post("https://fcm.googleapis.com/fcm/send/SYNTH", content=b"x")
        for url in (*REFUSED_ENDPOINTS, *REFUSED_FOR_THE_BOT):
            if push_module.PushOnly.permits(httpx.URL(url)):
                continue  # only the push services' own shapes pass (none of the above)
            with pytest.raises(push_module.HostRefused):
                await http.post(url, content=b"x")
        assert seen == ["https://fcm.googleapis.com/fcm/send/SYNTH"]
        await http.aclose()
        permitted = [u for u in (*REFUSED_ENDPOINTS, *REFUSED_FOR_THE_BOT)
                     if push_module.PushOnly.permits(httpx.URL(u))]  # fmt: skip
        assert permitted == ["https://fcm.googleapis.com/fcm/send/x"]  # the bot's no, push's yes

    run(go())


def test_the_summarys_journal_client_reaches_its_one_port():
    async def go():
        seen: list[str] = []
        http = digest_module.make_http("http://127.0.0.1:5586", httpx.MockTransport(
            lambda r: (seen.append(str(r.url)), httpx.Response(200, json={}))[1]))  # fmt: skip
        await http.post("http://127.0.0.1:5586/v1/family_day")
        for url in ("http://127.0.0.1:5000/", "http://127.0.0.1:5583/v1/state",
                    "https://api.telegram.org/", "http://127.0.0.1:55860/"):  # fmt: skip
            with pytest.raises(HostNotAllowed):
                await http.post(url)
        assert seen == ["http://127.0.0.1:5586/v1/family_day"]
        await http.aclose()

    run(go())


def test_the_allow_list_class_itself_refuses_a_fourth():
    guard = AllowList(frozenset({("https", "api.telegram.org", 443)}))
    assert guard.permits(httpx.URL("https://api.telegram.org/x"))
    for url in ("https://api.telegram.org:444/x", "http://api.telegram.org/x",
                "https://u:p@api.telegram.org/x", "https://API.telegram.org.evil.example/x"):  # fmt: skip
        assert not guard.permits(httpx.URL(url)), url
