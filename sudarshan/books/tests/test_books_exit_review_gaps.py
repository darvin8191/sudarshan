"""The exit-gaps review's findings (S1, R1, R2 and the LOW items), over the simulated Upstox (M6.9).

The review of fix/exit-gaps (376dc58..b14cb0e) found the scalping terminal's hold check never
reading the order book (S1), a repeated reversal after a part fill going beside the first (R1),
and an Exit all with an unreadable order book and only flat rows answering success (R2), with
smaller findings on a conversion whose new row is not shown yet (V1), Exit all over a part-filled
reversal (E3), a listed sale from holdings (L1) and the words a refused reversal, the scalping
Close all and the strategy module use (LOW 5).

Each test here fails on b14cb0e unless it says it is a control.
"""

from __future__ import annotations

import json
import time

import pytest
from books_upstox_world import ORDERS_URL, Broker, FakeResponse, install

from sudarshan.books import exit_guard

X = "NIFTY27OCT2625900CE"  # MIS, NFO
Y = "BANKNIFTY27OCT2657500PE"  # NRML, NFO
TOKEN_X = "NSE_FO|1001"


def _sent(broker, symbol):
    return [(o["side"], o["qty"]) for o in broker.orders if o["symbol"] == symbol]


def _fresh_book():
    import broker.upstox.api.order_api as upstox

    upstox._POSITION_BOOK.invalidate("token")


def _bought_not_listed(world, symbol, quantity):
    world.broker.fills.append((time.monotonic(), symbol, quantity))


def _reversal(world, symbol, quantity, action, size):
    return world.tv_smart_exit(symbol, quantity=quantity, action=action, position_size=size)


# -- S1. The scalping terminal's hold check reads the order book with the order's session ---------


@pytest.fixture
def scalping(monkeypatch):
    """The scalping close as production calls it: auth_token and broker None, the api key alone.
    Nothing here patches surface_exits.upstox_statuses; the order book is Upstox's own, as the
    patched get_order_book answers it, and the broker session is resolved from the api key."""
    import restx_api  # noqa: F401, I001  (first, or place_order_service's import cycle fails)

    import blueprints.scalping as scalping
    import database.auth_db as auth_db

    sent = []

    def place_order(order_data, api_key, auth_token, broker):
        sent.append((order_data["symbol"], order_data["action"], int(order_data["quantity"])))
        return True, {"status": "success", "orderid": f"2610270000{len(sent) + 1:02d}"}, 200

    monkeypatch.setattr("services.place_order_service.place_order", place_order)
    monkeypatch.setattr(scalping, "_is_derivative", lambda exchange: False)  # no symbol master
    monkeypatch.setattr(scalping, "_current_mode", lambda: "live")
    monkeypatch.setattr(
        auth_db,
        "get_auth_token_broker",
        lambda api_key: ("tok", "upstox") if api_key else (None, None),
    )
    scalping.sent = sent
    yield scalping
    del scalping.sent


def _book(monkeypatch, status, filled, pending):
    """Upstox's order book, were it read: the earlier exit (order 261027000001) in ``status``."""
    import broker.upstox.api.order_api as upstox

    monkeypatch.setattr(
        upstox,
        "get_order_book",
        lambda auth: {"status": "success", "data": [
            {"order_id": "261027000001", "status": status, "instrument_token": TOKEN_X,
             "exchange": "NFO", "product": "I", "transaction_type": "SELL", "order_type": "MARKET",
             "quantity": 65, "filled_quantity": filled, "pending_quantity": pending}]},
    )  # fmt: skip


def _production_close(scalping, quantity=65):
    return scalping._reducing_exit(X, "NFO", "MIS", "SELL", quantity, None, None, "key")


def test_s1_a_scalping_close_after_an_earlier_exit_that_completed_goes(scalping, monkeypatch):
    """An earlier exit of the same position (the app's Exit, complete at Upstox) was noted a
    minute ago. The trader closes the re-entered position from the terminal: it must go, with the
    order book read through the session resolved from the api key. On b14cb0e nothing was read
    and the close was refused for 150 s."""
    _book(monkeypatch, "complete", 65, 0)
    exit_guard.exit_placed("live", "NFO", X, "MIS", ("261027000001",))
    ok, body, status = _production_close(scalping)
    assert (ok, status) == (True, 200), body
    assert scalping.sent == [(X, "SELL", 65)]


def test_s1b_the_scalping_stop_fires_after_an_earlier_exit_that_completed(scalping, monkeypatch):
    """The trailing-stop engine's own exit (services/scalping_risk_monitor_service.py), whose
    _resolve_auth answers (api_key, None, None): the stop must go, not wait out 150 s."""
    from services.scalping_risk_monitor_service import ScalpingRiskMonitor

    _book(monkeypatch, "complete", 65, 0)
    monitor = ScalpingRiskMonitor()
    monkeypatch.setattr(monitor, "_mode", lambda: "live")
    monkeypatch.setattr(monitor, "_resolve_auth", lambda: ("key", None, None))  # as it answers
    monkeypatch.setattr(monitor, "_live_net_qty", lambda *a: 65)
    cleared = []
    monkeypatch.setattr(monitor, "_clear_state", lambda key, *a, **k: cleared.append(key))
    exit_guard.exit_placed("live", "NFO", X, "MIS", ("261027000001",))
    state = {"symbol": X, "exchange": "NFO", "product": "MIS", "mode": "live"}
    monitor._exit_worker(f"NFO:{X}:MIS", state, "sl", 90.0)
    assert scalping.sent == [(X, "SELL", 65)], "the stop's exit was refused"
    assert cleared == [f"NFO:{X}:MIS"]


def test_s1c_a_scalping_close_never_goes_beside_an_earlier_exit_still_working(
    scalping, monkeypatch
):
    """The earlier exit still rests at Upstox and its answer is older than the unconfirmed time
    (set to 0 here for 150 s). Read with the resolved session, the book shows it working and the
    close is refused, naming it. On b14cb0e the hold was dropped unread and SELL 65 went beside
    the working SELL 65: a reversal when both fill."""
    _book(monkeypatch, "open", 0, 65)
    monkeypatch.setattr(exit_guard, "EXIT_HOLD_UNCONFIRMED_S", 0.0)
    exit_guard.exit_placed("live", "NFO", X, "MIS", ("261027000001",))
    time.sleep(0.01)
    ok, body, status = _production_close(scalping)
    assert (ok, status) == (False, 409) and scalping.sent == [], body
    assert "still working at Upstox" in body["message"] and "order ending 0001" in body["message"]


def test_s1c_control_with_the_broker_auth_the_working_exit_is_seen(scalping, monkeypatch):
    """Control: given the broker's session outright, the same close reads the book and is
    refused the same way (this passed on b14cb0e)."""
    _book(monkeypatch, "open", 0, 65)
    monkeypatch.setattr(exit_guard, "EXIT_HOLD_UNCONFIRMED_S", 0.0)
    exit_guard.exit_placed("live", "NFO", X, "MIS", ("261027000001",))
    time.sleep(0.01)
    ok, body, status = scalping._reducing_exit(X, "NFO", "MIS", "SELL", 65, "tok", "upstox", "key")
    assert (ok, status) == (False, 409) and scalping.sent == []
    assert "still working at Upstox" in body["message"]


def test_s1_the_session_is_resolved_only_while_a_hold_is_open(scalping, monkeypatch):
    """A close with no hold open costs no session lookup and no order book read."""
    import database.auth_db as auth_db

    looked = []
    monkeypatch.setattr(
        auth_db, "get_auth_token_broker", lambda key: looked.append(key) or ("t", "upstox")
    )
    ok, _body, _status = _production_close(scalping)
    assert ok and looked == []


def test_s1_when_the_book_truly_cannot_be_read_the_unconfirmed_rule_stays(scalping, monkeypatch):
    """Control: the session is resolved and the order book read fails. The hold counts as not
    confirmed, and the close is refused with that sentence, as the rule says."""
    import broker.upstox.api.order_api as upstox

    monkeypatch.setattr(upstox, "get_order_book", lambda auth: {"status": "error"})
    exit_guard.exit_placed("live", "NFO", X, "MIS", ("261027000001",))
    ok, body, status = _production_close(scalping)
    assert (ok, status) == (False, 409) and scalping.sent == []
    assert (
        "not confirmed" in body["message"] and "could not be checked at Upstox" in body["message"]
    )


def test_s1_a_session_that_cannot_be_resolved_reads_as_not_read(scalping, monkeypatch):
    import database.auth_db as auth_db

    monkeypatch.setattr(auth_db, "get_auth_token_broker", lambda key: (None, None))
    _book(monkeypatch, "complete", 65, 0)
    exit_guard.exit_placed("live", "NFO", X, "MIS", ("261027000001",))
    ok, body, status = _production_close(scalping)
    assert (ok, status) == (False, 409) and scalping.sent == []
    assert "not confirmed" in body["message"]


# -- R1. A repeated reversal after the first one part-filled ---------------------------------------


def test_r1_a_repeated_reversal_after_the_first_part_filled_is_not_sent_beside_it(monkeypatch):
    """Long 65. TradingView reverses: SELL 130 to -65. Its market order fills 65 and the other 65
    rests at its protected price. The same alert again a second later: the corrected figure is 0,
    so on b14cb0e the order was judged an add, neither check ran, and SELL 65 went beside the
    resting 65. When the rest filled the account was short 130 for a -65 target."""
    broker = Broker({X: 65}, 60.0)
    broker.part[X] = 65  # the first order fills 65 of 130; 65 stays working
    world = install(monkeypatch, broker)
    code, body = _reversal(world, X, "130", "SELL", "-65")
    assert code == 200, body
    broker.part.pop(X)  # any later order fills whole
    _fresh_book()
    code2, body2 = _reversal(world, X, "130", "SELL", "-65")
    assert code2 == 409, (code2, body2)
    broker.fill_working()  # the market comes back: the resting 65 fills
    assert _sent(broker, X) == [("SELL", 130)]
    assert broker.true_qty(X) == -65


def test_r1b_a_repeated_reversal_after_the_first_filled_past_flat_is_not_sent_beside_it(
    monkeypatch,
):
    """The same with 100 of 130 filled: the corrected figure is -35, the target -65 read as an
    add of 30, and SELL 30 went beside the resting 30: -95."""
    broker = Broker({X: 65}, 60.0)
    broker.part[X] = 100
    world = install(monkeypatch, broker)
    assert _reversal(world, X, "130", "SELL", "-65")[0] == 200
    broker.part.pop(X)
    _fresh_book()
    code2, body2 = _reversal(world, X, "130", "SELL", "-65")
    assert code2 == 409, (code2, body2)
    broker.fill_working()
    assert _sent(broker, X) == [("SELL", 130)]
    assert broker.true_qty(X) == -65


def test_r1_control_a_size_0_exit_after_the_part_filled_reversal_is_held(monkeypatch):
    """Control: a size-0 exit asks the hold whatever the corrected figure says, and is refused."""
    broker = Broker({X: 65}, 60.0)
    broker.part[X] = 65
    world = install(monkeypatch, broker)
    assert _reversal(world, X, "130", "SELL", "-65")[0] == 200
    broker.part.pop(X)
    _fresh_book()
    code, body = world.tv_smart_exit(X, quantity="65", action="SELL", position_size="0")
    assert code == 409, body
    assert _sent(broker, X) == [("SELL", 130)]


def test_r1_control_an_order_after_the_first_reversal_completed_still_goes(monkeypatch):
    """Control: the hold ends when the order book shows the first reversal complete, so an alert
    further the same way, sized from the corrected figure, goes (the row still says long 65)."""
    broker = Broker({X: 65}, 60.0)
    world = install(monkeypatch, broker)
    assert _reversal(world, X, "130", "SELL", "-65")[0] == 200
    _fresh_book()
    code, body = _reversal(world, X, "195", "SELL", "-130")
    assert code == 200, body
    assert _sent(broker, X) == [("SELL", 130), ("SELL", 65)]
    assert broker.true_qty(X) == -130
    assert exit_guard.EXITS.placed_lately("live") == {("NFO", X, "MIS")}  # noted, as a reduction is


def test_r1_a_repeated_reversal_is_refused_even_when_the_book_does_not_list_the_first_yet(
    monkeypatch,
):
    """Control: the first fills 65 of 130 and the order book has not listed it yet (5 s lag). The
    repeat's corrected figure is then the row's 65 (no fill seen), the order crosses, and the hold
    refuses it as not confirmed; nothing goes beside the first."""
    broker = Broker({X: 65}, 60.0)
    broker.part[X] = 65
    broker.order_book_lag = 5.0
    world = install(monkeypatch, broker)
    assert _reversal(world, X, "130", "SELL", "-65")[0] == 200
    broker.part.pop(X)
    _fresh_book()
    code, body = _reversal(world, X, "130", "SELL", "-65")
    assert code == 409 and "not confirmed" in body["message"]
    assert _sent(broker, X) == [("SELL", 130)]


def test_r1_after_a_restart_the_working_rest_of_the_first_reversal_still_refuses_the_repeat(
    monkeypatch,
):
    """The holds are per process. After a restart the repeat has only the order book to go by: the
    first's remaining 65 rests as a MARKET order on the side that closes what the row shows (SELL,
    the row still says long 65), and that refuses the repeat even though the corrected figure is 0.
    Looked for on the corrected figure's side (BUY), it would not be found, and SELL 65 would go."""
    broker = Broker({X: 65}, 60.0)
    broker.part[X] = 65
    world = install(monkeypatch, broker)
    assert _reversal(world, X, "130", "SELL", "-65")[0] == 200
    broker.part.pop(X)
    monkeypatch.setattr(exit_guard, "EXITS", exit_guard.ExitsInFlight())  # the restart
    _fresh_book()
    code, body = _reversal(world, X, "130", "SELL", "-65")
    assert code == 409 and "SELL 65 MARKET order" in body["message"], body
    broker.fill_working()
    assert _sent(broker, X) == [("SELL", 130)] and broker.true_qty(X) == -65


# -- R2. Exit all with an unreadable order book and only flat rows ---------------------------------


def test_r2_exit_all_with_an_unreadable_order_book_and_only_flat_rows_never_answers_success(
    monkeypatch,
):
    """The positions book lists only a closed position (X, 0). Y was bought a second ago and is not
    listed yet. The order book cannot be read, so nothing can say whether anything is held that
    the rows do not show. On b14cb0e every flat row was skipped as nothing held and Exit all
    answered "All Open Positions SquaredOff" over the 30 held."""
    broker = Broker({X: 0}, 0.0)
    broker.orders_unreadable = True
    world = install(monkeypatch, broker)
    _bought_not_listed(world, Y, 30)
    code, body = world.app_exit_all()
    assert broker.true_qty(Y) == 30 and broker.orders == []
    assert code == 424 and body["status"] == "error", (code, body)
    assert "position opened in the last minute may not show yet" in body["message"]


def test_r2b_exit_all_with_an_unreadable_order_book_and_a_lagging_flat_row_never_answers_success(
    monkeypatch,
):
    """X was closed earlier (row 0) and bought again a second ago; the row still shows 0. The
    order book cannot be read. Exit all must not answer success."""
    broker = Broker({X: 0}, 60.0)
    broker.orders_unreadable = True
    world = install(monkeypatch, broker)
    _bought_not_listed(world, X, 65)
    code, body = world.app_exit_all()
    assert broker.true_qty(X) == 65
    assert code == 424 and body["status"] == "error", (code, body)


def test_r2_control_a_short_row_with_the_order_book_unread_is_not_sized_and_not_success(
    monkeypatch,
):
    """Control: a row that shows a quantity (a short, -30) with the order book unread is left out
    as not sized, with its sentence, as before; the flat-rows answer is not given over it."""
    broker = Broker({Y: -30}, 0.0)
    broker.orders_unreadable = True
    world = install(monkeypatch, broker)
    code, body = world.app_exit_all()
    assert code != 200 and body["status"] == "error" and broker.orders == []
    assert "Upstox did not send your orders in time" in body["message"]
    assert "position opened in the last minute" not in body["message"]


# -- V1. A conversion whose new row is not shown yet -----------------------------------------------


class RawBroker(Broker):
    """Fixed raw positions and order rows; records each order's side, product and quantity."""

    def __init__(self, positions, orders):
        super().__init__({}, 0.0)
        self.raw_positions = positions
        self.raw_orders = orders
        self.products = []

    def get(self, url, headers=None, **kwargs):
        if url.endswith(ORDERS_URL):
            return FakeResponse(200, {"status": "success", "data": list(self.raw_orders)})
        return FakeResponse(200, {"status": "success", "data": list(self.raw_positions)})

    def post(self, url, headers=None, content=None, **kwargs):
        payload = json.loads(content)
        self.products.append(
            (payload["transaction_type"], payload["product"], int(payload["quantity"]))
        )
        return super().post(url, headers=headers, content=content, **kwargs)


def _raw_row(token, exchange, name, product, quantity, overnight, bought, sold):
    return {"instrument_token": token, "exchange": exchange, "tradingsymbol": name,
            "product": product, "quantity": quantity, "overnight_quantity": overnight,
            "day_buy_quantity": bought, "day_sell_quantity": sold,
            "average_price": 100.0, "last_price": 101.0}  # fmt: skip


def _raw_order(order_id, token, exchange, name, product, side, quantity, status="complete"):
    filled = quantity if status == "complete" else 0
    return {"order_id": order_id, "instrument_token": token, "exchange": exchange,
            "tradingsymbol": name, "product": product, "transaction_type": side,
            "order_type": "MARKET", "status": status, "quantity": quantity,
            "filled_quantity": filled, "pending_quantity": quantity - filled,
            "order_timestamp": "2026-10-27 11:00:00"}  # fmt: skip


def test_v1_a_converted_part_sold_today_whose_row_is_not_shown_is_not_bought_back(monkeypatch):
    """NRML carried 130. In Upstox's app 65 were converted to MIS (no order), and that MIS 65 was
    sold today (an I order, complete). The positions book shows the D row (65) and no I row yet.
    The account holds +65 NRML and nothing in MIS. On b14cb0e Exit all sent SELL 65 NRML plus
    BUY 65 MIS and answered success, leaving the account long 65 MIS. Now the MIS is named, not
    sent, and the answer is not success."""
    positions = [_raw_row(TOKEN_X, "NFO", X, "D", 65, 65, 0, 0)]
    orders = [_raw_order("261027000070", TOKEN_X, "NFO", X, "I", "SELL", 65)]
    broker = RawBroker(positions, orders)
    world = install(monkeypatch, broker)
    code, body = world.app_exit_all()
    assert ("BUY", "I", 65) not in broker.products, (code, body, broker.products)
    assert broker.products == [("SELL", "D", 65)]
    assert code != 200 and body["status"] == "error"
    assert "traded today as MIS" in body["message"] and "only as NRML" in body["message"]
    assert "may have been converted" in body["message"]
    named = [p for p in body["positions"] if p["product"] == "MIS"]
    assert named and named[0]["outcome"] == "not_sent"


def test_v1_a_fresh_position_in_another_product_of_a_listed_instrument_is_named_not_sent(
    monkeypatch,
):
    """The same shape from the other side: NRML carried 65 (listed) and 65 bought as MIS a second
    ago, not listed yet. It cannot be told from a conversion, so the MIS is named and not sent;
    the NRML is exited. On b14cb0e both were sent."""
    positions = [_raw_row(TOKEN_X, "NFO", X, "D", 65, 65, 0, 0)]
    orders = [_raw_order("261027000071", TOKEN_X, "NFO", X, "I", "BUY", 65)]
    broker = RawBroker(positions, orders)
    world = install(monkeypatch, broker)
    code, body = world.app_exit_all()
    assert broker.products == [("SELL", "D", 65)], (code, body, broker.products)
    assert code != 200 and "traded today as MIS" in body["message"]


def test_v1_control_an_unlisted_instrument_with_no_row_at_all_is_still_exited(monkeypatch):
    """Control: a position with no row in any product is exited from its fills, as item 1 says."""
    world = install(monkeypatch, Broker({X: 65}, 0.0))
    _bought_not_listed(world, Y, 30)
    code, body = world.app_exit_all()
    assert (code, body["status"]) == (200, "success"), body
    assert _sent(world.broker, Y) == [("SELL", 30)]


def test_v1_the_sentence_is_for_a_trader():
    said = exit_guard.unlisted_in_another_product(("NFO", X, "MIS"), ["NRML"])
    assert said.startswith("NIFTY 25900 CE was traded today as MIS")
    assert "Upstox app" in said and "Positions" in said
    for word in ("None", "token", "HTTP", "424", "409", "order book", "row", "arm"):
        assert word not in said


# -- E3. Exit all over a part-filled reversal whose rest still works -------------------------------


def test_e3_exit_all_over_a_part_filled_reversal_does_not_say_all_squared_off(monkeypatch):
    """Long 65; the reversal SELL 130 fills 65 and 65 rests (its hold is noted). Exit all reads the
    corrected figure as flat. On b14cb0e it passed the row over and answered success, while the
    resting 65 was about to open a short. Now the hold is asked first, and the working order is
    named."""
    broker = Broker({X: 65}, 60.0)
    broker.part[X] = 65
    world = install(monkeypatch, broker)
    assert _reversal(world, X, "130", "SELL", "-65")[0] == 200
    broker.part.pop(X)
    _fresh_book()
    code, body = world.app_exit_all()
    assert code != 200 and body["status"] == "error", (code, body)
    assert "still working at Upstox" in body["message"] and "order ending 0001" in body["message"]
    assert _sent(broker, X) == [("SELL", 130)]
    flat = [p for p in body["positions"] if p["symbol"] == X]
    assert flat and flat[0]["outcome"] == "not_sent"


def test_e3_a_flat_row_whose_earlier_exit_is_complete_is_passed_over_and_its_hold_ends(monkeypatch):
    """The hold of a completed exit ends on Exit all's read, and the flat row is nothing to close;
    Exit all answers success with nothing more sent, as before (on b14cb0e the hold lingered,
    unasked, until the next single exit)."""
    world = install(monkeypatch, Broker({X: 65}, 0.0))
    assert world.phone_exit(X)[0] == 200
    _fresh_book()
    code, body = world.app_exit_all()
    assert (code, body["status"]) == (200, "success"), body
    assert _sent(world.broker, X) == [("SELL", 65)]
    assert exit_guard.EXITS.placed_lately("live") == set()  # the hold ended on that read


def test_e3_a_row_at_0_whose_own_exit_still_works_is_said_too(monkeypatch):
    """The app's Exit rests at Upstox (nothing filled); the owner then sells in Upstox's own app
    and that fills. The row shows 0 and the corrected figure is 0, and SUDARSHAN's SELL still
    works: it can open a short, so Exit all says so instead of answering success."""
    world = install(monkeypatch, Broker({X: 65}, 0.0))
    world.broker.part[X] = 0
    assert world.phone_exit(X)[0] == 200
    world.broker.fills.append((time.monotonic(), X, -65))  # sold in Upstox's app
    _fresh_book()
    code, body = world.app_exit_all()
    assert code != 200 and "still working at Upstox" in body["message"], (code, body)
    assert _sent(world.broker, X) == [("SELL", 65)]


def test_e3_control_a_flat_row_of_an_unknown_contract_is_passed_over(monkeypatch):
    """Control: a closed position whose token the symbol master does not know is nothing to
    close, not a contract Exit all could not send for."""
    import broker.upstox.api.order_api as upstox

    world = install(monkeypatch, Broker({X: 0, Y: -30}, 0.0))
    monkeypatch.setattr(
        upstox, "get_symbol", lambda token, exchange: Y if "1002" in token else None
    )
    code, body = world.app_exit_all()
    assert (code, body["status"]) == (200, "success"), body
    assert _sent(world.broker, Y) == [("BUY", 30)]


# -- L1. A sale from holdings that Upstox's positions do list --------------------------------------


@pytest.fixture
def cnc(monkeypatch):
    from books_upstox_world import BY_TOKEN
    from books_upstox_world import INSTRUMENTS as world_instruments

    token = "NSE_EQ|INE002A01018"
    monkeypatch.setitem(world_instruments, "RELIANCE", (token, "NSE", "D", "CNC"))
    monkeypatch.setitem(BY_TOKEN, token, "RELIANCE")
    return token


def test_l1_a_listed_cnc_sale_from_holdings_is_not_bought_back(monkeypatch, cnc):
    """Upstox lists a sale from holdings as a D row with a negative quantity on NSE. The unlisted
    path leaves such a sale alone; on b14cb0e the listed path bought the shares back."""
    positions = [_raw_row(cnc, "NSE", "RELIANCE", "D", -10, 0, 0, 10)]
    orders = [_raw_order("261027000080", cnc, "NSE", "RELIANCE", "D", "SELL", 10)]
    broker = RawBroker(positions, orders)
    world = install(monkeypatch, broker)
    code, body = world.app_exit_all()
    assert ("BUY", "D", 10) not in broker.products, (code, body, broker.products)
    assert broker.products == [] and (code, body["status"]) == (200, "success")
    assert "RELIANCE" not in str(body.get("message"))


def test_l1_control_an_fno_nrml_short_is_still_closed(monkeypatch):
    """Control: Upstox writes D for NRML too. A BANKNIFTY PE short carried as NRML is bought
    back by Exit all, as every position is."""
    token = "NSE_FO|1002"
    positions = [_raw_row(token, "NFO", Y, "D", -30, -30, 0, 0)]
    broker = RawBroker(positions, [])
    world = install(monkeypatch, broker)
    code, body = world.app_exit_all()
    assert (code, body["status"]) == (200, "success"), body
    assert broker.products == [("BUY", "D", 30)]


def test_l1_control_a_listed_cnc_purchase_is_sold_back(monkeypatch, cnc):
    """Control: a CNC net purchase today is a position, and is sold."""
    positions = [_raw_row(cnc, "NSE", "RELIANCE", "D", 10, 0, 10, 0)]
    orders = [_raw_order("261027000081", cnc, "NSE", "RELIANCE", "D", "BUY", 10)]
    broker = RawBroker(positions, orders)
    world = install(monkeypatch, broker)
    code, body = world.app_exit_all()
    assert (code, body["status"]) == (200, "success"), body
    assert broker.products == [("SELL", "D", 10)]
