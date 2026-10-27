"""The exit guard: claims, notes, and the busy positions no exit is sent for (M6.9).

sudarshan/books/exit_guard.py. Upstox's position book can say the old quantity for a moment after
an exit, and every exit is sized from it, so a second exit sized in that moment closes the
position again and reverses it. A position is busy while an exit for it is being placed (claimed)
or was placed in the last ``EXIT_SHOWS_WITHIN_S`` (noted); one position's exit is refused with a
sentence while it is busy, and Exit all, holding the whole book, sends nothing for a busy position
and closes the rest at once. These pin the guard itself, and the app's routes and services over
the real Upstox module with only its broker calls faked.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import pytest

import blueprints.orders as orders
import broker.upstox.api.order_api as upstox
import utils.session as session_utils
from sudarshan.books import exit_guard
from sudarshan.books.exit_guard import EXIT_ALL_BEING_PLACED, ExitsInFlight
from sudarshan.books.single_exit import EXIT_NOT_CONFIRMED

X = "NIFTY27OCT2625900CE"
Y = "BANKNIFTY27OCT2657500PE"
EXIT = {"symbol": X, "exchange": "NFO", "product": "MIS"}
OTHER = {"symbol": Y, "exchange": "NFO", "product": "NRML"}
POSITION = ("NFO", X, "MIS")
ON_ITS_WAY = re.compile(
    r"\AAn exit for NIFTY 25900 CE is already on its way \(sent \d\d:\d\d:\d\d\)\. Check Orders, "
    r"and exit it again only if it is still open\.\Z"
)
WAIT = 10  # seconds; never reached when the code is right
REPO = Path(__file__).resolve().parents[3]


class _Clock:
    """exit_guard's own ``time``, at a moment a test chooses (never the shared module's)."""

    def __init__(self, epoch: float):
        self.epoch = epoch
        self.ticks = 1000.0

    def time(self) -> float:
        return self.epoch

    def monotonic(self) -> float:
        return self.ticks


# -- The guard itself ----------------------------------------------------------------------------


def test_one_exit_at_a_time_for_a_position_and_the_time_it_began(monkeypatch):
    clock = _Clock(1793078533.0)  # 10:52:13 IST
    monkeypatch.setattr(exit_guard, "time", clock)
    guard = ExitsInFlight()
    with guard.one("live", POSITION) as busy:
        assert busy is None
        clock.epoch += 5  # a second tap, five seconds on
        with guard.one("live", POSITION) as again:
            assert again == (
                "An exit for NIFTY 25900 CE is already on its way (sent 10:52:13). Check Orders, "
                "and exit it again only if it is still open."
            )
        assert len(guard) == 1  # the refusal took nothing
    assert len(guard) == 0
    with guard.one("live", POSITION) as busy:  # nothing noted: free once let go
        assert busy is None


def test_an_exit_noted_is_busy_for_as_long_as_the_phone_holds_it_and_no_longer(monkeypatch):
    clock = _Clock(1793078533.0)
    monkeypatch.setattr(exit_guard, "time", clock)
    guard = ExitsInFlight()
    guard.placed("live", POSITION)
    clock.ticks += exit_guard.EXIT_SHOWS_WITHIN_S
    assert guard.why_busy("live", POSITION).startswith("An exit for NIFTY 25900 CE is already")
    assert guard.busy("live") == {POSITION: guard.why_busy("live", POSITION)}
    clock.ticks += 0.01
    assert guard.why_busy("live", POSITION) is None
    assert guard.busy("live") == {}
    assert guard._placed == {}  # nothing is kept past its time: the worker never restarts


def test_an_exit_all_holds_the_book_and_never_waits():
    guard = ExitsInFlight()
    with guard.one("live", POSITION):  # an exit being placed
        started = time.monotonic()
        with guard.every("live") as busy:
            assert busy is None  # at once: the Exit all goes, and skips what is busy
            assert guard.busy("live").keys() == {POSITION}
            with guard.every("live") as second:
                assert second == EXIT_ALL_BEING_PLACED
            with guard.one("live", ("NFO", Y, "NRML")) as single:
                assert single == EXIT_ALL_BEING_PLACED
            assert guard.why_busy("live", ("NFO", Y, "NRML")) == EXIT_ALL_BEING_PLACED
        assert time.monotonic() - started < 0.5
    assert len(guard) == 0


def test_the_latest_of_a_claim_and_a_note_is_the_time_said(monkeypatch):
    clock = _Clock(1793078533.0)
    monkeypatch.setattr(exit_guard, "time", clock)
    guard = ExitsInFlight()
    guard.placed("live", POSITION)  # 10:52:13
    clock.epoch += 4
    with guard.one("live", ("NFO", Y, "NRML")):
        clock.epoch += 2
        guard._positions[("live", *POSITION)] = clock.epoch  # an exit for it began at 10:52:19
        assert "(sent 10:52:19)" in guard.busy("live")[POSITION]
        assert "(sent 10:52:19)" in guard.why_busy("live", POSITION)
        with guard.one("live", POSITION) as busy:  # what a second phone is told
            assert "(sent 10:52:19)" in busy
        assert "(sent 10:52:17)" in guard.busy("live")[("NFO", Y, "NRML")]
        del guard._positions[("live", *POSITION)]
    assert "(sent 10:52:13)" in guard.busy("live")[POSITION]
    assert "(sent 10:52:13)" in guard.why_busy("live", POSITION)


def test_the_books_are_apart():
    guard = ExitsInFlight()
    with guard.every("sandbox") as busy:
        assert busy is None
        with guard.one("live", POSITION) as live:
            assert live is None
    guard.placed("live", POSITION)
    with guard.one("sandbox", POSITION) as busy:
        assert busy is None
        with guard.every("live") as live:
            assert live is None
    assert guard.placed_lately("sandbox") == set()
    assert len(guard) == 0


def test_the_same_position_written_another_way_is_the_same():
    exit_guard.exit_placed("live", " nfo", X.lower(), "mis ")
    assert exit_guard.EXITS.placed_lately("live") == {POSITION}
    assert exit_guard.why_busy("live", "NFO", X, "MIS") is not None
    with exit_guard.one_exit("live", "nfo", f" {X.lower()} ", "Mis") as busy:
        assert ON_ITS_WAY.match(busy)


def test_a_claim_is_let_go_however_its_holder_ends():
    guard = ExitsInFlight()
    with pytest.raises(RuntimeError), guard.one("live", POSITION) as busy:
        assert busy is None
        raise RuntimeError("the broker call broke")
    with pytest.raises(RuntimeError), guard.every("live"):
        raise RuntimeError("the close-all broke")
    assert len(guard) == 0


def test_the_claim_is_held_with_the_lock_let_go():
    """Under eventlet the guard's lock is a real one: a greenlet waiting on it while another
    holds it stops the worker. The lock is held for dict work only, never while a claim is."""
    guard = ExitsInFlight()
    with guard.one("live", POSITION):
        answered = []
        thread = threading.Thread(target=lambda: answered.append(guard.why_busy("live", POSITION)))
        thread.start()
        thread.join(1.0)
        assert answered and ON_ITS_WAY.match(answered[0])


@pytest.mark.parametrize(
    ("position", "name"),
    [
        (("NFO", "NIFTY27OCT2625900CE", "MIS"), "NIFTY 25900 CE"),
        (("NFO", "BANKNIFTY27OCT2657500PE", "NRML"), "BANKNIFTY 57500 PE"),
        (("NFO", "VEDL25APR24292.5CE", "NRML"), "VEDL 292.5 CE"),
        (("NFO", "BANKNIFTY27OCT26FUT", "NRML"), "BANKNIFTY FUT"),
        (("MCX", "CRUDEOILM20MAY24FUT", "NRML"), "CRUDEOILM FUT"),
        (("NSE", "SBIN", "MIS"), "SBIN"),
        (("NSE", "M&M", "CNC"), "M&M"),
        (("NSE", "NIFTY27OCT2625900CE", "MIS"), "NIFTY27OCT2625900CE"),  # not a derivative segment
    ],
)
def test_a_position_is_named_as_the_phone_names_it(position, name):
    assert exit_guard.trader_name(position) == name


@pytest.fixture
def server_zone(monkeypatch):
    """Set the process's own time zone, as a server kept on UTC (or anywhere) has it."""
    import time as process_time

    def set_zone(name: str) -> None:
        monkeypatch.setenv("TZ", name)
        process_time.tzset()

    yield set_zone
    monkeypatch.undo()
    process_time.tzset()


@pytest.mark.parametrize("zone", ["UTC", "America/New_York", "Asia/Kolkata"])
def test_the_time_said_is_india_s_whatever_the_server_s_zone(server_zone, zone):
    server_zone(zone)
    assert exit_guard.clock(1793078533.0) == "10:52:13"


class _MeetAfterTheFirstHold:
    """A lock whose first release in each of two threads waits for the other thread's.

    What an unlucky switch between a check and its claim does, made to happen every time: both
    threads have run their first hold before either goes on. A guard whose check and claim are
    one hold still lets only one through; one that claims in a second hold lets both through.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._meet = threading.Barrier(2)
        self._local = threading.local()

    def __enter__(self):
        self._lock.acquire()
        return self

    def __exit__(self, *exc):
        self._lock.release()
        if not getattr(self._local, "met", False):
            self._local.met = True
            self._meet.wait(WAIT)
        return False


@pytest.mark.parametrize("claims", ["one", "every"])
def test_two_threads_released_together_claim_once(claims):
    """Barrier-synchronised (gthread's real threads): the check and the claim are one hold."""
    guard = ExitsInFlight()
    guard._lock = _MeetAfterTheFirstHold()
    start = threading.Barrier(2)
    seen = []

    def claim():
        start.wait(WAIT)
        held = guard.one("live", POSITION) if claims == "one" else guard.every("live")
        with held as busy:
            seen.append(busy)

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT)
    guard._lock = threading.Lock()
    assert seen.count(None) == 1
    refused = next(busy for busy in seen if busy is not None)
    assert ON_ITS_WAY.match(refused) if claims == "one" else refused == EXIT_ALL_BEING_PLACED
    assert len(guard) == 0


@pytest.mark.parametrize("run", range(3))
def test_an_exit_and_an_exit_all_released_together_never_both_send_it(run):
    """Barrier-synchronised (gthread): the exit is refused, or the Exit all finds it busy."""
    guard = ExitsInFlight()
    guard._lock = _MeetAfterTheFirstHold()
    start = threading.Barrier(2)
    seen = {}
    claimed = threading.Event()

    def exit_one():
        start.wait(WAIT)
        with guard.one("live", POSITION) as busy:
            seen["exit"] = busy
            if busy is None:
                claimed.set()
                time.sleep(0.2)  # the broker call
                guard.placed("live", POSITION)

    def exit_all():
        start.wait(WAIT)
        with guard.every("live") as busy:
            seen["exit all"] = busy
            seen["busy"] = set(guard.busy("live"))

    threads = [threading.Thread(target=exit_one), threading.Thread(target=exit_all)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT)
    guard._lock = threading.Lock()
    assert seen["exit all"] is None  # the Exit all always goes
    if seen["exit"] is None:
        assert seen["busy"] == {POSITION}  # and sends nothing for the position being exited
    else:
        assert seen["exit"] == EXIT_ALL_BEING_PLACED and seen["busy"] == set()
    assert len(guard) == 0


def test_close_all_once_refuses_a_second_at_once_and_lets_go_when_it_breaks():
    calls = []

    def close_all(key, token):
        calls.append((key, token))
        assert exit_guard.close_all_once("live", close_all, key, token) == (
            {"status": "error", "message": EXIT_ALL_BEING_PLACED},
            409,
        )
        return {"status": "success"}, 200

    assert exit_guard.close_all_once("live", close_all, "key", "token") == (
        {"status": "success"},
        200,
    )
    assert calls == [("key", "token")]

    def broken(key, token):
        raise RuntimeError("the broker module broke")

    with pytest.raises(RuntimeError):
        exit_guard.close_all_once("live", broken, "key", "token")
    assert len(exit_guard.EXITS) == 0


def test_an_exit_counts_as_placed_as_long_as_the_phone_holds_it():
    sent = (REPO / "frontend/src/sudarshan/books/sent.ts").read_text(encoding="utf-8")
    hold_ms = int(re.search(r"export const SENT_HOLD_MS = ([0-9_]+)", sent)[1].replace("_", ""))
    assert exit_guard.EXIT_SHOWS_WITHIN_S == hold_ms / 1000


def _app_request_limit_s() -> int:
    """The time nginx gives a request to the app (install/install.sh, the gunicorn location)."""
    script = (REPO / "install/install.sh").read_text(encoding="utf-8")
    block = re.search(r"location / \{\s*proxy_pass http://unix:[^}]*\}", script)
    assert block, "the app's nginx location was not found"
    return int(re.search(r"proxy_read_timeout (\d+)s;", block[0])[1])


def test_an_exit_all_ends_inside_the_time_nginx_gives_its_request():
    """Its reads (the positions with Upstox's 429 back-off, the order book cut off at its own
    limit), then EXIT_ALL_SENDS_FOR_S for sending, and the last exit's own bound (item 2)."""
    from broker.upstox.api.rate_limiter import BASE_BACKOFF, MAX_RETRIES
    from utils.broker_backpressure import BROKER_MAX_ORDER_QUEUE_WAIT_SECONDS
    from utils.httpx_client import REQUEST_TIMEOUT_SECONDS

    back_off = sum(BASE_BACKOFF * 2**attempt for attempt in range(MAX_RETRIES))
    reads = REQUEST_TIMEOUT_SECONDS + back_off + exit_guard.EXIT_ALL_ORDERS_READ_S
    last_exit = REQUEST_TIMEOUT_SECONDS + BROKER_MAX_ORDER_QUEUE_WAIT_SECONDS
    budget = reads + exit_guard.EXIT_ALL_SENDS_FOR_S + last_exit
    assert budget <= _app_request_limit_s(), budget
    assert exit_guard.EXIT_ALL_SENDS_FOR_S >= 5  # and it is not cut short in an ordinary day
    assert exit_guard.EXIT_ALL_ORDERS_READ_S <= 5  # a slow order book costs a few seconds only


def test_the_phone_s_hold_of_an_unanswered_exit_is_not_what_keeps_overlaps_safe():
    """The hold ends before a slow Exit all may: the server answers a tap after it with 409."""
    sent = (REPO / "frontend/src/sudarshan/books/sent.ts").read_text(encoding="utf-8")
    hold_ms = int(
        re.search(r"export const NO_ANSWER_HOLD_MS = ([0-9_]+)", sent)[1].replace("_", "")
    )
    assert hold_ms / 1000 < _app_request_limit_s()
    with exit_guard.EXITS.every("live"):
        assert exit_guard.why_busy("live", "NFO", X, "MIS") == EXIT_ALL_BEING_PLACED


# -- The app's routes and services, over the real Upstox module ---------------------------------

ROWS = {  # Upstox's own position rows: instrument token, exchange, product
    X: ("NSE_FO|1001", "NFO", "I"),
    Y: ("NSE_FO|1002", "NFO", "D"),
}
BY_TOKEN = {token: symbol for symbol, (token, _exchange, _product) in ROWS.items()}
API_KEY = "test-only-api-key"


class Answer:
    def __init__(self, status: int):
        self.status = status
        self.status_code = status


def _client():
    from flask import Flask

    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(orders.orders_bp)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user"] = "owner"
        sess["broker"] = "upstox"
    return client


@pytest.fixture
def broker(monkeypatch):
    """Upstox's order module over a book that never shows an exit (it lags that long), its
    orders recorded; one held at the broker until told, when asked."""
    import database.settings_db as settings_db
    import services.close_position_service as close_service
    import services.place_smart_order_service as smart_service
    from database.latency_db import init_latency_db

    init_latency_db()
    state = {
        "book": {X: 65, Y: -30},
        "orders": [],
        "hold": set(),  # symbols whose order waits at the broker until "release"
        "entered": threading.Event(),
        "release": threading.Event(),
        "answer": None,  # a fixed answer for every order, when set
    }

    def get_positions(auth):
        rows = [
            {
                "instrument_token": token,
                "exchange": exchange,
                "product": product,
                "tradingsymbol": symbol,
                "quantity": state["book"][symbol],
            }
            for symbol, (token, exchange, product) in ROWS.items()
            if symbol in state["book"]
        ]
        return {"status": "success", "data": rows}

    def place_order_api(data, auth):
        state["orders"].append((data["symbol"], data["action"], int(data["quantity"])))
        if data["symbol"] in state["hold"]:
            state["entered"].set()
            assert state["release"].wait(WAIT)
        if state["answer"] is not None:
            return state["answer"]
        orderid = f"2610270000{len(state['orders']):02d}"
        return Answer(200), {"status": "success", "data": {"order_ids": [orderid]}}, orderid

    monkeypatch.setattr(upstox, "get_positions", get_positions)
    monkeypatch.setattr(upstox, "place_order_api", place_order_api)
    monkeypatch.setattr(upstox, "get_symbol", lambda token, exchange: BY_TOKEN.get(token))
    monkeypatch.setattr(upstox, "get_br_symbol", lambda symbol, exchange: symbol)
    upstox._POSITION_BOOK.invalidate("token")
    monkeypatch.setattr(session_utils, "is_session_valid", lambda: True)
    monkeypatch.setattr(orders, "get_auth_token", lambda user: "token")
    monkeypatch.setattr(orders, "get_analyze_mode", lambda: False)
    monkeypatch.setattr(settings_db, "get_analyze_mode", lambda: False)
    monkeypatch.setattr(orders, "get_api_key_for_tradingview", lambda user: None)
    monkeypatch.setattr(close_service, "get_analyze_mode", lambda: False)
    monkeypatch.setattr(smart_service, "get_analyze_mode", lambda: False)
    monkeypatch.setattr(
        orders,
        "dynamic_import",
        lambda name, module, names: {
            "place_smartorder_api": upstox.place_smartorder_api,
            "get_open_position": upstox.get_open_position,
        },
    )
    monkeypatch.setattr(close_service, "import_broker_module", lambda name: upstox)
    monkeypatch.setattr(smart_service, "import_broker_module", lambda name: upstox)
    return state


def _post(path, body):
    response = _client().post(path, json=body)
    return response.status_code, response.get_json()


def _in_background(fn, *args):
    out = {}

    def run():
        out["result"] = fn(*args)

    thread = threading.Thread(target=run)
    thread.start()
    return thread, out


def _api_exit_all():  # TradingView, scripts, and Telegram's /closeall through the SDK
    from services.close_position_service import close_position

    _ok, body, code = close_position(
        position_data={"strategy": "TradingView"}, auth_token="token", broker="upstox"
    )
    return code, body


def _smart(position_size="0", quantity="0", action="SELL", symbol=X):
    from services.place_smart_order_service import place_smart_order

    _ok, body, code = place_smart_order(
        {
            "apikey": API_KEY,
            "strategy": "TradingView",
            "symbol": symbol,
            "exchange": "NFO",
            "action": action,
            "quantity": quantity,
            "position_size": position_size,
            "product": "MIS" if symbol == X else "NRML",
            "pricetype": "MARKET",
        },
        auth_token="token",
        broker="upstox",
    )
    return code, body


def test_a_second_exit_for_a_position_is_refused_at_once_while_the_first_is_placed(broker):
    broker["hold"].add(X)
    first, answer = _in_background(_post, "/close_position", EXIT)
    assert broker["entered"].wait(WAIT)
    started = time.monotonic()
    code, body = _post("/close_position", EXIT)
    assert time.monotonic() - started < 1.0  # not behind the first, at the broker's lock
    assert code == 409 and ON_ITS_WAY.match(body["message"]), body
    broker["release"].set()
    first.join(WAIT)
    assert answer["result"][0] == 200
    assert broker["orders"] == [(X, "SELL", 65)]
    assert len(exit_guard.EXITS) == 0


def test_after_it_was_placed_the_book_not_showing_it_yet_it_is_still_refused(broker):
    """Two phones, one after the other (the review's S3): the book still says 65."""
    assert _post("/close_position", EXIT)[0] == 200
    code, body = _post("/close_position", EXIT)
    assert code == 409 and ON_ITS_WAY.match(body["message"])
    assert broker["orders"] == [(X, "SELL", 65)]


@pytest.mark.parametrize("run", range(3))
def test_two_exits_released_together_send_one_order(broker, run):
    """Barrier-synchronised: two requests on real threads (gthread) arrive at once."""
    start = threading.Barrier(2)
    codes = []

    def post():
        start.wait(WAIT)
        codes.append(_post("/close_position", EXIT)[0])

    threads = [threading.Thread(target=post) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT)
    assert sorted(codes) == [200, 409]
    assert broker["orders"] == [(X, "SELL", 65)]


@pytest.mark.parametrize(
    "other",
    [OTHER, {**EXIT, "product": "NRML"}, {**EXIT, "exchange": "BFO"}],
    ids=["another symbol", "another product", "another exchange"],
)
def test_another_position_is_not_held_up(broker, other):
    broker["hold"].add(X)
    first, answer = _in_background(_post, "/close_position", EXIT)
    assert broker["entered"].wait(WAIT)
    assert exit_guard.why_busy("live", other["exchange"], other["symbol"], other["product"]) is None
    broker["release"].set()
    first.join(WAIT)
    assert answer["result"][0] == 200


def test_the_same_position_written_another_way_is_held(broker):
    assert _post("/close_position", EXIT)[0] == 200
    same = {"symbol": f" {X.lower()} ", "exchange": "nfo", "product": "mis"}
    assert _post("/close_position", same)[0] == 409


def test_an_exit_all_holds_off_a_single_exit_and_a_second_exit_all(broker):
    broker["hold"].add(X)
    everything, answer = _in_background(_api_exit_all)
    assert broker["entered"].wait(WAIT)
    assert _post("/close_position", OTHER) == (
        409,
        {"status": "error", "message": EXIT_ALL_BEING_PLACED},
    )
    assert _post("/close_all_positions", {}) == (
        409,
        {"status": "error", "message": EXIT_ALL_BEING_PLACED},
    )
    code, body = _smart(symbol=Y)  # TradingView's exit for another position
    assert (code, body["message"]) == (409, EXIT_ALL_BEING_PLACED)
    broker["release"].set()
    everything.join(WAIT)
    assert answer["result"][0] == 200
    assert broker["orders"] == [(X, "SELL", 65), (Y, "BUY", 30)]


def test_an_exit_all_during_an_exit_closes_the_rest_at_once(broker):
    """The phone's exit is still being placed (one slow Upstox call): an unattended Exit all
    (TradingView, Flow, a script) closes everything else now, and says why not that one."""
    broker["hold"].add(X)
    single, placed = _in_background(_post, "/close_position", EXIT)
    assert broker["entered"].wait(WAIT)
    started = time.monotonic()
    code, body = _api_exit_all()
    assert time.monotonic() - started < 1.0
    assert broker["orders"] == [(X, "SELL", 65), (Y, "BUY", 30)]
    assert code == 409 and body["not_confirmed"] is True
    assert body["message"].startswith(
        "Exit all did not close every position. Of 2 exits, 1 was sent and 1 was not sent. "
        "An exit for NIFTY 25900 CE is already on its way (sent "
    )
    broker["release"].set()
    single.join(WAIT)
    assert placed["result"][0] == 200
    assert len(exit_guard.EXITS) == 0


@pytest.mark.parametrize("surface", ["app", "api", "flow"])
def test_every_exit_all_surface_takes_the_one_claim(broker, surface, monkeypatch):
    import database.auth_db as auth_db
    import services.close_position_service as close_service

    monkeypatch.setattr(close_service, "get_auth_token_broker", lambda key: ("token", "upstox"))
    monkeypatch.setattr(auth_db, "verify_api_key", lambda key: "owner")
    monkeypatch.setattr(auth_db, "get_order_mode", lambda user: "auto")

    def send():
        if surface == "app":
            return _post("/close_all_positions", {})
        if surface == "api":
            return _api_exit_all()
        from services.flow_openalgo_client import FlowOpenAlgoClient

        result = FlowOpenAlgoClient(API_KEY).close_all_positions()
        return (200, result) if result["status"] == "success" else (result["code"], result)

    with exit_guard.EXITS.every("live"):  # another Exit all is being placed
        code, body = send()
        assert code == 409
        assert EXIT_ALL_BEING_PLACED in (body.get("message"), body.get("error"))
    assert broker["orders"] == []
    assert send()[0] == 200  # the app's own route does not claim twice and refuse itself
    assert sorted(broker["orders"]) == [(Y, "BUY", 30), (X, "SELL", 65)]
    assert len(exit_guard.EXITS) == 0


def test_a_smart_order_entry_is_never_refused(broker):
    """TradingView's entries and new sizes go through whatever is busy: nothing is sized to 0."""
    broker["book"] = {Y: -30}
    with exit_guard.EXITS.every("live"), exit_guard.EXITS.one("live", POSITION):
        exit_guard.exit_placed("live", "NFO", X, "MIS")
        assert _smart(position_size="65", quantity="65", action="BUY")[0] == 200
        assert _smart(position_size="0", quantity="65", action="BUY")[0] == 200  # flat: fresh
    assert broker["orders"] == [(X, "BUY", 65), (X, "BUY", 65)]


def test_a_smart_exit_from_the_api_sends_nothing_for_a_busy_position(broker):
    """The review's S4/S5: TradingView's exit and the app's, either way round."""
    assert _post("/close_position", EXIT)[0] == 200
    code, body = _smart()
    assert code == 409 and ON_ITS_WAY.match(body["message"])
    assert broker["orders"] == [(X, "SELL", 65)]
    # And the other way: its exit noted, an Exit all sends nothing for it.
    assert _smart(symbol=Y, action="BUY")[0] == 200
    code, body = _api_exit_all()
    assert broker["orders"] == [(X, "SELL", 65), (Y, "BUY", 30)]
    assert (code, body.get("not_confirmed")) == (409, None)


def test_an_exit_is_noted_before_its_claim_is_let_go(broker, monkeypatch):
    """An Exit all that comes the moment the exit's claim is let go must find it noted."""
    note = ExitsInFlight.placed
    noting = threading.Event()

    def slow_to_note(self, book, position):
        noting.set()
        time.sleep(0.3)
        note(self, book, position)

    monkeypatch.setattr(ExitsInFlight, "placed", slow_to_note)
    single, _ = _in_background(_post, "/close_position", EXIT)
    assert noting.wait(WAIT)
    code, _body = _api_exit_all()  # while the exit is being noted: it is still claimed
    single.join(WAIT)
    assert broker["orders"] == [(X, "SELL", 65), (Y, "BUY", 30)]
    assert code == 409


@pytest.mark.parametrize(
    ("answer", "noted"),
    [
        ((Answer(200), {"status": "success"}, "261027000001"), True),
        ((Answer(200), {"status": "success", "data": {"order_ids": []}}, None), True),
        ((upstox._ErrorResponse(500), {"status": "error", "message": "?"}, None), True),
        ((Answer(503), {"status": "error", "message": "?"}, None), True),
        ((Answer(400), {"status": "error", "message": "Insufficient funds"}, None), False),
        ((None, {"status": "error", "message": "Instrument token not found"}, None), False),
    ],
    ids=["placed", "no order id", "broke after the send", "Upstox 5xx", "refused", "never sent"],
)
def test_only_an_exit_that_may_have_placed_an_order_is_noted(broker, answer, noted):
    broker["answer"] = answer
    _post("/close_position", EXIT)
    assert (POSITION in exit_guard.EXITS.placed_lately("live")) is noted
    assert len(exit_guard.EXITS) == 0


def test_an_exit_that_breaks_at_the_broker_is_noted(broker, monkeypatch):
    def broken(data, auth):
        broker["orders"].append((data["symbol"], data["action"], int(data["quantity"])))
        raise RuntimeError("the call broke after Upstox may have taken it")

    monkeypatch.setattr(upstox, "place_order_api", broken)
    code, body = _post("/close_position", EXIT)
    assert code == 500 and body["not_confirmed"] is True
    assert POSITION in exit_guard.EXITS.placed_lately("live")


def test_the_app_s_exit_decides_its_pipe_once(broker, monkeypatch):
    """The review's LOW-3: switched to live between the route's read and the service's, the
    Analyze-mode exit used to be claimed in the sandbox's book and sent to the broker."""
    import services.order_router_service as order_router
    import services.place_smart_order_service as smart_service
    import services.sandbox_service as sandbox_service

    monkeypatch.setattr(orders, "get_analyze_mode", lambda: True)  # the route's read
    monkeypatch.setattr(smart_service, "get_analyze_mode", lambda: False)  # switched meanwhile
    monkeypatch.setattr(orders, "get_api_key_for_tradingview", lambda user: API_KEY)
    monkeypatch.setattr(order_router, "should_route_to_pending", lambda key, kind: False)
    monkeypatch.setattr(smart_service, "get_auth_token_broker", lambda key: ("token", "upstox"))
    claimed = []

    def sandbox(order_data, api_key, original_data):
        claimed.append(exit_guard.why_busy("sandbox", "NFO", X, "MIS"))
        return True, {"status": "success", "orderid": "SB1", "mode": "analyze"}, 200

    monkeypatch.setattr(sandbox_service, "sandbox_place_smart_order", sandbox)
    assert _post("/close_position", EXIT)[0] == 200
    assert broker["orders"] == []  # never to the broker
    assert len(claimed) == 1 and ON_ITS_WAY.match(claimed[0])  # held in the sandbox's book
    assert len(exit_guard.EXITS) == 0


def test_an_exit_in_the_sandbox_is_held_in_the_sandbox_s_book(broker, monkeypatch):
    monkeypatch.setattr(orders, "get_analyze_mode", lambda: True)
    monkeypatch.setattr(orders, "get_api_key_for_tradingview", lambda user: API_KEY)
    with exit_guard.one_exit("sandbox", "NFO", X, "MIS"):
        code, body = _post("/close_position", EXIT)
    assert code == 409 and ON_ITS_WAY.match(body["message"])
    assert exit_guard.EXITS.placed_lately("sandbox") == set()  # the sandbox's book never lags


# -- Flow's "Close Positions" node naming a position (item 10) ------------------------------------


def _flow_close(symbol=X, product="MIS"):
    from services.close_position_service import close_position

    ok, body, code = close_position(
        position_data={
            "strategy": "flow_workflow",
            "symbol": symbol,
            "exchange": "NFO",
            "product": product,
        },
        auth_token="token",
        broker="upstox",
    )
    return code, body


def test_flow_s_close_node_with_a_symbol_closes_that_position_only_live(broker):
    """Upstream closed every open position here, whatever symbol the node named."""
    code, body = _flow_close()
    assert (code, body["status"], body["orderid"]) == (200, "success", "261027000001")
    assert broker["orders"] == [(X, "SELL", 65)]
    code, body = _flow_close()  # the same node, again, before the book shows the first
    assert code == 409 and ON_ITS_WAY.match(body["message"])
    assert broker["orders"] == [(X, "SELL", 65)]
    assert _flow_close(symbol="NIFTY27OCT2625800CE")[0] == 400  # nothing to close: nothing sent
    assert broker["orders"] == [(X, "SELL", 65)]


def test_flow_s_close_of_one_position_that_breaks_is_not_confirmed(broker, monkeypatch):
    """The second review's C3: whatever breaks once the exit is on its way to the broker may
    have placed it, so the node is told "not confirmed" (a 500), never a refusal (a 4xx)."""
    import services.close_position_service as close_service

    def broken(data, auth):
        raise RuntimeError("the broker module broke")

    module = type("Broker", (), {"place_smartorder_api": staticmethod(broken)})
    monkeypatch.setattr(close_service, "import_broker_module", lambda name: module)
    code, body = _flow_close()
    assert code == 500
    assert body == {"status": "error", "message": EXIT_NOT_CONFIRMED, "not_confirmed": True}
    assert broker["orders"] == []


def test_flow_s_close_node_with_a_symbol_closes_that_position_only_in_the_sandbox(
    broker, monkeypatch
):
    import services.close_position_service as close_service
    import services.sandbox_service as sandbox_service

    monkeypatch.setattr(close_service, "get_analyze_mode", lambda: True)
    closed = []

    def sandbox_close_position(close_data, api_key, original_data):
        closed.append((dict(close_data), exit_guard.why_busy("sandbox", "NFO", X, "MIS")))
        return True, {"status": "success", "message": "Position closed", "mode": "analyze"}, 200

    monkeypatch.setattr(sandbox_service, "sandbox_close_position", sandbox_close_position)
    from services.flow_openalgo_client import FlowOpenAlgoClient

    monkeypatch.setattr(close_service, "get_auth_token_broker", lambda key: ("token", "upstox"))
    result = FlowOpenAlgoClient(API_KEY).close_position(X, "NFO", "MIS")
    assert result["status"] == "success"
    [(close_data, held)] = closed
    assert close_data == {"symbol": X, "exchange": "NFO", "product": "MIS"}
    assert ON_ITS_WAY.match(held)  # held in the sandbox's book while it closes
    assert broker["orders"] == []
    with exit_guard.one_exit("sandbox", "NFO", X, "MIS"):
        result = FlowOpenAlgoClient(API_KEY).close_position(X, "NFO", "MIS")
    assert (result["status"], result["code"]) == ("error", 409)
    assert len(closed) == 1
