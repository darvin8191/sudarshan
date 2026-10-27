"""The kill switch: activation and resume (M5.13, design section 3; amendments R1, R3, R7).

The services each step calls are replaced by recorders, so what is checked is the sequence: the
switch is on and saved before any broker call, every step is tried whatever became of the ones
before, Exit all goes through the one claim and is never retried, and resuming needs the phrase,
the hold and no activation or Exit all running.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest

from sudarshan.guard import flag, gate, killswitch, words
from sudarshan.guard.store import KillSwitchState, utcnow

REPO = Path(__file__).resolve().parents[3]
WAIT = 10


class Account:
    """Every service a kill switch step calls, recorded in order."""

    def __init__(self, monkeypatch):
        import database.oauth_db as oauth_db
        import services.cancel_all_order_service as cancel_all
        import services.cancel_gtt_order_service as cancel_gtt
        import services.close_position_service as close
        import services.gtt_orderbook_service as gtt_book
        import services.sandbox_service as sandbox
        from services.agent import settings as agent_settings

        self.calls: list[str] = []
        self.broken: set[str] = set()
        self.exit_all_status = 200
        self.agent_on = False
        self.gtts = [{"trigger_id": "GTT-1"}, {"trigger_id": "GTT-2"}]

        def record(name, answer):
            def call(*args, **kwargs):
                self.calls.append(name)
                if name in self.broken:
                    raise RuntimeError(f"{name} broke")
                if name.startswith("live") and kwargs.get("force_live") is not True:
                    raise AssertionError(f"{name} was not sent with force_live")
                return answer() if callable(answer) else answer

            return call

        cancelled = {"status": "success", "canceled_orders": ["1", "2"], "failed_cancellations": []}
        monkeypatch.setattr(
            cancel_all,
            "cancel_all_orders_with_auth",
            record("live_cancel_all", (True, cancelled, 200)),
        )
        monkeypatch.setattr(
            gtt_book,
            "get_gtt_orderbook_with_auth",
            record("gtt_book", lambda: (True, {"status": "success", "data": self.gtts}, 200)),
        )
        monkeypatch.setattr(
            cancel_gtt,
            "cancel_gtt_order_with_auth",
            record("live_cancel_gtt", (True, {"status": "success"}, 200)),
        )
        monkeypatch.setattr(
            close,
            "close_position_with_auth",
            record(
                "live_exit_all",
                lambda: (
                    self.exit_all_status == 200,
                    {"status": "success", "message": "All Open Positions Squared Off"},
                    self.exit_all_status,
                ),
            ),
        )
        monkeypatch.setattr(
            sandbox,
            "sandbox_cancel_all_orders",
            record("sandbox_cancel_all", (True, {"status": "success", "canceled_count": 1}, 200)),
        )
        monkeypatch.setattr(
            sandbox,
            "sandbox_gtt_orderbook",
            record(
                "sandbox_gtt_book",
                (True, {"status": "success", "data": [{"trigger_id": "S1"}]}, 200),
            ),
        )
        monkeypatch.setattr(
            sandbox,
            "sandbox_cancel_gtt_order",
            record("sandbox_cancel_gtt", (True, {"status": "success"}, 200)),
        )
        monkeypatch.setattr(
            sandbox,
            "sandbox_close_position",
            record(
                "sandbox_exit_all",
                (True, {"status": "success", "message": "Closed 1 positions"}, 200),
            ),
        )
        monkeypatch.setattr(oauth_db, "revoke_all_tokens", record("mcp", 3))

        def engaged(*args, **kwargs):
            return self.agent_on

        def set_agent(on):
            self.calls.append(f"agent_{'on' if on else 'off'}")
            self.agent_on = on
            return on

        monkeypatch.setattr(agent_settings, "is_kill_switch_engaged", engaged)
        monkeypatch.setattr(agent_settings, "set_kill_switch", set_agent)
        monkeypatch.setattr(killswitch, "preview_live", lambda owner, gtts=True: {
            "orders": 2, "stops": 1, "protected": 1, "positions": 1})  # fmt: skip
        monkeypatch.setattr(killswitch, "_tell", lambda owner, text: self.told(text))
        monkeypatch.setattr(killswitch, "_announce", lambda state: None)
        self.messages: list[str] = []

    def told(self, text):
        self.calls.append("telegram")
        self.messages.append(text)
        return True, words.KS_TELEGRAM_SENT, {}


@pytest.fixture
def account(monkeypatch, risk_store):
    fresh = gate.Gate(store=risk_store)
    monkeypatch.setattr(gate, "GATE", fresh)
    monkeypatch.setenv(flag.ENV, "on")
    monkeypatch.setenv("MCP_HTTP_ENABLED", "True")
    monkeypatch.setattr(killswitch, "DRAIN_S", 0.5)
    monkeypatch.setattr(killswitch, "SECOND_SWEEP_AFTER_S", 0.0)
    return Account(monkeypatch)


OWNER = killswitch.Owner(
    user="owner", surface="app", actor="owner", auth_token="token", broker="upstox", api_key="KEY"
)


def _on(exit_all=False, phrase="stop"):
    return killswitch.activate(OWNER, exit_all=exit_all, phrase=phrase)


def test_with_the_guard_off_on_this_server_nothing_happens(account, monkeypatch, risk_store):
    monkeypatch.setenv(flag.ENV, "off")
    answer = _on()
    assert (answer.status, answer.message) == (503, words.KS_NOT_HERE)
    assert account.calls == [] and not risk_store.state().on


@pytest.mark.parametrize("phrase", [None, "", "STOPP", "halt"])
def test_a_wrong_phrase_changes_nothing(account, risk_store, phrase):
    answer = _on(phrase=phrase)
    assert (answer.status, answer.message) == (400, words.PHRASE_WRONG)
    assert account.calls == [] and gate.GATE.state() == KillSwitchState()


def test_turning_it_on_saves_it_before_any_broker_call_then_clears_up(
    account, risk_store, monkeypatch
):
    saved_before = []
    original = risk_store.save_state

    def watch(**changes):
        saved_before.append(list(account.calls))
        return original(**changes)

    monkeypatch.setattr(risk_store, "save_state", watch)
    answer = _on(phrase=" Stop ")  # case and spaces aside (amendment R3)
    assert answer.status == 200 and answer.state.on and not answer.state.running
    assert saved_before[0] == []  # the first save came before every call
    assert account.calls == [
        "telegram",
        "live_cancel_all",
        "gtt_book",
        "live_cancel_gtt",
        "live_cancel_gtt",
        "sandbox_cancel_all",
        "sandbox_gtt_book",
        "sandbox_cancel_gtt",
        "mcp",
        "agent_on",
        "telegram",
    ]
    saved = risk_store.state()
    assert saved.on and not saved.running and saved.agent_engaged_by_us
    steps = [step["step"] for step in saved.report["steps"]]
    assert steps == [
        "saved", "told", "drain", "live_orders", "live_gtts", "sandbox_orders", "sandbox_gtts",
        "mcp", "agent",
    ]  # fmt: skip
    assert all(step["ok"] for step in saved.report["steps"])
    live_orders = saved.report["steps"][3]["words"]
    assert live_orders == (
        "2 open orders cancelled, including 1 stop-loss order that protected 1 position."
    )
    assert risk_store.history()[0].kind == "ks_on"


def test_exit_all_goes_through_the_one_claim_and_is_never_retried(account, risk_store):
    account.exit_all_status = 409  # another Exit all holds the book
    answer = _on(exit_all=True)
    assert account.calls.count("live_exit_all") == 1
    exit_step = next(s for s in answer.state.report["steps"] if s["step"] == "live_exit_all")
    assert exit_step == {
        "step": "live_exit_all",
        "ok": False,
        "words": words.KS_EXIT_ALL_BUSY,
        "detail": {"status": 409},
    }
    assert "sandbox_exit_all" in account.calls


def test_every_step_is_tried_whatever_broke_before_it(account, risk_store):
    account.broken = {"live_cancel_all", "gtt_book", "sandbox_cancel_all", "mcp"}
    answer = _on(exit_all=True)
    assert answer.status == 200 and gate.GATE.state().on and risk_store.state().on
    tried = [step["step"] for step in answer.state.report["steps"]]
    assert tried == [
        "saved", "told", "drain", "live_orders", "live_gtts", "live_exit_all",
        "live_orders_again", "sandbox_orders", "sandbox_gtts", "sandbox_exit_all", "mcp", "agent",
    ]  # fmt: skip
    failed = {s["step"] for s in answer.state.report["steps"] if not s["ok"]}
    assert failed == {"live_orders", "live_gtts", "live_orders_again", "sandbox_orders", "mcp"}


def test_in_the_sandbox_stage_the_live_account_is_untouched(account, monkeypatch):
    monkeypatch.setenv(flag.ENV, "sandbox")
    answer = _on(exit_all=True)
    assert answer.status == 200
    assert not [c for c in account.calls if c.startswith(("live", "gtt_book"))]
    assert "sandbox_exit_all" in account.calls


def test_without_a_broker_login_the_live_steps_say_so(account):
    owner = killswitch.Owner(user="owner", surface="app", actor="owner", api_key="KEY")
    answer = killswitch.activate(owner, exit_all=False, phrase="STOP")
    notes = [s for s in answer.state.report["steps"] if s["step"] == "live"]
    assert notes == [{"step": "live", "ok": False, "words": words.KS_LIVE_NOT_REACHED}]


def test_a_send_on_its_way_is_waited_for_then_swept_again(account, monkeypatch):
    kind = gate._Kind(gate.LIVE, gate._upstox_place, gate._refuse_with_response, None)
    order = {"symbol": "CE", "exchange": "NFO", "action": "BUY", "quantity": "65", "product": "MIS"}
    gate.GATE.set_state(KillSwitchState())
    waiting = gate.GATE.admit(kind, (order, "token"), {}).token  # never answered
    answered = gate.GATE.admit(kind, (order, "token"), {}).token
    threading.Timer(0.2, gate.GATE.settle, (answered, gate.ANSWERED, "OID")).start()
    started = time.monotonic()
    answer = _on()
    assert time.monotonic() - started >= killswitch.DRAIN_S
    drain = next(s for s in answer.state.report["steps"] if s["step"] == "drain")
    assert drain["detail"] == {"sent": 2, "waiting": 1} and not drain["ok"]
    assert account.calls.count("live_cancel_all") == 2  # looked again for the one still on its way
    assert waiting is not None


def test_two_activations_at_once_run_the_steps_once(account):
    """Barrier-synchronised: both ask together; one turns it on, the other is told it is on."""
    start = threading.Barrier(2)
    answers = []

    def ask():
        start.wait(WAIT)
        answers.append(_on())

    threads = [threading.Thread(target=ask) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT)
    statuses = sorted(a.status for a in answers)
    assert statuses in ([200, 200], [200, 409])
    assert account.calls.count("live_cancel_all") == 1
    assert (
        sum(
            a.message.startswith("The kill switch is already on") or a.status == 409
            for a in answers
        )
        == 1
    )


def test_it_never_touches_analyze_mode_or_the_order_mode(account, monkeypatch):
    import database.auth_db as auth_db
    import database.settings_db as settings_db

    def forbidden(*args, **kwargs):
        raise AssertionError("the kill switch changed a mode")

    monkeypatch.setattr(settings_db, "set_analyze_mode", forbidden)
    for name in ("set_order_mode", "update_order_mode"):
        if hasattr(auth_db, name):
            monkeypatch.setattr(auth_db, name, forbidden)
    assert _on(exit_all=True).status == 200


# -- resuming -------------------------------------------------------------------------------------


def _resume(phrase="STOP"):
    return killswitch.resume(OWNER, phrase=phrase)


def _on_since(seconds_ago, **fields):
    state = KillSwitchState(
        on=True,
        since=utcnow() - timedelta(seconds=seconds_ago),
        activation_id="ks-test",
        **fields,
    )
    gate.GATE.set_state(state)
    return state


def test_resume_needs_the_phrase_and_the_hold(account, risk_store):
    _on_since(10)
    assert _resume("go").status == 400
    early = _resume()
    assert early.status == 423 and re.search(r"\b5\d seconds from now\b", early.message)
    assert gate.GATE.state().on


def test_resume_waits_for_a_running_activation_and_an_exit_all(account):
    _on_since(120, running=True)
    killswitch._running.add("ks-test")
    try:
        assert _resume().status == 409
    finally:
        killswitch._running.discard("ks-test")
    _on_since(120)
    from sudarshan.books import exit_guard

    with exit_guard.EXITS.every("live"):
        answer = _resume()
    assert (answer.status, answer.message) == (409, words.KS_EXIT_ALL_STILL_PLACED)


def test_resume_turns_it_off_saved_first_and_lets_the_agent_go_only_if_it_stopped_it(
    account, risk_store
):
    _on_since(120, agent_engaged_by_us=True)
    account.agent_on = True
    answer = _resume()
    assert (answer.status, answer.message) == (200, words.KS_RESUMED)
    assert not gate.GATE.state().on and not risk_store.state().on
    assert account.calls[-2:] == ["agent_off", "telegram"]
    account.calls.clear()
    _on_since(120, agent_engaged_by_us=False)
    account.agent_on = True  # stopped by the owner himself, not by the switch
    assert _resume().status == 200
    assert "agent_off" not in account.calls


def test_a_switch_that_cannot_be_saved_as_off_stays_on(account, risk_store, monkeypatch):
    _on_since(120)

    def broken(**changes):
        raise RuntimeError("disk full")

    monkeypatch.setattr(risk_store, "save_state", broken)
    answer = _resume()
    assert (answer.status, answer.message) == (503, words.KS_NOT_SAVED_OFF)
    assert gate.GATE.state().on


def test_it_stays_on_across_a_restart(account, risk_store, monkeypatch):
    assert _on().status == 200
    restarted = gate.Gate(store=risk_store)
    monkeypatch.setattr(gate, "GATE", restarted)
    assert restarted.state().on


def test_an_activation_cut_short_by_a_restart_is_settled_and_said(account, risk_store, monkeypatch):
    risk_store.save_state(on=True, since=utcnow(), activation_id="ks-old", running=True)
    restarted = gate.Gate(store=risk_store)
    monkeypatch.setattr(gate, "GATE", restarted)
    killswitch.recover()
    state = restarted.state()
    assert state.on and not state.running
    assert state.report["restarted"] == words.KS_RESTARTED
    assert not risk_store.state().running


def test_upstox_s_own_kill_switch_is_never_set():
    """It needs flat positions and locks the segment for 12 hours: SUDARSHAN only reads it."""
    for path in REPO.rglob("*.py"):
        if any(part in {".venv", "node_modules", "tests", "test"} for part in path.parts):
            continue
        text = path.read_text(errors="ignore")
        for line in text.splitlines():
            if "user/kill-switch" in line:
                assert not re.search(r"post|put|patch", line, re.IGNORECASE), f"{path}: {line}"


# -- what it will cancel, counted fresh (amendment R1) ---------------------------------------------


def test_the_live_preview_counts_orders_gtts_and_the_stops_that_protect_positions(monkeypatch):
    import broker.upstox.api.order_api as order_api
    import services.gtt_orderbook_service as gtt_book
    from sudarshan.guard import readers

    monkeypatch.setattr(readers, "live_rows", lambda auth: [
        {"instrument_token": "NSE_FO|1", "product": "I", "quantity": 65},
        {"instrument_token": "NSE_FO|2", "product": "D", "quantity": -30},
        {"instrument_token": "NSE_FO|3", "product": "I", "quantity": 0},
    ])  # fmt: skip

    def order(token, product, side, kind, status="open"):
        return {"instrument_token": token, "product": product, "transaction_type": side,
                "order_type": kind, "status": status}  # fmt: skip

    monkeypatch.setattr(order_api, "get_order_book", lambda auth: {"status": "success", "data": [
        order("NSE_FO|1", "I", "SELL", "SL"),  # protects the long
        order("NSE_FO|1", "I", "SELL", "SL-M", "trigger pending"),  # protects it too
        order("NSE_FO|1", "I", "BUY", "SL"),  # a buy stop on a long protects nothing
        order("NSE_FO|1", "D", "SELL", "SL"),  # another product: no position there
        order("NSE_FO|2", "D", "BUY", "SL"),  # protects the short
        order("NSE_FO|2", "D", "BUY", "LIMIT"),  # a limit is not a stop
        order("NSE_FO|1", "I", "SELL", "SL", "complete"),  # done: not open
    ]})  # fmt: skip
    monkeypatch.setattr(
        gtt_book, "get_gtt_orderbook_with_auth", lambda *a: (True, {"data": [{}, {}]}, 200)
    )
    counts = killswitch.preview_live(OWNER)
    assert counts == {"orders": 6, "stops": 3, "protected": 2, "positions": 2, "gtts": 2}


def test_the_sandbox_preview_reads_the_sandbox_s_books(monkeypatch):
    import services.sandbox_service as sandbox

    monkeypatch.setenv(flag.ENV, "sandbox")
    monkeypatch.setattr(sandbox, "sandbox_get_orderbook", lambda key, data: (True, {"data": {"orders": [
        {"symbol": "SBIN", "exchange": "NSE", "product": "MIS", "action": "SELL", "pricetype": "SL",
         "order_status": "trigger pending"},
        {"symbol": "SBIN", "exchange": "NSE", "product": "MIS", "action": "BUY", "pricetype": "LIMIT",
         "order_status": "open"},
        {"symbol": "SBIN", "exchange": "NSE", "product": "MIS", "action": "SELL", "pricetype": "SL",
         "order_status": "cancelled"},
    ]}}, 200))  # fmt: skip
    monkeypatch.setattr(sandbox, "sandbox_get_positions", lambda key, data: (True, {"data": [
        {"symbol": "SBIN", "exchange": "NSE", "product": "MIS", "quantity": 10}]}, 200))  # fmt: skip
    monkeypatch.setattr(sandbox, "sandbox_gtt_orderbook", lambda key: (True, {"data": [{}]}, 200))
    counts = killswitch.preview(OWNER)
    assert counts == {"orders": 2, "gtts": 1, "stops": 1, "protected": 1, "positions": 1}
