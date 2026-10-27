"""The order size limit in the gate (M20.7), and batches held to the owner's limits whole: a
basket, a multi-leg order and a split send nothing when one part is over the band or the cap.

The Upstox book is the fake of test_guard_gate.py, with the quote of test_guard_gate_band.py and
the master contract's lot sizes beside it. The services are the real ones, on a fake broker
module whose order call carries the real gate.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_guard_gate import CE, PE, broker, change, order, refused_message, row, working
from test_guard_gate_band import QuotedBook

from sudarshan.guard import band, flag, gate, readers, rules, words
from sudarshan.guard.store import KillSwitchState


class SizedBook(QuotedBook):
    """The fake book and quotes, and the master contract's lot sizes."""

    def __init__(self):
        super().__init__()
        self.lots: dict[str, float] = {CE: 65, PE: 65}

    def lot_size(self, symbol, exchange):
        self.calls.append("lot")
        if "lot" in self.fail:
            raise readers.Unreadable("lot")
        return self.lots.get(symbol)

    def sandbox_position(self, owner, symbol, exchange, product):
        self.calls.append(f"sandbox {owner}")
        return 0


@pytest.fixture
def sized(risk_store, monkeypatch):
    book = SizedBook()
    fresh = gate.Gate(store=risk_store, read=book)
    fresh.set_state(KillSwitchState())
    monkeypatch.setattr(gate, "GATE", fresh)
    monkeypatch.setenv(flag.ENV, "on")
    risk_store.save_settings("owner", size_on=True)  # 2 lots per F&O order, 1,000 shares in cash
    return SimpleNamespace(gate=fresh, book=book, store=risk_store)


def test_three_lots_at_a_two_lot_cap_are_refused_in_words(sized):
    upstox = broker()
    assert refused_message(upstox.place(order("BUY", 195, symbol=PE), "token")) == (
        "This order is 3 lots of NIFTY 25900 PE; your limit is 2 lots per F&O order (Settings > "
        "Risk). Check the quantity. Nothing was sent."
    )
    assert upstox.reached == []


def test_two_lots_go(sized):
    assert broker().place(order("BUY", 130, symbol=PE), "token")[2] == "OID0"


def test_an_exit_is_never_limited_and_reads_no_lot_size(sized):
    sized.book.rows = [row(CE, 650)]
    assert broker().place(order("SELL", 650), "token")[2] == "OID0"
    assert "lot" not in sized.book.calls


def test_crossing_counts_only_the_part_beyond_closing(sized):
    sized.book.rows = [row(CE, 65)]
    upstox = broker()
    assert refused_message(upstox.place(order("SELL", 260), "token")) == (
        "This order would close NIFTY 25900 CE and open 3 lots the other way; your limit is 2 "
        "lots per F&O order (Settings > Risk). To exit, sell 65. Nothing was sent."
    )
    assert upstox.place(order("SELL", 195), "token")[2] == "OID0"  # 1 closes, 2 open: the cap


def test_cash_is_held_in_shares(sized):
    upstox = broker()
    big = order("BUY", 1500, symbol="RELIANCE", exchange="NSE", product="CNC")
    assert refused_message(upstox.place(big, "token")) == (
        "This order is 1,500 shares of RELIANCE; your limit is 1,000 shares per cash order "
        "(Settings > Risk). Check the quantity. Nothing was sent."
    )
    assert "lot" not in sized.book.calls


def test_an_unknown_lot_size_lets_the_order_go_and_says_so(sized):
    sized.book.lots.pop(PE)
    assert broker().place(order("BUY", 6500, symbol=PE), "token")[2] == "OID0"
    assert sized.gate.last_problem()["words"] == words.SIZE_NOT_CHECKED


def test_a_modify_raising_an_entry_over_the_cap_is_refused(sized):
    sized.book.orders["E1"] = working(rules.BUY, 65, price=95.0)
    upstox = broker()
    body, status = upstox.modify(change("E1", 195, 95.0), "token")
    assert status == 403 and body["message"].startswith("This order is 3 lots of NIFTY 25900 CE")
    assert upstox.modify(change("E1", 130, 95.0), "token")[1] == 200


def test_a_gtt_adding_risk_is_held_to_the_cap(sized):
    gtt = order("BUY", 195, symbol=PE, trigger_type="SINGLE")
    assert refused_message(broker().gtt_place(gtt, "token")).startswith("This order is 3 lots")


def test_with_the_size_limit_off_nothing_is_read(sized):
    sized.store.save_settings("owner", size_on=False)
    assert broker().place(order("BUY", 6500, symbol=PE), "token")[2] == "OID0"
    assert sized.book.calls == []


# -- batches, held whole ---------------------------------------------------------------------------


def _part(action, quantity, symbol, **extra):
    return gate._upstox_place(order(action, quantity, symbol=symbol, **extra), "token")


def test_a_batch_with_one_part_over_the_cap_is_refused_whole(sized):
    found = sized.gate.check_together(
        [_part("BUY", 65, CE), _part("SELL", 195, PE), _part("BUY", 65, PE)]
    )
    assert found is not None
    index, decision = found
    assert index == 1 and decision.code == gate.SIZE_LIMIT
    assert sized.gate.sends() == 0  # nothing recorded: each part is decided again as it is sent
    sized.gate.flush_notes()
    refused = [entry for entry in sized.store.history() if entry.kind == "order_refused"]
    assert refused and refused[0].detail["code"] == gate.SIZE_LIMIT  # in the risk history


def test_a_batch_within_the_limits_passes_and_exits_are_never_held(sized):
    sized.book.rows = [row(CE, 650)]
    assert sized.gate.check_together([_part("SELL", 650, CE), _part("BUY", 130, PE)]) is None


def test_two_parts_selling_one_long_take_its_room_once(sized):
    """Each judged alone would read as the exit of the same 65; together the second opens a
    short of 3 lots, over the cap."""
    sized.book.rows = [row(CE, 65)]
    found = sized.gate.check_together([_part("SELL", 65, CE), _part("SELL", 195, CE)])
    assert found is not None and found[0] == 1


def test_a_batch_part_far_from_the_market_is_refused_whole(sized):
    sized.store.save_settings("owner", band_on=True)
    sized.book.quotes[PE] = band.Quote(ltp=150.0, bid=149.9, ask=150.1)
    found = sized.gate.check_together(
        [_part("BUY", 65, CE), _part("BUY", 65, PE, pricetype="LIMIT", price="186")]
    )
    assert found is not None and found[1].code == gate.PRICE_BAND


def test_a_batch_is_not_checked_when_neither_limit_is_on(sized):
    sized.store.save_settings("owner", size_on=False)
    assert sized.gate.check_together([_part("BUY", 6500, PE)]) is None
    assert sized.book.calls == []


@pytest.fixture
def services(sized, monkeypatch):
    """The real basket, split and multi-leg services, Live, on a fake broker module whose order
    call carries the real gate."""
    import database.settings_db as settings_db
    import restx_api  # noqa: F401 - imported first, as the app does, to settle its import cycle
    from services import basket_order_service, options_multiorder_service, split_order_service

    upstox = broker()
    module = SimpleNamespace(place_order_api=upstox.place)
    for service in (basket_order_service, options_multiorder_service, split_order_service):
        monkeypatch.setattr(service, "get_analyze_mode", lambda: False)
        if hasattr(service, "import_broker_module"):
            monkeypatch.setattr(service, "import_broker_module", lambda name: module)
    monkeypatch.setattr(settings_db, "get_analyze_mode", lambda: False)
    monkeypatch.setattr(split_order_service, "get_order_rate_limit", lambda: 0)
    return SimpleNamespace(
        basket=basket_order_service,
        split=split_order_service,
        multi=options_multiorder_service,
        reached=upstox.reached,
    )


def test_a_basket_with_one_order_over_the_cap_sends_nothing(services):
    data = {
        "apikey": "key",
        "strategy": "hedge",
        "orders": [order("SELL", 130, symbol=CE), order("BUY", 195, symbol=PE)],
    }
    ok, body, status = services.basket.process_basket_order_with_auth(data, "token", "upstox", data)
    assert (ok, status) == (False, 403)
    assert body["message"].startswith(
        "Nothing in this basket was sent, so no part goes without the others. Order 2: This "
        "order is 3 lots of NIFTY 25900 PE"
    )
    assert services.reached == []


def test_a_basket_within_the_cap_is_sent_whole(services):
    data = {"apikey": "key", "strategy": "hedge",
            "orders": [order("SELL", 130, symbol=CE), order("BUY", 130, symbol=PE)]}  # fmt: skip
    ok, body, status = services.basket.process_basket_order_with_auth(data, "token", "upstox", data)
    assert (ok, status) == (True, 200) and len(services.reached) == 2


def test_a_split_over_the_cap_in_all_sends_no_part(services):
    data = {**order("BUY", 650, symbol=PE), "splitsize": 65, "apikey": "key"}
    ok, body, status = services.split.split_order_with_auth(data, "token", "upstox", data)
    assert (ok, status) == (False, 403)
    assert body["message"].startswith(
        "Nothing of this split order was sent: it is held to your limits as the one order it is. "
        "This order is 10 lots of NIFTY 25900 PE"
    )
    assert services.reached == []


def test_a_multi_leg_order_with_one_leg_over_the_cap_sends_nothing(services, monkeypatch):
    def resolved(leg, common, api_key, ltp):
        symbol = CE if leg["option_type"] == "CE" else PE
        return True, {"symbol": symbol, "exchange": "NFO"}, 200

    monkeypatch.setattr(services.multi, "resolve_leg_symbol", resolved)
    monkeypatch.setattr(services.multi, "get_underlying_ltp", lambda *args: (True, 25900.0, None))
    legs = [
        {"offset": "ATM", "option_type": "CE", "action": "BUY", "quantity": 65},
        {"offset": "ATM", "option_type": "PE", "action": "SELL", "quantity": 195},
    ]
    data = {"apikey": "key", "underlying": "NIFTY", "exchange": "NSE_INDEX",
            "expiry_date": "27OCT26", "strategy": "s", "legs": legs}  # fmt: skip
    ok, body, status = services.multi.process_multiorder_with_auth(
        data, "token", "upstox", "key", data
    )
    assert (ok, status) == (False, 403)
    assert body["message"].startswith(
        "Nothing in this multi-leg order was sent, so no part goes without the others. Leg 2: "
    )
    assert services.reached == []


def test_with_the_guard_off_a_batch_is_not_looked_at(services, monkeypatch):
    monkeypatch.delenv(flag.ENV)
    monkeypatch.setattr(gate.Gate, "check_together", lambda *args: pytest.fail("looked at"))
    data = {"apikey": "key", "strategy": "s",
            "orders": [order("BUY", 6500, symbol=PE)]}  # fmt: skip
    ok, _body, status = services.basket.process_basket_order_with_auth(
        data, "token", "upstox", data
    )
    assert (ok, status) == (True, 200) and len(services.reached) == 1


def test_an_analyze_mode_basket_is_held_whole_against_the_sandbox(services, sized, monkeypatch):
    import database.auth_db as auth_db
    import database.settings_db as settings_db

    monkeypatch.setattr(settings_db, "get_analyze_mode", lambda: True)
    monkeypatch.setattr(services.basket, "get_analyze_mode", lambda: True)
    monkeypatch.setattr(auth_db, "verify_api_key", lambda key: "owner" if key == "key" else None)
    analyzed = []
    monkeypatch.setattr(services.basket, "emit_analyzer_error",
                        lambda data, message: analyzed.append(message) or {"message": message})  # fmt: skip
    data = {"apikey": "key", "strategy": "s",
            "orders": [order("BUY", 65, symbol=CE), order("BUY", 195, symbol=PE)]}  # fmt: skip
    ok, body, status = services.basket.process_basket_order_with_auth(data, "token", "upstox", data)
    assert (ok, status) == (False, 403)
    assert analyzed and analyzed[0].startswith("Nothing in this basket was sent")
    assert "sandbox owner" in sized.book.calls  # judged on the sandbox's own book
