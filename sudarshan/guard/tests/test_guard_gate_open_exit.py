# ruff: noqa: F811 - pytest fixtures are imported, then named as arguments
"""Release review A, H1: one exit at a time per position under the switch.

A market exit that Upstox accepted but has not filled yet (in flight, or resting beyond market
price protection) is still on its way to the book. Its claim stays until the book shows the
order final, so a second exit of the same position is refused, not sent beside it.
"""

from __future__ import annotations

from test_guard_gate import CE, ON, broker, order, refused_message, row
from test_guard_gate_claims import claims  # noqa: F401 - fixture


def test_a_second_market_exit_is_refused_while_the_first_is_still_working(claims):
    claims.book.rows = [row(CE, 65)]
    claims.gate.set_state(ON)
    upstox = broker()
    assert upstox.place(order("SELL", 65), "token")[2] == "OID0"
    claims.book.statuses = {"OID0": "open"}  # accepted, not filled
    refused_message(upstox.place(order("SELL", 65), "token"))
    assert claims.book.calls.count("statuses") == 1


def test_the_second_exit_goes_once_the_first_is_final(claims):
    claims.book.rows = [row(CE, 65)]
    claims.gate.set_state(ON)
    upstox = broker()
    assert upstox.place(order("SELL", 65), "token")[2] == "OID0"
    claims.book.statuses = {"OID0": "cancelled"}  # it never filled: nothing is on its way
    assert upstox.place(order("SELL", 65), "token")[2] == "OID1"


def test_a_resting_stop_does_not_hold_the_exit_back(claims):
    claims.book.rows = [row(CE, 65)]
    claims.gate.set_state(ON)
    upstox = broker()
    stop = order("SELL", 65, pricetype="SL-M", trigger_price="98.5")
    assert upstox.place(stop, "token")[2] == "OID0"
    claims.book.statuses = {"OID0": "open"}
    assert upstox.place(order("SELL", 65), "token")[2] == "OID1"
