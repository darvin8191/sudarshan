"""Release review A, M1: Exit all's sending budget covers the wait at the order pacer.

An exit is sized from reads made when Exit all began; the budget (EXIT_ALL_SENDS_FOR_S) promises
none is sent later than that. Behind a backlog at the pacer the wait used to be outside the check,
so an exit went out late. Scaled: a 1 s budget and a 3 s backlog (15 calls at 5 a second);
production is 15 s and 75 calls.
"""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import restx_api  # noqa: F401 - first, as app.py does
from broker.upstox.api import rate_limiter as rl

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "budget_world", REPO / "sudarshan" / "books" / "tests" / "books_upstox_world.py"
)
world_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(world_module)

CE, PE = "NIFTY27OCT2625900CE", "BANKNIFTY27OCT2657500PE"


def test_an_exit_waiting_behind_a_backlog_is_not_sent_after_the_budget(monkeypatch):
    from services.close_position_service import close_position_with_auth
    from sudarshan.books import exit_guard

    broker = world_module.Broker({CE: 65, PE: -30}, lag=0.0)
    world_module.install(monkeypatch, broker)
    limiter = rl.SlidingWindowLimiter("order", 5, 475, 1900, kind="order")
    monkeypatch.setitem(rl._LIMITERS, "order", limiter)
    monkeypatch.setattr(exit_guard, "EXIT_ALL_SENDS_FOR_S", 1.0)
    for _ in range(15):  # other order calls already queued: a script, webhooks, a basket
        limiter.reserve()
    started = time.monotonic()
    ok, body, code = close_position_with_auth({"apikey": "KEY"}, "token", "upstox", {"apikey": "KEY"})
    sent_late = [order for order in broker.orders if order["at"] - started > 1.3]
    assert not sent_late, "an exit was sent after the budget, from reads that old"
    outcomes = {item["symbol"]: item["outcome"] for item in body.get("positions", [])}
    assert outcomes and set(outcomes.values()) == {"not_sent"}, body
    assert "sent nothing for" in body["message"]
    assert broker.true_qty(CE) == 65 and broker.true_qty(PE) == -30  # nothing went, said so


def test_an_exit_that_leaves_the_queue_in_time_is_sent(monkeypatch):
    from services.close_position_service import close_position_with_auth

    broker = world_module.Broker({CE: 65}, lag=0.0)
    world_module.install(monkeypatch, broker)
    limiter = rl.SlidingWindowLimiter("order", 5, 475, 1900, kind="order")
    monkeypatch.setitem(rl._LIMITERS, "order", limiter)
    for _ in range(5):  # one second of backlog, well inside the 15 s budget
        limiter.reserve()
    ok, body, code = close_position_with_auth({"apikey": "KEY"}, "token", "upstox", {"apikey": "KEY"})
    assert code == 200 and broker.true_qty(CE) == 0
