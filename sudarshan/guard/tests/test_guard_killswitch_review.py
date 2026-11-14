"""The kill switch over a simulated Upstox: the independent review's findings, each pinned.

Every test here fails on the code the review was made against (d628ff3).
"""

from __future__ import annotations

import importlib.util
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

import restx_api  # noqa: F401 - first, as app.py does
from sudarshan.guard import daily, flag, gate, killswitch, rules, words
from sudarshan.guard.store import KillSwitchState, RiskSettings, utcnow

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "review_ks_books_world", REPO / "sudarshan" / "books" / "tests" / "books_upstox_world.py"
)
world_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(world_module)

CE = "NIFTY27OCT2625900CE"
OWNER = killswitch.Owner("owner", "app", "owner", auth_token="token", broker="upstox")


class Upstox(world_module.Broker):
    """Cancels; a cancel of an id in ``refuse_once`` is refused once (the order had just ended)."""

    def __init__(self, held, lag=0.0):
        super().__init__(held, lag)
        self.refuse_once: set[str] = set()
        self.refuse_always: set[str] = set()
        self.deleted: list[str] = []
        self.statuses: dict[str, str] = {}  # order id -> the raw status Upstox shows for it

    def delete(self, url, headers=None, **kwargs):
        orderid = parse_qs(urlparse(url).query)["order_id"][0]
        with self.lock:
            self.deleted.append(orderid)
            if orderid in self.refuse_always:
                return world_module.FakeResponse(
                    400, {"status": "error", "errors": [{"message": "Cannot cancel"}]}
                )
            if orderid in self.refuse_once:
                self.refuse_once.discard(orderid)
                self.working = [o for o in self.working if o["id"] != orderid]
                return world_module.FakeResponse(
                    400, {"status": "error", "errors": [{"message": "Order already complete"}]}
                )
            self.working = [o for o in self.working if o["id"] != orderid]
        return world_module.FakeResponse(200, {"status": "success", "data": {"order_id": orderid}})

    def _order_book(self, timeout=None):
        response = super()._order_book(timeout)
        with self.lock:
            for row in response.json()["data"]:
                if row["order_id"] in self.statuses:
                    row["status"] = self.statuses[row["order_id"]]
        return response


@pytest.fixture
def upstox(monkeypatch, risk_store):
    import services.gtt_orderbook_service as gtt_book

    broker = Upstox({CE: 65, "SBIN": 0})
    world = world_module.install(monkeypatch, broker)
    monkeypatch.setattr(
        gtt_book,
        "get_gtt_orderbook_with_auth",
        lambda *args, **kwargs: (True, {"status": "success", "data": []}, 200),
    )
    monkeypatch.setattr(killswitch, "_tell", lambda owner, text: (True, "told", {}))
    monkeypatch.setattr(killswitch, "_announce", lambda state: None)
    monkeypatch.setattr(
        killswitch, "_engage_agent", lambda: (True, "agent", {"engaged_by_us": False})
    )
    monkeypatch.setattr(killswitch, "DRAIN_S", 0.3)
    monkeypatch.setattr(killswitch, "CANCELS_POLL_S", 0.05)
    monkeypatch.setattr(killswitch, "SECOND_SWEEP_AFTER_S", 0.0)
    fresh = gate.Gate(store=risk_store)
    fresh.set_state(KillSwitchState())
    monkeypatch.setattr(gate, "GATE", fresh)
    monkeypatch.setenv(flag.ENV, "on")
    return world


def _steps(answer) -> dict:
    return {step["step"]: step for step in answer.state.report["steps"]}


def _order(answer) -> list[str]:
    return [step["step"] for step in answer.state.report["steps"]]


# -- H1: the second look comes before Exit all, and never cancels its exit --------------------------


def test_h1_the_second_look_runs_before_exit_all_and_leaves_its_exit(upstox):
    broker = upstox.broker
    broker.rest(CE, "SELL", 65)  # the stop loss protecting the long
    broker.part[CE] = 0  # the exit rests unfilled
    unreadable = {"reads": 0}
    real = broker._order_book

    def book(timeout=None):
        unreadable["reads"] += 1
        broker.orders_unreadable = unreadable["reads"] <= 3  # the first three reads fail
        return real(timeout)

    broker._order_book = book
    answer = killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    steps = _steps(answer)
    names = _order(answer)
    assert names.index("live_orders_again") < names.index("live_exit_all")
    sent = steps["live_exit_all"]["detail"]["positions"][0]
    assert sent["outcome"] == "sent"
    assert sent["orderids"][0] not in broker.deleted  # the exit was not cancelled
    assert [o["symbol"] for o in broker.working] == [CE]  # it rests; the stop loss was cancelled


def test_h1_both_looks_pass_their_cancelled_ids_on(upstox, monkeypatch):
    broker = upstox.broker
    broker.rest(CE, "SELL", 65)
    broker.rest("SBIN", "BUY", 1, kind="LIMIT", trigger=0)
    seen_by_exit_all = {}
    real = killswitch._exit_all_live

    def spy(owner, cancelled, seen, *rest):
        seen_by_exit_all["ids"] = list(cancelled)
        return real(owner, cancelled, seen, *rest)

    monkeypatch.setattr(killswitch, "_exit_all_live", spy)
    broker.refuse_always.add(broker.working[-1]["id"])  # forces the second look
    first = {o["id"] for o in broker.working}
    killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    assert set(seen_by_exit_all["ids"]) == first - broker_refused(broker)


def broker_refused(broker):
    return set(broker.refuse_always)


# -- H2: an exit the switch cancelled is not "on its way" -------------------------------------------


def test_h2_a_resting_exit_the_switch_cancelled_does_not_stop_exit_all(upstox):
    broker = upstox.broker
    broker.part[CE] = 0  # the phone's exit rests unfilled
    code, body = upstox.phone_exit(CE)
    assert code == 200, body
    answer = killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    each = _steps(answer)["live_exit_all"]["detail"]["positions"]
    assert [(i["symbol"], i["outcome"]) for i in each] == [(CE, "sent")]
    assert [o["symbol"] for o in broker.working] == [CE]  # the new exit rests


# -- H3: an order let through before the switch is refused at send time -----------------------------


def _slow_pacer(monkeypatch, per_second=1):
    from broker.upstox.api import rate_limiter

    slow = rate_limiter.SlidingWindowLimiter("order", per_second, 475, 1900, kind="order")
    monkeypatch.setitem(rate_limiter._LIMITERS, "order", slow)
    return slow


def _runaway(count, answers):
    from services.place_order_service import place_order

    entry = {"apikey": "KEY", "strategy": "Runaway", "symbol": "SBIN", "exchange": "NSE",
             "action": "BUY", "quantity": "1", "pricetype": "MARKET", "product": "MIS"}  # fmt: skip
    threads = [
        threading.Thread(
            target=lambda: answers.append(
                place_order(dict(entry), auth_token="token", broker="upstox")
            )
        )
        for _ in range(count)
    ]
    for thread in threads:
        thread.start()
    return threads


def test_h3_entries_let_through_before_the_switch_are_not_sent_after_it(upstox, monkeypatch):
    import services.place_order_service as place_service

    monkeypatch.setattr(place_service, "get_analyze_mode", lambda: False)
    _slow_pacer(monkeypatch)
    answers: list = []
    threads = _runaway(5, answers)
    deadline = time.monotonic() + 30
    while gate.GATE.sends() < 5:
        assert time.monotonic() < deadline, "the five entries were never admitted"
        time.sleep(0.02)
    answer = killswitch.activate(OWNER, exit_all=False, phrase="STOP")
    finished = time.monotonic()
    for thread in threads:
        thread.join(30)
    drain = _steps(answer)["drain"]
    after = [o for o in upstox.broker.orders if o["symbol"] == "SBIN" and o["at"] > finished]
    assert after == []  # nothing reached Upstox once the switch had said it was done
    assert drain["detail"]["stopped"] >= 3 and drain["ok"]
    assert "stopped before being sent" in drain["words"]
    refused = [body.get("message", "") for ok, body, _code in answers if not ok]
    assert len(refused) >= 3
    assert all("kill switch is on" in text and "not sent" in text for text in refused)


def test_h3_the_switch_s_own_exit_does_not_wait_behind_that_backlog(upstox, monkeypatch):
    import services.place_order_service as place_service

    monkeypatch.setattr(place_service, "get_analyze_mode", lambda: False)
    _slow_pacer(monkeypatch)
    upstox.broker.part[CE] = 0
    answers: list = []
    threads = _runaway(7, answers)
    deadline = time.monotonic() + 30
    while gate.GATE.sends() < 7:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    started = time.monotonic()
    answer = killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    for thread in threads:
        thread.join(30)
    exit_at = [o["at"] for o in upstox.broker.orders if o["symbol"] == CE][0]
    assert exit_at - started < 4.0  # behind seven entries at one a second it would be ~7 s
    assert _steps(answer)["live_exit_all"]["detail"]["positions"][0]["outcome"] == "sent"


def test_h3_a_reducing_send_waiting_for_its_turn_is_still_sent(upstox, monkeypatch):
    """An exit judged reducing before the switch went on keeps its place."""
    upstox.broker.part[CE] = 0
    token = None
    with gate.GATE._lock:
        send = gate.Send(
            order=gate.Order(gate.LIVE, gate.PLACE, "NFO", CE, "MIS", rules.SELL, 65, "token"),
            quantity=65,
            reducing=True,
            expect=0,
            position_then=65,
            admitted=time.monotonic(),
            sent_at=time.time(),
        )
        token = 901
        gate.GATE._sends[token] = send
    gate.GATE.set_state(KillSwitchState(on=True, since=utcnow(), activation_id="ks-x"))
    assert gate.GATE.send_refusal(token) is None
    assert gate.GATE.withdrawn([token]) == []


def test_h3_an_unjudged_send_is_refused_once_the_switch_is_on_and_not_before(upstox):
    order = gate.Order(gate.LIVE, gate.PLACE, "NSE", "SBIN", "MIS", rules.BUY, 1, "token")
    with gate.GATE._lock:
        for token in (902, 903):
            gate.GATE._sends[token] = gate.Send(
                order=order, quantity=1, reducing=None, expect=None, position_then=None,
                admitted=time.monotonic(), sent_at=time.time(),
            )  # fmt: skip
    assert gate.GATE.send_refusal(902) is None  # the switch is off
    gate.GATE.set_state(KillSwitchState(on=True, since=utcnow(), activation_id="ks-x"))
    said = gate.GATE.send_refusal(902)
    assert said == words.kill_switch_on_at_send(killswitch._clock(utcnow()))
    assert gate.GATE.unanswered([902]) == []  # it no longer counts as on its way
    assert gate.GATE.withdrawn([902, 903]) == [902]
    assert gate.GATE.unanswered([903]) == [903]


def test_h3_the_pacer_gives_back_the_slot_of_a_send_it_withdraws():
    from broker.upstox.api import rate_limiter

    limiter = rate_limiter.SlidingWindowLimiter("order", 1, 475, 1900, kind="order")
    limiter.acquire()  # the first slot is now
    refusing = {"on": False}

    def check():
        return "withdrawn" if refusing["on"] else None

    outcome = []

    def waiting():
        token = rate_limiter.SEND_CHECK.set(check)
        try:
            limiter.acquire()  # its slot is a second away
            outcome.append("sent")
        except rate_limiter.SendWithdrawn as exc:
            outcome.append(exc.text)
        finally:
            rate_limiter.SEND_CHECK.reset(token)

    thread = threading.Thread(target=waiting)
    thread.start()
    time.sleep(0.05)
    assert limiter.depth() == 2
    refusing["on"] = True
    thread.join(5)
    assert outcome == ["withdrawn"]
    assert limiter.depth() == 1  # the slot was given back
    assert limiter.reserve() < 1.0


def test_h3_a_send_with_no_check_waits_as_before():
    from broker.upstox.api import rate_limiter

    limiter = rate_limiter.SlidingWindowLimiter("order", 1, 475, 1900, kind="order")
    limiter.acquire()
    started = time.monotonic()
    limiter.acquire()
    assert time.monotonic() - started >= 0.9


# -- H4: every order not final is cancelled, by id ---------------------------------------------------


def test_h4_after_market_and_pending_orders_are_cancelled_by_id(upstox):
    broker = upstox.broker
    broker.rest(CE, "SELL", 65)  # the stop loss
    broker.rest("SBIN", "BUY", 1, kind="LIMIT", trigger=0)
    amo = broker.working[-1]["id"]
    broker.statuses[amo] = "after market order req received"
    broker.rest("SBIN", "BUY", 2, kind="LIMIT", trigger=0)
    pending = broker.working[-1]["id"]
    broker.statuses[pending] = "validation pending"
    answer = killswitch.activate(OWNER, exit_all=False, phrase="STOP")
    step = _steps(answer)["live_orders"]
    assert broker.working == []
    assert step["ok"] is True
    assert step["words"].startswith("3 open orders cancelled")
    assert {amo, pending} <= set(step["detail"]["cancelled_ids"])
    assert "live_orders_again" not in _steps(answer)


def test_h4_a_cancel_refused_for_an_order_still_working_is_said_and_looked_at_again(upstox):
    broker = upstox.broker
    broker.rest("SBIN", "BUY", 1, kind="LIMIT", trigger=0)
    stuck = broker.working[-1]["id"]
    broker.statuses[stuck] = "validation pending"
    broker.refuse_always.add(stuck)
    answer = killswitch.activate(OWNER, exit_all=False, phrase="STOP")
    steps = _steps(answer)
    assert steps["live_orders"]["ok"] is False
    assert "could not be cancelled and may still be working" in steps["live_orders"]["words"]
    assert "live_orders_again" in steps
    assert [o["id"] for o in broker.working] == [stuck]


def test_h4_the_preview_and_the_step_count_the_same_orders(upstox):
    broker = upstox.broker
    broker.rest("SBIN", "BUY", 1, kind="LIMIT", trigger=0)
    broker.statuses[broker.working[-1]["id"]] = "after market order req received"
    assert killswitch.preview_live(OWNER, gtts=False)["orders"] == 1
    answer = killswitch.activate(OWNER, exit_all=False, phrase="STOP")
    assert _steps(answer)["live_orders"]["words"].startswith("1 open order cancelled")


# -- M3 / M4 / M5 --------------------------------------------------------------------------------


def test_m4_a_single_gtt_stop_is_counted_as_a_stop(monkeypatch):
    import broker.upstox.api.order_api as order_api
    import services.gtt_orderbook_service as gtt_book
    from sudarshan.guard import readers

    monkeypatch.setattr(readers, "live_rows", lambda auth: [
        {"instrument_token": "NSE_FO|1001", "exchange": "NFO", "product": "D", "quantity": 65}])  # fmt: skip
    monkeypatch.setattr(order_api, "get_order_book", lambda auth: {"status": "success", "data": []})
    monkeypatch.setattr(order_api, "get_symbol", lambda token, exchange: CE)

    def gtt(trigger_type="SINGLE", action="SELL"):
        return {"trigger_id": "G1", "symbol": CE, "exchange": "NFO", "product": "NRML",
                "action": action, "quantity": 65, "trigger_type": trigger_type}  # fmt: skip

    def counted(row):
        monkeypatch.setattr(
            gtt_book, "get_gtt_orderbook_with_auth", lambda *a: (True, {"data": [row]}, 200)
        )
        return killswitch.preview_live(OWNER)

    stop = counted(gtt())
    assert (stop["stops"], stop["gtt_stops"], stop["protected"]) == (0, 1, 1)
    said = words.ks_preview(stop)
    assert "including 1 stop-loss GTT that protects 1 position" in said
    assert "open without a stop" in said
    assert "gtt_stops" not in counted(gtt("OCO"))  # a two-leg GTT opens a position
    assert "gtt_stops" not in counted(gtt(action="BUY"))  # a buy against a long is no stop


def test_m4_the_sandbox_counts_a_single_gtt_stop_too(monkeypatch):
    import services.sandbox_service as sandbox

    monkeypatch.setenv(flag.ENV, "sandbox")
    monkeypatch.setattr(
        sandbox, "sandbox_get_orderbook", lambda key, data: (True, {"data": {"orders": []}}, 200)
    )
    monkeypatch.setattr(sandbox, "sandbox_get_positions", lambda key, data: (True, {"data": [
        {"symbol": "SBIN", "exchange": "NSE", "product": "MIS", "quantity": 10}]}, 200))  # fmt: skip
    monkeypatch.setattr(sandbox, "sandbox_gtt_orderbook", lambda key: (True, {"data": [
        {"symbol": "SBIN", "exchange": "NSE", "product": "MIS", "action": "SELL",
         "trigger_type": "SINGLE"}]}, 200))  # fmt: skip
    counts = killswitch.preview(killswitch.Owner("owner", "app", "owner", api_key="KEY"))
    assert (counts["stops"], counts["gtt_stops"], counts["protected"]) == (0, 1, 1)


def test_m5_the_reads_before_and_after_the_cancel_are_cut_off(upstox, monkeypatch):
    from sudarshan.guard import readers

    monkeypatch.setattr(readers, "READ_LIMIT_S", 0.3)
    broker = upstox.broker
    broker.rest(CE, "SELL", 65)
    broker.order_book_delay = 3.0  # far past the limit: every read is cut off at 0.3 s
    started = time.monotonic()
    answer = killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    took = time.monotonic() - started
    assert answer.status == 200
    assert took < 8.0  # unbounded, each of a dozen reads would take the full 3 s


def test_m3_exit_all_that_left_positions_out_says_so_in_its_own_words(monkeypatch, risk_store):
    from guard_world import Account

    fresh = gate.Gate(store=risk_store)
    monkeypatch.setattr(gate, "GATE", fresh)
    monkeypatch.setenv(flag.ENV, "on")
    monkeypatch.setattr(killswitch, "SECOND_SWEEP_AFTER_S", 0.0)
    account = Account(monkeypatch)
    account.exit_all_status = 409
    account.exit_all_body = {
        "status": "error",
        "message": "Exit all did not close every position. Of 2 exits, 1 was not sent.",
        "positions": [{"symbol": CE, "outcome": "not_sent", "reason": "busy"}],
    }
    owner = killswitch.Owner("owner", "app", "owner", "token", "upstox", "KEY")
    answer = killswitch.activate(owner, exit_all=True, phrase="STOP")
    step = _steps(answer)["live_exit_all"]
    assert step["ok"] is False and "did not close every position" in step["words"]
    assert words.KS_EXIT_ALL_BUSY not in step["words"]
    # With no positions list the book is held by another Exit all.
    gate.GATE.set_state(KillSwitchState())
    account.exit_all_body = {"status": "error", "message": "Exit all is still being placed."}
    answer = killswitch.activate(owner, exit_all=True, phrase="STOP")
    assert _steps(answer)["live_exit_all"]["words"] == words.KS_EXIT_ALL_BUSY


# -- M1 / M2: what Telegram says ---------------------------------------------------------------------


class _Bot:
    async def _in_app_world(self, fn, *args, timeout: float, offload: bool = True):
        return fn(*args)


class _Message:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


@pytest.fixture
def chat(monkeypatch, risk_store):
    from types import SimpleNamespace

    from guard_world import Account

    import database.telegram_db as telegram_db
    import database.user_db as user_db
    import services.telegram_bot_service as bot_service

    fresh = gate.Gate(store=risk_store)
    fresh.set_state(KillSwitchState())
    monkeypatch.setattr(gate, "GATE", fresh)
    monkeypatch.setattr(killswitch, "DRAIN_S", 0.2)
    monkeypatch.setattr(killswitch, "SECOND_SWEEP_AFTER_S", 0.0)
    account = Account(monkeypatch)
    monkeypatch.setattr(
        telegram_db, "get_telegram_user", lambda tid: {"openalgo_username": "owner"}
    )
    monkeypatch.setattr(user_db, "find_user_by_username", lambda: SimpleNamespace(username="owner"))
    # The owner's own chat, linked with this server's API key (v0.5.2: owner_of asks the bot's
    # link check, which reads the chat's stored key and this server's keys).
    monkeypatch.setattr(bot_service, "get_user_credentials", lambda tid: {"api_key": "owner-key"})
    monkeypatch.setattr(
        bot_service, "get_username_by_apikey", lambda key: "owner" if key == "owner-key" else None
    )
    monkeypatch.setattr(telegram_db, "log_command", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        killswitch,
        "owner_for",
        lambda username, **kw: killswitch.Owner(
            username, kw["surface"], kw["actor"], "token", "upstox", "KEY"
        ),
    )
    return account


def _send(*args):
    import asyncio
    from types import SimpleNamespace

    from sudarshan.guard import telegram

    message = _Message()
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(type="private", id=107),
        effective_user=SimpleNamespace(id=7),
        effective_message=message,
    )
    asyncio.run(telegram.handle(_Bot(), update, SimpleNamespace(args=list(args))))
    return message.replies[0]


def test_m1_in_the_sandbox_stage_telegram_says_live_orders_are_not_checked(chat, monkeypatch):
    monkeypatch.setenv(flag.ENV, "sandbox")
    text = _send("exit", "STOP")
    assert text.startswith("Kill switch on for the sandbox only: Live orders are not checked")
    assert "Upstox was not touched" in text
    assert not [c for c in chat.calls if c.startswith("live")]
    monkeypatch.setenv(flag.ENV, "on")
    gate.GATE.set_state(KillSwitchState())
    assert _send("on", "STOP").startswith("Kill switch on: new orders are refused")


def test_m2_exit_on_a_switch_already_on_says_so_and_exits_nothing(chat, monkeypatch):
    monkeypatch.setenv(flag.ENV, "on")
    first = _send("on", "STOP")
    chat.calls.clear()
    second = _send("exit", "STOP")
    assert second != first
    assert "already on" in second and "Exit all was not run" in second and "/closeall" in second
    assert "live_exit_all" not in chat.calls and "sandbox_exit_all" not in chat.calls
    assert "already on" in _send("on", "STOP")  # a plain "on" gets no Exit all sentence
    assert "Exit all was not run" not in _send("on", "STOP")


def test_l9_a_telegram_activation_sends_its_report_once(chat, monkeypatch):
    monkeypatch.setenv(flag.ENV, "on")
    reply = _send("on", "STOP")
    report = words.ks_report_message(gate.GATE.state().report)
    assert reply == report
    assert report not in chat.messages  # the reply is the report; no second copy
    gate.GATE.set_state(KillSwitchState())
    killswitch.activate(
        killswitch.Owner("owner", "app", "owner", "token", "upstox", "KEY"),
        exit_all=False,
        phrase="STOP",
    )
    assert any(text.startswith("Kill switch on:") for text in chat.messages)  # the app is told


# -- L1, L6, L7 --------------------------------------------------------------------------------------


def _on_for(seconds, **fields):
    return KillSwitchState(
        on=True, since=utcnow() - timedelta(seconds=seconds), activation_id="ks-x", **fields
    )


def test_l1_resume_says_new_orders_stay_refused_while_the_day_is_locked(chat, monkeypatch):
    import extensions

    monkeypatch.setenv(flag.ENV, "sandbox")
    monkeypatch.setattr(extensions, "emit_from_any_thread", lambda *args, **kwargs: None)
    day = daily.day_of(datetime.now(UTC)).isoformat()
    gate.GATE._lock_day(
        "sandbox",
        gate._Guards(day, RiskSettings(daily_loss_limit=4000.0)),
        daily.DayFigure(-4100.0, 0.0),
    )
    gate.GATE.set_state(_on_for(120))
    answer = killswitch.resume(
        killswitch.Owner("owner", "app", "owner", api_key="KEY"), phrase="STOP"
    )
    assert answer.status == 200 and answer.message == words.KS_RESUMED_LOCKED
    assert not answer.state.on


def test_l6_unreadable_settings_still_let_the_switch_be_turned_on(chat, monkeypatch):
    monkeypatch.setenv(flag.ENV, "on")
    monkeypatch.setattr(killswitch, "_settings", lambda: None)
    owner = killswitch.Owner("owner", "app", "owner", "token", "upstox", "KEY")
    assert killswitch.activate(owner, exit_all=False, phrase="nope").status == 400
    assert (
        killswitch.activate(owner, exit_all=False, phrase=RiskSettings().confirm_phrase).status
        == 200
    )


def test_l7_resume_saves_and_announces_only_after_the_gate_confirms(chat, monkeypatch, risk_store):
    monkeypatch.setenv(flag.ENV, "on")
    announced = []
    monkeypatch.setattr(killswitch, "_announce", lambda state: announced.append(state.on))
    gate.GATE.set_state(_on_for(120))
    real = gate.GATE.turn_off
    monkeypatch.setattr(gate.GATE, "turn_off", lambda *a: False)  # another resume got there first
    saved = []
    monkeypatch.setattr(risk_store, "save_state", lambda **kw: saved.append(kw))
    owner = killswitch.Owner("owner", "app", "owner", api_key="KEY")
    answer = killswitch.resume(owner, phrase="STOP")
    assert answer.status == 409 and saved == [] and announced == []
    monkeypatch.setattr(gate.GATE, "turn_off", real)


def test_l7_a_resume_that_cannot_be_saved_leaves_the_switch_on(chat, monkeypatch, risk_store):
    monkeypatch.setenv(flag.ENV, "on")
    gate.GATE.set_state(_on_for(120))

    def broken(**kw):
        raise OSError("disk")

    monkeypatch.setattr(risk_store, "save_state", broken)
    answer = killswitch.resume(
        killswitch.Owner("owner", "app", "owner", api_key="KEY"), phrase="STOP"
    )
    assert (answer.status, answer.message) == (503, words.KS_NOT_SAVED_OFF)
    assert gate.GATE.state().on


# -- mutation survivors: the hold, the gate's turn_off, the phrase ----------------------------------


def test_k9_k10_the_hold_is_sixty_seconds_to_the_second(chat, monkeypatch):
    monkeypatch.setenv(flag.ENV, "on")
    owner = killswitch.Owner("owner", "app", "owner", api_key="KEY")
    gate.GATE.set_state(_on_for(59))
    assert killswitch.resume(owner, phrase="STOP").status == 423  # one second early
    gate.GATE.set_state(_on_for(60.5))
    assert killswitch.resume(owner, phrase="STOP").status == 200  # half a second after


def test_g3_g4_turn_off_needs_the_activation_and_a_finished_run():
    fresh = gate.Gate(store=None)
    on = KillSwitchState(on=True, since=utcnow(), activation_id="ks-a", running=False)
    fresh.set_state(on)
    assert fresh.turn_off("ks-other", KillSwitchState()) is False
    assert fresh.state() is on
    fresh.set_state(KillSwitchState(on=True, since=utcnow(), activation_id="ks-a", running=True))
    assert fresh.turn_off("ks-a", KillSwitchState()) is False
    assert fresh.state().on


def test_k5b_an_empty_phrase_matches_nothing():
    assert killswitch.phrase_matches("", "") is False
    assert killswitch.phrase_matches("anything", "") is False
    assert killswitch.phrase_matches("  ", "  ") is False


def test_l5_mcp_is_left_out_when_off_and_honest_about_access_tokens_when_on(chat, monkeypatch):
    monkeypatch.setenv(flag.ENV, "on")
    owner = killswitch.Owner("owner", "app", "owner", "token", "upstox", "KEY")
    monkeypatch.setenv("MCP_HTTP_ENABLED", "False")
    answer = killswitch.activate(owner, exit_all=False, phrase="STOP")
    assert "mcp" not in _order(answer)
    gate.GATE.set_state(KillSwitchState())
    monkeypatch.setenv("MCP_HTTP_ENABLED", "True")
    answer = killswitch.activate(owner, exit_all=False, phrase="STOP")
    said = _steps(answer)["mcp"]["words"]
    assert "revoked" in said and "up to 15 minutes" in said and "access revoked" not in said
