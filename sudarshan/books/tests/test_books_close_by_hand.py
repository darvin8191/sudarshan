"""v0.15.0 verify V1b: a refusal that sends the trader to the Upstox app while SUDARSHAN may still
send the exit itself says to close there with a market order, never a limit order left waiting.

The books count only a MARKET order as an exit on its way (``surface_exits.sized``). A strategy
run keeps a refused leg managed and tries its exit again (its stop, target, square-off and a
pending stop), so a LIMIT close left resting in the app was not seen, SUDARSHAN's market exit
went, and both filled: the position was reversed. Fix round 2 said so in the held-exit messages
(``held_exits``); these are the books' own refusal sentences, shown as the refusal of a Stop, a
Close leg or an exit alert. The same sentences answer surfaces that send nothing more after a
refusal (the app's Exit, Exit all, a scalping close), so the retrying caller gets its own words
and the shared sentence stays as it is.
"""

from __future__ import annotations

import pytest

# restx_api first (place_order_service's import cycle), and at collection: pytest puts sudarshan/
# first on the import path when it sets up the books package, and an import after that would find
# sudarshan/portfolio instead of the repository's own portfolio package.
import restx_api  # noqa: F401
from sudarshan.books import exit_guard, held_exits, surface_exits

X = "NIFTY27OCT2625900CE"
POSITION = ("NFO", X, "MIS")
#: What the retrying caller's words end with: the held-exit messages' own sentence (fix round 2).
HOW = (
    "If you want out now, close it in the Upstox app with a market order; SUDARSHAN then sends "
    "nothing. Do not leave a limit order waiting there: SUDARSHAN's exit can still go, and both "
    "would fill."
)

# The shared words, left as they are for the app's Exit, Exit all and a scalping close.
POSITIONS_NOT_READ = (
    "Upstox did not send your positions in time, so SUDARSHAN could not confirm how much you hold "
    "in NIFTY 25900 CE and sent no exit. Close it from the Upstox app, or try again in a minute."
)
ORDERS_NOT_READ = (
    "Upstox did not send your orders in time, so SUDARSHAN could not confirm how much you hold in "
    "NIFTY 25900 CE and sent no exit. Close it from the Upstox app, or try again in a minute."
)
NOT_ADDING_UP = (
    "SUDARSHAN could not work out how much you hold in NIFTY 25900 CE from Upstox's positions and "
    "orders, and sent no exit. Close it from the Upstox app, or check Positions and Orders and try "
    "again in a minute."
)
EARLIER_NOT_CONFIRMED = (
    "The earlier exit for NIFTY 25900 CE (sent {at}) is not confirmed: it may have gone through, "
    "and Upstox's positions can take a minute to show it, so no exit was sent. Check Orders: if "
    "that exit is not there and the position is still open, close it from the Upstox app."
)

# The retrying caller's own words: the final wording of each changed sentence.
POSITIONS_NOT_READ_TRIED = (
    "Upstox did not send your positions in time, so SUDARSHAN could not confirm how much you hold "
    "in NIFTY 25900 CE and sent no exit. Try again in a minute. If you want out now, close it in "
    "the Upstox app with a market order; SUDARSHAN then sends nothing. Do not leave a limit order "
    "waiting there: SUDARSHAN's exit can still go, and both would fill."
)
ORDERS_NOT_READ_TRIED = (
    "Upstox did not send your orders in time, so SUDARSHAN could not confirm how much you hold in "
    "NIFTY 25900 CE and sent no exit. Try again in a minute. If you want out now, close it in the "
    "Upstox app with a market order; SUDARSHAN then sends nothing. Do not leave a limit order "
    "waiting there: SUDARSHAN's exit can still go, and both would fill."
)
NOT_ADDING_UP_TRIED = (
    "SUDARSHAN could not work out how much you hold in NIFTY 25900 CE from Upstox's positions and "
    "orders, and sent no exit. Check Positions and Orders and try again in a minute. If you want "
    "out now, close it in the Upstox app with a market order; SUDARSHAN then sends nothing. Do not "
    "leave a limit order waiting there: SUDARSHAN's exit can still go, and both would fill."
)
EARLIER_NOT_CONFIRMED_TRIED = (
    "The earlier exit for NIFTY 25900 CE (sent {at}) is not confirmed: it may have gone through, "
    "and Upstox's positions can take a minute to show it, so no exit was sent. Check Orders: if "
    "that exit is not there and the position is still open, close it in the Upstox app with a "
    "market order; SUDARSHAN then sends nothing. Do not leave a limit order waiting there: "
    "SUDARSHAN's exit can still go, and both would fill."
)

AT = 1_793_000_000.0  # an epoch moment; the sentences show it as IST


def _shared_and_tried():
    at = exit_guard.clock(AT)
    return [
        (exit_guard.positions_not_read(POSITION), POSITIONS_NOT_READ, POSITIONS_NOT_READ_TRIED),
        (exit_guard.exit_not_sized(POSITION, False), ORDERS_NOT_READ, ORDERS_NOT_READ_TRIED),
        (exit_guard.exit_not_sized(POSITION, True), NOT_ADDING_UP, NOT_ADDING_UP_TRIED),
        (
            exit_guard.close_not_confirmed(POSITION, AT),
            EARLIER_NOT_CONFIRMED.format(at=at),
            EARLIER_NOT_CONFIRMED_TRIED.format(at=at),
        ),
    ]


# ---- the words ---------------------------------------------------------------------------------
def test_the_shared_sentences_are_left_word_for_word():
    """The app's Exit, Exit all and a scalping close with no stop set send nothing more after
    them, so the caveat would be untrue there; the kill switch also matches ``exit_not_sized``
    word for word (sudarshan/guard/killswitch.py)."""
    for built, shared, _tried in _shared_and_tried():
        assert built == shared


def test_each_changed_sentence_in_the_retrying_caller_s_words():
    for built, _shared, tried in _shared_and_tried():
        assert exit_guard.while_tried(built) == tried
        assert "with a market order; SUDARSHAN then sends nothing" in tried
        assert "Do not leave a limit order waiting there" in tried
        assert "Close it from the Upstox app" not in tried
        assert exit_guard.while_tried(tried) == tried  # said once, never twice


def test_the_held_exit_messages_and_the_books_say_it_in_one_sentence():
    assert exit_guard.CLOSE_BY_HAND == HOW == held_exits.CLOSE_IN_UPSTOX
    assert POSITIONS_NOT_READ_TRIED.endswith(HOW) and NOT_ADDING_UP_TRIED.endswith(HOW)


def _left():
    working = exit_guard.WorkingOrder("SELL", 65, "MARKET", order_id="261027000001")
    return {
        # the run's exit goes only in the run's product, where nothing is held: a limit order
        # left for the other product cannot fill beside it
        "held_in_another_product": exit_guard.held_in_another_product(POSITION, {"NRML": 65}),
        # an order that reduces or crosses (TradingView, a script): sent once, says to check
        "not_sized_order": exit_guard.exit_not_sized(POSITION, False, order=True),
        "not_adding_up_order": exit_guard.exit_not_sized(POSITION, True, order=True),
        # Exit all: it leaves out a position with an order of any kind working on its closing
        # side, so a limit order left in the app stops it, and it is not tried again otherwise
        "unlisted_not_sized": exit_guard.unlisted_not_sized(POSITION),
        "unlisted_in_another_product": exit_guard.unlisted_in_another_product(POSITION, ["NRML"]),
        # not sent to the app at all
        "on_its_way": exit_guard.on_its_way(POSITION, AT),
        "first_exit_unconfirmed": exit_guard.first_exit_unconfirmed(POSITION, AT),
        "close_not_confirmed_order": exit_guard.close_not_confirmed(POSITION, AT, order=True),
        "exit_working": exit_guard.exit_working(POSITION, AT, ("261027000001",)),
        "market_exit_working": exit_guard.market_exit_working(POSITION, [working]),
        "nothing_left": exit_guard.nothing_left_to_close(POSITION),
        "other_way": exit_guard.nothing_left_to_close(POSITION, -65),
        "exit_all_busy": exit_guard.EXIT_ALL_BEING_PLACED,
    }


@pytest.mark.parametrize("name", list(_left()))
def test_every_other_sentence_comes_back_as_it_came(name):
    said = _left()[name]
    assert exit_guard.while_tried(said) == said


# ---- through exit_once -------------------------------------------------------------------------
def _close(book, *, tries_again=False, sent=None, side="SELL"):
    sent = [] if sent is None else sent

    def send():
        sent.append(1)
        return True, {"status": "success", "orderid": "261027000009"}, 200

    answer = surface_exits.exit_once(
        "live", *POSITION, send, side=side, quantity="65",
        read_book=lambda *a: book, resize=lambda q: None, tries_again=tries_again,
    )  # fmt: skip
    return answer, sent


SIZE_REFUSALS = [
    (surface_exits.Book(positions_read=False, orders_read=False), POSITIONS_NOT_READ),
    (surface_exits.Book(orders_read=False), ORDERS_NOT_READ),
    (surface_exits.Book(held=None), NOT_ADDING_UP),
]
TRIED = {
    POSITIONS_NOT_READ: POSITIONS_NOT_READ_TRIED,
    ORDERS_NOT_READ: ORDERS_NOT_READ_TRIED,
    NOT_ADDING_UP: NOT_ADDING_UP_TRIED,
}


@pytest.mark.parametrize(("book", "shared"), SIZE_REFUSALS)
def test_a_size_refusal_is_said_in_the_words_of_the_surface_it_goes_to(book, shared):
    (ok, body, status), sent = _close(book)
    assert (ok, status, sent, body["message"]) == (False, 424, [], shared)
    surface_exits.REFUSALS.clear()
    (ok, body, status), sent = _close(book, tries_again=True)
    assert (ok, status, sent, body["message"]) == (False, 424, [], TRIED[shared])
    assert body["why"] == "unconfirmed" and body[surface_exits.HELD] is True


def test_the_memory_of_a_refusal_keeps_the_shared_words_for_every_surface():
    """A strategy run's refusal kept 2 s is read by a scalping close of the same position, and
    the other way round: each gets its own words, and the memory holds the shared ones."""
    sent = []
    (_ok, run_body, _s), _ = _close(surface_exits.Book(orders_read=False), tries_again=True)
    assert run_body["message"] == ORDERS_NOT_READ_TRIED
    (_ok, scalping_body, _s), _ = _close(surface_exits.Book(held=65), sent=sent)  # from memory
    assert scalping_body["message"] == ORDERS_NOT_READ and sent == []
    kept = surface_exits.REFUSALS.get(("live", POSITION, "SELL"))
    assert kept[1]["message"] == ORDERS_NOT_READ
    assert surface_exits.kept_refusal("live", *POSITION, "SELL")[1]["message"] == ORDERS_NOT_READ
    tried = surface_exits.kept_refusal("live", *POSITION, "SELL", tries_again=True)
    assert tried[1]["message"] == ORDERS_NOT_READ_TRIED
    assert tried[1]["why"] == "unconfirmed" and tried[2] == 424  # only the words differ

    surface_exits.REFUSALS.clear()
    _close(surface_exits.Book(held=None))  # the scalping close's refusal is kept this time
    (_ok, run_body, _s), _ = _close(surface_exits.Book(held=65), tries_again=True, sent=sent)
    assert run_body["message"] == NOT_ADDING_UP_TRIED and sent == []


def test_an_earlier_close_not_confirmed_is_said_in_the_words_of_the_surface_it_goes_to():
    """A close whose answer named no order (a timeout) holds the position; the next exit inside
    the hold is refused with ``close_not_confirmed``, which sends him to the app."""

    def timed_out():
        return False, {"status": "error", "message": "The read operation timed out"}, 502

    surface_exits.exit_once(
        "live", *POSITION, timed_out, side="SELL", quantity="65",
        read_book=lambda *a: surface_exits.Book(held=65), resize=lambda q: None,
    )  # fmt: skip
    for tries_again in (False, True):
        (ok, body, status), sent = _close(surface_exits.Book(held=65), tries_again=tries_again)
        assert (ok, status, sent, body["why"]) == (False, 409, [], "earlier")
        said = body["message"]
        assert said.startswith("The earlier exit for NIFTY 25900 CE (sent ")
        if tries_again:
            assert said.endswith(EARLIER_NOT_CONFIRMED_TRIED.split("so no exit was sent. ")[1])
        else:
            assert said.endswith(EARLIER_NOT_CONFIRMED.split("so no exit was sent. ")[1])


@pytest.mark.parametrize(
    ("book", "why"),
    [
        (surface_exits.Book(held=0), "no_position"),
        (surface_exits.Book(held=-65), "other_way"),
        (surface_exits.Book(held=0, elsewhere={"NRML": 65}), "elsewhere"),
    ],
)
def test_a_refusal_that_sends_nobody_to_the_app_is_the_same_for_every_surface(book, why):
    (_ok, shared, _s), _ = _close(book)
    surface_exits.REFUSALS.clear()
    (_ok, tried, _s), _ = _close(book, tries_again=True)
    assert shared == tried and shared["why"] == why


def test_only_a_refusal_of_exit_once_s_own_is_worded_again():
    """A broker's or the risk gate's refusal, a sent order and a non-dict answer pass as they
    came, whatever their words."""
    foreign = (False, {"status": "error", "message": ORDERS_NOT_READ}, 403)
    sent = (True, {"status": "success", "orderid": "1"}, 200)
    for answer in (foreign, sent, (False, None, 500)):
        assert surface_exits.worded(answer, True) == answer
    held = surface_exits._held_answer(ORDERS_NOT_READ, 424, "unconfirmed")
    assert surface_exits.worded(held) == held
    assert surface_exits.worded(held, True)[1]["message"] == ORDERS_NOT_READ_TRIED
    assert held[1]["message"] == ORDERS_NOT_READ  # never changed in place


def test_the_sandbox_answers_as_it_always_did():
    answer = surface_exits.exit_once(
        "sandbox", *POSITION, lambda: (True, {"orderid": "SB"}, 200), side="SELL",
        quantity="65", tries_again=True,
    )  # fmt: skip
    assert answer == (True, {"orderid": "SB"}, 200)


# ---- the strategy module, through its own dispatch ---------------------------------------------
@pytest.fixture
def dispatch(monkeypatch):
    import services.place_order_service as place_order_service
    from services.strategy_module import order_dispatch as od

    sent = []

    def place_order_with_auth(order, auth_token, broker, original, force_live=False):
        sent.append(order["quantity"])
        return True, {"status": "success", "orderid": "261027000001"}, 200

    monkeypatch.setattr(od, "resolve_live_auth", lambda api_key: ("tok", "upstox", None))
    monkeypatch.setattr(place_order_service, "place_order_with_auth", place_order_with_auth)
    od.sent = sent
    yield od
    del od.sent


def _exit_order():
    return {"symbol": X, "exchange": "NFO", "action": "SELL", "quantity": "65", "product": "MIS",
            "pricetype": "MARKET", "price": "0", "trigger_price": "0", "strategy": "S"}  # fmt: skip


@pytest.mark.parametrize(("book", "shared"), SIZE_REFUSALS)
def test_a_strategy_exit_held_for_its_size_is_refused_in_the_run_s_words(
    dispatch, monkeypatch, book, shared
):
    monkeypatch.setattr(surface_exits, "upstox_book", lambda *a: book)
    result = dispatch.dispatch_order(mode="live", api_key="k", order=_exit_order(), closes=True)
    assert (result.ok, result.held, dispatch.sent) == (False, True, [])
    assert result.error == TRIED[shared]
    # the answer repeated from the 2 s memory, before the run writes an order row: the same
    kept = dispatch.kept_hold("live", _exit_order())
    assert (kept.held, kept.error) == (True, TRIED[shared])
    # and the memory itself still holds the shared words, for a scalping close of it
    assert surface_exits.kept_refusal("live", *POSITION, "SELL")[1]["message"] == shared


# ---- the scalping terminal ---------------------------------------------------------------------
# A refused Close sends nothing more by itself. With a stop loss or target set on the position,
# the server's risk monitor (services/scalping_risk_monitor_service.py) sends its own exit when
# one is reached and tries again every few seconds while it stays due: a limit order left in the
# app could then fill beside it, so only then does the close get the retrying caller's words.
MONITOR_KEY = f"NFO:{X}:MIS"


@pytest.fixture
def scalping(monkeypatch):
    import blueprints.scalping as scalping
    from services import scalping_risk_monitor_service as monitor

    sent = []

    def place_order(order_data, api_key, auth_token, broker):
        sent.append(int(order_data["quantity"]))
        return True, {"status": "success", "orderid": "261027000001"}, 200

    monkeypatch.setattr("services.place_order_service.place_order", place_order)
    monkeypatch.setattr(scalping, "_is_derivative", lambda exchange: False)  # no symbol master
    monkeypatch.setattr(scalping, "_current_mode", lambda: "live")
    monkeypatch.setattr(
        surface_exits, "upstox_book", lambda *a: surface_exits.Book(orders_read=False)
    )
    states: dict[str, dict] = {}
    monkeypatch.setattr(monitor.get_scalping_risk_monitor(), "_states", states)
    scalping.sent, scalping.stops = sent, states
    yield scalping
    del scalping.sent, scalping.stops


def _scalping_close(scalping):
    return scalping._reducing_exit(X, "NFO", "MIS", "SELL", 65, "tok", "upstox", "key")


def test_a_scalping_close_with_no_stop_set_keeps_the_shared_words(scalping):
    ok, body, status = _scalping_close(scalping)
    assert (ok, status, scalping.sent, body["message"]) == (False, 424, [], ORDERS_NOT_READ)


@pytest.mark.parametrize(
    "stop",
    [{"initial_sl": 90.0}, {"current_sl": 95.5, "trailing_enabled": True}, {"target": 150.0}],
)
def test_a_scalping_close_with_a_stop_or_target_set_says_how_to_close_by_hand(scalping, stop):
    scalping.stops[MONITOR_KEY] = {"symbol": X, "exchange": "NFO", "product": "MIS",
                                   "mode": "live", **stop}  # fmt: skip
    ok, body, status = _scalping_close(scalping)
    assert (ok, status, scalping.sent, body["message"]) == (False, 424, [], ORDERS_NOT_READ_TRIED)


@pytest.mark.parametrize(
    ("key", "stop"),
    [
        (MONITOR_KEY, {"mode": "analyze", "initial_sl": 90.0}),  # the sandbox's stop
        ("NFO:NIFTY27OCT2626000CE:MIS", {"mode": "live", "initial_sl": 90.0}),  # another one
        (f"NFO:{X}:NRML", {"mode": "live", "initial_sl": 90.0}),  # another product
        (MONITOR_KEY, {"mode": "live", "target": 0}),  # nothing set
    ],
)
def test_a_stop_that_cannot_send_this_exit_changes_nothing(scalping, key, stop):
    scalping.stops[key] = stop
    _ok, body, _status = _scalping_close(scalping)
    assert body["message"] == ORDERS_NOT_READ


def test_a_scalping_close_that_goes_is_answered_as_before_with_a_stop_set(scalping, monkeypatch):
    scalping.stops[MONITOR_KEY] = {"mode": "live", "initial_sl": 90.0}
    monkeypatch.setattr(surface_exits, "upstox_book", lambda *a: surface_exits.Book(held=65))
    assert _scalping_close(scalping) == (
        True,
        {"status": "success", "orderid": "261027000001"},
        200,
    )
    assert scalping.sent == [65]


def test_the_monitor_s_answer_never_breaks_a_close(scalping, monkeypatch):
    from services import scalping_risk_monitor_service as monitor

    def broken(*args):
        raise RuntimeError("monitor gone")

    monkeypatch.setattr(monitor.get_scalping_risk_monitor(), "watches", broken)
    _ok, body, status = _scalping_close(scalping)
    assert (status, body["message"]) == (424, ORDERS_NOT_READ)
