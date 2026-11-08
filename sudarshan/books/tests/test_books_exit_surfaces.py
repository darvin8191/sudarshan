"""The scalping terminal's closes and the strategy module's exits under the one-at-a-time rule
(M6.9, item 3 of fix/exit-gaps; sudarshan/books/surface_exits.py).

Both surfaces size their own exits and send a plain order. In Live they now claim the position
while the order is placed, respect the hold of an earlier exit (reading the order book only
then), and note the hold after. Every test here fails on 376dc58 unless it says it is a control.
"""

from __future__ import annotations

import threading
import time

import pytest

from sudarshan.books import exit_guard, surface_exits

X = "NIFTY27OCT2625900CE"
POSITION = ("NFO", X, "MIS")


def _ok(orderid="261027000001"):
    return True, {"status": "success", "orderid": orderid}, 200


def _refusal(status=400):
    return False, {"status": "error", "message": "Upstox refused it"}, status


# -- the helper itself ---------------------------------------------------------------------------------


def test_a_close_claims_its_position_while_it_is_placed_and_lets_go_after():
    seen = []

    def send():
        seen.append(exit_guard.why_busy("live", *POSITION))
        return _ok()

    answer = surface_exits.exit_once("live", *POSITION, send, lambda: {})
    assert answer == _ok()
    assert len(seen) == 1 and "on its way" in seen[0]
    assert len(exit_guard.EXITS) == 0


def test_a_close_is_refused_while_another_exit_of_the_position_is_being_placed():
    sent = []
    with exit_guard.one_exit("live", *POSITION):
        ok, body, status = surface_exits.exit_once(
            "live", *POSITION, lambda: sent.append(1) or _ok(), lambda: {}
        )
    assert (ok, status) == (False, 409) and sent == []
    assert "An exit for NIFTY 25900 CE is already on its way" in body["message"]
    assert "Orders" in body["message"]


def test_a_close_is_refused_while_exit_all_holds_the_book():
    sent = []
    with exit_guard.EXITS.every("live"):
        ok, body, status = surface_exits.exit_once(
            "live", *POSITION, lambda: sent.append(1) or _ok(), lambda: {}
        )
    assert (ok, status) == (False, 409) and sent == []
    assert body["message"] == exit_guard.EXIT_ALL_BEING_PLACED


def test_a_close_notes_the_hold_so_the_next_exit_is_held_until_the_book_shows_it_ended():
    surface_exits.exit_once("live", *POSITION, lambda: _ok("261027000007"), lambda: {})
    assert exit_guard.EXITS.placed_lately("live") == {POSITION}
    # The app's Exit a moment later: the order is listed as open, so it is refused.
    said = exit_guard.hold_verdict("live", *POSITION, {"261027000007": "open"})
    assert said and "order ending 0007" in said and "still working at Upstox" in said
    # Once the book shows it complete, the hold ends.
    assert exit_guard.hold_verdict("live", *POSITION, {"261027000007": "complete"}) is None
    assert exit_guard.EXITS.placed_lately("live") == set()


def test_a_second_close_reads_the_book_only_while_a_hold_is_open():
    reads = []

    def statuses():
        reads.append(1)
        return {"261027000001": "complete"}

    surface_exits.exit_once("live", *POSITION, _ok, statuses)
    assert reads == []  # no hold open: no read
    surface_exits.exit_once("live", *POSITION, _ok, statuses)
    assert reads == [1]  # the first's hold: read once, found complete, ended


def test_a_second_close_while_the_first_still_works_is_refused_with_the_order_named():
    surface_exits.exit_once("live", *POSITION, lambda: _ok("261027000031"), lambda: {})
    sent = []
    ok, body, status = surface_exits.exit_once(
        "live", *POSITION, lambda: sent.append(1) or _ok(), lambda: {"261027000031": "open"}
    )
    assert (ok, status) == (False, 409) and sent == []
    assert (
        "order ending 0031" in body["message"]
        and "Cancel or modify it in Orders" in body["message"]
    )


def test_a_second_close_while_the_book_cannot_be_read_says_the_first_is_not_confirmed():
    surface_exits.exit_once("live", *POSITION, lambda: _ok("261027000031"), lambda: {})
    ok, body, status = surface_exits.exit_once("live", *POSITION, _ok, lambda: None)
    assert (ok, status) == (False, 409)
    assert (
        "not confirmed" in body["message"] and "could not be checked at Upstox" in body["message"]
    )


def test_a_refused_close_notes_no_hold_and_lets_the_claim_go():
    ok, _body, status = surface_exits.exit_once("live", *POSITION, _refusal, lambda: {})
    assert (ok, status) == (False, 400)
    assert exit_guard.EXITS.placed_lately("live") == set() and len(exit_guard.EXITS) == 0


@pytest.mark.parametrize(
    "answer", [(False, {"status": "error", "message": "x"}, 502), (False, "", 500)]
)
def test_an_answer_that_may_have_placed_an_order_notes_the_hold(answer):
    surface_exits.exit_once("live", *POSITION, lambda: answer, lambda: {})
    assert exit_guard.EXITS.placed_lately("live") == {POSITION}


def test_a_send_that_raised_notes_the_hold_and_lets_the_claim_go():
    def send():
        raise RuntimeError("dropped after the send")

    with pytest.raises(RuntimeError):
        surface_exits.exit_once("live", *POSITION, send, lambda: {})
    assert exit_guard.EXITS.placed_lately("live") == {POSITION} and len(exit_guard.EXITS) == 0


def test_a_split_close_notes_every_slice():
    answer = (True, {"status": "success", "results": [{"orderid": "1", "status": "success"},
                                                      {"orderid": "2", "status": "success"}]}, 200)  # fmt: skip
    assert surface_exits.order_ids(answer) == ("1", "2")
    surface_exits.exit_once("live", *POSITION, lambda: answer, lambda: {})
    assert exit_guard.hold_verdict("live", *POSITION, {"1": "complete", "2": "open"})
    assert exit_guard.hold_verdict("live", *POSITION, {"1": "complete", "2": "complete"}) is None


def test_the_sandbox_is_left_alone():
    """Control: the sandbox's book shows an exit the moment it is placed."""
    sent = []
    with exit_guard.EXITS.every("live"):
        ok, _body, _status = surface_exits.exit_once(
            "sandbox", *POSITION, lambda: sent.append(1) or _ok(), lambda: {}
        )
    assert ok and sent == [1]
    assert exit_guard.EXITS.placed_lately("sandbox") == set()


def test_two_closes_released_together_send_one_order():
    """Barrier-synchronised: the first claims and places (slowly), the second finds the claim and
    is refused; nothing waits on the guard's lock across the send."""
    barrier = threading.Barrier(2)
    sent = []
    results = [None, None]

    def send():
        sent.append(1)
        time.sleep(0.3)
        return _ok(f"26102700000{len(sent)}")

    def worker(index):
        barrier.wait(5)
        results[index] = surface_exits.exit_once("live", *POSITION, send, lambda: None)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    codes = sorted(status for _ok, _body, status in results)
    assert codes == [200, 409] and sent == [1]


# -- the scalping terminal ---------------------------------------------------------------------------------


@pytest.fixture
def scalping(monkeypatch):
    import restx_api  # noqa: F401, I001  (first, or place_order_service's import cycle fails)

    import blueprints.scalping as scalping

    sent = []

    def place_order(order_data, api_key, auth_token, broker):
        sent.append((order_data["symbol"], order_data["action"], int(order_data["quantity"])))
        return True, {"status": "success", "orderid": f"2610270000{len(sent):02d}"}, 200

    monkeypatch.setattr("services.place_order_service.place_order", place_order)
    monkeypatch.setattr(scalping, "_is_derivative", lambda exchange: False)  # no symbol master
    monkeypatch.setattr(scalping, "_current_mode", lambda: "live")
    monkeypatch.setattr(surface_exits, "upstox_statuses", lambda auth, broker: {})
    scalping.sent = sent
    yield scalping
    del scalping.sent


def _close(scalping, quantity=65):
    return scalping._reducing_exit(X, "NFO", "MIS", "SELL", quantity, "tok", "upstox", "key")


def test_a_scalping_close_claims_its_position_and_notes_the_hold(scalping):
    ok, body, status = _close(scalping)
    assert (ok, status) == (True, 200) and scalping.sent == [(X, "SELL", 65)]
    assert exit_guard.EXITS.placed_lately("live") == {POSITION}
    assert body["orderid"] == "261027000001"


def test_a_scalping_close_is_refused_while_the_app_s_exit_is_on_its_way(scalping):
    with exit_guard.one_exit("live", "NFO", X, "MIS"):
        ok, body, status = _close(scalping)
    assert (ok, status) == (False, 409) and scalping.sent == []
    assert "already on its way" in body["message"]


def test_a_scalping_close_is_refused_while_its_own_earlier_close_still_works(scalping, monkeypatch):
    _close(scalping)
    monkeypatch.setattr(surface_exits, "upstox_statuses", lambda a, b: {"261027000001": "open"})
    ok, body, status = _close(scalping)
    assert (ok, status) == (False, 409) and scalping.sent == [(X, "SELL", 65)]
    assert "still working at Upstox" in body["message"]


def test_a_scalping_close_goes_once_the_book_shows_the_earlier_one_complete(scalping, monkeypatch):
    _close(scalping)
    monkeypatch.setattr(surface_exits, "upstox_statuses", lambda a, b: {"261027000001": "complete"})
    ok, _body, _status = _close(scalping)
    assert ok and scalping.sent == [(X, "SELL", 65), (X, "SELL", 65)]


def test_a_scalping_close_in_analyze_mode_is_not_guarded(scalping, monkeypatch):
    """Control: the sandbox book shows the exit at once."""
    monkeypatch.setattr(scalping, "_current_mode", lambda: "analyze")
    with exit_guard.EXITS.every("live"):
        ok, _body, _status = _close(scalping)
    assert ok and scalping.sent == [(X, "SELL", 65)]
    assert exit_guard.EXITS.placed_lately("live") == set()


def test_exit_all_leaves_a_position_a_scalping_close_is_placing_to_it(scalping, monkeypatch):
    """The other direction: Exit all starting while the scalping close is on its way to Upstox
    leaves the position out, instead of sending a second exit beside it."""
    entered = threading.Event()
    release = threading.Event()
    seen = {}

    def place_order(order_data, api_key, auth_token, broker):
        entered.set()
        release.wait(5)
        return True, {"status": "success", "orderid": "261027000001"}, 200

    monkeypatch.setattr("services.place_order_service.place_order", place_order)
    thread = threading.Thread(target=lambda: _close(scalping), daemon=True)
    thread.start()
    assert entered.wait(5)
    seen["busy"] = exit_guard.busy_positions("live")
    release.set()
    thread.join(5)
    assert POSITION in seen["busy"] and "already on its way" in seen["busy"][POSITION]


# -- the strategy module -------------------------------------------------------------------------------


@pytest.fixture
def dispatch(monkeypatch):
    # The import order the dispatch tests use: restx_api first, or place_order_service's import
    # cycle fails with a partially initialised module.
    import restx_api  # noqa: F401
    import services.place_order_service as place_order_service
    from services.strategy_module import order_dispatch as od

    sent = []

    def place_order_with_auth(order, auth_token, broker, original, force_live=False):
        sent.append((order["symbol"], order["action"], order["quantity"], force_live))
        return True, {"status": "success", "orderid": f"2610270000{len(sent):02d}"}, 200

    monkeypatch.setattr(od, "resolve_live_auth", lambda api_key: ("tok", "upstox", None))
    monkeypatch.setattr(place_order_service, "place_order_with_auth", place_order_with_auth)
    monkeypatch.setattr(surface_exits, "upstox_statuses", lambda auth, broker: {})
    od.sent = sent
    yield od
    del od.sent


def _exit_order():
    return {"symbol": X, "exchange": "NFO", "action": "SELL", "quantity": "65", "product": "MIS",
            "pricetype": "MARKET", "price": "0", "trigger_price": "0", "strategy": "S"}  # fmt: skip


def test_a_strategy_exit_claims_its_position_keeps_force_live_and_notes_the_hold(dispatch):
    result = dispatch.dispatch_order(mode="live", api_key="k", order=_exit_order(), closes=True)
    assert result.ok and result.broker_order_id == "261027000001"
    assert dispatch.sent == [(X, "SELL", "65", True)]
    assert exit_guard.EXITS.placed_lately("live") == {POSITION}


def test_a_strategy_exit_is_a_refused_dispatch_while_another_exit_is_on_its_way(dispatch):
    with exit_guard.one_exit("live", "NFO", X, "MIS"):
        result = dispatch.dispatch_order(mode="live", api_key="k", order=_exit_order(), closes=True)
    assert result.ok is False and dispatch.sent == []
    assert "already on its way" in result.error
    assert result.broker_order_id is None


def test_a_held_strategy_exit_says_so_and_a_broker_refusal_does_not(dispatch, monkeypatch):
    """SUDARSHAN M6.9 (words): the engine records a hold as a hold, not as a rejection, so the
    dispatch result marks one; a refusal from the broker or the pipe is not marked."""
    import services.place_order_service as place_order_service

    with exit_guard.one_exit("live", "NFO", X, "MIS"):
        result = dispatch.dispatch_order(mode="live", api_key="k", order=_exit_order(), closes=True)
    assert result.ok is False and result.held is True
    dispatch.dispatch_order(mode="live", api_key="k", order=_exit_order(), closes=True)
    monkeypatch.setattr(surface_exits, "upstox_statuses", lambda a, b: {"261027000001": "open"})
    result = dispatch.dispatch_order(mode="live", api_key="k", order=_exit_order(), closes=True)
    assert result.ok is False and result.held is True and "still working" in result.error
    monkeypatch.setattr(
        place_order_service,
        "place_order_with_auth",
        lambda *a, **k: (False, {"status": "error", "message": "Upstox refused it"}, 400),
    )
    monkeypatch.setattr(surface_exits, "upstox_statuses", lambda a, b: {"261027000001": "complete"})
    result = dispatch.dispatch_order(mode="live", api_key="k", order=_exit_order(), closes=True)
    assert result.ok is False and result.held is False and result.error == "Upstox refused it"


def test_a_strategy_exit_is_refused_while_exit_all_holds_the_book(dispatch):
    with exit_guard.EXITS.every("live"):
        result = dispatch.dispatch_order(mode="live", api_key="k", order=_exit_order(), closes=True)
    assert result.ok is False and result.error == exit_guard.EXIT_ALL_BEING_PLACED
    assert dispatch.sent == []


def test_a_strategy_exit_is_refused_while_its_earlier_exit_still_works(dispatch, monkeypatch):
    dispatch.dispatch_order(mode="live", api_key="k", order=_exit_order(), closes=True)
    monkeypatch.setattr(surface_exits, "upstox_statuses", lambda a, b: {"261027000001": "open"})
    result = dispatch.dispatch_order(mode="live", api_key="k", order=_exit_order(), closes=True)
    assert result.ok is False and "still working at Upstox" in result.error
    assert len(dispatch.sent) == 1


def test_a_strategy_entry_claims_nothing(dispatch):
    """Control: an entry goes while Exit all holds the book, and notes no hold."""
    with exit_guard.EXITS.every("live"):
        result = dispatch.dispatch_order(mode="live", api_key="k", order=_exit_order())
    assert result.ok and dispatch.sent == [(X, "SELL", "65", True)]
    assert exit_guard.EXITS.placed_lately("live") == set()


def test_a_sandbox_strategy_exit_is_not_guarded(dispatch, monkeypatch):
    """Control: the run's own mode decides, as before; the sandbox book shows the exit at once."""
    import services.sandbox_service as sandbox_service

    monkeypatch.setattr(
        sandbox_service,
        "sandbox_place_order",
        lambda order, api_key, original: (True, {"status": "success", "orderid": "SB1"}, 200),
    )
    with exit_guard.EXITS.every("live"):
        result = dispatch.dispatch_order(
            mode="sandbox", api_key="k", order=_exit_order(), closes=True
        )
    assert result.ok and result.broker_order_id == "SB1"
    assert exit_guard.EXITS.placed_lately("live") == set()


def test_the_engine_and_the_signal_path_mark_their_exits():
    """The two exit dispatches pass closes=True (the signal path: closes=exiting), the entry
    dispatch does not."""
    import inspect

    from services.strategy_module import engine, signals

    exits = inspect.getsource(engine._exit_legs)
    assert "closes=True" in exits
    entries = inspect.getsource(engine)
    assert entries.count("dispatch_order(") == 2  # one entry, one exit
    assert "closes=exiting" in inspect.getsource(signals)
