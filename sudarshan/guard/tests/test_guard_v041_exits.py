"""The v0.4.1 release review's findings N1 to N9, as tests (M6.9 exits, M5.13 kill switch).

Each test asserts the safe outcome the order path must keep, over the simulated Upstox of
sudarshan/books/tests/books_upstox_world.py (its order book lists every order with its fill, and
its positions rows carry the day's buy and sell quantities, lagging as the real book can). Stage
`on` unless a test says otherwise. The clock fixture moves time.monotonic forward for every module
at once (the gate, the exit guard, Exit all's budget and the simulated Upstox's fills and lag), so
"16 s later" costs no real waiting.
"""

from __future__ import annotations

import time

import pytest
from v041_world import CE, ON, OWNER, Upstox, _order, _steps, _world, world_module

from sudarshan.guard import killswitch, readers
from sudarshan.guard.store import KillSwitchState

PE = "BANKNIFTY27OCT2657500PE"  # NRML in the world


@pytest.fixture
def clock(monkeypatch):
    real = time.monotonic
    offset = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: real() + offset[0])

    class Clock:
        @staticmethod
        def advance(seconds: float) -> None:
            offset[0] += seconds

    return Clock()


def _sent(broker, symbol):
    return [(o["side"], o["qty"]) for o in broker.orders if o["symbol"] == symbol]


# -- H1: how long a working market exit's claim lasts ------------------------------------------


def test_h1_a_second_exit_goes_15_s_after_the_first_answer_while_the_first_still_works(
    monkeypatch, risk_store, clock
):
    """Under MPP an accepted market exit can rest as a limit at its protected price. Its claim
    must stay while the order book shows it working; the gate forgets it 15 s after its answer
    (gate.py _forget, SHOWS_WITHIN_S), not 150 s."""
    from broker.upstox.api import order_api

    world = _world(monkeypatch, risk_store, {CE: 65}, ON)
    broker = world.broker
    broker.part[CE] = 0  # accepted, nothing filled: it rests at Upstox
    first = order_api.place_order_api(_order(CE, "SELL", 65), "token")
    second = order_api.place_order_api(_order(CE, "SELL", 65), "token")
    clock.advance(16.0)
    order_api._POSITION_BOOK.invalidate("token")
    statuses = readers.live_order_statuses("token")
    third = order_api.place_order_api(_order(CE, "SELL", 65), "token")
    del broker.part[CE]
    broker.fill_working()  # the market comes back to both resting exits
    print(f"\nfirst {first[2]}, at once {second[2]}, 16 s later {third[2]} (book then: {statuses});"
          f" orders {_sent(broker, CE)}; true position {broker.true_qty(CE)}")  # fmt: skip
    assert first[2] and second[2] is None
    assert third[2] is None, "a second exit was sent beside the first, still working"


def test_item4_the_gate_judges_a_second_exit_on_a_book_still_60_s_behind(
    monkeypatch, risk_store, clock
):
    """The first exit filled; Upstox's positions book shows it a minute late (the changelog's
    premise). 16 s on the claim is gone and the old row judges the second exit."""
    from broker.upstox.api import order_api

    world = _world(monkeypatch, risk_store, {CE: 65}, ON)
    broker = world.broker
    broker.lag = 60.0
    first = order_api.place_order_api(_order(CE, "SELL", 65), "token")
    second = order_api.place_order_api(_order(CE, "SELL", 65), "token")
    clock.advance(16.0)
    order_api._POSITION_BOOK.invalidate("token")
    third = order_api.place_order_api(_order(CE, "SELL", 65), "token")
    print(f"\nfirst {first[2]}, at once {second[2]}, 16 s later {third[2]};"
          f" orders {_sent(broker, CE)}; true position {broker.true_qty(CE)}")  # fmt: skip
    assert first[2] and second[2] is None
    assert broker.true_qty(CE) == 0, "the second exit reversed the position"


# -- H2: the part-filled cap ------------------------------------------------------------------


@pytest.mark.parametrize("lag", [0.0, 60.0], ids=["book up to date", "book 60 s behind"])
def test_h2_a_part_size_closing_order_that_part_filled_leaves_a_position_after_exit_all(
    monkeypatch, risk_store, lag
):
    """Long 130. A LIMIT SELL 65 (a target, for half) has filled 30; 35 rest. The switch with
    Exit all cancels the 35 and caps the next exit at ordered - filled = 35, but 100 is held."""
    world = _world(monkeypatch, risk_store, {CE: 130}, KillSwitchState())
    broker = world.broker
    broker.lag = lag
    with broker.lock:
        broker.working.append({"symbol": CE, "side": "SELL", "qty": 35, "kind": "LIMIT",
                               "ordered": 65, "filled": 30, "id": "251027000019999",
                               "at": time.time()})  # fmt: skip
        broker.fills.append((time.monotonic(), CE, -30))
    answer = killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    steps = _steps(answer)
    print(f"\nlag {lag}: orders {_sent(broker, CE)}; true position {broker.true_qty(CE)};"
          f" cancel step: {steps['live_orders']['words']!r};"
          f" exit all step: {steps['live_exit_all']['words']!r}")  # fmt: skip
    assert broker.true_qty(CE) == 0, "Exit all left part of the position held"


def test_item4_a_tv_exit_16_s_after_the_switchs_exit_all_sells_the_old_quantity_again(
    monkeypatch, risk_store, clock
):
    """The H2 scenario, then a TradingView exit (a smart order to 0) 16 s later: the cap and the
    Exit all note are gone after 15 s, and the positions book is still a minute behind."""
    world = _world(monkeypatch, risk_store, {CE: 130}, KillSwitchState())
    broker = world.broker
    broker.lag = 60.0
    broker.part[CE] = 65
    world.phone_exit(CE)
    del broker.part[CE]
    answer = killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    after_switch = broker.true_qty(CE)
    clock.advance(16.0)
    import broker.upstox.api.order_api as order_api

    order_api._POSITION_BOOK.invalidate("token")
    code, body = world.tv_smart_exit(CE)
    print(f"\nafter the switch {after_switch}; TV exit 16 s later: {code} {body.get('status')};"
          f" orders {_sent(broker, CE)}; true position {broker.true_qty(CE)};"
          f" exit all step {_steps(answer)['live_exit_all']['words']!r}")  # fmt: skip
    assert after_switch == 0
    assert broker.true_qty(CE) == 0, "an exit sized from the old book reversed the position"


def test_item4_live_today_a_tv_exit_16_s_after_the_app_exit_sells_again(
    monkeypatch, risk_store, clock
):
    """Stage `off` (Live as it runs today, before v0.4.1 too): the exit guard's note is 15 s."""
    from sudarshan.guard import flag

    world = _world(monkeypatch, risk_store, {CE: 130}, KillSwitchState())
    monkeypatch.setenv(flag.ENV, "off")
    broker = world.broker
    broker.lag = 60.0
    code1, _ = world.phone_exit(CE)
    clock.advance(16.0)
    import broker.upstox.api.order_api as order_api

    order_api._POSITION_BOOK.invalidate("token")
    code2, body2 = world.tv_smart_exit(CE)
    print(f"\nphone exit {code1}; TV exit 16 s later {code2} {body2.get('status')};"
          f" orders {_sent(broker, CE)}; true position {broker.true_qty(CE)}")  # fmt: skip
    assert broker.true_qty(CE) == 0, "an exit sized from the old book reversed the position"


# -- M1: what the trader sees when an exit's turn comes too late (Live today) -------------------


@pytest.mark.parametrize("backlog", [4, 15], ids=["one fits", "none fits"])
def test_m1_a_late_exit_is_not_sent_and_says_so(monkeypatch, risk_store, backlog):
    """Scaled: a 0.9 s budget at 5 a second. 4 queued calls leave room for the first exit only;
    15 leave room for none. Stage `off`: this is Live as it runs today."""
    from broker.upstox.api import rate_limiter as rl
    from services.close_position_service import close_position_with_auth
    from sudarshan.books import exit_guard
    from sudarshan.books.telegram_words import alert_template, close_failed_line
    from sudarshan.guard import flag

    broker = world_module.Broker({CE: 65, PE: -30}, lag=0.0)
    world_module.install(monkeypatch, broker)
    monkeypatch.setenv(flag.ENV, "off")
    limiter = rl.SlidingWindowLimiter("order", 5, 475, 1900, kind="order")
    monkeypatch.setitem(rl._LIMITERS, "order", limiter)
    monkeypatch.setattr(exit_guard, "EXIT_ALL_SENDS_FOR_S", 0.9)
    for _ in range(backlog):
        limiter.reserve()
    depth = limiter.depth()
    ok, body, code = close_position_with_auth(
        {"apikey": "KEY"}, "token", "upstox", {"apikey": "KEY"}
    )
    outcomes = [(p["symbol"], p["outcome"], p.get("reason", "")[:60]) for p in body["positions"]]
    print(
        f"\nbacklog {backlog}: {code} not_confirmed={body.get('not_confirmed')}; sent to Upstox "
        f"{[(o['symbol'], o['qty']) for o in broker.orders]}; slots {depth} -> {limiter.depth()}"
    )
    print("  outcomes:", outcomes)
    print("  message:", body["message"])
    print("  telegram:", alert_template("closeposition", {}, body, "T").split("\n")[0], "|",
          close_failed_line({}, body))  # fmt: skip
    late = [p for p in body["positions"] if p["outcome"] == "not_sent"]
    sent_symbols = {o["symbol"] for o in broker.orders}
    assert late and all(p["symbol"] not in sent_symbols for p in late)
    # N7: no cause is guessed, and the next step is named
    assert all("ran out of its" in p["reason"] and "slow" not in p["reason"] for p in late)
    assert "Check Positions, then send Exit all again." in body["message"]
    assert body.get("not_confirmed") is (True if broker.orders else None)


# -- L4: Upstox's cancel of every open order, against its documentation -------------------------


class Multi(Upstox):
    """The simulated Upstox with DELETE /v2/order/multi/cancel answering as told."""

    def __init__(self, held, answer):
        super().__init__(held, lag=0.0)
        self.answer = answer  # callable(working) -> (status, body, ids cancelled)
        self.multi_calls = 0

    def delete(self, url, headers=None, **kwargs):
        if "/v2/order/multi/cancel" not in url:
            return super().delete(url, headers, **kwargs)
        with self.lock:
            self.multi_calls += 1
            status, body, gone = self.answer(list(self.working))
            self.working = [order for order in self.working if order["id"] not in gone]
        return world_module.FakeResponse(status, body)


def _partial(working):
    keep = working[-1]
    gone = [order["id"] for order in working[:-1]]
    token = world_module.INSTRUMENTS[keep["symbol"]][0]
    errors = [{"error_code": "UDAPI100049", "message": "Order cannot be cancelled now.",
               "property_path": None, "invalid_value": None, "order_id": keep["id"],
               "instrument_key": token}]  # fmt: skip
    body = {"status": "partial_success", "data": {"order_ids": gone}, "errors": errors,
            "summary": {"total": len(working), "success": len(gone), "error": 1}}  # fmt: skip
    return 207, body, gone


def _too_many(working):
    body = {"status": "error", "errors": [{"errorCode": "UDAPI1110", "error_code": "UDAPI1110",
            "message": "More than 10 open orders: cancel them by segment or tag.",
            "propertyPath": None, "invalidValue": None}]}  # fmt: skip
    return 400, body, []


def _rate_limited(working):
    body = {"status": "error", "errors": [{"errorCode": "UDAPI10005",
            "message": "Too Many Request Sent"}]}  # fmt: skip
    return 429, body, []


@pytest.mark.parametrize(
    "answer", [_partial, _too_many, _rate_limited], ids=["207 partial", "400 UDAPI1110", "429"]
)
def test_l4_an_answer_that_does_not_prove_every_cancel_sends_no_exit_and_says_how_many(
    monkeypatch, risk_store, answer
):
    import services.gtt_orderbook_service as gtt_book
    from sudarshan.guard import flag, gate

    broker = Multi({CE: 65, PE: -30}, answer)
    world_module.install(monkeypatch, broker)
    monkeypatch.setattr(gtt_book, "get_gtt_orderbook_with_auth",
                        lambda *a, **k: (True, {"status": "success", "data": []}, 200))  # fmt: skip
    told = []
    monkeypatch.setattr(
        killswitch, "_tell", lambda owner, text: told.append(text) or (True, "", {})
    )
    monkeypatch.setattr(killswitch, "_announce", lambda state: None)
    monkeypatch.setattr(
        killswitch, "_engage_agent", lambda: (True, "agent", {"engaged_by_us": False})
    )
    monkeypatch.setattr(killswitch, "DRAIN_S", 0.2)
    monkeypatch.setattr(killswitch, "CANCELS_POLL_S", 0.05)
    monkeypatch.setattr(killswitch, "SECOND_SWEEP_AFTER_S", 0.0)
    monkeypatch.setattr(readers, "READ_LIMIT_S", 0.3)
    fresh = gate.Gate(store=risk_store)
    fresh.set_state(KillSwitchState())
    monkeypatch.setattr(gate, "GATE", fresh)
    monkeypatch.setenv(flag.ENV, "on")
    broker.rest(CE, "SELL", 65)  # a stop under the long CE
    broker.rest(PE, "BUY", 30)  # a stop over the short PE
    broker.order_book_delay = 20.0  # the order book hangs: every read is cut off
    result = killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    steps = _steps(result)
    market = [(o["symbol"], o["side"], o["qty"]) for o in broker.orders]
    print(
        f"\n{answer.__name__}: multi-cancel calls {broker.multi_calls}; still working "
        f"{[(o['symbol'], o['side']) for o in broker.working]}; market exits {market}"
    )
    for name in ("live_orders", "live_orders_again", "live_exit_all"):
        if name in steps:
            print(f"  {name}: ok={steps[name]['ok']} {steps[name]['words']!r}")
    print("  told:", [t[:160] for t in told])
    assert market == [], "a market exit was sent beside a closing order that may still work"
    said = steps["live_exit_all"]["words"] + steps["live_orders"]["words"]
    if answer is _partial:
        assert "1" in said and "cancel" in said, "the step does not say how many were cancelled"
    assert "Upstox app" in steps["live_exit_all"]["words"]


# -- N2 in whole lots: a target for half the position, one lot of it filled ------------------


@pytest.mark.parametrize("lag", [0.0, 60.0], ids=["book up to date", "book 60 s behind"])
def test_n2_whole_lots_a_target_for_half_part_filled_leaves_half_held(monkeypatch, risk_store, lag):
    """Long 260 (4 lots of 65). A target LIMIT SELL 130 has filled 65; 65 rest. The switch with
    Exit all cancels the rest; 195 are held, and Exit all must close them."""
    world = _world(monkeypatch, risk_store, {CE: 260}, KillSwitchState())
    broker = world.broker
    broker.lag = lag
    with broker.lock:
        broker.working.append({"symbol": CE, "side": "SELL", "qty": 65, "kind": "LIMIT",
                               "ordered": 130, "filled": 65, "id": "251027000018888",
                               "at": time.time()})  # fmt: skip
        broker.fills.append((time.monotonic(), CE, -65))
    answer = killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    steps = _steps(answer)
    print(f"\nlag {lag}: orders {_sent(broker, CE)}; true position {broker.true_qty(CE)};"
          f" exit all step: {steps['live_exit_all']['words']!r}")  # fmt: skip
    assert broker.true_qty(CE) == 0, "Exit all left part of the position held"


# -- item 3 in Live today: a market exit resting beyond price protection --------------------


@pytest.mark.parametrize("second", ["tv smart exit", "flow close", "app exit all", "phone exit"])
def test_item3_live_today_a_second_exit_16_s_after_a_resting_app_exit(
    monkeypatch, risk_store, clock, second
):
    """Stage `off` (Live as it runs today). The app's Exit SELL 65 is accepted and rests (a market
    order held at its protected price). 16 s later a second exit of the same position arrives."""
    from broker.upstox.api import order_api
    from sudarshan.guard import flag

    world = _world(monkeypatch, risk_store, {CE: 65}, KillSwitchState())
    monkeypatch.setenv(flag.ENV, "off")
    broker = world.broker
    broker.part[CE] = 0  # accepted, nothing filled: it rests at Upstox
    code1, body1 = world.phone_exit(CE)
    clock.advance(16.0)
    order_api._POSITION_BOOK.invalidate("token")
    if second == "tv smart exit":
        code2, body2 = world.tv_smart_exit(CE)
    elif second == "flow close":
        code2, body2 = world.flow_close(CE)
    elif second == "app exit all":
        code2, body2 = world.app_exit_all()
    else:
        code2, body2 = world.phone_exit(CE)
    working = [(o["side"], o["qty"]) for o in broker.working]
    del broker.part[CE]
    broker.fill_working()  # the market comes back to every resting exit
    print(f"\n{second}: first {code1}; second {code2} {str((body2 or {}).get('message'))[:90]!r};"
          f" working before the fill {working}; true position {broker.true_qty(CE)}")  # fmt: skip
    assert broker.true_qty(CE) == 0, "a second exit was sent beside the first, still working"


# -- L4: answers that do not prove every order was cancelled --------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        {"status": "success", "data": {"order_ids": ["1"]}},  # no summary at all
        {
            "status": "success",
            "data": {"order_ids": ["1"]},
            "errors": [{"order_id": "2"}],
            "summary": {"total": 2, "success": 1},
        },  # fmt: skip
    ],
    ids=["no summary", "errors listed, no error count"],
)
def test_l4_a_success_that_does_not_prove_every_cancel_is_not_counted_done(monkeypatch, answer):
    from broker.upstox.api import order_api

    monkeypatch.setattr(order_api, "get_api_response", lambda *a, **k: dict(answer))
    cancelled, not_confirmed = order_api.cancel_every_open_order_api("token")
    print(f"\n{answer}: cancelled={cancelled} not_confirmed={not_confirmed}")
    assert cancelled is False


def test_l4_a_cancel_all_with_no_answer_is_not_called_refused(monkeypatch):
    """A timeout after the request left may come after Upstox acted: it must read "not
    confirmed", never "did not go through"."""
    from broker.upstox.api import order_api
    from sudarshan.guard import words

    monkeypatch.setattr(
        order_api,
        "get_api_response",
        lambda *a, **k: {"status": "error", "message": "timed out", "not_confirmed": True},
    )
    cancelled, not_confirmed = order_api.cancel_every_open_order_api("token")
    assert (cancelled, not_confirmed) == (False, True)
    said = words.ks_orders_not_cancelled_unread(None, None, not_confirmed)
    assert "not confirmed" in said and "did not go through" not in said


# -- N8: the cancel without the book has a limit of its own ------------------------------------


def test_n8_the_cancel_of_every_open_order_is_cut_off_after_10_s_and_is_not_confirmed(monkeypatch):
    from broker.upstox.api import order_api

    seen = {}

    class Client:
        def delete(self, url, headers=None, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            raise __import__("httpx").ReadTimeout("The read operation timed out")

    monkeypatch.setenv("BROKER_API_KEY", "fake")
    monkeypatch.setattr(order_api, "get_httpx_client", lambda: Client())
    cancelled, not_confirmed = order_api.cancel_every_open_order_api("token")
    assert seen["timeout"] == 10.0
    assert (cancelled, not_confirmed) == (False, True)


# -- N9: after a 200 the cancelled orders must show ended before Exit all ---------------------


class Cancelling(Multi):
    """Cancel of every open order answers 200 at once, but the cancelled orders read "cancel
    pending" for the next ``pending_reads`` order book reads; the first ``fail_reads`` reads of
    the order book fail (it was unreadable when the switch looked)."""

    def __init__(self, held):
        super().__init__(held, None)
        self.fail_reads = 0
        self.pending_reads = 2
        self.cancelling: dict[str, int] = {}

    def delete(self, url, headers=None, **kwargs):
        if "/v2/order/multi/cancel" not in url:
            return super().delete(url, headers, **kwargs)
        with self.lock:
            ids = [order["id"] for order in self.working]
            for orderid in ids:
                self.cancelling[orderid] = self.pending_reads
        body = {"status": "success", "data": {"order_ids": ids},
                "summary": {"total": len(ids), "success": len(ids), "error": 0}}  # fmt: skip
        return world_module.FakeResponse(200, body)

    def _order_book(self, timeout=None):
        with self.lock:
            if self.fail_reads > 0:
                self.fail_reads -= 1
                raise __import__("httpx").ReadTimeout("The read operation timed out")
            for orderid, left in list(self.cancelling.items()):
                if left <= 0:
                    self.working = [o for o in self.working if o["id"] != orderid]
                    del self.cancelling[orderid]
        response = super()._order_book(timeout)
        with self.lock:
            for row in response.json()["data"]:
                if row["order_id"] in self.cancelling:
                    row["status"] = "cancel pending"
                    self.cancelling[row["order_id"]] -= 1
        return response


def test_n9_exit_all_waits_for_the_orders_a_200_cancelled_to_show_ended(monkeypatch, risk_store):
    import services.gtt_orderbook_service as gtt_book
    from sudarshan.guard import flag, gate

    broker = Cancelling({CE: 65})
    world_module.install(monkeypatch, broker)
    monkeypatch.setattr(gtt_book, "get_gtt_orderbook_with_auth",
                        lambda *a, **k: (True, {"status": "success", "data": []}, 200))  # fmt: skip
    monkeypatch.setattr(killswitch, "_tell", lambda owner, text: (True, "", {}))
    monkeypatch.setattr(killswitch, "_announce", lambda state: None)
    monkeypatch.setattr(
        killswitch, "_engage_agent", lambda: (True, "agent", {"engaged_by_us": False})
    )
    monkeypatch.setattr(killswitch, "DRAIN_S", 0.2)
    monkeypatch.setattr(killswitch, "CANCELS_POLL_S", 0.05)
    monkeypatch.setattr(killswitch, "SECOND_SWEEP_AFTER_S", 0.0)
    fresh = gate.Gate(store=risk_store)
    fresh.set_state(KillSwitchState())
    monkeypatch.setattr(gate, "GATE", fresh)
    monkeypatch.setenv(flag.ENV, "on")
    broker.rest(CE, "SELL", 65)  # the stop loss protecting the long
    broker.fail_reads = 6  # the switch's first looks at the order book fail: it cancels without it
    result = killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    steps = _steps(result)
    assert steps["live_orders"]["detail"].get("without_book") is True, steps
    assert steps["live_orders"]["detail"]["cancelled_ids"], steps
    assert steps["live_exit_all"]["detail"]["cancels_shown"] is True, steps
    assert broker.true_qty(CE) == 0, steps["live_exit_all"]["words"]


# -- N5: the words for a partial and an unconfirmed cancel -------------------------------------


def test_n5_the_words_say_how_many_went_and_never_call_an_unconfirmed_cancel_refused():
    from sudarshan.guard import words

    partial = words.ks_orders_not_cancelled_unread(1, 2, False)
    assert "1 of 2 orders" in partial and "still be working" in partial
    unconfirmed = words.ks_orders_not_cancelled_unread(None, None, True)
    assert "not confirmed" in unconfirmed and "did not go through" not in unconfirmed
    assert "did not go through" in words.ks_orders_not_cancelled_unread(None, None, False)


@pytest.mark.parametrize(
    ("status", "body", "expect"),
    [
        (
            200,
            {
                "status": "success",
                "data": {"order_ids": ["1", "2"]},
                "summary": {"total": 2, "success": 2, "error": 0},
            },
            (True, False),
        ),
        (
            200,
            {
                "status": "success",
                "data": {"order_ids": ["1"]},
                "summary": {"total": 2, "success": 1, "error": 0},
            },
            (False, True),
        ),
        (
            207,
            {
                "status": "partial_success",
                "data": {"order_ids": ["1"]},
                "summary": {"total": 2, "success": 1, "error": 1},
            },
            (False, False),
        ),
        (
            500,
            {"status": "error", "errors": [{"message": "down"}], "not_confirmed": True},
            (False, True),
        ),
    ],
    ids=["all went", "success but short", "207", "5xx"],
)
def test_n6_a_cancel_counts_as_done_only_when_the_summary_proves_every_order(
    monkeypatch, status, body, expect
):
    from broker.upstox.api import order_api

    monkeypatch.setattr(order_api, "get_api_response", lambda *a, **k: dict(body))
    assert order_api.cancel_every_open_order_api("token") == expect


def test_a_multi_cancel_of_more_than_10_orders_is_not_done(monkeypatch):
    from broker.upstox.api import order_api

    monkeypatch.setattr(
        order_api,
        "get_api_response",
        lambda *a, **k: {"status": "error", "errors": [{"errorCode": "UDAPI1110", "message": "x"}]},
    )
    assert order_api.cancel_every_open_order_api("token") == (False, False)
