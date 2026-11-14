"""The alert route without orders (A4): the token, the limits, the sender check, the log line and
its redaction, the dry run. ``intake.handle`` is called as the route calls it, on a fake desk."""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from contextlib import closing

import pytest
from automation_world import API_KEY, NOW, TV, FakeDesk, indicator_alert, strategy_alert
from flask import Flask

from sudarshan.automation import contract, flag, intake
from sudarshan.automation import model as m
from sudarshan.automation import store as store_mod

FORM = {"name": "ORB", "kind": "future", "underlying": "NIFTY", "product": "MIS", "lots": 1}


@pytest.fixture
def desk():
    return FakeDesk()


@pytest.fixture(autouse=True)
def _fresh_counters(monkeypatch):
    monkeypatch.setenv(flag.ENV, "sandbox")
    intake._per_link.clear()
    intake._refused.clear()


def make(store, state=m.ACTIVE, **change):
    strategy, token = store.create(contract.clean({**FORM, **change}))
    if state != m.STOPPED:
        strategy = store.set_state(strategy.id, strategy.version, state)
    return strategy, token


def send(store, desk, token, body, ip=TV):
    return intake.handle(token, body, ip, desk, store)


def rows(store, strategy_id):
    return list(reversed(store.alerts(strategy_id)))


# ---- one answer for every unknown ---------------------------------------------------------------


def test_flag_off_malformed_unknown_and_a_wrong_sender_get_the_same_answer(
    store, desk, monkeypatch
):
    strategy, token = make(store)
    body = indicator_alert("long", NOW - 1)
    answers = {
        "unknown": send(store, desk, contract.TOKEN_PREFIX + "x" * 43, body),
        "malformed": send(store, desk, "not-a-token", body),
        "short": send(store, desk, token[:-1], body),
        "wrong sender": send(store, desk, token, body, ip="203.0.113.9"),
        "own address": send(store, desk, token, body, ip="127.0.0.1"),
    }
    monkeypatch.setenv(flag.ENV, "off")
    answers["flag off"] = send(store, desk, token, body)  # a real link, automation off
    assert {(a.status, json.dumps(a.body, sort_keys=True)) for a in answers.values()} == {
        (404, '{"message": "Not found", "status": "error"}')
    }
    assert rows(store, strategy.id) == [] and desk.placed == []
    assert intake.local_sender_seen_ms() > 0  # the page can say the proxy hides the sender


def test_flag_off_opens_no_store_and_imports_no_order_code(tmp_path, monkeypatch, desk):
    """Mutation 11 and the flag-off pin: the answer is given before anything is touched."""
    monkeypatch.setenv(flag.ENV, "off")
    made = store_mod.AutomationStore(tmp_path / "never" / "automation.db")
    monkeypatch.setattr(store_mod, "_shared", store_mod.LazyInit(lambda: made, "test"))
    # taken out for this test only and put back after it: other tests hold the module
    monkeypatch.delitem(sys.modules, "services.place_smart_order_service", raising=False)
    got = intake.handle(contract.TOKEN_PREFIX + "x" * 43, b"{}", TV)
    assert (got.status, got.body) == (404, {"status": "error", "message": "Not found"})
    assert not (tmp_path / "never").exists()
    assert "services.place_smart_order_service" not in sys.modules


def test_a_shape_that_is_not_a_token_is_refused_without_a_lookup(desk, monkeypatch):
    class NoStore:
        def __getattr__(self, name):
            raise AssertionError("the store was asked")

    for bad in ("", "sdt_", "x" * 47, "sdt_" + "!" * 43, "sdt_" + "k" * 44, None):
        assert intake.handle(bad, b"{}", TV, desk, NoStore()).status == 404
    assert intake.handle("sdt_" + "k" * 43, b"{}", "198.51.100.7", desk, NoStore()).status == 404


def test_the_sender_list_can_be_replaced_by_the_owner(store, desk, monkeypatch):
    _, token = make(store)
    monkeypatch.setenv("SUDARSHAN_AUTOMATION_SENDERS", "198.51.100.7, 198.51.100.8")
    body = indicator_alert("long", NOW - 1)
    assert send(store, desk, token, body, ip="198.51.100.8").status == 200
    assert send(store, desk, token, indicator_alert("flat", NOW), ip=TV).status == 404


def test_unknown_links_are_counted_in_one_log_line_and_keep_nothing(store, desk, caplog):
    make(store)
    with caplog.at_level(logging.WARNING, logger=intake.logger.name):
        for n in range(200):
            token = contract.TOKEN_PREFIX + f"{n:043d}"
            assert send(store, desk, token, b"{}").status == 404
    said = [r.getMessage() for r in caplog.records]
    assert len(said) == 1 and "nobody made" in said[0] and "sdt_" not in said[0]
    assert len(intake._refused) <= 3 and len(intake._per_link) == 0  # no key per guess


# ---- limits -------------------------------------------------------------------------------------


def test_a_body_over_8_kib_is_refused(store, desk):
    strategy, token = make(store)
    got = send(store, desk, token, b"x" * (contract.MAX_BODY + 1))
    assert got.status == 413 and rows(store, strategy.id) == []
    assert send(store, desk, token, b"x" * contract.MAX_BODY).status == 400  # read, and not JSON


def test_sixty_alerts_a_minute_per_link_then_refused_until_the_minute_turns(store, desk):
    strategy, token = make(store, max_alerts_day=200)
    codes = [
        send(store, desk, token, indicator_alert("flat", NOW - 1, comment=str(n))).status
        for n in range(contract.TOKEN_PER_MINUTE + 5)
    ]
    assert codes == [200] * contract.TOKEN_PER_MINUTE + [429] * 5
    other, other_token = make(store, name="Second", underlying="BANKNIFTY")
    assert (
        send(store, desk, other_token, indicator_alert("flat", NOW)).status == 200
    )  # its own count
    desk.clock += 60
    assert send(store, desk, token, indicator_alert("flat", desk.clock)).status == 200


# ---- the message and the log line ---------------------------------------------------------------


def test_a_bad_message_is_logged_with_its_sentence_and_told_nothing_else(store, desk):
    strategy, token = make(store)
    got = send(store, desk, token, b'{"v":1,"id":"{{strategy.order.id}}","position":"long"}')
    assert (got.status, got.body) == (
        400,
        {"status": "error", "message": "The alert message could not be read."},
    )
    (row,) = rows(store, strategy.id)
    assert row["verdict"] == "bad_message"
    assert row["sentence"].startswith("ORB: the alert message still has strategy.order.id in it")
    assert store.state(strategy.id).alerts_today == 0 and desk.placed == []


def test_the_public_answer_names_no_strategy_no_verdict_and_no_position(store, desk):
    strategy, token = make(store)
    first = send(store, desk, token, strategy_alert("long", NOW - 1, before="flat"))
    again = send(store, desk, token, strategy_alert("long", NOW - 1, before="flat"))
    late = send(store, desk, token, strategy_alert("long", NOW - 500, before="flat", order_id="X"))
    assert first.body == {"status": "success", "message": "handled"} == late.body
    assert again.body == {"status": "success", "message": "already handled"}
    for answer in (first, again, late):
        text = json.dumps(answer.body)
        assert "ORB" not in text and "NIFTY" not in text and "late" not in text


def test_what_is_kept_of_an_alert_holds_no_token_no_key_and_no_unknown_text(store, desk, caplog):
    """No token, API key or pasted secret in a log line, an event, a notice or the stored text."""
    strategy, token = make(store)
    fired = int((NOW - 1) * 1000)
    body = json.dumps({"v": 1, "position": "long", "fired": fired, "price": API_KEY}).encode()
    pasted = json.dumps(
        {
            "v": 1,
            "position": "long",
            "fired": fired,
            "comment": f"https://x.invalid/sudarshan/tv/{token}",
        }
    ).encode()
    with caplog.at_level(logging.DEBUG):
        assert send(store, desk, token, body).status == 200
        assert send(store, desk, token, body).status == 200  # and its repeat
        assert send(store, desk, token, pasted).status == 400  # the link pasted into the message
        assert send(store, desk, token, b'{"apikey": "' + API_KEY.encode() + b'"}').status == 400
    with closing(sqlite3.connect(store.path)) as db:
        dump = "\n".join(db.iterdump())
    seen = dump + caplog.text + json.dumps(desk.emitted) + json.dumps(desk.told)
    assert token not in seen and token[4:] not in seen and API_KEY not in seen
    assert "(removed)" in dump  # the comment that carried the link
    stored = [
        json.loads(r["payload"]) for r in map(store.alert, range(1, 4)) if r["payload"][0] == "{"
    ]
    assert all(
        set(p) <= {"v", "position", "fired", "comment", "price", "_other_keys"} for p in stored
    )


def test_each_line_carries_the_two_times_and_the_senders_address(store, desk):
    strategy, token = make(store)
    send(store, desk, token, indicator_alert("flat", NOW - 7, bar=int((NOW - 60) * 1000)))
    full = store.alert(rows(store, strategy.id)[0]["id"])
    assert (full["fired_s"], full["bar_s"]) == (int(NOW - 7), int(NOW - 60))
    assert (
        full["tv_delay_ms"] == 7000 and full["ip"] == TV and full["received_ms"] == int(NOW * 1000)
    )
    assert full["claim"] == f"{int(NOW - 7)}|-|flat:"


# ---- the verdict it would give ------------------------------------------------------------------


def test_refusals_are_written_under_the_claim_with_their_sentences(store, desk):
    strategy, token = make(store, chart="NSE:NIFTY1!", kind="same")
    cases = [
        (strategy_alert("long", NOW - 500, order_id="a", ticker="NSE:NIFTY1!"), "late"),
        (strategy_alert("long", NOW - 1, order_id="b", ticker="NSE:BANKNIFTY1!"), "not_mapped"),
        (strategy_alert("long", NOW - 1, order_id="c"), "not_mapped"),  # names no chart
    ]
    for body, verdict in cases:
        got = send(store, desk, token, body)
        assert (got.status, got.verdict) == (200, verdict), got.sentence
    said = [r["sentence"] for r in rows(store, strategy.id)]
    assert "arrived 8 min 20 s late and was not sent" in said[0]
    assert (
        "came from the chart NSE:BANKNIFTY1!, and this strategy is set up for NSE:NIFTY1!"
        in said[1]
    )
    assert "does not name its chart" in said[2]
    assert store.state(strategy.id).alerts_today == 3 and desk.placed == []
    assert [e["verdict"] for e in desk.emitted] == ["late", "not_mapped", "not_mapped"]
    assert all(
        set(e) == {"kind", "strategy_id", "name", "verdict", "sentence", "at_ms"}
        for e in desk.emitted
    )


def test_a_stopped_strategy_records_stopped_and_tells_once_a_day(store, desk):
    strategy, token = make(store, state=m.STOPPED)
    for n in range(3):
        got = send(store, desk, token, indicator_alert("long", NOW - 3 + n))
        assert got.verdict == "stopped"
    assert [t[1] for t in desk.told] == ["stopped"]
    assert desk.told[0][2] == "ORB is stopped. Its alerts are being ignored."
    desk.clock += 86_400
    send(store, desk, token, indicator_alert("long", desk.clock - 1))
    assert [t[1] for t in desk.told] == ["stopped", "stopped"]


def test_the_same_alert_twice_is_one_claim_and_one_repeat_line(store, desk):
    strategy, token = make(store)
    body = strategy_alert("long", NOW - 1, before="flat")
    assert send(store, desk, token, body).verdict != "repeat"
    assert send(store, desk, token, body).verdict == "repeat"
    lines = rows(store, strategy.id)
    assert [r["verdict"] == "repeat" for r in lines] == [False, True]
    assert lines[1]["sentence"] == "Repeat of the alert fired at 10:15:01. Nothing was sent."
    assert store.state(strategy.id).alerts_today == 1
    assert [t for t in desk.told if t[1] == "repeat"] == []  # nobody is told of a repeat


def test_a_link_replaced_while_an_alert_waited_is_nobodys(store, desk):
    strategy, token = make(store)
    original = store.claim_alert

    def replaced_first(strategy_id, decide, day, token=None):
        store.replace_token(strategy_id, store.strategy(strategy_id).version)
        return original(strategy_id, decide, day, token=token)

    store.claim_alert = replaced_first
    got = send(store, desk, token, indicator_alert("long", NOW - 1))
    assert got.status == 404 and rows(store, strategy.id) == [] and desk.placed == []


# ---- the dry run --------------------------------------------------------------------------------


def test_check_says_what_an_alert_would_do_and_writes_nothing(store, desk):
    strategy, _ = make(store)
    got = intake.check(strategy.id, strategy_alert("long", NOW - 1, before="flat"), desk, store)
    assert got["verdict"] == "sent"
    assert got["sentence"] == "Would send an order to the Sandbox to be long 65 NIFTY 24 Nov FUT."
    assert got["order"] == {
        "strategy": "ORB", "exchange": "NFO", "symbol": "NIFTY24NOV26FUT", "product": "MIS",
        "action": "BUY", "pricetype": "MARKET", "quantity": "65", "position_size": "65",
        "price": "0", "trigger_price": "0", "disclosed_quantity": "0",
    }  # fmt: skip
    late = intake.check(strategy.id, strategy_alert("short", NOW - 500), desk, store)
    assert late["verdict"] == "late" and late["order"] is None
    bad = intake.check(strategy.id, b"buy now", desk, store)
    assert bad["verdict"] == "bad_message" and "Copy it again from Alert set-up" in bad["sentence"]
    assert (
        intake.check(999, b"{}", desk, store)["sentence"]
        == "This strategy is no longer there. Reload the page."
    )
    assert rows(store, strategy.id) == [] and desk.placed == [] and desk.emitted == []
    assert store.state(strategy.id) == m.State(strategy.id)
    assert API_KEY not in json.dumps(got)


def test_check_works_with_the_flag_off(store, desk, monkeypatch):
    monkeypatch.setenv(flag.ENV, "off")
    strategy, _ = make(store, state=m.STOPPED)
    got = intake.check(strategy.id, indicator_alert("long", NOW - 1), desk, store)
    assert got["verdict"] == "stopped"


# ---- the route itself ---------------------------------------------------------------------------


@pytest.fixture
def client(store, desk, monkeypatch):
    import blueprints.sudarshan_automation as routes
    from limiter import limiter

    monkeypatch.setattr(intake, "DESK", lambda: desk)
    monkeypatch.setattr(store_mod, "_shared", store_mod.LazyInit(lambda: store, "test"))
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    app = Flask(__name__)
    app.config.update(TESTING=True, RATELIMIT_ENABLED=True)
    limiter.init_app(app)
    app.register_blueprint(routes.sudarshan_automation_bp)
    limiter.reset()
    return app.test_client()


def post(client, token, body=b"{}", ip=TV, **kw):
    return client.post(f"/sudarshan/tv/{token}", data=body, environ_base={"REMOTE_ADDR": ip}, **kw)


def test_the_route_answers_as_the_intake_does(client, store):
    strategy, token = make(store)
    ok = post(client, token, indicator_alert("flat", NOW - 1), content_type="text/plain")
    assert (ok.status_code, ok.get_json()) == (200, {"status": "success", "message": "handled"})
    assert post(client, "sdt_" + "0" * 43).status_code == 404
    assert post(client, token, ip="203.0.113.9").status_code == 404
    assert post(client, token, b"not json").status_code == 400
    assert client.get(f"/sudarshan/tv/{token}").status_code == 405


def test_the_route_refuses_a_large_body_from_its_header_before_reading_it(
    client, store, monkeypatch
):
    _, token = make(store)
    called = []
    monkeypatch.setattr(intake, "handle", lambda *a, **k: called.append(a))
    big = post(client, token, b"x" * (contract.MAX_BODY + 1))
    assert big.status_code == 413 and called == []
    assert big.get_json()["message"] == "The alert message is too large."


def test_the_route_with_the_flag_off_is_an_unknown_link_and_calls_nothing(
    client, store, monkeypatch
):
    _, token = make(store)
    unknown = post(client, "sdt_" + "0" * 43)
    monkeypatch.setenv(flag.ENV, "off")
    monkeypatch.setattr(intake, "handle", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    off = post(client, token, indicator_alert("flat", NOW - 1))
    assert (off.status_code, off.get_data()) == (unknown.status_code, unknown.get_data())
    assert off.headers["Content-Type"] == unknown.headers["Content-Type"]


def test_one_caller_is_held_to_120_a_minute(client, store):
    _, token = make(store)
    codes = [
        post(client, "sdt_" + f"{n:043d}").status_code
        for n in range(contract.CALLER_PER_MINUTE + 3)
    ]
    assert codes[: contract.CALLER_PER_MINUTE] == [404] * contract.CALLER_PER_MINUTE
    assert codes[contract.CALLER_PER_MINUTE :] == [429] * 3
    limited = post(client, token)
    assert limited.get_json() == {
        "status": "error",
        "message": "Too many alerts. Try again in a minute.",
    }
    assert post(client, token, indicator_alert("flat", NOW), ip="34.212.75.30").status_code == 200


def test_the_alert_route_is_exempt_from_csrf_and_nothing_else_is(store, desk, monkeypatch):
    from flask_wtf.csrf import CSRFProtect

    import blueprints.sudarshan_automation as routes
    from limiter import limiter

    monkeypatch.setattr(intake, "DESK", lambda: desk)
    monkeypatch.setattr(store_mod, "_shared", store_mod.LazyInit(lambda: store, "test"))
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test-only-" + "4" * 32, RATELIMIT_ENABLED=False)
    app.csrf = CSRFProtect(app)  # as app.py keeps it
    limiter.init_app(app)
    app.register_blueprint(routes.sudarshan_automation_bp)
    app.add_url_rule("/other", "other", lambda: "x", methods=["POST"])
    _, token = make(store)
    client = app.test_client()
    sent = post(client, token, indicator_alert("flat", NOW - 1))
    assert sent.status_code == 200  # no CSRF token, and taken
    assert client.post("/other").status_code == 400  # every other POST still needs one


def test_the_traffic_log_never_keeps_the_token():
    from utils.url_redaction import redact_url_credentials

    token = "sdt_" + "k" * 43
    assert redact_url_credentials(f"/sudarshan/tv/{token}") == "/sudarshan/tv/<redacted>"
    line = f"POST https://x.example/sudarshan/tv/{token}?a=1 HTTP/1.1"
    assert token not in redact_url_credentials(line)
    # the upstream rules it was added beside still hold
    assert redact_url_credentials("/strategy/webhook/abc") == "/strategy/webhook/<redacted>"
    assert redact_url_credentials("/flow/webhook/abc/NIFTY") == "/flow/webhook/<redacted>/NIFTY"
    assert redact_url_credentials("/sudarshan/risk/state") == "/sudarshan/risk/state"
