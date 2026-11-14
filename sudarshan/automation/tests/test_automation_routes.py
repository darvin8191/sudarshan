"""Owner routes and the switch (A6). ORDER PATH: the switch decides whether orders go.

The real blueprint runs on a minimal app with CSRF and sessions as OpenAlgo has them; the desk is
the tests' fake. Who may call, what each refusal says, the one-Active-per-contract claim, the Go
Live checks and the phrase.
"""

from __future__ import annotations

import ast
import json
import threading
from pathlib import Path

import pytest
from automation_world import (
    API_KEY,
    NOW,
    TV,
    FakeDesk,
    csrf_headers,
    fresh_owner,
    indicator_alert,
    login_session,
    make_app,
)

import blueprints.sudarshan_automation as routes
from sudarshan.automation import contract, control, flag, intake, messages, payload, words
from sudarshan.automation import model as m
from sudarshan.automation import store as store_mod

REPO = Path(__file__).resolve().parents[3]
API = contract.API_PREFIX
FORM = {"name": "ORB", "kind": "future", "underlying": "NIFTY", "product": "MIS", "lots": 1}
JSON = {"Accept": "application/json"}
ROUTES = [
    ("get", f"{API}/list", None),
    ("get", f"{API}/one?id=1", None),
    ("post", f"{API}/create", FORM),
    ("put", f"{API}/update", {"id": 1, "version": 1, "changes": {"max_age_s": 60}}),
    ("post", f"{API}/state", {"id": 1, "version": 1, "state": "active"}),
    ("post", f"{API}/mode", {"id": 1, "version": 1, "mode": "live", "phrase": "STOP"}),
    ("post", f"{API}/token", {"id": 1, "version": 1}),
    ("get", f"{API}/alerts", None),
    ("post", f"{API}/check", {"id": 1, "message": "{}"}),
    ("get", f"{API}/others", None),
]
CHANGES = [route for route in ROUTES if route[0] != "get"]


@pytest.fixture
def desk():
    return FakeDesk()


@pytest.fixture
def client(store, desk, monkeypatch):
    fresh_owner()
    monkeypatch.setenv(flag.ENV, "sandbox")
    monkeypatch.setattr(intake, "DESK", lambda: desk)
    monkeypatch.setattr(control, "Desk", lambda: desk)
    monkeypatch.setattr(store_mod, "_shared", store_mod.LazyInit(lambda: store, "test"))
    intake._per_link.clear()
    return make_app().test_client()


def call(client, method, path, body=None, headers=None):
    return getattr(client, method)(path, json=body, headers={**JSON, **(headers or {})})


def owner(client, method, path, body=None):
    login_session(client)
    return call(client, method, path, body, csrf_headers(client))


def make(client, **change) -> dict:
    made = owner(client, "post", f"{API}/create", {**FORM, **change})
    assert made.status_code == 200, made.get_json()
    return made.get_json()


# ---- who may call -------------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_owner_routes_need_a_logged_in_session(client, store, method, path, body):
    response = call(client, method, path, body, csrf_headers(client))
    assert response.status_code == 401 and store.count() == 0


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_owner_routes_refuse_any_other_account(client, store, method, path, body):
    login_session(client, "family-viewer")
    response = call(client, method, path, body, csrf_headers(client))
    assert response.status_code == 403
    assert response.get_json() == {"status": "error", "message": words.OWNER_ONLY}
    assert store.count() == 0


@pytest.mark.parametrize(("method", "path", "body"), CHANGES)
def test_changes_need_the_csrf_token(client, store, method, path, body):
    login_session(client)
    assert call(client, method, path, body).status_code == 400
    assert store.count() == 0


def test_every_route_that_is_not_a_get_is_in_write_routes():
    """The family layer's test walks WRITE_ROUTES; one missing from it would go unchecked."""
    tree = ast.parse(Path(routes.__file__).read_text())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and getattr(decorator.func, "attr", "") == "route"
                ):
                    methods = {
                        e.value
                        for k in decorator.keywords
                        if k.arg == "methods"
                        for e in k.value.elts
                    }
                    if methods - {"GET"} and node.name != "alert":
                        found.add(node.name)
    listed = {path.rsplit("/", 1)[1] for _method, path in routes.WRITE_ROUTES}
    assert found == {f"api_{name}" for name in listed} and len(listed) == 6
    assert {(method, API + path) for method, path, *_ in contract.ROUTES} >= set(
        routes.WRITE_ROUTES
    )


def test_the_blueprint_serves_exactly_the_contracts_routes():
    app = make_app()
    served = {
        (method, rule.rule)
        for rule in app.url_map.iter_rules()
        if rule.rule.startswith(API)
        for method in rule.methods - {"HEAD", "OPTIONS"}
    }
    assert served == {(method, API + path) for method, path, *_ in contract.ROUTES}


# ---- create, read, edit -------------------------------------------------------------------------


def test_create_gives_the_link_once_and_no_other_answer_ever_does(client, store):
    made = make(client)
    token = made["token"]
    assert contract.TOKEN.fullmatch(token) and made["link_path"] == f"/sudarshan/tv/{token}"
    strategy = made["strategy"]
    assert set(strategy) == set(contract.STRATEGY)
    assert (strategy["state"], strategy["mode"], strategy["token_tail"]) == (
        "stopped",
        "sandbox",
        token[-4:],
    )
    assert strategy["trades"] == "NIFTY future, this month · Intraday · 1 lot"
    assert strategy["sandbox_run"] == {"alerts": 0, "sessions": 0, "sent": 0, "refused": 0}
    assert set(made["messages"]) == set(contract.ALERT_MESSAGES)
    later = [
        owner(client, "get", f"{API}/list"),
        owner(client, "get", f"{API}/one?id=1"),
        owner(
            client, "put", f"{API}/update", {"id": 1, "version": 1, "changes": {"max_age_s": 60}}
        ),
        owner(client, "post", f"{API}/state", {"id": 1, "version": 2, "state": "active"}),
        owner(client, "get", f"{API}/alerts"),
        owner(client, "post", f"{API}/check", {"id": 1, "message": "{}"}),
    ]
    for answer in later:
        assert answer.status_code == 200, answer.get_json()
        text = answer.get_data(as_text=True)
        assert token not in text and "token_sha256" not in text and API_KEY not in text
    again = owner(client, "post", f"{API}/token", {"id": 1, "version": 3}).get_json()
    assert again["token"] != token and store.by_token(token) is None


def test_a_form_that_cannot_be_saved_is_refused_in_its_sentence(client, store):
    bad = owner(client, "post", f"{API}/create", {**FORM, "kind": "option"})
    assert bad.status_code == 400
    assert bad.get_json() == {
        "status": "error",
        "message": "Option strategies come in a later release.",
    }
    make(client)
    twice = owner(client, "post", f"{API}/create", FORM)
    assert twice.status_code == 400 and "already has this name" in twice.get_json()["message"]


def test_at_most_fifty_strategies(client, store, monkeypatch):
    monkeypatch.setattr(contract, "STRATEGIES_MOST", 2)
    make(client)
    make(client, name="Second")
    third = owner(client, "post", f"{API}/create", {**FORM, "name": "Third"})
    assert (
        third.status_code == 400
        and third.get_json()["message"] == "You can have at most 2 strategies."
    )


def test_the_list_says_the_stage_in_words_and_why_live_waits(client, desk, monkeypatch):
    make(client)
    listing = owner(client, "get", f"{API}/list").get_json()
    assert set(listing) == set(contract.ANSWERS["ListAnswer"])
    assert (listing["stage"], listing["stage_words"]) == (
        "sandbox",
        "Automation is in Sandbox only.",
    )
    assert (
        listing["live_allowed"] is False
        and listing["live_blocked_why"] == words.LIVE_WAITS_FOR_STAGE
    )
    monkeypatch.setenv(flag.ENV, "on")
    assert (
        owner(client, "get", f"{API}/list").get_json()["live_blocked_why"]
        == words.LIVE_WAITS_FOR_GUARD
    )
    desk.guard, desk.kill_switch = "on", True
    listing = owner(client, "get", f"{API}/list").get_json()
    assert (listing["live_allowed"], listing["live_blocked_why"], listing["kill_switch_on"]) == (
        True,
        None,
        True,
    )


def test_the_page_is_told_when_the_proxy_hides_the_senders_address(
    client, store, desk, monkeypatch
):
    made = make(client)
    monkeypatch.setattr(intake, "_local_sender_ms", 0)
    assert owner(client, "get", f"{API}/list").get_json()["notes"] == []
    intake.handle(made["token"], b"{}", "127.0.0.1", desk, store)
    desk.clock = __import__("time").time()
    assert owner(client, "get", f"{API}/list").get_json()["notes"] == [words.SENDER_HIDDEN]


def test_an_edit_carries_the_version_and_is_cleaned_like_a_new_form(client, store):
    make(client)
    edited = owner(client, "put", f"{API}/update", {"id": 1, "version": 1, "changes": {"lots": 2}})
    assert edited.status_code == 200 and edited.get_json()["strategy"]["lots"] == 2
    stale = owner(client, "put", f"{API}/update", {"id": 1, "version": 1, "changes": {"lots": 3}})
    assert (stale.status_code, stale.get_json()["message"]) == (409, words.CHANGED_ELSEWHERE)
    bad = owner(client, "put", f"{API}/update", {"id": 1, "version": 2, "changes": {"lots": 0}})
    assert bad.status_code == 400 and "Lots must be a whole number" in bad.get_json()["message"]
    sneaky = owner(
        client, "put", f"{API}/update", {"id": 1, "version": 2, "changes": {"mode": "live"}}
    )
    assert (
        sneaky.status_code == 400 and store.strategy(1).mode == "sandbox"
    )  # the mode has its route
    gone = owner(client, "put", f"{API}/update", {"id": 9, "version": 1, "changes": {"lots": 2}})
    assert (gone.status_code, gone.get_json()["message"]) == (404, words.NOT_FOUND)


def test_while_a_position_is_held_only_the_name_and_the_limits_change(client, store, desk):
    made = make(client)
    owner(client, "post", f"{API}/state", {"id": 1, "version": 1, "state": "active"})
    intake.handle(made["token"], indicator_alert("long", NOW - 1), TV, desk, store)
    version = store.strategy(1).version
    for changes in (
        {"lots": 2},
        {"product": "NRML"},
        {"underlying": "BANKNIFTY"},
        {"direction": "long"},
    ):
        refused = owner(
            client, "put", f"{API}/update", {"id": 1, "version": version, "changes": changes}
        )
        assert refused.status_code == 409
        assert refused.get_json()["message"] == (
            "ORB holds NIFTY 24 Nov FUT long in the Sandbox. Close it, then change this."
        )
    ok = owner(
        client, "put", f"{API}/update",
        {"id": 1, "version": version, "changes": {"name": "ORB 5m", "max_alerts_day": 30}},
    )  # fmt: skip
    assert ok.status_code == 200 and ok.get_json()["strategy"]["name"] == "ORB 5m"
    assert ok.get_json()["strategy"]["held_symbol"] == "NIFTY24NOV26FUT"


# ---- the switch ---------------------------------------------------------------------------------


def test_start_and_the_two_stops(client, store, desk):
    make(client)
    started = owner(client, "post", f"{API}/state", {"id": 1, "version": 1, "state": "active"})
    assert started.get_json()["strategy"]["state"] == "active"
    exits = owner(client, "post", f"{API}/state", {"id": 1, "version": 2, "state": "exits_only"})
    assert exits.get_json()["strategy"]["stopped_why"] == "by you, from the app"
    stopped = owner(client, "post", f"{API}/state", {"id": 1, "version": 3, "state": "stopped"})
    assert stopped.get_json()["strategy"]["state"] == "stopped"
    assert [e["state"] for e in desk.emitted if e["kind"] == "strategy"] == [
        "active",
        "exits_only",
        "stopped",
    ]
    assert [t[2] for t in desk.told] == [
        "ORB is Active. Changed from the app.",
        "ORB is Stopped for new entries. Its exit alert still goes. Changed from the app.",
        "ORB is Stopped. Its alerts are ignored. Changed from the app.",
    ]
    assert desk.phrases_asked == []  # a Sandbox strategy starts without the phrase
    odd = owner(client, "post", f"{API}/state", {"id": 1, "version": 4, "state": "armed"})
    assert odd.status_code == 400 and odd.get_json()["message"] == "Choose Active or Stopped."
    stale = owner(client, "post", f"{API}/state", {"id": 1, "version": 1, "state": "active"})
    assert stale.status_code == 409


def test_a_second_strategy_on_the_same_contract_is_refused_with_its_sentence(client, store):
    make(client)
    make(client, name="Second")
    owner(client, "post", f"{API}/state", {"id": 1, "version": 1, "state": "active"})
    refused = owner(client, "post", f"{API}/state", {"id": 2, "version": 1, "state": "active"})
    assert refused.status_code == 409
    assert refused.get_json()["message"] == (
        "Second was not started: ORB already trades the same contract and product. Two strategies "
        "cannot share one position. Stop the other first, or choose another product."
    )
    assert store.strategy(2).state == "stopped"


@pytest.mark.parametrize("round_", range(5))
def test_barrier_4_two_strategies_started_together_on_one_contract(store, desk, round_):
    """One Active, one refused with its sentence. The claim is the unique index; the mutation
    that drops it leaves both Active."""
    first, _ = store.create(contract.clean(FORM))
    second, _ = store.create(contract.clean({**FORM, "name": "Second"}))
    barrier, results = threading.Barrier(2), {}

    def start(strategy):
        barrier.wait(timeout=10)
        try:
            got = control.switch(
                strategy.id, strategy.version, m.ACTIVE, "the app", desk=desk, store=store
            )
            results[strategy.name] = got.state
        except control.Refused as refused:
            results[strategy.name] = str(refused)

    threads = [threading.Thread(target=start, args=(s,)) for s in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(results.values(), key=len)[0] == "active"
    assert "already trades the same contract and product" in sorted(results.values(), key=len)[1]
    assert [s.state for s in store.strategies()].count(m.ACTIVE) == 1


# ---- Go Live ------------------------------------------------------------------------------------


def go_live(client, version=1, phrase="STOP"):
    return owner(
        client,
        "post",
        f"{API}/mode",
        {"id": 1, "version": version, "mode": "live", "phrase": phrase},
    )


def test_live_cannot_be_chosen_at_stage_sandbox(client, store, desk):
    make(client)
    refused = go_live(client)
    assert (refused.status_code, refused.get_json()["message"]) == (409, words.LIVE_WAITS_FOR_STAGE)
    assert store.strategy(1).mode == "sandbox" and desk.phrases_asked == []


def test_live_waits_for_the_risk_guard(client, store, desk, monkeypatch):
    """Mutation 10 at the switch."""
    monkeypatch.setenv(flag.ENV, "on")
    make(client)
    for guard in ("off", "sandbox"):
        desk.guard = guard
        refused = go_live(client)
        assert (refused.status_code, refused.get_json()["message"]) == (
            409,
            words.LIVE_WAITS_FOR_GUARD,
        )
    assert store.strategy(1).mode == "sandbox" and desk.phrases_asked == []


def test_live_needs_the_phrase_and_then_starting_needs_it_again(client, store, desk, monkeypatch):
    monkeypatch.setenv(flag.ENV, "on")
    desk.guard = "on"
    make(client)
    for wrong in ("", "stop it", None):
        refused = go_live(client, phrase=wrong)
        assert (refused.status_code, refused.get_json()["message"]) == (400, words.WRONG_PHRASE)
    assert store.strategy(1).mode == "sandbox"
    live = go_live(client, phrase=" stop ")  # case and surrounding space aside, as the risk layer
    assert live.status_code == 200 and live.get_json()["strategy"]["mode"] == "live"
    assert desk.phrases_asked[-1] == ("set ORB to Live", "the app")
    assert desk.told[-1][2] == "ORB now sends to Upstox (Live). Changed from the app."
    # starting a Live strategy
    no_phrase = owner(client, "post", f"{API}/state", {"id": 1, "version": 2, "state": "active"})
    assert (no_phrase.status_code, no_phrase.get_json()["message"]) == (400, words.WRONG_PHRASE)
    assert store.strategy(1).state == "stopped"
    started = owner(
        client, "post", f"{API}/state", {"id": 1, "version": 2, "state": "active", "phrase": "STOP"}
    )
    assert started.status_code == 200 and desk.phrases_asked[-1] == ("start ORB Live", "the app")
    # stopping never needs it
    stopped = owner(client, "post", f"{API}/state", {"id": 1, "version": 3, "state": "stopped"})
    assert stopped.status_code == 200
    # and a Live strategy cannot be started once the guard steps back
    desk.guard = "sandbox"
    again = owner(
        client, "post", f"{API}/state", {"id": 1, "version": 4, "state": "active", "phrase": "STOP"}
    )
    assert (again.status_code, again.get_json()["message"]) == (409, words.LIVE_WAITS_FOR_GUARD)


def test_the_mode_cannot_change_while_a_position_is_held(client, store, desk, monkeypatch):
    monkeypatch.setenv(flag.ENV, "on")
    desk.guard = "on"
    made = make(client)
    owner(client, "post", f"{API}/state", {"id": 1, "version": 1, "state": "active"})
    intake.handle(made["token"], indicator_alert("long", NOW - 1), TV, desk, store)
    refused = go_live(client, version=2)
    assert refused.status_code == 409
    assert refused.get_json()["message"] == (
        "ORB holds NIFTY 24 Nov FUT long in the Sandbox. Close it, then change this."
    )
    assert store.strategy(1).mode == "sandbox"


def test_the_real_phrase_check_reads_the_risk_layers_and_records_a_wrong_try(tmp_path, monkeypatch):
    from sudarshan.automation.desk import Desk
    from sudarshan.guard import store as risk_store

    fresh_owner()
    fresh = risk_store.RiskStore(tmp_path / "risk.db")
    monkeypatch.setattr(risk_store, "STORE", fresh)
    try:
        desk = Desk()
        assert desk.phrase_ok("stop", "start ORB Live", "app") is True  # the default phrase
        assert desk.phrase_ok("", "start ORB Live", "app") is False
        assert fresh.history() == []  # an empty box is asked for again and not recorded
        assert desk.phrase_ok("guess", "start ORB Live", "app") is False
        (entry,) = fresh.history()
        assert entry.kind == "phrase_refused" and "guess" not in json.dumps(entry.detail)
        assert entry.detail["asked"] == "start ORB Live"
        fresh.save_settings("test", confirm_phrase="MOR")
        assert (
            desk.phrase_ok("stop", "x", "app") is False
            and desk.phrase_ok("mor", "x", "app") is True
        )
    finally:
        fresh.dispose()


# ---- the log, the dry run, the messages ---------------------------------------------------------


def test_the_log_is_paged_and_never_shows_the_stored_text_or_the_address(
    client, store, desk, monkeypatch
):
    made = make(client, max_alerts_day=200)
    owner(client, "post", f"{API}/state", {"id": 1, "version": 1, "state": "active"})
    monkeypatch.setattr(contract, "ALERTS_PAGE", 3)
    for n in range(5):
        intake.handle(made["token"], indicator_alert("flat", NOW - 5 + n), TV, desk, store)
    page = owner(client, "get", f"{API}/alerts?strategy=1").get_json()
    assert len(page["alerts"]) == 3 and page["more"] is True
    assert set(page["alerts"][0]) == set(contract.ALERT)
    older = owner(
        client, "get", f"{API}/alerts?strategy=1&before={page['alerts'][-1]['id']}"
    ).get_json()
    assert len(older["alerts"]) == 2 and older["more"] is False
    assert TV not in json.dumps(page) and "payload" not in json.dumps(page)


def test_a_line_left_claimed_by_a_stopped_server_reads_not_confirmed(client, store, desk):
    made = make(client)
    store.note_alert(1, verdict="claimed", received_ms=int(NOW * 1000) - 200_000, claim="1|-|long:")
    store.note_alert(1, verdict="claimed", received_ms=int(NOW * 1000) - 2_000, claim="2|-|long:")
    lines = owner(client, "get", f"{API}/alerts?strategy=1").get_json()["alerts"]
    assert [line["verdict"] for line in lines] == ["claimed", "not_confirmed"]
    assert lines[1]["sentence"] == (
        "ORB: the server stopped while this alert was being handled, so its order is not "
        "confirmed. Check Orders."
    )
    assert made["strategy"]["last_alert"] is None


def test_check_is_a_dry_run(client, store, desk):
    make(client)
    owner(client, "post", f"{API}/state", {"id": 1, "version": 1, "state": "active"})
    text = json.dumps({"v": 1, "position": "long", "fired": int((NOW - 1) * 1000)})
    got = owner(client, "post", f"{API}/check", {"id": 1, "message": text}).get_json()
    assert set(got) == set(contract.ANSWERS["CheckAnswer"])
    assert got["sentence"] == "Would send an order to the Sandbox to be long 65 NIFTY 24 Nov FUT."
    assert desk.placed == [] and store.alerts(1) == []
    bad = owner(client, "post", f"{API}/check", {"id": 1, "message": "x" * 9000})
    assert bad.status_code == 400


def test_others_is_read_only(client, desk):
    got = owner(client, "get", f"{API}/others").get_json()
    assert got["status"] == "success" and set(got["others"][0]) == set(contract.OTHER)


@pytest.mark.parametrize(
    ("direction", "sides"),
    [("both", ["long", "short", "flat"]), ("long", ["long", "flat"]), ("short", ["short", "flat"])],
)
def test_every_alert_message_given_to_the_owner_is_one_the_intake_reads(direction, sides):
    """Each form, filled in as TradingView would, parses. None carries a secret."""
    given = messages.for_strategy(m.Strategy(id=1, name="ORB", direction=direction))
    filled = given["strategy"]
    for placeholder, value in {
        "{{strategy.order.id}}": "Long", "{{strategy.order.action}}": "buy",
        "{{strategy.order.contracts}}": "1", "{{strategy.market_position}}": "long",
        "{{strategy.market_position_size}}": "1", "{{strategy.prev_market_position}}": "flat",
        "{{strategy.prev_market_position_size}}": "0", "{{strategy.position_size}}": "1",
        "{{strategy.order.price}}": "24510.5", "{{exchange}}": "NSE", "{{ticker}}": "NIFTY1!",
        "{{interval}}": "1", "{{time}}": "2026-11-16T04:44:00Z", "{{timenow}}": "2026-11-16T04:45:02Z",
    }.items():  # fmt: skip
        filled = filled.replace(placeholder, value)
    alert = payload.parse(filled)
    assert (alert.position, alert.before, alert.order_id, alert.ticker) == (
        "long",
        "flat",
        "Long",
        "NIFTY1!",
    )
    seen = []
    for text in given["condition"]:
        for placeholder, value in {
            "{{exchange}}": "NSE", "{{ticker}}": "NIFTY", "{{interval}}": "5",
            "{{time}}": "2026-11-16T04:44:00Z", "{{timenow}}": "2026-11-16T04:45:02Z",
        }.items():  # fmt: skip
            text = text.replace(placeholder, value)
        seen.append(payload.parse(text).position)
    assert seen == sides
    # the indicator's script writes this text itself
    scripted = '{"v":1,"position":"short","ticker":"NSE:NIFTY","interval":"5","bar":1794804240000,"fired":1794804302431}'
    assert payload.parse(scripted).fired_s == 1794804302
    assert "f_msg" in given["indicator"] and "alert.freq_once_per_bar_close" in given["indicator"]
    everything = json.dumps(given)
    assert "apikey" not in everything.lower() and "sdt_" not in everything
