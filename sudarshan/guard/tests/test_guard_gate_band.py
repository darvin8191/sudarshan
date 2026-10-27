"""The limit price band in the gate (M20.7): a priced order adding risk too far from the market is
refused before it is sent; exits, MARKET orders and GTTs never are; the market is read only when
the band needs it, and an order with no market to hold it to goes, noted.

The Upstox book is the fake of test_guard_gate.py, with Upstox's quote beside it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_guard_gate import CE, ON, PE, Book, broker, change, order, refused_message, row, working

from sudarshan.guard import band, flag, gate, readers, rules, words
from sudarshan.guard.store import KillSwitchState

FUT = "NIFTY27OCT26FUT"


class QuotedBook(Book):
    """The fake book, and Upstox's quote for each instrument."""

    def __init__(self):
        super().__init__()
        self.quotes: dict[str, band.Quote] = {}

    def quote(self, pipe, owner, symbol, exchange):
        self.calls.append("quote")
        if "quote" in self.fail:
            raise readers.Unreadable("quote")
        return self.quotes.get(symbol, band.Quote())


@pytest.fixture
def banded(risk_store, monkeypatch):
    book = QuotedBook()
    fresh = gate.Gate(store=risk_store, read=book)
    fresh.set_state(KillSwitchState())
    monkeypatch.setattr(gate, "GATE", fresh)
    monkeypatch.setenv(flag.ENV, "on")
    risk_store.save_settings("owner", band_on=True)  # options 20% at least ₹2, futures 1%, cash 5%
    book.quotes[PE] = band.Quote(ltp=150.0, bid=149.9, ask=150.1)
    return SimpleNamespace(gate=fresh, book=book, store=risk_store)


def limit(action="BUY", price=150.0, symbol=PE, **extra):
    return order(action, symbol=symbol, pricetype="LIMIT", price=str(price), **extra)


def test_a_buy_far_above_the_market_is_refused_in_words(banded):
    upstox = broker()
    assert refused_message(upstox.place(limit(price=186.0), "token")) == (
        "Your buy price ₹186.00 is 23.9% above the market (₹150.10); the price band is 20%, at "
        "least ₹2 (Settings > Risk). Check the price. Nothing was sent."
    )
    assert upstox.reached == []
    banded.gate.flush_notes()
    refused = [entry for entry in banded.store.history() if entry.kind == "order_refused"]
    assert refused and refused[0].detail["code"] == gate.PRICE_BAND


def test_a_price_inside_the_band_goes(banded):
    assert broker().place(limit(price=175.0), "token")[2] == "OID0"


def test_a_sell_far_below_the_market_is_refused(banded):
    """A short sale: the touch for a sell is the lower of the last price and the bid."""
    message = refused_message(broker().place(limit("SELL", price=100.0), "token"))
    assert message.startswith("Your sell price ₹100.00 is 33.3% below the market (₹149.90);")


def test_an_exit_is_never_held_to_the_band_and_reads_no_market(banded):
    banded.book.rows = [row(PE, 65)]
    upstox = broker()
    assert upstox.place(limit("SELL", price=1.0), "token")[2] == "OID0"
    assert "quote" not in banded.book.calls


def test_a_market_order_is_never_held_to_the_band_and_costs_no_read(banded):
    assert broker().place(order("BUY", symbol=PE), "token")[2] == "OID0"
    assert banded.book.calls == []


def test_with_the_band_off_a_far_price_goes_and_nothing_is_read(banded):
    banded.store.save_settings("owner", band_on=False)
    assert broker().place(limit(price=500.0), "token")[2] == "OID0"
    assert banded.book.calls == []


def test_the_passive_side_goes_unless_both_sides_are_checked(banded):
    upstox = broker()
    assert upstox.place(limit(price=60.0), "token")[2] == "OID0"  # a low bid rests
    banded.store.save_settings("owner", band_both_sides=True)
    message = refused_message(upstox.place(limit(price=60.0), "token"))
    assert message.startswith("Your buy price ₹60.00 is 60% below the market (₹150.10);")


def test_a_cheap_option_takes_the_rupee_floor(banded):
    banded.book.quotes[PE] = band.Quote(ltp=5.0, bid=4.95, ask=5.05)
    upstox = broker()
    assert upstox.place(limit(price=7.0), "token")[2] == "OID0"  # 5.05 + 2: inside
    message = refused_message(upstox.place(limit(price=7.2), "token"))
    assert message.startswith("Your buy price ₹7.20 is 42.6% above the market (₹5.05);")


def test_a_stop_limit_is_held_to_its_own_trigger_without_a_market_read(banded):
    stop = order("BUY", symbol=PE, pricetype="SL", price="200", trigger_price="160")
    message = refused_message(broker().place(stop, "token"))
    assert message == (
        "Your stop-loss buy price ₹200.00 is 25% above its trigger (₹160.00); the price band is "
        "20%, at least ₹2 (Settings > Risk). Check the price. Nothing was sent."
    )
    assert "quote" not in banded.book.calls


def test_a_stop_market_already_through_the_market_is_refused(banded):
    banded.book.quotes[FUT] = band.Quote(ltp=25900.0, bid=25899.0, ask=25901.0)
    stop = order("BUY", symbol=FUT, product="NRML", pricetype="SL-M", trigger_price="25500")
    assert refused_message(broker().place(stop, "token")) == (
        "Your stop-loss buy trigger ₹25,500.00 is already 1.5% below the market (₹25,901.00), so "
        "it would go at once; the price band is 1% (Settings > Risk). Check the trigger. Nothing "
        "was sent."
    )


def test_no_market_price_lets_the_order_go_and_says_so(banded):
    banded.book.fail.add("quote")
    assert broker().place(limit(price=500.0), "token")[2] == "OID0"
    assert banded.gate.last_problem()["words"] == words.BAND_NOT_CHECKED
    banded.gate.flush_notes()
    noted = [entry for entry in banded.store.history() if entry.kind == "guard_unavailable"]
    assert noted and noted[0].summary == words.BAND_NOT_CHECKED


def test_a_quote_with_no_prices_is_no_market_either(banded):
    banded.book.quotes[PE] = band.Quote()  # no trade and no depth yet
    assert broker().place(limit(price=500.0), "token")[2] == "OID0"
    assert banded.gate.last_problem()["words"] == words.BAND_NOT_CHECKED


def test_a_modify_to_a_price_far_above_the_market_is_refused(banded):
    banded.book.quotes[CE] = band.Quote(ltp=100.0, bid=99.9, ask=100.1)
    banded.book.orders["E1"] = working(rules.BUY, 65, price=95.0)  # a resting entry
    upstox = broker()
    body, status = upstox.modify(change("E1", 65, 130.0), "token")
    assert status == 403
    assert body["message"].startswith("Your buy price ₹130.00 is 29.9% above the market (₹100.10)")
    assert upstox.reached == []
    assert upstox.modify(change("E1", 65, 101.0), "token")[1] == 200


def test_a_modify_of_an_exit_is_never_held_to_the_band(banded):
    banded.book.rows = [row(CE, 65)]
    banded.book.orders["X1"] = working(rules.SELL, 65, price=140.0)
    assert broker().modify(change("X1", 65, 10.0), "token")[1] == 200
    assert "quote" not in banded.book.calls


def test_a_gtt_is_not_held_to_the_band(banded):
    """A GTT is priced for when it triggers, not for the market now."""
    gtt = order("BUY", symbol=PE, trigger_type="SINGLE", pricetype="LIMIT", price="500")
    assert broker().gtt_place(gtt, "token")[2] == "GTT-1"


def test_the_kill_switch_is_said_before_the_band(banded):
    banded.gate.set_state(ON)
    message = refused_message(broker().place(limit(price=500.0), "token"))
    assert message.startswith("New orders are refused: the kill switch is on")


def test_settings_that_cannot_be_read_leave_the_band_unchecked(banded, monkeypatch):
    def broken():
        raise OSError("disk")

    monkeypatch.setattr(banded.store, "settings", broken)
    assert broker().place(limit(price=500.0), "token")[2] == "OID0"


class SandboxBook:
    def __init__(self, quote):
        self.calls: list[str] = []
        self._quote = quote

    def sandbox_position(self, owner, symbol, exchange, product):
        self.calls.append("position")
        return 0

    def quote(self, pipe, owner, symbol, exchange):
        self.calls.append(f"quote {pipe} {owner}")
        return self._quote


def test_a_sandbox_order_is_held_to_the_band_too(banded, monkeypatch):
    book = SandboxBook(band.Quote(ltp=150.0, bid=149.9, ask=150.1))
    fresh = gate.Gate(store=banded.store, read=book)
    fresh.set_state(KillSwitchState())
    kind = gate._Kind(
        gate.SANDBOX, gate._sandbox_place, gate._refuse_in_the_sandbox, gate._answer_in_the_sandbox
    )
    data = {"symbol": PE, "exchange": "NFO", "product": "MIS", "action": "BUY", "quantity": 65,
            "pricetype": "LIMIT", "price": 186}  # fmt: skip
    decision = fresh.admit(kind, (SimpleNamespace(user_id="owner"), data), {})
    assert decision.refused and decision.code == gate.PRICE_BAND
    assert book.calls == ["position", "quote sandbox owner"]


# -- the reader ----------------------------------------------------------------------------------


@pytest.fixture
def upstox_quotes(monkeypatch):
    import broker.upstox.api.data as data

    asked: list[tuple] = []

    class FakeData:
        def __init__(self, auth):
            self.auth = auth

        def get_quotes(self, symbol, exchange):
            asked.append((self.auth, symbol, exchange))
            return {"ltp": 150.0, "bid": 149.9, "ask": 150.1, "volume": 10}

    monkeypatch.setattr(data, "BrokerData", FakeData)
    readers._quotes.clear()
    yield asked
    readers._quotes.clear()


def test_a_quote_is_kept_a_second_and_never_keyed_by_the_token(upstox_quotes):
    first = readers.quote("live", "secret-session", PE, "NFO")
    assert first == band.Quote(150.0, 149.9, 150.1)
    assert readers.quote("live", "secret-session", PE, "NFO") == first
    assert upstox_quotes == [("secret-session", PE, "NFO")]
    assert "secret-session" not in repr(readers._quotes.keys())


def test_a_sandbox_order_is_priced_with_the_user_s_own_upstox_session(upstox_quotes, monkeypatch):
    from sudarshan.guard import killswitch

    sessions = {"owner": ("session-1", "upstox"), "other": ("session-2", "zerodha")}

    def owner_for(user, **kwargs):
        token, broker_name = sessions[user]
        return killswitch.Owner(user, "app", user, auth_token=token, broker=broker_name)

    monkeypatch.setattr(killswitch, "owner_for", owner_for)
    assert readers.quote("sandbox", "owner", PE, "NFO") == band.Quote(150.0, 149.9, 150.1)
    assert upstox_quotes == [("session-1", PE, "NFO")]
    with pytest.raises(readers.Unreadable):
        readers.quote("sandbox", "other", PE, "NFO")


def test_a_quote_that_fails_is_unreadable(monkeypatch):
    import broker.upstox.api.data as data

    class Broken:
        def __init__(self, auth):
            pass

        def get_quotes(self, symbol, exchange):
            raise RuntimeError("Upstox said no")

    monkeypatch.setattr(data, "BrokerData", Broken)
    readers._quotes.clear()
    with pytest.raises(readers.Unreadable):
        readers.quote("live", "session", PE, "NFO")
