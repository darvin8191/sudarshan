"""One exit at a time per position from the app's own screens (M6.9, sudarshan/books/exit_guard.py).

The phone gives up on an exit after 45 s; the server goes on placing it. A second exit sent
meanwhile is sized from a book that does not show the first fill, and reverses the position. The
app's own close routes therefore refuse a second request with 409 and a sentence while the first
for the same position, or any Exit all, is still running in this process.
"""

from __future__ import annotations

import threading
import time

import pytest

import blueprints.orders as orders
import utils.session as session_utils
from sudarshan.books import exit_guard
from sudarshan.books.exit_guard import (
    AN_EXIT_BEING_PLACED,
    EXIT_ALL_BEING_PLACED,
    EXIT_BEING_PLACED,
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
    from database.latency_db import init_latency_db

    init_latency_db()
    monkeypatch.setattr(session_utils, "is_session_valid", lambda: True)
    monkeypatch.setattr(orders, "get_auth_token", lambda user: "token")
    monkeypatch.setattr(orders, "get_analyze_mode", lambda: False)
    monkeypatch.setattr(settings_db, "get_analyze_mode", lambda: False)
    monkeypatch.setattr(orders, "get_api_key_for_tradingview", lambda user: None)
    monkeypatch.setattr(close_service, "get_analyze_mode", lambda: False)
    monkeypatch.setattr(exit_guard, "EXITS", ExitsInFlight())

    gate = {"entered": threading.Event(), "release": threading.Event(), "placed": []}

    def place_smartorder_api(order_data, auth_token):
        gate["placed"].append(order_data["symbol"])
        if len(gate["placed"]) == 1:  # the first is held until told; any other goes straight on
            gate["entered"].set()
            assert gate["release"].wait(WAIT)
        return Answer(200), {"status": "success"}, f"25102100000{len(gate['placed'])}"

    def close_all_positions(api_key, auth):
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
    single, answer = _post_in_background("/close_position", EXIT)
    assert routes["entered"].wait(WAIT)
    everything = _client().post("/close_all_positions", json={})
    assert everything.status_code == 409
    assert everything.get_json()["message"] == AN_EXIT_BEING_PLACED
    routes["release"].set()
    single.join(WAIT)
    assert answer["code"] == 200
    assert routes["placed"] == [EXIT["symbol"]]


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


@pytest.mark.parametrize("every", [False, True], ids=["exit", "exit all"])
def test_a_view_that_raises_lets_its_claim_go(monkeypatch, every):
    from flask import Flask

    monkeypatch.setattr(exit_guard, "EXITS", ExitsInFlight())
    monkeypatch.setattr(exit_guard, "_book", lambda: "live")
    app = Flask(__name__)

    @app.post("/boom")
    @exit_guard.one_exit_at_a_time(every=every)
    def boom():
        raise RuntimeError("the view broke")

    assert app.test_client().post("/boom", json=EXIT).status_code == 500
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
