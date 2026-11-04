# ruff: noqa: F811 - pytest fixtures are imported, then named as arguments
"""Release review A, L3: an exit nobody judged is not withdrawn at the pacer.

With every limit off the gate lets an order through without judging it. If the switch goes on
while it waits for its turn at the pacer, it is judged against the last positions read: an exit
goes (decision c, a lone exit always goes); an order that adds risk is withdrawn, as is one
with no book to be judged by.
"""

from __future__ import annotations

import time

from test_guard_gate import CE, ON, row
from test_guard_gate_claims import claims  # noqa: F401 - fixture

from sudarshan.guard import gate, rules


def _unjudged(claims, side, quantity=65, token=7):
    order = gate.Order(gate.LIVE, gate.PLACE, "NFO", CE, "MIS", side, quantity, "token")
    with claims.gate._lock:
        claims.gate._sends[token] = gate.Send(
            order=order, quantity=quantity, reducing=None, expect=None, position_then=None,
            admitted=time.monotonic(), sent_at=time.time(),
        )  # fmt: skip
    return token


def test_an_unjudged_exit_waiting_at_the_pacer_goes_when_the_switch_goes_on(claims):
    claims.book.cached = [row(CE, 65)]  # the last positions read
    token = _unjudged(claims, rules.SELL)
    claims.gate.set_state(ON)
    assert claims.gate.send_refusal(token) is None
    assert claims.gate.withdrawn([token]) == []
    assert claims.gate._sends[token].reducing is True  # it now counts as the exit it is


def test_an_unjudged_order_that_adds_risk_is_withdrawn(claims):
    claims.book.cached = [row(CE, 65)]
    token = _unjudged(claims, rules.BUY)
    claims.gate.set_state(ON)
    assert claims.gate.send_refusal(token)
    assert claims.gate.withdrawn([token]) == [token]


def test_an_unjudged_exit_with_no_book_to_judge_it_by_is_withdrawn(claims):
    token = _unjudged(claims, rules.SELL)  # nothing kept, nothing cached
    claims.gate.set_state(ON)
    assert claims.gate.send_refusal(token)
    assert claims.book.calls.count("rows") == 0  # and no read was made while it waited


def test_two_unjudged_exits_of_one_position_do_not_both_go(claims):
    claims.book.cached = [row(CE, 65)]
    first, second = _unjudged(claims, rules.SELL, token=7), _unjudged(claims, rules.SELL, token=8)
    claims.gate.set_state(ON)
    answers = [claims.gate.send_refusal(first), claims.gate.send_refusal(second)]
    assert answers[0] is None and answers[1]  # the second would sell what the first sells
