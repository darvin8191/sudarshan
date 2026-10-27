"""The risk layer's routes (M5.13, M16.7, M20.7): who may call them, CSRF, the phrase, and the
answer each situation gets.

The real blueprint runs on a minimal app (guard_world.make_app) with CSRF and the limiter as
OpenAlgo has them; the services the kill switch's steps call are recorded (guard_world.Account).
"""

from __future__ import annotations

import ast
import json
from datetime import timedelta
from pathlib import Path

import pytest
from guard_world import (
    FAKE_API_KEY,
    OWNER,
    Account,
    add_owner,
    csrf_headers,
    login_session,
    make_app,
    reset_openalgo_db,
)

import blueprints.sudarshan_risk as risk_routes
from sudarshan.guard import daily, flag, gate, killswitch, settings_rules, words
from sudarshan.guard.store import KillSwitchState, RiskSettings, utcnow

REPO = Path(__file__).resolve().parents[3]
JSON = {"Accept": "application/json"}
OWNER_ROUTES = [
    ("get", "/sudarshan/risk/state", None),
    ("get", "/sudarshan/risk/status", None),
    ("get", "/sudarshan/risk/preview", None),
    ("post", "/sudarshan/risk/killswitch", {"phrase": "STOP"}),
    ("post", "/sudarshan/risk/resume", {"phrase": "STOP"}),
    ("get", "/sudarshan/risk/settings", None),
    ("put", "/sudarshan/risk/settings", {"settings": {"size_on": True}}),
    ("get", "/sudarshan/risk/audit", None),
]
CHANGES = [route for route in OWNER_ROUTES if route[0] != "get"]


@pytest.fixture
def world(monkeypatch, risk_store):
    reset_openalgo_db()
    add_owner()
    fresh = gate.Gate(store=risk_store)
    fresh.set_state(KillSwitchState())
    monkeypatch.setattr(gate, "GATE", fresh)
    monkeypatch.setenv(flag.ENV, "sandbox")
    monkeypatch.setattr(killswitch, "DRAIN_S", 0.2)
    account = Account(monkeypatch)
    app = make_app()
    return app.test_client(), account, risk_store


def _call(client, method, path, body=None, headers=None):
    return getattr(client, method)(path, json=body, headers={**JSON, **(headers or {})})


def _owner_call(client, method, path, body=None):
    login_session(client)
    return _call(client, method, path, body, csrf_headers(client))


# -- who may call ----------------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "body"), OWNER_ROUTES)
def test_owner_routes_need_a_logged_in_session(world, method, path, body):
    client, account, store = world
    response = _call(client, method, path, body, csrf_headers(client))
    assert response.status_code == 401
    assert account.calls == [] and not store.state().on


@pytest.mark.parametrize(("method", "path", "body"), OWNER_ROUTES)
def test_owner_routes_refuse_any_other_account(world, method, path, body):
    client, account, store = world
    login_session(client, "family-viewer")
    response = _call(client, method, path, body, csrf_headers(client))
    assert response.status_code == 403
    assert response.get_json()["message"] == words.OWNER_ONLY
    assert account.calls == [] and not store.state().on


@pytest.mark.parametrize(("method", "path", "body"), CHANGES)
def test_changes_need_the_csrf_token(world, method, path, body):
    client, account, store = world
    login_session(client)
    assert _call(client, method, path, body).status_code == 400
    assert account.calls == [] and not store.state().on


def test_every_route_that_changes_something_is_listed_for_the_family_layer():
    """The future viewer test walks WRITE_ROUTES; a write route missing from it would go
    unchecked."""
    app = make_app()
    found = set()
    for rule in app.url_map.iter_rules():
        if rule.rule.startswith("/sudarshan/risk/"):
            found |= {(method, rule.rule) for method in rule.methods - {"GET", "HEAD", "OPTIONS"}}
    assert found == set(risk_routes.WRITE_ROUTES)


def test_the_app_registers_the_blueprint_and_the_bot_its_command():
    """Read, not imported: importing app.py starts the whole platform."""
    tree = ast.parse((REPO / "app.py").read_text(encoding="utf-8"))
    imported = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "blueprints.sudarshan_risk"
        for alias in node.names
    ]
    registered = [
        node.args[0].id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", None) == "register_blueprint"
        and node.args
        and isinstance(node.args[0], ast.Name)
    ]
    assert imported == ["sudarshan_risk_bp"] and "sudarshan_risk_bp" in registered
    bot = (REPO / "services" / "telegram_bot_service.py").read_text(encoding="utf-8")
    assert 'CommandHandler("killswitch", self.cmd_killswitch)' in bot
    assert "risk_telegram.handle(self, update, context)" in bot
    help_text = bot[bot.index("async def cmd_help") : bot.index("async def cmd_link")]
    assert "\n/killswitch on|exit|off STOP: stop all new orders (owner only)\n" in help_text


# -- turning it on and off ----------------------------------------------------------------------


def test_turning_it_on_from_the_app(world, monkeypatch):
    client, account, store = world
    answer = _owner_call(client, "post", "/sudarshan/risk/killswitch", {"phrase": "halt"})
    assert (answer.status_code, answer.get_json()["message"]) == (400, words.PHRASE_WRONG)
    assert not store.state().on

    answer = _owner_call(client, "post", "/sudarshan/risk/killswitch", {"phrase": " stop "})
    body = answer.get_json()
    assert answer.status_code == 200 and body["status"] == "success"
    assert body["message"] == words.ks_report_lead(store.state().report)
    assert body["data"]["on"] is True and body["data"]["surface"] == "app"
    assert body["data"]["exit_all"] is False and store.state().on
    assert body["activated"] is True

    again = _owner_call(client, "post", "/sudarshan/risk/killswitch", {"phrase": "STOP"})
    assert again.status_code == 200 and again.get_json()["activated"] is False
    assert again.get_json()["message"].startswith("The kill switch is already on")


def test_while_it_is_being_turned_on_another_activation_gets_409(world, monkeypatch):
    client, account, store = world
    running = KillSwitchState(on=True, since=utcnow(), running=True, activation_id="ks-busy")
    gate.GATE.set_state(running)
    monkeypatch.setattr(killswitch, "_running", {"ks-busy"})
    answer = _owner_call(client, "post", "/sudarshan/risk/killswitch", {"phrase": "STOP"})
    assert (answer.status_code, answer.get_json()["message"]) == (409, words.KS_BEING_TURNED_ON)
    resume = _owner_call(client, "post", "/sudarshan/risk/resume", {"phrase": "STOP"})
    assert resume.status_code == 409


def test_with_the_guard_off_on_this_server_the_switch_answers_503(world, monkeypatch):
    client, account, store = world
    monkeypatch.setenv(flag.ENV, "off")
    answer = _owner_call(client, "post", "/sudarshan/risk/killswitch", {"phrase": "STOP"})
    assert (answer.status_code, answer.get_json()["message"]) == (503, words.KS_NOT_HERE)


@pytest.mark.parametrize("exit_all", ["false", "true", 1, None])
def test_exit_all_is_only_ever_a_json_true_or_false(world, exit_all):
    """A script's "false" in quotes must never exit every position."""
    client, account, store = world
    answer = _owner_call(
        client, "post", "/sudarshan/risk/killswitch", {"phrase": "STOP", "exit_all": exit_all}
    )
    assert (answer.status_code, answer.get_json()["message"]) == (
        400,
        words.EXIT_ALL_NOT_UNDERSTOOD,
    )
    assert account.calls == [] and not store.state().on


def test_resume_needs_the_phrase_and_the_minute(world):
    client, account, store = world
    assert (
        _owner_call(client, "post", "/sudarshan/risk/killswitch", {"phrase": "STOP"}).status_code
        == 200
    )

    early = _owner_call(client, "post", "/sudarshan/risk/resume", {"phrase": "STOP"})
    assert early.status_code == 423
    assert early.get_json()["message"].startswith("The kill switch can be turned off")
    wrong = _owner_call(client, "post", "/sudarshan/risk/resume", {"phrase": "go"})
    assert (wrong.status_code, wrong.get_json()["message"]) == (400, words.PHRASE_WRONG)
    assert store.state().on
    refused = [row for row in store.history() if row.kind == "resume_refused"]
    assert [row.summary for row in refused] == [
        words.ks_resume_refused(words.PHRASE_WRONG),
        words.ks_resume_refused(early.get_json()["message"]),
    ]

    held = store.state()
    gate.GATE.set_state(KillSwitchState(**{
        **held.__dict__, "since": held.since - timedelta(seconds=61)}))  # fmt: skip
    done = _owner_call(client, "post", "/sudarshan/risk/resume", {"phrase": "STOP"})
    assert (done.status_code, done.get_json()["message"]) == (200, words.KS_RESUMED)
    assert done.get_json()["data"]["on"] is False and not store.state().on
    again = _owner_call(client, "post", "/sudarshan/risk/resume", {"phrase": "STOP"})
    assert (again.status_code, again.get_json()["message"]) == (200, words.KS_ALREADY_OFF)


# -- scripts and alerts: on only (amendment R8) ---------------------------------------------------


def test_the_api_key_route_turns_it_on_without_a_session_or_csrf(world):
    client, account, store = world
    reset_openalgo_db()
    add_owner(api_key=True)
    answer = _call(client, "post", "/sudarshan/risk/api/killswitch", {"apikey": FAKE_API_KEY})
    body = answer.get_json()
    assert answer.status_code == 200, body
    assert body["data"]["on"] is True and body["data"]["surface"] == "api"
    assert body["data"]["actor"] == "api key"
    assert "sandbox_cancel_all" in account.calls  # the key reaches the sandbox's books


@pytest.mark.parametrize("key", [None, "", "not-the-key", 12345])
def test_the_api_key_route_refuses_a_key_that_is_not_valid(world, key):
    client, account, store = world
    answer = _call(client, "post", "/sudarshan/risk/api/killswitch", {"apikey": key})
    assert (answer.status_code, answer.get_json()["message"]) == (403, words.API_KEY_NOT_VALID)
    assert account.calls == [] and not store.state().on


def test_a_script_can_never_resume(world):
    """The only route without a session is the activation; resume needs the owner's session,
    and a call without one is refused whether or not it carries a CSRF token."""
    client, account, store = world
    gate.GATE.set_state(KillSwitchState(on=True, since=utcnow() - timedelta(minutes=5)))
    body = {"apikey": FAKE_API_KEY, "phrase": "STOP"}
    assert _call(client, "post", "/sudarshan/risk/resume", body).status_code == 400
    with_token = _call(client, "post", "/sudarshan/risk/resume", body, csrf_headers(client))
    assert with_token.status_code == 401 and gate.GATE.state().on
    api_routes = [path for _method, path in risk_routes.WRITE_ROUTES if "/api/" in path]
    assert api_routes == ["/sudarshan/risk/api/killswitch"]


def test_too_many_calls_are_answered_in_words(monkeypatch, risk_store):
    reset_openalgo_db()
    add_owner()
    monkeypatch.setattr(gate, "GATE", gate.Gate(store=risk_store))
    client = make_app(rate_limits=True).test_client()
    answers = [
        _call(client, "post", "/sudarshan/risk/api/killswitch", {"apikey": "nope"})
        for _ in range(6)
    ]
    assert [a.status_code for a in answers] == [403] * 5 + [429]
    assert answers[-1].get_json()["message"] == words.TOO_MANY_REQUESTS


# -- what the page reads -----------------------------------------------------------------------


def test_status_shows_the_stage_switch_settings_and_timing_and_never_the_phrase(world):
    client, account, store = world
    store.save_settings("test", confirm_phrase="peacock blue")
    answer = _owner_call(client, "get", "/sudarshan/risk/status")
    assert answer.status_code == 200
    assert "peacock" not in answer.get_data(as_text=True).lower()
    data = answer.get_json()["data"]
    assert data["stage"] == "sandbox"
    assert data["kill_switch"]["on"] is False and data["kill_switch"]["known"] is True
    assert data["settings"]["phrase_set"] is True and "confirm_phrase" not in data["settings"]
    assert set(data["gate"]) == {"orders", "p50_ms", "p95_ms", "max_ms", "last_problem"}
    assert data["upstox_kill_switch"] is None  # no broker login in this session


def test_the_banner_s_read_carries_the_switch_and_reads_no_broker(world, monkeypatch):
    client, account, store = world
    from sudarshan.guard import readers

    def no_broker(*args, **kwargs):
        raise AssertionError("the banner's read reached the broker")

    monkeypatch.setattr(readers, "upstox_kill_switch", no_broker)
    monkeypatch.setattr(killswitch, "owner_for", no_broker)
    data = _owner_call(client, "get", "/sudarshan/risk/state").get_json()["data"]
    assert data == {
        "stage": "sandbox",
        "kill_switch": killswitch.public_state(KillSwitchState()),
        "daily": {"sandbox": {"locked": False}},
    }
    gate.GATE.set_state(KillSwitchState(on=True, since=utcnow(), surface="telegram"))
    data = _owner_call(client, "get", "/sudarshan/risk/state").get_json()["data"]
    assert data["kill_switch"]["on"] is True and data["kill_switch"]["surface"] == "telegram"


def _today() -> str:
    from datetime import UTC, datetime

    return daily.day_of(datetime.now(UTC)).isoformat()


def _lock_today(pipe: str, figure: float, limit: float, monkeypatch) -> None:
    """Lock the day as an order at the limit does: the gate writes it and keeps it."""
    import extensions

    monkeypatch.setattr(extensions, "emit_from_any_thread", lambda *args, **kwargs: None)
    guards = gate._Guards(_today(), RiskSettings(daily_loss_limit=limit))
    gate.GATE._lock_day(pipe, guards, daily.DayFigure(figure, 0.0))


def test_the_banner_s_read_says_which_covered_book_the_day_has_locked(world, monkeypatch):
    client, account, store = world
    store.lock_day("sandbox", _today(), -4100.0, 4000.0)  # before a restart: read at first use
    data = _owner_call(client, "get", "/sudarshan/risk/state").get_json()["data"]
    assert data["daily"] == {"sandbox": {"locked": True}}  # "sandbox": Live is not covered
    monkeypatch.setenv(flag.ENV, "on")
    data = _owner_call(client, "get", "/sudarshan/risk/state").get_json()["data"]
    assert data["daily"] == {"live": {"locked": False}, "sandbox": {"locked": True}}


def test_status_shows_today_s_figure_the_limit_and_the_lock(world, monkeypatch):
    client, account, store = world
    from sudarshan.guard import readers

    monkeypatch.setattr(readers, "sandbox_day", lambda user: daily.DayFigure(-3000.0, -250.5))
    store.save_settings("test", daily_loss_limit=4000.0)
    shown = _owner_call(client, "get", "/sudarshan/risk/status").get_json()["data"]["daily"]
    assert set(shown) == {"sandbox"}
    day = shown["sandbox"]
    assert (day["figure"], day["approximate"], day["reached"]) == (-3250.5, False, False)
    assert (day["lock"], day["unchecked"]) == (None, False) and day["as_of"]

    monkeypatch.setattr(readers, "sandbox_day", lambda user: daily.DayFigure(-4000.0, 0.0))
    _lock_today("sandbox", -4000.0, 4000.0, monkeypatch)
    day = _owner_call(client, "get", "/sudarshan/risk/status").get_json()["data"]["daily"]
    day = day["sandbox"]
    assert day["reached"] is True
    assert (day["lock"]["figure"], day["lock"]["limit"]) == (-4000.0, 4000.0)


def test_status_reads_the_live_figure_from_upstox_s_rows(world, monkeypatch):
    client, account, store = world
    from sudarshan.guard import readers

    monkeypatch.setenv(flag.ENV, "on")
    owner = killswitch.Owner(OWNER, "app", OWNER, auth_token="token", broker="upstox")
    monkeypatch.setattr(killswitch, "owner_for", lambda *args, **kwargs: owner)
    monkeypatch.setattr(readers, "upstox_kill_switch", lambda auth: [])
    monkeypatch.setattr(readers, "sandbox_day", lambda user: daily.DayFigure(0.0, 0.0))
    monkeypatch.setattr(readers, "live_cached_rows", lambda auth: None)
    rows = [{"realised": -1200.0, "unrealised": 200.0}, {"pnl": -50.0}]  # one has no day figures
    monkeypatch.setattr(readers, "live_rows", lambda auth: rows)
    live = _owner_call(client, "get", "/sudarshan/risk/status").get_json()["data"]["daily"]["live"]
    assert (live["figure"], live["approximate"], live["reached"]) == (-1050.0, True, False)


def test_a_figure_that_cannot_be_read_is_shown_as_unknown(world, monkeypatch):
    client, account, store = world
    from sudarshan.guard import readers

    def unreadable(user):
        raise readers.Unreadable("sandbox day")

    monkeypatch.setattr(readers, "sandbox_day", unreadable)
    answer = _owner_call(client, "get", "/sudarshan/risk/status")
    assert answer.status_code == 200
    day = answer.get_json()["data"]["daily"]["sandbox"]
    assert (day["figure"], day["reached"]) == (None, False)


def test_status_says_the_pace_this_server_sends_orders_at(world):
    from broker.upstox.api import rate_limiter

    client, account, store = world
    data = _owner_call(client, "get", "/sudarshan/risk/status").get_json()["data"]
    assert data["pacing"] == {"per_second": rate_limiter.ORDER_LIMITER.max_per_second}


def test_status_reads_upstox_s_own_kill_switch_when_logged_in_to_upstox(world, monkeypatch):
    client, account, store = world
    from sudarshan.guard import readers

    owner = killswitch.Owner(OWNER, "app", OWNER, auth_token="token", broker="upstox")
    monkeypatch.setattr(killswitch, "owner_for", lambda *args, **kwargs: owner)
    monkeypatch.setattr(readers, "upstox_kill_switch", lambda auth: [
        {"segment": "NSE_FO", "on": False, "active": True},
        {"segment": "NSE_EQ", "on": True, "active": True}])  # fmt: skip
    data = _owner_call(client, "get", "/sudarshan/risk/status").get_json()["data"]
    assert data["upstox_kill_switch"] == [
        {"segment": "NSE_FO", "on": False, "active": True, "label": "NSE F&O"},
        {"segment": "NSE_EQ", "on": True, "active": True, "label": "NSE cash"},
    ]


def test_upstox_s_own_kill_switch_is_read_and_kept_a_minute(monkeypatch):
    import broker.upstox.api.order_api as order_api
    from sudarshan.guard import readers

    calls = []

    def answer(endpoint, auth, method="GET", **kwargs):
        calls.append((endpoint, method))
        return {
            "status": "success",
            "data": [
                {"segment": "NSE_FO", "segment_status": "ACTIVE", "kill_switch_enabled": False},
                {"segment": "MCX_FO", "segment_status": "INACTIVE", "kill_switch_enabled": True},
            ],
        }

    monkeypatch.setattr(order_api, "get_api_response", answer)
    readers._upstox_kill_switch.clear()
    first = readers.upstox_kill_switch("token-a")
    assert readers.upstox_kill_switch("token-a") == first
    assert first == [
        {"segment": "NSE_FO", "on": False, "active": True},
        {"segment": "MCX_FO", "on": True, "active": False},
    ]
    assert calls == [("/v2/user/kill-switch", "GET")]
    monkeypatch.setattr(order_api, "get_api_response", lambda *a, **k: {"status": "error"})
    readers._upstox_kill_switch.clear()
    with pytest.raises(readers.Unreadable):
        readers.upstox_kill_switch("token-a")


def test_preview_says_what_turning_it_on_will_cancel(world, monkeypatch):
    client, account, store = world
    counts = {"orders": 3, "gtts": 1, "stops": 1, "protected": 1, "positions": 2}
    monkeypatch.setattr(killswitch, "preview", lambda owner: counts)
    data = _owner_call(client, "get", "/sudarshan/risk/preview").get_json()["data"]
    assert data == {"counts": counts, "message": words.ks_preview(counts)}
    assert data["message"].startswith("3 open orders and 1 GTT will be cancelled, including 1")

    def broken(owner):
        raise RuntimeError("the book could not be read")

    monkeypatch.setattr(killswitch, "preview", broken)
    data = _owner_call(client, "get", "/sudarshan/risk/preview").get_json()["data"]
    assert data == {"counts": None, "message": words.ks_preview(None)}


def test_the_audit_writes_counts_kept_in_memory_before_it_is_read(world, monkeypatch):
    client, account, store = world
    order = []
    monkeypatch.setattr(gate.GATE, "flush_notes", lambda: order.append("flush"))
    history = store.history
    monkeypatch.setattr(store, "history", lambda limit=50: order.append("read") or history(limit))
    store.audit("ks_on", "Kill switch on.", surface="app", actor=OWNER)
    rows = _owner_call(client, "get", "/sudarshan/risk/audit?limit=x").get_json()["data"]
    assert order == ["flush", "read"]
    assert [(row["kind"], row["summary"], row["count"]) for row in rows] == [
        ("ks_on", "Kill switch on.", 1)
    ]


# -- settings ------------------------------------------------------------------------------------


def _put(client, settings, phrase=None):
    body = {"settings": settings, **({"phrase": phrase} if phrase is not None else {})}
    return _owner_call(client, "put", "/sudarshan/risk/settings", body)


def test_tightening_needs_no_phrase_and_loosening_does(world):
    client, account, store = world
    on = _put(client, {"size_on": True, "max_lots_fo": 2})
    assert (on.status_code, on.get_json()["message"]) == (200, words.SETTINGS_SAVED)
    assert on.get_json()["data"]["size_on"] is True and store.settings().max_lots_fo == 2

    looser = _put(client, {"max_lots_fo": 5})
    assert (looser.status_code, looser.get_json()["message"]) == (400, words.LOOSEN_NEEDS_PHRASE)
    assert looser.get_json()["needs_phrase"] is True  # the page asks for it and sends it again
    assert on.get_json()["needs_phrase"] is False
    assert store.settings().max_lots_fo == 2
    off = _put(client, {"size_on": False}, phrase="nope")
    assert off.status_code == 400 and store.settings().size_on is True
    assert (off.get_json()["message"], off.get_json()["needs_phrase"]) == (words.PHRASE_WRONG, True)

    done = _put(client, {"max_lots_fo": 5, "size_on": False}, phrase="Stop")
    assert done.status_code == 200
    assert store.settings().max_lots_fo == 5 and store.settings().size_on is False
    rows = [row for row in store.history() if row.kind == "settings_changed"]
    assert [row.summary for row in rows] == [
        words.settings_changed(["max_lots_fo", "size_on"], True),
        words.settings_changed(["size_on"], False),  # 2 lots is already the value kept
    ]


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"max_lots_fo": 0}, words.setting_invalid("max_lots_fo")),
        ({"max_lots_fo": 2.5}, words.setting_invalid("max_lots_fo")),
        ({"band_opt_pct": "20"}, words.setting_invalid("band_opt_pct")),
        ({"band_on": "yes"}, words.setting_invalid("band_on")),
        ({"daily_loss_limit": 0}, words.setting_invalid("daily_loss_limit")),
        ({"no_such_setting": 1}, words.SETTINGS_NOT_UNDERSTOOD),
        (None, words.SETTINGS_NOT_UNDERSTOOD),
        ({"confirm_phrase": "stop!"}, words.PHRASE_INVALID),
    ],
)
def test_a_setting_that_cannot_be_saved_says_which(world, settings, message):
    client, account, store = world
    before = store.settings()
    answer = _put(client, settings, phrase="STOP")
    assert (answer.status_code, answer.get_json()["message"]) == (400, message)
    assert store.settings() == before


def test_a_new_phrase_needs_the_old_one_and_is_never_shown(world):
    client, account, store = world
    assert _put(client, {"confirm_phrase": "Kesari"}).status_code == 400
    answer = _put(client, {"confirm_phrase": "Kesari"}, phrase="STOP")
    assert answer.status_code == 200 and store.settings().confirm_phrase == "Kesari"
    assert "kesari" not in answer.get_data(as_text=True).lower()
    row = next(row for row in store.history() if row.kind == "settings_changed")
    assert "kesari" not in json.dumps(row.detail).lower()
    assert row.detail["changes"] == {"confirm_phrase": "(changed)"}
    assert (
        "kesari"
        not in _owner_call(client, "get", "/sudarshan/risk/settings").get_data(as_text=True).lower()
    )


def test_loosening_the_daily_limit_on_a_locked_day_is_told_and_the_lock_stays(world, monkeypatch):
    client, account, store = world
    told = []
    monkeypatch.setattr(killswitch, "_tell", lambda owner, text: told.append((owner.user, text)))
    assert _put(client, {"daily_loss_limit": 4000.0}).status_code == 200
    assert _put(client, {"daily_loss_limit": 5000.0}, phrase="STOP").status_code == 200
    assert told == []  # not locked: a change like any other
    _lock_today("sandbox", -5000.0, 5000.0, monkeypatch)
    assert _put(client, {"daily_loss_limit": 3000.0}).status_code == 200  # tighter: nothing told
    assert _put(client, {"daily_loss_limit": 6000.0}, phrase="STOP").status_code == 200
    assert _put(client, {"daily_loss_limit": None}, phrase="STOP").status_code == 200
    assert told == [
        (OWNER, words.daily_lock_told(-5000.0, 5000.0, "sandbox")),
        (OWNER, words.daily_loosened_told(3000.0, 6000.0)),
        (OWNER, words.daily_loosened_told(6000.0, None)),
    ]
    assert gate.GATE.day_lock("sandbox") is not None


def test_the_settings_screen_reads_every_setting_but_the_phrase(world):
    client, account, store = world
    data = _owner_call(client, "get", "/sudarshan/risk/settings").get_json()["data"]
    assert data == settings_rules.public(store.settings())
    assert "confirm_phrase" not in data and data["phrase_set"] is True
