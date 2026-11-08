"""Release review v0.4.1, L6: an order row whose ``filled_quantity`` is null.

Upstox may leave ``filled_quantity`` null on an order that has reached no fill (a stop waiting
for its trigger, a request still being validated, a rejection). On 376dc58 one such row made the
whole instrument impossible to size (sudarshan.books.fills answered None), so every exit of the
position was refused with "could not work out" while that order stood. Such a row now reads as
nothing filled, which is what its status says; on a status that may have part-filled (open,
cancel pending, complete) a null filled quantity still cannot be worked out, and nothing is sent.
"""

from __future__ import annotations

from books_upstox_world import Broker, install

X = "NIFTY27OCT2625900CE"  # long 65, MIS in the world


class Upstox(Broker):
    """The simulated Upstox, whose order book shows ``filled_quantity`` null for the orders in
    ``null_filled`` (by id), as Upstox's can."""

    def __init__(self, held, lag=0.0):
        super().__init__(held, lag)
        self.null_filled: set[str] = set()

    def _order_book(self, timeout=None):
        response = super()._order_book(timeout)
        for row in response.json()["data"]:
            if row["order_id"] in self.null_filled:
                row["filled_quantity"] = None
        return response


def _resting(broker, kind="SL-M", trigger=98.5):
    broker.rest(X, "SELL", 65, kind=kind, trigger=trigger)  # a closing order, placed elsewhere
    broker.null_filled.add(broker.working[-1]["id"])


def test_l6_a_resting_stop_with_no_filled_quantity_does_not_block_the_exit(monkeypatch):
    broker = Upstox({X: 65})
    world = install(monkeypatch, broker)
    _resting(broker)  # a stop loss, trigger pending
    code, body = world.phone_exit(X)
    assert code == 200, body
    assert broker.true_qty(X) == 0


def test_l6_a_tradingview_exit_goes_too(monkeypatch):
    broker = Upstox({X: 65})
    world = install(monkeypatch, broker)
    _resting(broker)  # a stop loss, trigger pending
    code, body = world.tv_smart_exit(X, quantity="65")
    assert code == 200, body
    assert broker.true_qty(X) == 0


def test_l6_an_open_order_with_no_filled_quantity_still_refuses_the_exit(monkeypatch):
    """An open order may have part-filled: with its filled quantity unknown, nothing is sent."""
    broker = Upstox({X: 65})
    world = install(monkeypatch, broker)
    _resting(broker, kind="LIMIT", trigger=0)  # open at the exchange
    code, body = world.phone_exit(X)
    assert code != 200
    assert broker.true_qty(X) == 65
    assert "Upstox app" in str(body.get("message"))
