"""Telegram's ``/killswitch`` (M16.7): the owner's own chat only, one command line each, and
every command run where the web app's code runs (``TelegramBotService._in_app_world``).

The bot here is a stand-in whose ``_in_app_world`` runs the call at once and records it; the
real one under a real eventlet hub is in test_guard_eventlet.py.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from guard_world import Account

from sudarshan.guard import flag, gate, killswitch, telegram, words
from sudarshan.guard.store import KillSwitchState

OWNER_TELEGRAM_ID = 7


class Bot:
    """TelegramBotService's ``_in_app_world``, run inline; or failing as told."""

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
        self.replies: list[tuple[str, dict]] = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


@pytest.fixture
def chat(monkeypatch, risk_store):
    import database.telegram_db as telegram_db
    import database.user_db as user_db

    fresh = gate.Gate(store=risk_store)
    fresh.set_state(KillSwitchState())
    monkeypatch.setattr(gate, "GATE", fresh)
    monkeypatch.setenv(flag.ENV, "on")
    monkeypatch.setattr(killswitch, "DRAIN_S", 0.2)
    account = Account(monkeypatch)
    linked = {OWNER_TELEGRAM_ID: {"openalgo_username": "owner"}, 8: {"openalgo_username": "kin"}}
    monkeypatch.setattr(
        telegram_db, "get_telegram_user", lambda telegram_id: linked.get(telegram_id)
    )
    monkeypatch.setattr(user_db, "find_user_by_username", lambda: SimpleNamespace(username="owner"))
    logged: list[tuple] = []
    monkeypatch.setattr(telegram_db, "log_command", lambda *args, **kwargs: logged.append(args))
    owners: list[dict] = []

    def owner_for(username, **kwargs):
        owners.append({"username": username, **kwargs})
        return killswitch.Owner(
            username, kwargs["surface"], kwargs["actor"], "token", "upstox", "KEY"
        )

    monkeypatch.setattr(killswitch, "owner_for", owner_for)
    return SimpleNamespace(account=account, store=risk_store, logged=logged, owners=owners)


def send(*args, chat_type="private", user_id=OWNER_TELEGRAM_ID, bot=None):
    bot = bot or Bot()
    message = Message()
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(type=chat_type, id=100 + user_id),
        effective_user=SimpleNamespace(id=user_id),
        effective_message=message,
    )
    asyncio.run(telegram.handle(bot, update, SimpleNamespace(args=list(args))))
    assert len(message.replies) == 1
    text, kwargs = message.replies[0]
    assert kwargs == {}  # plain text, shown as written: nothing to escape
    return text, bot


@pytest.mark.parametrize(
    ("chat_type", "user_id"),
    [
        ("group", OWNER_TELEGRAM_ID),
        ("supergroup", OWNER_TELEGRAM_ID),
        ("private", 9),
        ("private", 8),
    ],
    ids=["group chat", "supergroup", "not linked", "linked to another account"],
)
def test_only_the_owner_s_own_chat_is_answered(chat, chat_type, user_id):
    text, _bot = send("on", "STOP", chat_type=chat_type, user_id=user_id)
    assert text == words.TG_ONLY_YOUR_CHAT
    assert chat.account.calls == [] and not chat.store.state().on and chat.owners == []
    assert chat.logged == [(user_id, "killswitch_refused", 100 + user_id)]


def test_on_with_the_phrase_turns_it_on_and_keeps_positions(chat):
    text, bot = send("on", "STOP")
    state = chat.store.state()
    assert state.on and not state.exit_all and state.surface == "telegram"
    assert state.actor == f"telegram:{OWNER_TELEGRAM_ID}"
    assert "live_exit_all" not in chat.account.calls and "live_cancel_all" in chat.account.calls
    assert text == words.ks_report_message(state.report)
    assert bot.timeouts == [telegram.TIMEOUT_S["on"]]
    assert chat.owners == [
        {"username": "owner", "surface": "telegram", "actor": f"telegram:{OWNER_TELEGRAM_ID}"}
    ]
    assert chat.logged == [(OWNER_TELEGRAM_ID, "killswitch_on", 100 + OWNER_TELEGRAM_ID)]


def test_exit_turns_it_on_and_exits_every_position(chat):
    send("exit", "stop")
    assert chat.store.state().exit_all and "live_exit_all" in chat.account.calls


@pytest.mark.parametrize("args", [("on",), ("on", "go"), ("exit", "STOPP"), ("off", "halt")])
def test_a_wrong_or_missing_phrase_changes_nothing(chat, args):
    text, _bot = send(*args)
    assert text == words.PHRASE_WRONG
    assert chat.account.calls == [] and not chat.store.state().on


def test_off_needs_the_phrase_and_the_minute(chat):
    send("on", "STOP")
    text, bot = send("off", "STOP")
    assert text.startswith("The kill switch can be turned off") and chat.store.state().on
    assert bot.timeouts == [telegram.TIMEOUT_S["off"]]
    held = chat.store.state()
    gate.GATE.set_state(KillSwitchState(**{
        **held.__dict__, "since": held.since - timedelta(seconds=61)}))  # fmt: skip
    text, _bot = send("off", "Stop")
    assert text == words.KS_RESUMED and not chat.store.state().on


def test_the_bare_command_says_where_it_stands_and_what_it_would_cancel(chat):
    text, bot = send()
    counts = killswitch.preview_live(None)  # the account's recorded preview
    assert text == "\n".join(
        [words.TG_OFF, words.ks_preview(counts, telegram=True), "", words.TG_USAGE]
    )
    assert "/killswitch exit with your phrase" in text
    assert bot.timeouts == [telegram.STATUS_TIMEOUT_S] and not chat.store.state().on
    send("on", "STOP")
    text, _bot = send("status")
    assert text.startswith("The kill switch is already on since ")
    assert words.TG_RESUME_HOW in text and words.ks_report_lead(chat.store.state().report) in text


def test_an_unknown_word_gets_the_usage_and_changes_nothing(chat):
    text, _bot = send("stop", "STOP")
    assert text == words.TG_USAGE and chat.account.calls == [] and not chat.store.state().on


def test_a_command_that_takes_long_says_it_is_still_working(chat):
    text, _bot = send("on", "STOP", bot=Bot(fails=TimeoutError()))
    assert text == words.TG_STILL_WORKING


def test_a_command_that_breaks_says_so_in_words(chat):
    text, _bot = send("on", "STOP", bot=Bot(fails=RuntimeError("the database is locked")))
    assert text == words.TG_BROKE


def test_the_phrase_is_never_logged(chat):
    for args in (("on", "STOP"), ("off", "STOP"), ("on", "secret words")):
        send(*args)
    assert chat.logged and not any(
        "STOP" in str(entry) or "secret" in str(entry) for entry in chat.logged
    )
