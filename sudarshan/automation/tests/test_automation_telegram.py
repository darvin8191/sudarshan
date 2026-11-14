"""Telegram's ``/auto`` and the automation's notices (A9): the owner's own chat only, one command
line each, every command run where the web app's code runs, one notice a minute per strategy and
reason.

The bot here is a stand-in whose ``_in_app_world`` runs the call at once; the real one under a
real eventlet hub is the last test, in a subprocess (monkey-patching is global).
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from automation_world import NOW, TV, FakeDesk, indicator_alert

from sudarshan.automation import contract, flag, intake, telegram
from sudarshan.automation import model as m

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
FORM = {"name": "ORB", "kind": "future", "underlying": "NIFTY", "product": "MIS", "lots": 1}
OWNER_ID = 7
OLD_KEY_ID = 9  # linked under the owner's name, but its stored key is not this server's API key


class Bot:
    def __init__(self, fails: BaseException | None = None):
        self.timeouts: list[float] = []
        self.fails = fails

    async def _in_app_world(self, fn, *args, timeout: float, offload: bool = True):
        self.timeouts.append(timeout)
        if self.fails is not None:
            raise self.fails
        return fn(*args)


class Message:
    def __init__(self):
        self.replies: list[str] = []

    async def reply_text(self, text, **kwargs):
        assert kwargs == {}  # plain text, shown exactly as written
        self.replies.append(text)


@pytest.fixture
def desk():
    return FakeDesk()


@pytest.fixture
def chat(store, desk, monkeypatch):
    import database.telegram_db as telegram_db
    import database.user_db as user_db
    import services.telegram_bot_service as bot_service
    from sudarshan.automation import store as store_mod

    monkeypatch.setenv(flag.ENV, "sandbox")
    linked = {
        OWNER_ID: {"openalgo_username": "owner"},
        8: {"openalgo_username": "kin"},
        OLD_KEY_ID: {"openalgo_username": "owner"},
    }
    monkeypatch.setattr(
        telegram_db, "get_telegram_user", lambda telegram_id: linked.get(telegram_id)
    )
    # Since v0.5.2 the owner's chat is the one whose stored key is this server's API key now
    # (sudarshan.guard.telegram.owner_of, through the bot's own _linked_owner): the owner's is.
    stored = {OWNER_ID: "owner-key", 8: "kin-key", OLD_KEY_ID: "a-key-from-before"}
    here = {"owner-key": "owner", "kin-key": "kin"}
    monkeypatch.setattr(
        bot_service,
        "get_user_credentials",
        lambda telegram_id: {"api_key": stored[telegram_id]} if telegram_id in stored else None,
    )
    monkeypatch.setattr(bot_service, "get_username_by_apikey", lambda key: here.get(key))
    monkeypatch.setattr(user_db, "find_user_by_username", lambda: SimpleNamespace(username="owner"))
    logged: list[tuple] = []
    monkeypatch.setattr(telegram_db, "log_command", lambda *args, **kwargs: logged.append(args))
    monkeypatch.setattr(store_mod, "_shared", store_mod.LazyInit(lambda: store, "test"))
    monkeypatch.setattr(
        telegram, "answer", functools.partial(telegram.answer, desk=desk, store=store)
    )
    intake._per_link.clear()

    def send(*args, user_id=OWNER_ID, kind="private", bot=None):
        message, bot = Message(), bot or Bot()
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(type=kind, id=user_id),
            effective_user=SimpleNamespace(id=user_id),
            effective_message=message,
        )
        asyncio.run(telegram.handle(bot, update, SimpleNamespace(args=list(args))))
        assert len(message.replies) == 1 and bot.timeouts == [telegram.TIMEOUT_S]
        return message.replies[0]

    send.logged = logged
    return send


def make(store, state=m.ACTIVE, **change):
    strategy, token = store.create(contract.clean({**FORM, **change}))
    if state != m.STOPPED:
        strategy = store.set_state(strategy.id, strategy.version, state)
    return strategy, token


# ---- who may ask --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("user_id", "kind"),
    [
        (8, "private"),
        (99, "private"),
        (OLD_KEY_ID, "private"),
        (OWNER_ID, "group"),
        (OWNER_ID, "supergroup"),
    ],
)
def test_only_the_owners_own_private_chat_is_answered(chat, store, user_id, kind):
    """12: the same from another chat is refused. A linked family account, a stranger, a chat
    linked under the owner's name whose stored key is not this server's API key, and the owner
    himself in a group all get the one sentence, and nothing changes."""
    strategy, _ = make(store)
    for args in ((), ("stop", "ORB"), ("stop", "all"), ("start", "ORB", "STOP")):
        assert chat(*args, user_id=user_id, kind=kind) == telegram.ONLY_YOUR_CHAT
    assert store.strategy(strategy.id).state == m.ACTIVE
    assert {entry[1] for entry in chat.logged} == {"auto_refused"}


# ---- the commands -------------------------------------------------------------------------------


def test_auto_lists_every_strategy_in_one_line_each(chat, store, desk):
    assert chat() == telegram.NO_STRATEGIES
    strategy, token = make(store)
    make(store, state=m.STOPPED, name="Second", underlying="BANKNIFTY")
    intake.handle(token, indicator_alert("long", NOW - 1), TV, desk, store)
    assert chat().splitlines() == [
        "ORB: Active · Sandbox · long NIFTY 24 Nov FUT · last alert 10:15:02",
        "Second: Stopped · Sandbox · holds nothing · no alert yet",
    ]
    assert chat("status") == chat()


def test_stop_stops_new_entries_and_start_starts(chat, store, desk):
    strategy, token = make(store)
    said = chat("stop", "orb")  # the name, whatever its case
    assert (
        said == "ORB is Stopped for new entries. Its exit alert still goes. Changed from Telegram."
    )
    assert store.strategy(strategy.id).state == m.EXITS_ONLY
    assert chat("start", "ORB") == "ORB is Active. Changed from Telegram."
    assert store.strategy(strategy.id).state == m.ACTIVE
    assert desk.phrases_asked == []  # a Sandbox strategy needs no phrase
    assert [entry[1] for entry in chat.logged] == ["auto_stop", "auto_start"]
    assert [e["state"] for e in desk.emitted if e["kind"] == "strategy"] == ["exits_only", "active"]


def test_stop_all_stops_every_active_strategy_and_leaves_the_rest(chat, store):
    make(store)
    make(store, name="Second", underlying="BANKNIFTY")
    make(store, state=m.STOPPED, name="Third", product="NRML")
    said = chat("stop", "all").splitlines()
    assert len(said) == 2 and all("is Stopped for new entries" in line for line in said)
    assert [s.state for s in store.strategies()] == [m.EXITS_ONLY, m.EXITS_ONLY, m.STOPPED]
    assert chat("stop", "all") == "No strategy is Active."


def test_a_name_with_spaces_and_a_name_that_is_not_there(chat, store):
    make(store, name="ORB 5 min")
    assert "is Stopped for new entries" in chat("stop", "ORB", "5", "min")
    assert chat("start", "ORB", "5", "min") == "ORB 5 min is Active. Changed from Telegram."
    missing = chat("stop", "Nope")
    assert missing.splitlines()[0] == "No strategy is called Nope."
    assert "ORB 5 min: Active" in missing  # and the list, so the right name can be typed


def test_starting_a_live_strategy_needs_the_phrase(chat, store, desk, monkeypatch):
    monkeypatch.setenv(flag.ENV, "on")
    desk.guard = "on"
    strategy, _ = make(store, state=m.STOPPED)
    store.set_mode(strategy.id, strategy.version, m.LIVE)
    assert chat("start", "ORB") == "That is not your confirm phrase. Nothing was changed."
    assert chat("start", "ORB", "guess") == "That is not your confirm phrase. Nothing was changed."
    assert store.strategy(strategy.id).state == m.STOPPED
    assert chat("start", "ORB", "stop") == "ORB is Active. Changed from Telegram."
    assert desk.phrases_asked[-1] == ("start ORB Live", "Telegram")
    assert chat("stop", "ORB").startswith("ORB is Stopped for new entries")  # no phrase to stop


def test_what_telegram_cannot_do(chat, store):
    make(store)
    assert chat("stop", "everything", "ORB").startswith("No strategy is called")  # only on the page
    for args in (("live", "ORB"), ("stop",), ("start",), ("delete", "ORB"), ("stopall",)):
        assert chat(*args) == telegram.USAGE
    assert [s.mode for s in store.strategies()] == [
        "sandbox"
    ]  # a change to Live is on the page only
    assert [s.state for s in store.strategies()] == [m.ACTIVE]


def test_a_command_that_takes_too_long_or_breaks_says_so_in_words(chat):
    assert chat(bot=Bot(TimeoutError())) == telegram.STILL_WORKING
    assert chat(bot=Bot(RuntimeError("boom"))) == telegram.BROKE


# ---- notices ------------------------------------------------------------------------------------


def test_one_notice_a_minute_per_strategy_and_reason_the_rest_counted():
    sent, clock = [], [1000.0]
    notices = telegram.Notices(send=sent.append, clock=lambda: clock[0])
    assert notices.tell(1, "late", "ORB: a buy alert arrived 2 min late and was not sent.") is True
    for _ in range(3):
        clock[0] += 10
        assert (
            notices.tell(1, "late", "ORB: a buy alert arrived 2 min late and was not sent.")
            is False
        )
    assert notices.tell(1, "gap", "ORB: an earlier alert did not arrive.") is True  # another reason
    assert notices.tell(2, "late", "Second: late.") is True  # another strategy
    clock[0] += 31  # a minute after the first
    assert notices.tell(1, "late", "ORB: a buy alert arrived 3 min late and was not sent.") is True
    assert sent == [
        "ORB: a buy alert arrived 2 min late and was not sent.",
        "ORB: an earlier alert did not arrive.",
        "Second: late.",
        "ORB: a buy alert arrived 3 min late and was not sent. (and 3 more like it)",
    ]


def test_what_the_order_alerts_and_the_log_already_say_is_told_to_nobody():
    sent = []
    notices = telegram.Notices(send=sent.append)
    for reason in ("repeat", "sent", "no_action", "order_refused", "not_confirmed"):
        assert notices.tell(1, reason, "x") is False
    assert notices.tell(1, "late", "") is False and sent == []


def test_a_notice_that_cannot_be_queued_never_fails_an_alert(store, desk, monkeypatch):
    def broken(text):
        raise RuntimeError("telegram is down")

    monkeypatch.setenv(flag.ENV, "sandbox")
    monkeypatch.setattr(telegram, "NOTICES", telegram.Notices(send=broken))
    from sudarshan.automation.desk import Desk

    desk.tell = Desk.tell.__get__(desk)  # the real path to the notices
    strategy, token = make(store)
    got = intake.handle(token, indicator_alert("long", NOW - 500), TV, desk, store)
    assert (got.status, got.verdict) == (200, "late")


def test_the_queue_is_the_one_the_kill_switch_uses_and_goes_to_the_owner_only(monkeypatch):
    import database.telegram_db as telegram_db
    import database.user_db as user_db
    import services.telegram_alert_service as service

    queued = []
    monkeypatch.setattr(service.telegram_alert_service, "is_bot_active", lambda: True)
    monkeypatch.setattr(
        service.alert_executor, "submit", lambda fn, *args: queued.append((fn, args))
    )
    monkeypatch.setattr(user_db, "find_user_by_username", lambda: SimpleNamespace(username="owner"))
    asked = []

    def linked(username):
        asked.append(username)
        return {"telegram_id": 4242}

    monkeypatch.setattr(telegram_db, "get_telegram_user_by_username", linked)
    telegram._queue("ORB: a buy alert arrived 2 min late and was not sent.")
    ((fn, (chat_id, text)),) = queued
    assert fn == service.telegram_alert_service.send_alert_sync and chat_id == 4242
    assert asked == ["owner"]
    assert text.splitlines()[0] == "सुदर्शन / SUDARSHAN"
    assert (
        text.splitlines()[1] == "Automation: ORB: a buy alert arrived 2 min late and was not sent."
    )
    # the bot off, or no linked account: nothing is queued and nothing fails
    monkeypatch.setattr(service.telegram_alert_service, "is_bot_active", lambda: False)
    telegram._queue("x")
    monkeypatch.setattr(service.telegram_alert_service, "is_bot_active", lambda: True)
    monkeypatch.setattr(telegram_db, "get_telegram_user_by_username", lambda username: None)
    telegram._queue("x")
    assert len(queued) == 1


def test_the_bot_registers_the_command():
    source = (REPO / "services" / "telegram_bot_service.py").read_text()
    assert 'CommandHandler("auto", self.cmd_auto)' in source
    assert "await automation_telegram.handle(self, update, context)" in source


# ---- under a real eventlet hub ------------------------------------------------------------------

EVENTLET = """
import eventlet
eventlet.monkey_patch()

import json, os, sys, threading, time
sys.path[:0] = [{repo!r}, {here!r}]
import flask
import eventlet.patcher
_orig = eventlet.patcher.original("threading")
ticks = []

def ticker():
    while True:
        ticks.append(time.monotonic())
        eventlet.sleep(0.05)

eventlet.spawn(ticker)
eventlet.sleep(0.1)
"""


def test_auto_stop_runs_on_the_hub_and_waits_for_the_alert_being_handled(tmp_path):
    """/auto stop arrives on the bot's real thread while an alert of the strategy is placing its
    order (1.5 s). The command is handed to the hub (TelegramBotService._in_app_world), waits for
    the strategy's lock cooperatively while the hub keeps turning, and answers when the alert is
    done: the order was placed before the stop was answered, and none after."""
    pytest.importorskip("eventlet", reason="eventlet is installed by the production installer")
    script = EVENTLET.format(repo=str(REPO), here=str(HERE)) + textwrap.dedent(
        """
        import asyncio, functools, types
        import services.telegram_bot_service as tg
        import database.telegram_db as telegram_db
        from utils import real_threading as rt
        from automation_world import NOW, FakeDesk, TV, indicator_alert
        from sudarshan.automation import contract, intake, store, telegram
        import sudarshan.guard.telegram as guard_telegram

        assert rt.start_hub_worker() is True
        eventlet.sleep(0.05)
        hub = rt._real_get_ident()
        desk = FakeDesk()
        # a named moment plus the time this script has run, never the wall clock: the desk's
        # contract list is fixed, and on the wall clock this test turned red near each expiry
        started = time.monotonic()
        desk.now = lambda: NOW + (time.monotonic() - started)
        desk.delay = 1.5
        STORE = store.shared()
        strategy, token = STORE.create(contract.clean(
            {"name": "ORB", "kind": "future", "underlying": "NIFTY", "product": "MIS", "lots": 1}))
        STORE.set_state(strategy.id, strategy.version, "active")
        guard_telegram.owner_of = lambda chat, user: "owner"
        telegram_db.log_command = lambda *args, **kwargs: None
        ran_on, real_answer = [], telegram.answer

        def recorded(chat, user, args):
            ran_on.append(rt._real_get_ident())
            return real_answer(chat, user, args, desk=desk, store=STORE)

        telegram.answer = recorded
        placing = eventlet.spawn(intake.handle, token, indicator_alert("long", desk.now()), TV, desk, STORE)
        eventlet.sleep(0.3)
        replies, out = [], {}

        class Message:
            async def reply_text(self, text, **kwargs):
                replies.append(text)

        update = types.SimpleNamespace(
            effective_chat=types.SimpleNamespace(type="private", id=7),
            effective_user=types.SimpleNamespace(id=7), effective_message=Message())
        svc = tg.TelegramBotService()

        def bot_thread():
            async def scenario():
                started = time.monotonic()
                await telegram.handle(svc, update, types.SimpleNamespace(args=["stop", "ORB"]))
                out["took"] = time.monotonic() - started
            asyncio.run(scenario())

        start = time.monotonic()
        t = _orig.Thread(target=bot_thread, daemon=True)
        t.start()
        assert rt.join(t, timeout=30), "the bot thread never finished"
        end = time.monotonic()
        first = placing.wait()
        after = intake.handle(token, indicator_alert("short", desk.now()), TV, desk, STORE)
        print(json.dumps({
            "on_hub": ran_on == [hub], "took": out["took"], "reply": replies[0],
            "first": first.verdict, "after": after.verdict, "placed": len(desk.placed),
            "state": STORE.strategy(strategy.id).state, "locks": len(intake.LOCKS),
            "ticks": sum(1 for x in ticks if start < x < end), "elapsed": end - start}))
        """
    )
    env = {
        "PATH": "/usr/bin:/bin",
        "SUDARSHAN_AUTOMATION": "sandbox",
        "SUDARSHAN_AUTOMATION_DB": f"{tmp_path}/automation.db",
        "API_KEY_PEPPER": "e" * 64,
        "APP_KEY": "test-only-app-key-" + "4" * 32,
        "DATABASE_URL": f"sqlite:///{tmp_path}/openalgo.db",
        "LOG_DIR": f"{tmp_path}/log",
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),  # where eventlet is, if beside the venv
    }
    done = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
        env=env,
    )
    assert done.returncode == 0, done.stderr[-3000:]
    result = json.loads(done.stdout.strip().splitlines()[-1])
    assert result["on_hub"] is True
    assert result["reply"] == (
        "ORB is Stopped for new entries. Its exit alert still goes. Changed from Telegram."
    )
    assert 0.8 < result["took"] < 5.0  # it waited for the order on its way, not for the timeout
    assert (result["first"], result["after"], result["placed"]) == ("sent", "stopped", 1)
    assert result["state"] == "exits_only" and result["locks"] == 0
    assert result["ticks"] >= result["elapsed"] * 10  # the hub never stalled
