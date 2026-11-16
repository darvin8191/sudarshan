"""B4 (M16.1): ``/server`` and ``/trinetra`` in the owner's own chat (P8 5.4, 10.4).

From a group chat, an account not linked, an account linked to another OpenAlgo user and the
owner's chat linked with a key that is no longer this OpenAlgo's: the refusal sentence and no
figure. From the owner's private chat: the server watch's last report, TRINETRA's readings. The
handlers are driven with fake Update objects and a stand-in for ``_in_app_world``, as the
``/killswitch`` tests are; no network. Every reading and figure is invented.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from sudarshan.ops import telegram as ops_telegram
from sudarshan.signals import contract as c
from sudarshan.signals import telegram as signals_telegram

OWNER_ID, KIN_ID, STRANGER_ID, OLD_KEY_ID = 7, 8, 9, 12
NOW = 1794800712.0  # Mon 16 Nov 2026 09:15:12 IST (invented)
NOW_MS = int(NOW * 1000)


class Bot:
    """TelegramBotService's ``_in_app_world``, run inline; or failing as told."""

    def __init__(self, fails: BaseException | None = None):
        self.fails, self.calls = fails, 0

    async def _in_app_world(self, fn, *args, timeout: float, offload: bool = True):
        self.calls += 1
        if self.fails is not None:
            raise self.fails
        return fn(*args)


class Message:
    def __init__(self):
        self.replies: list[tuple[str, dict]] = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


@pytest.fixture
def chats(monkeypatch):
    import database.telegram_db as telegram_db
    import database.user_db as user_db
    import services.telegram_bot_service as bot_service

    linked = {OWNER_ID: {"openalgo_username": "owner"}, KIN_ID: {"openalgo_username": "kin"},
              OLD_KEY_ID: {"openalgo_username": "owner"}}  # fmt: skip
    stored = {OWNER_ID: "owner-key", KIN_ID: "kin-key", OLD_KEY_ID: "owner-key-before-a-new-one"}
    keys_here = {"owner-key": "owner", "kin-key": "kin"}
    monkeypatch.setattr(telegram_db, "get_telegram_user", lambda tid: linked.get(tid))
    monkeypatch.setattr(bot_service, "get_user_credentials",
                        lambda tid: {"api_key": stored[tid]} if tid in stored else None)  # fmt: skip
    monkeypatch.setattr(bot_service, "get_username_by_apikey", lambda key: keys_here.get(key))
    monkeypatch.setattr(user_db, "find_user_by_username", lambda: SimpleNamespace(username="owner"))
    logged: list[tuple] = []
    monkeypatch.setattr(telegram_db, "log_command", lambda *args, **kwargs: logged.append(args))
    reads = {"report": 0, "state": 0}

    def report():
        reads["report"] += 1
        return REPORT

    def state():
        reads["state"] += 1
        return STATE, ""

    monkeypatch.setattr(ops_telegram, "watch_report", report)
    monkeypatch.setattr(signals_telegram, "read_state", state)
    monkeypatch.setattr(ops_telegram.time, "time", lambda: NOW + 30)
    return SimpleNamespace(logged=logged, reads=reads)


def send(module, chat_type="private", user_id=OWNER_ID, bot=None) -> str:
    message = Message()
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(type=chat_type, id=100 + user_id),
        effective_user=SimpleNamespace(id=user_id),
        effective_message=message,
    )
    asyncio.run(module.handle(bot or Bot(), update, SimpleNamespace(args=[])))
    ((text, kwargs),) = message.replies
    assert kwargs == {}  # plain text, shown as written: no parse mode
    return text


REPORT = {
    "v": 1, "at": int(NOW), "release": "v0.15.0",
    "heartbeat": {"state": "not_set", "words": "ignored: the watch's own words are used"},
    "checks": [
        {"name": "service_feed", "level": "failing"}, {"name": "disk", "level": "warning"},
        {"name": "login", "level": "fine"}, {"name": "memory", "level": "fine"},
        {"name": "service_family", "level": "off"}, {"name": "certificate", "level": "unread"},
    ],
}  # fmt: skip


def reading(module="regime", instrument="NIFTY", **over) -> dict:
    out = {"module": module, "instrument": instrument, "state": "DIST", "label": "FII distribution",
           "bias": "bear", "confidence": 0.712, "quality": "ok", "lapsed": False, "sessions": 3,
           "valid_until_ms": None}  # fmt: skip
    return out | over


STATE = {
    "now_ms": NOW_MS, "lang": "hi", "alerts": [],
    "freshness": {"status": "fresh", "words": "TRINETRA · updated 09:14:58"},
    "readings": [
        reading("gap_bias", label="Gap down", confidence=0.64, sessions=1, lapsed=True,
                valid_until_ms=NOW_MS - 60_000),
        reading(instrument="BANKNIFTY", label="Range", bias="neutral", confidence=None,
                quality="degraded", sessions=None),
        reading(),
    ],
    "notes": [{"key": "gaps", "count": 2, "words": "2 documents from TRINETRA did not arrive today."}],
}  # fmt: skip


# ---- who is answered ----------------------------------------------------------------------------


@pytest.mark.parametrize("module", [ops_telegram, signals_telegram], ids=["server", "trinetra"])
@pytest.mark.parametrize(
    ("chat_type", "user_id"),
    [("group", OWNER_ID), ("supergroup", OWNER_ID), ("private", STRANGER_ID), ("private", KIN_ID)],
    ids=["group chat", "supergroup", "not linked", "linked to another OpenAlgo user"],
)
def test_only_the_owners_own_chat_gets_a_figure(chats, module, chat_type, user_id):
    text = send(module, chat_type, user_id)
    assert text == ops_telegram.ONLY_YOUR_CHAT
    assert chats.reads == {"report": 0, "state": 0}  # nothing was read for them
    name = "server" if module is ops_telegram else "trinetra"
    assert chats.logged == [(user_id, f"{name}_refused", 100 + user_id)]


@pytest.mark.parametrize("module", [ops_telegram, signals_telegram], ids=["server", "trinetra"])
def test_the_owners_chat_with_an_old_key_is_asked_to_link_again(chats, module):
    text = send(module, user_id=OLD_KEY_ID)
    name = "server" if module is ops_telegram else "trinetra"
    assert text.startswith("This chat is not linked with your current OpenAlgo API key")
    assert text.endswith(f"then send /{name} again.") and chats.reads == {"report": 0, "state": 0}


def test_server_answers_the_owner_with_the_watchs_last_report(chats):
    text = send(ops_telegram)
    assert text.split("\n") == [
        "सुदर्शन / SUDARSHAN",
        "Server checks at 09:15 (under a minute ago), release v0.15.0.",
        "Needs you: price feed (not working), disk space (to look at). The System page has each.",
        "Fine: 2, switched off: 1, could not be read just now: 1.",
        "No outside heartbeat is set up: nobody is told if the whole server stops.",
    ]
    assert chats.logged == [(OWNER_ID, "server", 100 + OWNER_ID)]


def test_trinetra_answers_the_owner_with_the_readings_under_the_freshness_sentence(chats):
    text = send(signals_telegram)
    assert text.split("\n") == [
        "सुदर्शन / SUDARSHAN",
        "TRINETRA · updated 09:14:58",
        "NIFTY regime: FII distribution (bearish), confidence 0.71, 3 sessions.",
        "BANKNIFTY regime: Range (neutral). TRINETRA marks this reading as resting on incomplete "
        "data.",
        "NIFTY gap bias: Gap down (bearish), confidence 0.64, 1 session. Lapsed 09:14",
        "2 documents from TRINETRA did not arrive today.",
    ]
    for banned in ("Real-time", "Live", "LIVE"):
        assert banned not in text


@pytest.mark.parametrize("module", [ops_telegram, signals_telegram], ids=["server", "trinetra"])
def test_a_slow_or_failing_command_says_so_in_words(chats, module):
    slow = send(module, bot=Bot(TimeoutError()))
    assert slow.startswith("Still working.")
    broke = send(module, bot=Bot(RuntimeError("a fault of ours")))
    assert broke == module.BROKE and "RuntimeError" not in broke and "fault" not in broke


# ---- the words ----------------------------------------------------------------------------------


def test_server_without_a_report_or_with_an_old_one():
    assert ops_telegram.server_text(None, NOW).split("\n")[1] == ops_telegram.NO_REPORT
    old = ops_telegram.server_text(REPORT | {"heartbeat": {"state": "sent"}}, NOW + 3600)
    lines = old.split("\n")
    assert lines[1] == (
        "The server watch last reported at 09:15, 60 minutes ago, and not since: what follows is "
        "as it was then. If this stays, the watch has stopped; the System page shows the same."
    )
    assert not any("heartbeat" in line for line in lines)  # sent: nothing to say
    fine = ops_telegram.server_text({"at": NOW, "checks": [{"name": "disk", "level": "fine"}],
                                     "release": "not a tag"}, NOW)  # fmt: skip
    assert fine.split("\n")[1:] == ["Server checks at 09:15 (under a minute ago).",
                                    "Nothing needs you.", "Fine: 1."]  # fmt: skip


def test_the_report_file_is_read_without_waiting_and_a_bad_one_is_not_guessed(tmp_path):
    assert ops_telegram.watch_report(tmp_path) is None
    (tmp_path / "report.json").write_text("{not json")
    assert ops_telegram.watch_report(tmp_path) is None
    (tmp_path / "report.json").write_text(json.dumps({"at": True, "checks": []}))
    assert ops_telegram.watch_report(tmp_path) is None
    (tmp_path / "report.json").write_text(json.dumps(REPORT))
    assert ops_telegram.watch_report(tmp_path) == REPORT


def test_trinetra_when_the_view_is_off_or_stale_and_a_slash_in_a_label():
    stale = STATE | {"freshness": {"status": "stale", "words": "TRINETRA has not published since "
                                   "09:01. This is its last reading, 14 minutes old."}}  # fmt: skip
    assert signals_telegram.trinetra_text(stale).split("\n")[1].startswith("TRINETRA has not")
    tapped = signals_telegram.trinetra_text(STATE | {"readings": [reading(label="/closeall now")]})
    assert "/closeall" not in tapped and "∕closeall now" in tapped


def test_the_two_handlers_name_things_as_the_desk_and_the_notices_do():
    from sudarshan.alerts.notices import _CHECK_WORDS
    from sudarshan.alerts.signals_desk import MODULE_WORDS

    assert ops_telegram.CHECK_WORDS == _CHECK_WORDS
    assert signals_telegram.MODULE_WORDS == MODULE_WORDS


# ---- the state read ----------------------------------------------------------------------------


def test_the_state_read_is_sealed_as_the_bridge_seals_it(monkeypatch):
    from sudarshan.autologin.handoff import NonceBook, open_sealed
    from sudarshan.vault import SIGNALS_API_INFO, fernet_for

    key = fernet_for("p" * 48, SIGNALS_API_INFO)
    monkeypatch.setattr(
        signals_telegram.loopback, "holder", lambda url: signals_telegram.loopback.OURS
    )
    seen = []

    def post(url, content, **kwargs):
        seen.append((url, open_sealed(key, content, op="state", seen=NonceBook(4))))
        return httpx.Response(200, json={"status": "success", "data": STATE})

    state, said = signals_telegram.read_state("http://127.0.0.1:5588", key, post)
    assert state == STATE and said == ""
    ((url, message),) = seen
    assert url == "http://127.0.0.1:5588/v1/state"
    assert (message["op"], message["principal"], message["args"]) == ("state", "owner", {})

    def answers(payload, status=200):
        return lambda url, content, **kwargs: httpx.Response(status, json=payload)

    off = answers({"status": "success", "data": {"enabled": False, "message": c.WORDS["off"]}})
    assert signals_telegram.read_state("http://127.0.0.1:5588", key, off) == (None, c.WORDS["off"])
    refused = answers({"status": "error", "code": "forbidden"}, 403)
    assert (
        signals_telegram.read_state("http://127.0.0.1:5588", key, refused)[1] == c.WORDS["sealed"]
    )

    def down(url, content, **kwargs):
        raise httpx.ConnectError("refused")

    assert (
        signals_telegram.read_state("http://127.0.0.1:5588", key, down)[1]
        == c.WORDS["not_answering"]
    )


def test_nothing_is_asked_of_a_port_nobody_or_another_account_holds(monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("asked a port it must not")

    loop = signals_telegram.loopback
    monkeypatch.setattr(loop, "holder", lambda url: loop.NOBODY)
    assert (
        signals_telegram.read_state("http://127.0.0.1:5588", object(), never)[1]
        == c.WORDS["not_answering"]
    )
    monkeypatch.setattr(loop, "holder", lambda url: loop.OTHER)
    assert (
        signals_telegram.read_state("http://127.0.0.1:5588", object(), never)[1] == c.WORDS["held"]
    )


# ---- the bot's marked lines, and the family bot ------------------------------------------------


def test_the_owner_bot_wires_both_commands_with_marked_lines_and_the_family_bot_has_neither():
    from pathlib import Path

    from sudarshan.family import bot as family_bot

    source = Path(__file__).resolve().parents[2].joinpath("services/telegram_bot_service.py")
    text = source.read_text()
    assert "# SUDARSHAN M16.1: /server and /trinetra, read-only, in the owner's own chat" in text
    assert 'CommandHandler("server", self.cmd_server)' in text
    assert 'CommandHandler("trinetra", self.cmd_trinetra)' in text
    assert "server" not in family_bot.COMMANDS and "trinetra" not in family_bot.COMMANDS
