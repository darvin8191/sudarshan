"""One exit at a time per position from the app's own screens (M6.9, sudarshan/books/exit_guard.py).

The phone gives up on an exit after 45 s; the server goes on placing it. A second exit sent
meanwhile is sized from a book that does not show the first fill, and reverses the position. The
app's own close routes therefore refuse a second request with 409 and a sentence while the first
for the same position, or any Exit all, is still running in this process.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import pytest

import blueprints.orders as orders
import utils.session as session_utils
from sudarshan.books import exit_guard
from sudarshan.books.exit_guard import (
    AN_EXIT_BEING_PLACED,
    EXIT_ALL_BEING_PLACED,
    EXIT_BEING_PLACED,
    EXIT_NOT_SHOWN_YET,
    ExitsInFlight,
)

EXIT = {"symbol": "NIFTY27OCT2625900CE", "exchange": "NFO", "product": "MIS"}
WAIT = 10  # seconds; never reached when the code is right


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
def routes(monkeypatch):
    """The live close routes, each with its own test client; the broker held until told."""
    import database.settings_db as settings_db
    import services.close_position_service as close_service
    import services.positionbook_service as positionbook_service
    from database.latency_db import init_latency_db

    init_latency_db()
    monkeypatch.setattr(session_utils, "is_session_valid", lambda: True)
    monkeypatch.setattr(orders, "get_auth_token", lambda user: "token")
    monkeypatch.setattr(orders, "get_analyze_mode", lambda: False)
    monkeypatch.setattr(settings_db, "get_analyze_mode", lambda: False)
    monkeypatch.setattr(orders, "get_api_key_for_tradingview", lambda user: None)
    monkeypatch.setattr(close_service, "get_analyze_mode", lambda: False)
    monkeypatch.setattr(exit_guard, "EXITS", ExitsInFlight())

    gate = {
        "entered": threading.Event(),
        "release": threading.Event(),
        "placed": [],
        "inside": 0,  # single exits inside the broker call right now
        "overlaps": [],  # how many were, each time an Exit all reached the broker
        # The live position book's rows, or a function of how many reads there have been.
        "book": [],
        "book_reads": 0,
        "reads_before_close": [],  # book reads, each time an Exit all reached the broker
    }

    def get_positionbook_with_auth(auth_token, broker, original_data=None):
        assert (auth_token, broker, original_data) == (
            "token",
            "upstox",
            None,
        )  # live, never sandbox
        gate["book_reads"] += 1
        book = gate["book"]
        rows = book(gate["book_reads"]) if callable(book) else book
        return True, {"status": "success", "data": rows}, 200

    monkeypatch.setattr(
        positionbook_service, "get_positionbook_with_auth", get_positionbook_with_auth
    )
    monkeypatch.setattr(exit_guard, "EXIT_SHOWS_POLL_S", 0.05)

    def place_smartorder_api(order_data, auth_token):
        gate["inside"] += 1
        try:
            gate["placed"].append(order_data["symbol"])
            if len(gate["placed"]) == 1:  # the first is held until told; others go straight on
                gate["entered"].set()
                assert gate["release"].wait(WAIT)
            return Answer(200), {"status": "success"}, f"25102100000{len(gate['placed'])}"
        finally:
            gate["inside"] -= 1

    def close_all_positions(api_key, auth):
        gate["overlaps"].append(gate["inside"])
        gate["reads_before_close"].append(gate["book_reads"])
        gate["placed"].append("ALL")
        gate["entered"].set()
        assert gate["release"].wait(WAIT)
        return {"status": "success", "message": "All Open Positions SquaredOff"}, 200

    monkeypatch.setattr(
        orders,
        "dynamic_import",
        lambda broker, module, names: {
            "place_smartorder_api": place_smartorder_api,
            "get_open_position": lambda *args: "0",
        },
    )
    monkeypatch.setattr(
        close_service,
        "import_broker_module",
        lambda broker: type(
            "Upstox", (), {"close_all_positions": staticmethod(close_all_positions)}
        ),
    )
    return gate


def _post_in_background(path, body):
    out = {}

    def run():
        response = _client().post(path, json=body)
        out["code"], out["body"] = response.status_code, response.get_json()

    thread = threading.Thread(target=run)
    thread.start()
    return thread, out


def _in_background(fn):
    out = {}

    def run():
        out["result"] = fn()

    thread = threading.Thread(target=run)
    thread.start()
    return thread, out


def test_two_overlapping_exits_for_one_position_give_one_order_and_one_409(routes):
    first, answer = _post_in_background("/close_position", EXIT)
    assert routes["entered"].wait(WAIT)

    second = _client().post("/close_position", json=EXIT)
    assert second.status_code == 409
    assert second.get_json() == {"status": "error", "message": EXIT_BEING_PLACED}

    routes["release"].set()
    first.join(WAIT)
    assert answer["code"] == 200 and answer["body"]["orderid"]
    assert routes["placed"] == [EXIT["symbol"]]
    assert len(exit_guard.EXITS) == 0


def test_two_exits_released_together_give_one_order_and_one_409(routes):
    """Barrier-synchronised: two requests on real threads (gthread) arrive at once."""
    start = threading.Barrier(2)
    codes = []

    def post():
        start.wait(WAIT)
        codes.append(_client().post("/close_position", json=EXIT).status_code)

    threads = [threading.Thread(target=post) for _ in range(2)]
    for thread in threads:
        thread.start()
    assert routes["entered"].wait(WAIT)  # one is placing, held at the broker
    deadline = time.monotonic() + WAIT
    while not codes and time.monotonic() < deadline:  # the other has its answer
        time.sleep(0.01)
    routes["release"].set()
    for thread in threads:
        thread.join(WAIT)
    assert sorted(codes) == [200, 409]
    assert routes["placed"] == [EXIT["symbol"]]


def test_a_refusal_takes_nothing_from_the_exit_it_was_refused_for(routes):
    first, answer = _post_in_background("/close_position", EXIT)
    assert routes["entered"].wait(WAIT)
    for _ in range(2):  # the second refusal shows the first left the claim alone
        assert _client().post("/close_position", json=EXIT).status_code == 409
    routes["release"].set()
    first.join(WAIT)
    assert answer["code"] == 200
    assert routes["placed"] == [EXIT["symbol"]]


@pytest.mark.parametrize(
    "other",
    [
        {**EXIT, "symbol": "BANKNIFTY27OCT2657500PE"},
        {**EXIT, "product": "NRML"},
        {**EXIT, "exchange": "BFO"},
    ],
    ids=["another symbol", "another product", "another exchange"],
)
def test_another_position_is_not_held_up(routes, other):
    first, answer = _post_in_background("/close_position", EXIT)
    assert routes["entered"].wait(WAIT)
    # While the first is still being placed.
    assert _client().post("/close_position", json=other).status_code == 200
    routes["release"].set()
    first.join(WAIT)
    assert answer["code"] == 200
    assert routes["placed"] == [EXIT["symbol"], other["symbol"]]


def test_the_same_position_written_another_way_is_held(routes):
    first, answer = _post_in_background("/close_position", EXIT)
    assert routes["entered"].wait(WAIT)
    same = {"symbol": " nifty27oct2625900ce ", "exchange": "nfo", "product": "mis"}
    assert _client().post("/close_position", json=same).status_code == 409
    routes["release"].set()
    first.join(WAIT)
    assert routes["placed"] == [EXIT["symbol"]]


def test_exit_all_and_a_single_exit_hold_each_other_off(routes):
    everything, answer = _post_in_background("/close_all_positions", {})
    assert routes["entered"].wait(WAIT)

    single = _client().post("/close_position", json=EXIT)
    assert single.status_code == 409
    assert single.get_json()["message"] == EXIT_ALL_BEING_PLACED
    again = _client().post("/close_all_positions", json={})
    assert again.status_code == 409
    assert again.get_json()["message"] == EXIT_ALL_BEING_PLACED
    # The refused Exit all took nothing from the one being placed.
    assert _client().post("/close_position", json=EXIT).status_code == 409

    routes["release"].set()
    everything.join(WAIT)
    assert answer["code"] == 200
    assert routes["placed"] == ["ALL"]


def test_exit_all_waits_for_a_single_exit_being_placed(routes):
    """The exit in flight ends first, then Exit all closes the rest; no new exit starts meanwhile."""
    single, answer = _post_in_background("/close_position", EXIT)
    assert routes["entered"].wait(WAIT)
    everything, closed = _post_in_background("/close_all_positions", {})
    time.sleep(0.3)
    assert closed == {} and routes["placed"] == [EXIT["symbol"]]  # waiting; nothing sent yet

    other = _client().post("/close_position", json={**EXIT, "symbol": "BANKNIFTY27OCT2657500PE"})
    assert (other.status_code, other.get_json()["message"]) == (409, EXIT_ALL_BEING_PLACED)
    again = _client().post("/close_all_positions", json={})
    assert (again.status_code, again.get_json()["message"]) == (409, EXIT_ALL_BEING_PLACED)

    routes["release"].set()
    single.join(WAIT)
    everything.join(WAIT)
    assert (answer["code"], closed["code"]) == (200, 200)
    assert routes["placed"] == [EXIT["symbol"], "ALL"]
    assert routes["overlaps"] == [0]  # Exit all reached the broker only after the exit ended
    assert len(exit_guard.EXITS) == 0


def test_the_claim_is_let_go_however_the_view_ends(routes, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("broker module broke")

    monkeypatch.setattr(orders, "dynamic_import", broken)
    assert _client().post("/close_position", json=EXIT).status_code == 500
    assert len(exit_guard.EXITS) == 0
    # A refusal answered by the guard itself takes no claim either.
    with exit_guard.EXITS.one("live", ("NFO", EXIT["symbol"], "MIS")) as busy:
        assert busy is None
        assert _client().post("/close_position", json=EXIT).status_code == 409
    assert len(exit_guard.EXITS) == 0


def test_a_view_that_raises_lets_its_claim_go(monkeypatch):
    from flask import Flask

    monkeypatch.setattr(exit_guard, "EXITS", ExitsInFlight())
    monkeypatch.setattr(exit_guard, "_book", lambda: "live")
    app = Flask(__name__)

    @app.post("/boom")
    @exit_guard.one_exit_at_a_time()
    def boom():
        raise RuntimeError("the view broke")

    assert app.test_client().post("/boom", json=EXIT).status_code == 500
    assert len(exit_guard.EXITS) == 0
    # It may have broken after the order went: an Exit all looks for it in the book first.
    assert exit_guard.EXITS.placed_lately("live") == {("NFO", EXIT["symbol"], "MIS")}


def test_a_close_all_that_raises_lets_its_claim_go(monkeypatch):
    monkeypatch.setattr(exit_guard, "EXITS", ExitsInFlight())

    def broken(api_key, auth):
        raise RuntimeError("the broker module broke")

    with pytest.raises(RuntimeError):
        exit_guard.close_all_once("live", broken, "key", "token")
    assert len(exit_guard.EXITS) == 0


def test_the_books_are_apart():
    guard = ExitsInFlight()
    position = ("NFO", EXIT["symbol"], "MIS")
    with guard.every("sandbox") as busy:
        assert busy is None
        with guard.one("live", position) as live:
            assert live is None
    with guard.one("sandbox", position) as busy:
        assert busy is None
        with guard.every("live") as live:
            assert live is None
        with guard.one("live", position) as live:
            assert live is None
    assert len(guard) == 0


def _analyze_mode_unreadable():
    raise RuntimeError("settings could not be read")


@pytest.mark.parametrize(
    ("analyze_mode", "book"),
    [(lambda: True, "sandbox"), (lambda: False, "live"), (_analyze_mode_unreadable, "live")],
    ids=["analyze", "live", "unreadable"],
)
def test_an_exit_is_claimed_in_the_book_it_goes_to(monkeypatch, analyze_mode, book):
    import database.settings_db as settings_db

    monkeypatch.setattr(settings_db, "get_analyze_mode", analyze_mode)
    assert exit_guard._book() == book


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
        held = (
            guard.one("live", ("NFO", "NIFTY27OCT2625900CE", "MIS"))
            if claims == "one"
            else guard.every("live")
        )
        with held as busy:
            seen.append(busy)

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT)
    refused = EXIT_BEING_PLACED if claims == "one" else EXIT_ALL_BEING_PLACED
    assert sorted(seen, key=str) == sorted([None, refused], key=str)
    guard._lock = threading.Lock()
    assert len(guard) == 0


# -- Every Exit all, from every surface, takes the one claim -------------------------------------

API_KEY = "test-only-api-key"


def _api_client():
    from flask import Flask
    from flask_restx import Api

    from restx_api import close_position as close_position_api

    app = Flask(__name__)
    app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    Api(app).add_namespace(close_position_api.api, path="/api/v1/closeposition")
    return app.test_client()


@pytest.fixture
def surfaces(routes, monkeypatch):
    """Each way an Exit all reaches Upstox, answering (status code, the sentence or None)."""
    import database.auth_db as auth_db
    import services.close_position_service as close_service
    from limiter import limiter

    monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setattr(close_service, "get_auth_token_broker", lambda key: ("token", "upstox"))
    monkeypatch.setattr(auth_db, "verify_api_key", lambda key: "owner")
    monkeypatch.setattr(auth_db, "get_order_mode", lambda user: "auto")

    def app_exit_all():
        response = _client().post("/close_all_positions", json={})
        return response.status_code, response.get_json().get("message")

    def api_close_position():  # TradingView, scripts, and Telegram's /closeall through the SDK
        body = {"apikey": API_KEY, "strategy": "TradingView"}
        response = _api_client().post("/api/v1/closeposition", json=body)
        return response.status_code, response.get_json().get("message")

    def flow_close_all_node():
        from services.flow_openalgo_client import FlowOpenAlgoClient

        result = FlowOpenAlgoClient(API_KEY).close_all_positions()
        if result["status"] == "success":
            return 200, None
        return result["code"], result["error"]

    return {"app": app_exit_all, "api": api_close_position, "flow": flow_close_all_node}


@pytest.mark.parametrize("surface", ["app", "api", "flow"])
def test_every_exit_all_surface_waits_while_one_is_being_placed(routes, surfaces, surface):
    """Often unattended (a TradingView alert, a Flow node, a script): nothing would resend it.

    While one of the app's exits is being placed it waits for that exit, then closes the rest;
    while another Exit all runs it is refused at once.
    """
    routes["release"].set()  # the broker answers as soon as it is called
    with exit_guard.EXITS.every("live") as busy:  # another Exit all is being placed
        assert busy is None
        assert surfaces[surface]() == (409, EXIT_ALL_BEING_PLACED)

    exit_in_flight = exit_guard.EXITS.one("live", ("NFO", EXIT["symbol"], "MIS"))
    assert exit_in_flight.__enter__() is None
    try:
        everything, answer = _in_background(surfaces[surface])
        time.sleep(0.3)
        assert answer == {} and routes["placed"] == []  # waiting; nothing sent yet
        other = _client().post(
            "/close_position", json={**EXIT, "symbol": "BANKNIFTY27OCT2657500PE"}
        )
        assert (other.status_code, other.get_json()["message"]) == (409, EXIT_ALL_BEING_PLACED)
    finally:
        exit_in_flight.__exit__(None, None, None)  # the exit ends
    everything.join(WAIT)
    closed = "All Open Positions Squared Off" if surface != "flow" else None
    assert answer["result"] == (200, closed)
    assert routes["placed"] == ["ALL"]
    assert len(exit_guard.EXITS) == 0


@pytest.mark.parametrize("surface", ["app", "api", "flow"])
def test_an_exit_all_whose_wait_runs_out_is_refused(routes, surfaces, monkeypatch, surface):
    monkeypatch.setattr(exit_guard, "EXIT_ALL_WAITS_S", 0.3)
    with exit_guard.EXITS.one("live", ("NFO", EXIT["symbol"], "MIS")):  # an exit that does not end
        started = time.monotonic()
        assert surfaces[surface]() == (409, AN_EXIT_BEING_PLACED)
        assert time.monotonic() - started >= 0.3
        assert len(exit_guard.EXITS) == 1  # its own claim let go; the exit's kept
    assert routes["placed"] == []
    assert len(exit_guard.EXITS) == 0


@pytest.mark.parametrize("second", ["app", "api", "flow"])
def test_a_second_exit_all_is_refused_at_once_while_the_first_waits(routes, surfaces, second):
    routes["release"].set()
    exit_in_flight = exit_guard.EXITS.one("live", ("NFO", EXIT["symbol"], "MIS"))
    assert exit_in_flight.__enter__() is None
    try:
        first, answer = _in_background(surfaces["api"])  # TradingView's, waiting
        time.sleep(0.3)
        started = time.monotonic()
        assert surfaces[second]() == (409, EXIT_ALL_BEING_PLACED)
        assert time.monotonic() - started < 1.0
    finally:
        exit_in_flight.__exit__(None, None, None)
    first.join(WAIT)
    assert answer["result"][0] == 200
    assert routes["placed"] == ["ALL"]


@pytest.mark.parametrize("surface", ["app", "api", "flow"])
def test_an_exit_all_from_any_surface_goes_through_once(routes, surfaces, surface):
    """The app's own route is not claimed twice: it does not refuse itself."""
    routes["release"].set()
    assert surfaces[surface]() == (
        200,
        "All Open Positions Squared Off" if surface != "flow" else None,
    )
    assert routes["placed"] == ["ALL"]
    assert len(exit_guard.EXITS) == 0


@pytest.mark.parametrize(
    "pair",
    [("app", "api"), ("api", "api"), ("api", "flow"), ("app", "flow")],
    ids=[
        "the phone and Telegram",
        "TradingView twice",
        "TradingView and Flow",
        "the phone and Flow",
    ],
)
def test_two_exit_alls_released_together_give_one_set_of_exits_and_one_409(routes, surfaces, pair):
    """Barrier-synchronised: two surfaces' Exit alls on real threads (gthread) at once."""
    start = threading.Barrier(2)
    answers = []

    def send(surface):
        start.wait(WAIT)
        answers.append(surfaces[surface]())

    threads = [threading.Thread(target=send, args=(surface,)) for surface in pair]
    for thread in threads:
        thread.start()
    assert routes["entered"].wait(WAIT)  # one is placing its exits, held at the broker
    deadline = time.monotonic() + WAIT
    while not answers and time.monotonic() < deadline:  # the other has its answer
        time.sleep(0.01)
    routes["release"].set()
    for thread in threads:
        thread.join(WAIT)
    assert sorted(code for code, _ in answers) == [200, 409]
    assert [words for code, words in answers if code == 409] == [EXIT_ALL_BEING_PLACED]
    assert routes["placed"] == ["ALL"]


def test_a_phone_exit_waits_while_an_exit_all_from_the_api_is_placed(routes, surfaces):
    def api_exit_all():
        surfaces["api"]()

    everything = threading.Thread(target=api_exit_all)
    everything.start()
    assert routes["entered"].wait(WAIT)
    single = _client().post("/close_position", json=EXIT)
    assert (single.status_code, single.get_json()["message"]) == (409, EXIT_ALL_BEING_PLACED)
    routes["release"].set()
    everything.join(WAIT)
    assert routes["placed"] == ["ALL"]


def test_a_smart_order_from_the_api_is_never_held_up(monkeypatch):
    """TradingView's entries go through /api/v1/placesmartorder: never refused by the guard."""
    import services.place_smart_order_service as smart_service

    monkeypatch.setattr(exit_guard, "EXITS", ExitsInFlight())
    monkeypatch.setattr(smart_service, "get_analyze_mode", lambda: False)
    placed = []

    def place_smartorder_api(order_data, auth_token):
        placed.append(order_data["symbol"])
        return Answer(200), {"status": "success"}, "251021000000009"

    monkeypatch.setattr(
        smart_service,
        "import_broker_module",
        lambda broker: type(
            "Upstox", (), {"place_smartorder_api": staticmethod(place_smartorder_api)}
        ),
    )
    order = {
        **EXIT,
        "apikey": API_KEY,
        "strategy": "TradingView",
        "action": "BUY",
        "quantity": "65",
        "position_size": "65",
        "pricetype": "MARKET",
    }
    with (
        exit_guard.EXITS.every("live"),
        exit_guard.EXITS.one("live", ("NFO", EXIT["symbol"], "MIS")),
    ):
        answer = smart_service.place_smart_order_with_auth(
            dict(order), "token", "upstox", dict(order)
        )
    assert answer == (True, {"status": "success", "orderid": "251021000000009"}, 200)
    assert placed == [EXIT["symbol"]]


@pytest.mark.parametrize("run", range(3))
def test_an_exit_and_an_exit_all_released_together_never_overlap(run):
    """Barrier-synchronised (gthread): the Exit all goes after the exit, or the exit is refused."""
    guard = ExitsInFlight()
    guard._lock = _MeetAfterTheFirstHold()
    start = threading.Barrier(2)
    seen = {}

    def exit_one():
        start.wait(WAIT)
        with guard.one("live", ("NFO", EXIT["symbol"], "MIS")) as busy:
            if busy is None:
                began = time.monotonic()
                time.sleep(0.2)  # the broker call
                seen["exit"] = (began, time.monotonic())
            else:
                seen["exit refused"] = busy

    def exit_all():
        start.wait(WAIT)
        with guard.every("live", wait_s=WAIT) as busy:
            seen["exit all"] = time.monotonic() if busy is None else busy

    threads = [threading.Thread(target=exit_one), threading.Thread(target=exit_all)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT)
    assert isinstance(seen["exit all"], float)  # the Exit all always goes
    if "exit" in seen:
        assert seen["exit all"] >= seen["exit"][1]  # only once the exit had ended
    else:
        assert seen["exit refused"] == EXIT_ALL_BEING_PLACED
    guard._lock = threading.Lock()
    assert len(guard) == 0


@pytest.mark.parametrize("surface", ["api", "flow"])
def test_a_phone_exit_and_an_unattended_exit_all_released_together_never_overlap(
    routes, surfaces, surface
):
    """Barrier-synchronised on real threads: one order for the position at a time, whichever wins."""
    start = threading.Barrier(2)
    answers = {}

    def phone_exit():
        start.wait(WAIT)
        response = _client().post("/close_position", json=EXIT)
        answers["exit"] = response.status_code, response.get_json().get("message")

    def exit_all():
        start.wait(WAIT)
        answers["exit all"] = surfaces[surface]()

    threads = [threading.Thread(target=phone_exit), threading.Thread(target=exit_all)]
    for thread in threads:
        thread.start()
    assert routes["entered"].wait(WAIT)  # one of them reached the broker and is held there
    time.sleep(0.3)  # the other has been refused, or is waiting
    routes["release"].set()
    for thread in threads:
        thread.join(WAIT)
    assert answers["exit all"][0] == 200  # the Exit all always goes
    assert routes["overlaps"] == [0]  # never while the phone's exit was at the broker
    if answers["exit"][0] == 200:
        assert routes["placed"] == [EXIT["symbol"], "ALL"]
    else:
        assert answers["exit"] == (409, EXIT_ALL_BEING_PLACED)
        assert routes["placed"] == ["ALL"]


class _SaysWhoHoldsIt:
    """The guard's lock, saying which thread holds it right now."""

    def __init__(self):
        self._lock = threading.Lock()
        self.holder = None

    def __enter__(self):
        self._lock.acquire()
        self.holder = threading.get_ident()
        return self

    def __exit__(self, *exc):
        self.holder = None
        self._lock.release()
        return False


def _until(condition):
    deadline = time.monotonic() + WAIT
    while not condition():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_an_exit_all_waits_holding_no_lock_and_once_per_exit_that_ends(monkeypatch):
    """Under eventlet the guard's lock is a real one, and a greenlet that waits while holding it
    stops the whole worker the moment another request needs it (an exit tapped meanwhile, or the
    exit in flight ending). So every wait starts with the lock let go, and the Exit all wakes only
    when an exit ends, instead of spinning (which under eventlet starves every other request)."""
    guard = ExitsInFlight()
    guard._lock = lock = _SaysWhoHoldsIt()
    holding = []  # one entry per wait: whether the waiting thread held the lock as it began
    wait_for = exit_guard.real_threading.wait_for

    def watched(event, timeout, *args, **kwargs):
        holding.append(lock.holder == threading.get_ident())
        return wait_for(event, timeout, *args, **kwargs)

    monkeypatch.setattr(exit_guard.real_threading, "wait_for", watched)
    ends = {kind: threading.Event() for kind in ("CE", "PE")}

    def exit_in_flight(kind):
        with guard.one("live", ("NFO", f"NIFTY27OCT2625900{kind}", "MIS")) as busy:
            assert busy is None
            assert ends[kind].wait(WAIT)

    exits = [threading.Thread(target=exit_in_flight, args=(kind,)) for kind in ends]
    for thread in exits:
        thread.start()
    _until(lambda: len(guard) == 2)

    def exit_all():
        with guard.every("live", wait_s=WAIT) as busy:
            return busy

    everything, answer = _in_background(exit_all)
    _until(lambda: len(holding) == 1)  # waiting for both
    ends["CE"].set()
    _until(lambda: len(holding) == 2)  # woken by the first to end, waiting for the other
    ends["PE"].set()
    everything.join(WAIT)
    for thread in exits:
        thread.join(WAIT)
    assert answer["result"] is None  # it went
    assert holding == [False, False]
    assert len(guard) == 0


def test_an_exit_all_waits_long_enough_for_one_slow_upstox_call():
    """The wait covers one exit's broker call at its bound, and the order window's queue before it."""
    from utils.broker_backpressure import BROKER_MAX_ORDER_QUEUE_WAIT_SECONDS
    from utils.httpx_client import REQUEST_TIMEOUT_SECONDS

    assert (
        exit_guard.EXIT_ALL_WAITS_S >= REQUEST_TIMEOUT_SECONDS + BROKER_MAX_ORDER_QUEUE_WAIT_SECONDS
    )
    # And not much longer: nginx gives the request 300 s, and the closes still follow the waits
    # (for the exits in flight, then for the book to show them).
    assert exit_guard.EXIT_ALL_WAITS_S + exit_guard.EXIT_SHOWS_WITHIN_S <= 150


# -- Exit all does not size its orders from a book that does not show an exit yet -----------------

STILL_OPEN = {**EXIT, "quantity": 65}  # EXIT's row in the book until its exit shows there
REPO = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("surface", ["app", "api", "flow"])
def test_an_exit_all_that_waited_for_an_exit_reads_the_book_until_it_shows_it(
    routes, surfaces, surface
):
    """Upstox's book can say the old quantity for a moment after an exit, and Exit all sizes its
    orders from that book: read then, it would close the position a second time and reverse it."""
    routes["book"] = lambda reads: [STILL_OPEN] if reads < 3 else []  # shows it at the third read
    single, answer = _post_in_background("/close_position", EXIT)
    assert routes["entered"].wait(WAIT)
    everything, result = _in_background(surfaces[surface])
    time.sleep(0.3)
    routes["release"].set()  # the exit is placed; the book does not show it yet
    single.join(WAIT)
    everything.join(WAIT)
    assert answer["code"] == 200
    assert result["result"][0] == 200
    assert routes["placed"] == [EXIT["symbol"], "ALL"]
    assert routes["reads_before_close"] == [3]  # not before the book showed the exit
    assert len(exit_guard.EXITS) == 0


def test_an_exit_all_just_after_an_exit_reads_the_book_until_it_shows_it(routes, surfaces):
    routes["release"].set()
    routes["book"] = lambda reads: [STILL_OPEN] if reads < 2 else []
    assert _client().post("/close_position", json=EXIT).status_code == 200
    assert surfaces["api"]() == (200, "All Open Positions Squared Off")
    assert routes["reads_before_close"] == [2]


@pytest.mark.parametrize("surface", ["app", "api", "flow"])
def test_an_exit_all_is_refused_when_the_book_does_not_show_the_exit_in_time(
    routes, surfaces, monkeypatch, surface
):
    monkeypatch.setattr(exit_guard, "EXIT_SHOWS_WITHIN_S", 1.0)
    routes["release"].set()
    routes["book"] = [STILL_OPEN]  # a book that lags that long, or an exit rejected once placed
    assert _client().post("/close_position", json=EXIT).status_code == 200
    started = time.monotonic()
    assert surfaces[surface]() == (409, EXIT_NOT_SHOWN_YET)
    assert time.monotonic() - started >= 1.0
    assert routes["placed"] == [EXIT["symbol"]]  # nothing more was sent
    assert len(exit_guard.EXITS) == 0


@pytest.mark.parametrize(
    ("reply", "code", "reads"),
    [
        ((Answer(200), {"status": "success"}, "251021000000001"), 200, [1]),
        ((Answer(503), {"status": "error", "message": "?"}, None), 503, [1]),  # may have gone
        (
            (Answer(400), {"status": "error", "message": "Quantity exceeds freeze limit"}, None),
            400,
            [0],
        ),
    ],
    ids=["placed", "not confirmed", "refused"],
)
def test_only_an_exit_that_may_have_placed_an_order_is_looked_for_in_the_book(
    routes, surfaces, monkeypatch, reply, code, reads
):
    monkeypatch.setattr(
        orders,
        "dynamic_import",
        lambda broker, module, names: {
            "place_smartorder_api": lambda order_data, auth_token: reply,
            "get_open_position": lambda *args: "0",
        },
    )
    routes["release"].set()
    assert _client().post("/close_position", json=EXIT).status_code == code
    assert surfaces["api"]()[0] == 200
    assert routes["reads_before_close"] == reads


def test_an_exit_is_noted_before_its_claim_is_let_go(routes, surfaces, monkeypatch):
    """A waiting Exit all wakes when the exit's claim is let go. Noted only after that, the exit
    would be missed, and the Exit all would read a book that does not show it yet."""
    note = ExitsInFlight.placed

    def slow_to_note(self, book, position):
        time.sleep(0.3)
        note(self, book, position)

    monkeypatch.setattr(ExitsInFlight, "placed", slow_to_note)
    routes["book"] = lambda reads: [STILL_OPEN] if reads < 2 else []
    single, _ = _post_in_background("/close_position", EXIT)
    assert routes["entered"].wait(WAIT)
    everything, result = _in_background(surfaces["api"])
    time.sleep(0.3)
    routes["release"].set()
    single.join(WAIT)
    everything.join(WAIT)
    assert result["result"][0] == 200
    assert routes["reads_before_close"] == [2]


def test_an_exit_counts_as_placed_just_now_for_a_while_only(monkeypatch):
    """Nothing is kept past its time: the worker never restarts."""
    guard = ExitsInFlight()
    position = ("NFO", EXIT["symbol"], "MIS")
    other = ("NFO", "BANKNIFTY27OCT2657500PE", "NRML")
    guard.placed("live", position)
    assert guard.placed_lately("live") == {position}
    assert guard.placed_lately("sandbox") == set()  # the books are apart
    monkeypatch.setattr(exit_guard, "EXIT_SHOWS_WITHIN_S", 0.05)
    time.sleep(0.1)
    assert guard.placed_lately("live") == set()
    guard.placed("sandbox", position)
    time.sleep(0.1)
    guard.placed("sandbox", other)
    assert {book: set(placed) for book, placed in guard._placed.items()} == {"sandbox": {other}}


def test_the_book_is_read_live_and_a_quantity_it_cannot_read_counts_as_open(monkeypatch):
    import services.positionbook_service as positionbook_service

    calls = []
    rows = [
        {"symbol": EXIT["symbol"], "exchange": "NFO", "product": "MIS", "quantity": 0},
        {
            "symbol": "BANKNIFTY27OCT2657500PE",
            "exchange": "nfo",
            "product": "nrml",
            "quantity": "-30",
        },
        {"symbol": "NIFTY27OCT2625800PE", "exchange": "NFO", "product": "MIS", "quantity": "?"},
        {"symbol": "SBIN", "exchange": "NSE", "product": "CNC", "quantity": 10},  # not asked about
    ]

    def read(auth_token, broker, original_data=None):
        calls.append((auth_token, broker, original_data))
        return True, {"status": "success", "data": rows}, 200

    monkeypatch.setattr(positionbook_service, "get_positionbook_with_auth", read)
    still_open = exit_guard.open_in_book("token", "upstox")
    shown = ("NFO", EXIT["symbol"], "MIS")
    short = ("NFO", "BANKNIFTY27OCT2657500PE", "NRML")
    unreadable = ("NFO", "NIFTY27OCT2625800PE", "MIS")
    assert still_open({shown, short, unreadable}) == {short, unreadable}
    assert calls == [("token", "upstox", None)]  # no request data: the live book, never the sandbox
    monkeypatch.setattr(
        positionbook_service,
        "get_positionbook_with_auth",
        lambda *args, **kwargs: (False, {"status": "error", "message": "?"}, 500),
    )
    assert still_open({shown}) is None


def test_a_book_that_cannot_be_read_does_not_show_an_exit(monkeypatch):
    monkeypatch.setattr(exit_guard, "EXIT_SHOWS_WITHIN_S", 0.2)
    monkeypatch.setattr(exit_guard, "EXIT_SHOWS_POLL_S", 0.05)
    position = ("NFO", EXIT["symbol"], "MIS")

    def broken(positions):
        raise RuntimeError("read broke")

    assert exit_guard._shown_in_book({position}, broken) is False
    assert exit_guard._shown_in_book({position}, lambda positions: None) is False
    assert exit_guard._shown_in_book({position}, lambda positions: set()) is True
    assert exit_guard._shown_in_book(set(), broken) is True  # nothing placed lately: no read


def test_an_exit_all_gives_the_book_as_long_to_show_an_exit_as_the_phone_does():
    sent = (REPO / "frontend/src/sudarshan/books/sent.ts").read_text(encoding="utf-8")
    hold_ms = int(
        re.search(r"export const SENT_HOLD_MS = ([0-9_]+)", sent).group(1).replace("_", "")
    )
    assert exit_guard.EXIT_SHOWS_WITHIN_S == hold_ms / 1000
    # About one read a second: soon enough, and at most 30 broker calls, far inside Upstox's
    # 500 a minute.
    assert 0.5 <= exit_guard.EXIT_SHOWS_POLL_S <= 1.0
