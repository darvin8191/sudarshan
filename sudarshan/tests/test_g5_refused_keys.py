"""G5 (observation 4): a request refused for its API key writes nothing to the owner's logs.

Anyone who reaches /api/v1 could add rows to the owner's order log: OpenAlgo checks a request's
fields before its key and logged a request its fields refused as a failed order, through an order
event (cancel, modify, close, basket, split, smart and GTT orders) or a direct write (order status,
open position, margin, analyzer). In analyze mode the owner's app also announced it with a sound.
Now an order event raised while refusing a request for its key (none, or a wrong one) reaches no
subscriber, and a direct write handed over then is dropped. A request with a valid key, and an
order failure from inside OpenAlgo (no /api/v1 request around it), is logged as before.

Everything here goes through what the code before G5 already had (the events, the bus, the log
executor, the real endpoints), so each test that says so fails on that code, and the ones for a
valid key or an internal order pass on both, by design.
"""

from __future__ import annotations

import pytest
from flask import Flask
from restx_routes import api_app, load, stop_the_health_monitor

import database.apilog_db as apilog_db
import database.auth_db as auth_db
from events import AnalyzerErrorEvent, OrderFailedEvent
from utils.event_bus import EventBus

VALID = "g5-valid-key-for-tests"  # pragma: allowlist secret
WRONG = "g5-wrong-key-for-tests"  # pragma: allowlist secret
#: (name, JSON body, headers, query) of requests whose key is refused, and of ones whose is not.
REFUSED = [
    ("no key", {}, {}, ""),
    ("a wrong key", {"apikey": WRONG}, {}, ""),
    ("a wrong key in the header", {}, {"X-API-KEY": WRONG}, ""),
    ("a wrong key in the query", {}, {}, f"?apikey={WRONG}"),
]
ACCEPTED = [
    ("a valid key", {"apikey": VALID}, {}, ""),
    ("a valid key in the header", {}, {"X-API-KEY": VALID}, ""),
    ("a valid key in the query", {}, {}, f"?apikey={VALID}"),
]


@pytest.fixture(scope="module")
def routes():
    """restx_api/cancel_order.py (an order event on refusal) and restx_api/orderstatus.py (a direct
    log write), each loaded on its own (restx_routes says why)."""
    yield load("cancel_order"), load("orderstatus")
    stop_the_health_monitor()


@pytest.fixture
def keys(monkeypatch):
    """OpenAlgo's key check, answering VALID for the owner; the keys it was asked about."""
    asked = []

    def verify(key):
        asked.append(key)
        return "owner" if key == VALID else None

    monkeypatch.setattr(auth_db, "verify_api_key", verify)
    return asked


@pytest.fixture
def app():
    app = Flask("sudarshan-g5-refused-keys-tests")
    app.config["TESTING"] = True
    return app


def delivered(event) -> list:
    """What a subscriber of the event's topic hears once the event is published."""
    heard = []
    bus = EventBus(workers=1)
    bus.subscribe(event.topic, heard.append, "test")
    bus.publish(event)
    bus._executor.shutdown(wait=True)
    return heard


def failure(app, path: str, body: dict, headers: dict, query: str = "", cls=OrderFailedEvent):
    """The failure event a view answering ``path`` raises, made inside that request."""
    with app.test_request_context(path + query, method="POST", json=body, headers=headers):
        return cls(mode="live", api_type="cancelorder", request_data=body,
                   response_data={"status": "error", "message": "refused"})  # fmt: skip


@pytest.mark.parametrize(("name", "body", "headers", "query"), REFUSED, ids=[r[0] for r in REFUSED])
@pytest.mark.parametrize("cls", [OrderFailedEvent, AnalyzerErrorEvent])
def test_an_order_event_of_a_request_refused_for_its_key_reaches_no_subscriber(
    app, keys, name, body, headers, query, cls
):
    """Before G5 every subscriber heard it: the log wrote a row, analyze mode sounded an alert."""
    event = failure(app, "/api/v1/cancelorder/", body, headers, query, cls)
    assert delivered(event) == []


@pytest.mark.parametrize(("name", "body", "headers", "query"), ACCEPTED, ids=[r[0] for r in ACCEPTED])
def test_an_order_event_of_a_request_with_a_valid_key_is_delivered_as_before(
    app, keys, name, body, headers, query
):
    event = failure(app, "/api/v1/cancelorder/", body, headers, query)
    assert delivered(event) == [event]


def test_an_order_failure_from_inside_openalgo_is_delivered_as_before(app, keys):
    """A strategy's or the app's own order carries no key and is no /api/v1 request: delivered,
    outside any request and inside one of the app's own pages alike. The key is never asked."""
    outside = OrderFailedEvent(mode="live", api_type="placeorder", response_data={"status": "error"})
    inside = failure(app, "/orders/api/place", {}, {})
    assert delivered(outside) == [outside] and delivered(inside) == [inside]
    assert keys == []


def test_the_key_is_checked_once_per_request_and_not_at_all_when_there_is_none(app, keys):
    with app.test_request_context("/api/v1/basketorder/", method="POST", json={"apikey": WRONG}):
        events = [OrderFailedEvent(mode="live", api_type="basketorder") for _ in range(3)]
    with app.test_request_context("/api/v1/basketorder/", method="POST", json={}):
        events.append(OrderFailedEvent(mode="live", api_type="basketorder"))
    assert [delivered(event) for event in events] == [[], [], [], []]
    assert keys == [WRONG]


def test_a_key_check_that_breaks_leaves_the_event_delivered(app, monkeypatch):
    """When it cannot tell, the request is logged as before; the order path never sees a raise."""

    def broken(key):
        raise RuntimeError("the users table could not be read")

    monkeypatch.setattr(auth_db, "verify_api_key", broken)
    event = failure(app, "/api/v1/cancelorder/", {"apikey": WRONG}, {})
    assert delivered(event) == [event]


def test_a_log_write_handed_over_while_refusing_a_key_is_dropped(app, keys):
    """The executor every direct order-log and analyzer-log write is handed to. Before G5 the
    refused request's write ran like any other."""
    ran = []
    executor = type(apilog_db.executor)(max_workers=1)
    for name, body, headers, query in REFUSED + ACCEPTED:
        with app.test_request_context("/api/v1/orderstatus/" + query, method="POST", json=body,
                                      headers=headers):  # fmt: skip
            executor.submit(ran.append, name)
    executor.submit(ran.append, "outside a request")
    executor.shutdown(wait=True)
    assert ran == [name for name, *_ in ACCEPTED] + ["outside a request"]


def test_openalgo_s_own_log_executor_drops_it(app, keys):
    ran = []
    with app.test_request_context("/api/v1/orderstatus/", method="POST", json={}):
        handed = apilog_db.executor.submit(ran.append, "refused")
    handed.result(timeout=10)  # the write itself, had it been handed over
    assert ran == []


@pytest.fixture
def api(monkeypatch, keys, routes):
    """The real routes on a bare app, with a bus and a log executor of the test's own (the same
    classes as OpenAlgo's), so every event and write a request causes is counted once settled."""
    import services.cancel_order_service as cancel_service

    cancel_endpoint, status_endpoint = routes
    app = api_app((cancel_endpoint, "/cancelorder"), (status_endpoint, "/orderstatus"))
    heard, writes = [], []
    bus = EventBus(workers=1)
    for topic in ("order.failed", "analyzer.error"):
        bus.subscribe(topic, heard.append, "test")
    executor = type(apilog_db.executor)(max_workers=1)
    monkeypatch.setattr(cancel_endpoint, "bus", bus)
    monkeypatch.setattr(cancel_service, "bus", bus)
    monkeypatch.setattr(status_endpoint, "log_executor", executor)
    monkeypatch.setattr(status_endpoint, "async_log_order", lambda kind, *rest: writes.append(kind))

    def analyze(on: bool) -> None:
        for module in (cancel_endpoint, status_endpoint):
            monkeypatch.setattr(module, "get_analyze_mode", lambda: on)

    def settle() -> tuple[list, list]:
        bus._executor.shutdown(wait=True)
        executor.shutdown(wait=True)
        return [(type(e).__name__, e.api_type) for e in heard], writes

    return app.test_client(), analyze, settle


def test_the_api_s_refusals_for_a_key_log_nothing_and_a_valid_key_s_failure_is_logged(api):
    """Live mode. Before G5 each of the four refused requests added a row: two through the order
    event of /cancelorder, two by the direct write of /orderstatus."""
    client, analyze, settle = api
    analyze(False)
    for body in ({}, {"apikey": WRONG}, {"apikey": VALID}):  # each lacks the order's own fields
        assert client.post("/api/v1/cancelorder/", json=body).status_code == 400
        assert client.post("/api/v1/orderstatus/", json=body).status_code == 400
    assert settle() == ([("OrderFailedEvent", "cancelorder")], ["orderstatus"])


def test_in_analyze_mode_a_refusal_for_a_key_reaches_no_screen(api):
    """Analyze mode. Before G5 both refused requests reached the owner's app as an analyzer update
    (an alert sound and a toast) and the analyzer log."""
    client, analyze, settle = api
    analyze(True)
    for body in ({}, {"apikey": WRONG}, {"apikey": VALID}):
        assert client.post("/api/v1/cancelorder/", json=body).status_code == 400
    assert settle() == ([("AnalyzerErrorEvent", "cancelorder")], [])


def test_in_analyze_mode_a_refused_order_status_or_position_call_reaches_no_screen(
    monkeypatch, keys, routes
):
    """Order status and open position tell the owner's app of an analyze-mode request themselves,
    not through the bus. Before G5 both refused requests to each wrote an analyzer-log row and
    reached the open app as an error toast."""
    import services.openposition_service as position_service
    import services.orderstatus_service as status_service

    status_endpoint = routes[1]
    position_endpoint = load("openposition")
    app = api_app((status_endpoint, "/orderstatus"), (position_endpoint, "/openposition"))
    shown, written, executors = [], [], []
    for endpoint, service in ((status_endpoint, status_service), (position_endpoint, position_service)):
        executor = type(apilog_db.executor)(max_workers=1)
        executors.append(executor)
        monkeypatch.setattr(endpoint, "get_analyze_mode", lambda: True)
        monkeypatch.setattr(service, "log_executor", executor)
        monkeypatch.setattr(service, "async_log_analyzer", lambda req, resp, kind: written.append(kind))
        monkeypatch.setattr(service, "emit_from_any_thread",
                            lambda event, data: shown.append(data["request"]["api_type"]))  # fmt: skip
    client = app.test_client()
    for body in ({}, {"apikey": WRONG}, {"apikey": VALID}):  # each lacks the request's own fields
        assert client.post("/api/v1/orderstatus/", json=body).status_code == 400
        assert client.post("/api/v1/openposition/", json=body).status_code == 400
    for executor in executors:
        executor.shutdown(wait=True)
    assert (shown, sorted(written)) == (["orderstatus", "openposition"], ["openposition", "orderstatus"])
