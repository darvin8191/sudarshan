"""Review of fix/guard-on, MEDIUM-2: the gate judges an exit on the positions corrected by the
day's fills, a position Upstox's positions book does not list yet included.

A position bought a moment ago is in Upstox's order book before its positions book lists it. The
gate took p from the positions rows alone, so such a position read as flat, its exit was judged an
opening order and refused under the kill switch, and Exit all's outcome then read "Upstox refused
its exit ... Exits and cancels still go": Upstox had refused nothing, and the exit it was about
had just been refused. Now the fills add a row for each instrument and product held with no
positions row (sudarshan.books.fills.unlisted_nets), per instrument across products, as exit
sizing does; and a refusal by the gate is given in its own sentence.
"""

from __future__ import annotations

import test_guard_gate as gate_tests
from test_guard_gate import CE, ON, PE, TOKENS, broker, order, refused_message, row

from broker.upstox.api import order_api
from sudarshan.guard import gate

PE_TOKEN = TOKENS[(PE, "NFO")]

# the fake books and the gate over them, from test_guard_gate.py
book, the_gate = gate_tests.book, gate_tests.the_gate


def _fill(oid: str, token: str, side: str, quantity: int, product: str = "I") -> dict:
    return {
        "order_id": oid,
        "exchange_order_id": f"1100000000{oid[-2:]}",
        "instrument_token": token,
        "tradingsymbol": "X",
        "exchange": "NFO",
        "product": product,
        "transaction_type": side,
        "order_type": "MARKET",
        "quantity": quantity,
        "filled_quantity": quantity,
        "pending_quantity": 0,
        "status": "complete",
        "average_price": 100.0,
        "order_timestamp": "2026-11-09 10:00:00",
    }


def test_m2_the_exit_of_a_position_the_rows_do_not_list_yet_goes_under_the_switch(the_gate, book):
    """Long 65 CE listed; 30 PE bought a moment ago, in the order book and not in the positions.
    Under the kill switch the PE exit (SELL 30) is an exit and goes; on 476fb31 it read as an
    opening order and was refused."""
    the_gate.set_state(ON)
    book.rows = [row(CE, 65)]
    book.order_rows = [_fill("1", TOKENS[(CE, "NFO")], "BUY", 65), _fill("2", PE_TOKEN, "BUY", 30)]
    upstox = broker()
    assert upstox.place(order("SELL", 30, symbol=PE), "token")[2] == "OID0"


def test_m2_an_order_that_opens_in_an_unlisted_instrument_is_still_refused(the_gate, book):
    """The control: nothing was bought in PE today, so BUY 30 PE opens a position: refused."""
    the_gate.set_state(ON)
    book.rows = [row(CE, 65)]
    book.order_rows = [_fill("1", TOKENS[(CE, "NFO")], "BUY", 65)]
    upstox = broker()
    assert "refused" in refused_message(upstox.place(order("BUY", 30, symbol=PE), "token"))


def test_m2_an_unlisted_short_is_covered_not_opened(the_gate, book):
    the_gate.set_state(ON)
    book.rows = []
    book.order_rows = [_fill("1", PE_TOKEN, "SELL", 30)]
    upstox = broker()
    # the exit is sized by the fills: 60 would cross into a long, and only 30 is let through
    res, body, ref = upstox.place(order("BUY", 60, symbol=PE), "token")
    assert ref == "OID0" and body["risk_note"].startswith("Only the exit was sent: buy 30")
    assert [d["quantity"] for _, d in upstox.reached] == ["30"]


def test_m2_an_unlisted_position_in_another_product_is_not_this_product_s(the_gate, book):
    """The fills show 30 PE bought as NRML (D); a SELL 30 PE as MIS is not its exit (the rows
    are judged per product, as exit sizing is): refused under the switch."""
    the_gate.set_state(ON)
    book.rows = []
    book.order_rows = [_fill("1", PE_TOKEN, "BUY", 30, product="D")]
    upstox = broker()
    assert "refused" in refused_message(upstox.place(order("SELL", 30, symbol=PE), "token"))
    assert upstox.place(order("SELL", 30, symbol=PE, product="NRML"), "token")[2] == "OID0"


def test_m2_the_added_row_carries_no_money_so_the_day_figure_ignores_it(the_gate, book):
    from sudarshan.guard import daily

    book.order_rows = [_fill("1", PE_TOKEN, "BUY", 30)]
    rows = the_gate._corrected("token", [])
    assert rows == [
        {
            "instrument_token": PE_TOKEN,
            "product": "I",
            "quantity": 30,
            "exchange": "NFO",
            "tradingsymbol": "X",
            "unlisted": True,
        }
    ]
    assert daily.live_figure(rows) == daily.DayFigure(0.0, 0.0, False)


def test_m2_a_refusal_by_the_gate_is_never_called_upstox_s():
    """Exit all's outcome for an exit the risk gate refused carries the gate's own sentence."""
    held = ("NFO", PE, "MIS")
    said = "New orders are refused: the kill switch is on since 10:00."
    outcome = order_api._exit_outcome(
        held, "refused", gate.RefusedResponse(), {"status": "error", "message": said}, None
    )
    assert outcome["outcome"] == "not_sent"
    assert outcome["reason"] == said
    assert "Upstox refused" not in outcome["reason"]


def test_m2_a_refusal_by_upstox_still_says_so():
    held = ("NFO", PE, "MIS")

    class Refused:
        status = 400
        status_code = 400

    outcome = order_api._exit_outcome(
        held, "refused", Refused(), {"status": "error", "message": "UDAPI1021: Market closed"}, None
    )
    assert outcome["reason"] == "Upstox refused its exit: Market closed"
