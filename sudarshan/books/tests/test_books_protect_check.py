"""Protect's side and book are the owner's, not the server's (M5.6; v0.14.0 review, M5). ORDER PATH.

The owner confirms "sell 50 X if the price falls to 1,240" on his phone. The request states that
side, and the server places that side or refuses: it never picks a direction from a price that
moved since the sheet. At stage ``sandbox`` the request goes to the sandbox whatever the app's
mode is when it arrives.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sudarshan.books import protect_check, protect_flag

ROOT = Path(__file__).resolve().parents[3]


def protect(side="falls", trigger=1240.0, **change):
    """The page's one request (frontend/src/sudarshan/books/protect/protect.ts protectRequest)."""
    return {
        "strategy": "Protect", "trigger_type": "SINGLE", "exchange": "NSE", "symbol": "RELIANCE",
        "action": "SELL", "product": "CNC", "quantity": 50, "pricetype": "LIMIT", "price": trigger,
        "triggerprice_sl": trigger if side == "falls" else 0,
        "triggerprice_tg": trigger if side == "rises" else 0,
        **change,
    }  # fmt: skip


def test_the_label_is_the_pages_own():
    page = (ROOT / "frontend/src/sudarshan/books/protect/protect.ts").read_text(encoding="utf-8")
    assert re.search(r"export const STRATEGY = '([^']+)'", page).group(1) == protect_check.STRATEGY
    assert "strategy: STRATEGY" in page


# ---- the side: vectors ---------------------------------------------------------------------------

VECTORS = [
    # side, trigger, the server's last price -> Upstox trigger_type, refused?
    ("falls", 1240.0, 1265.0, "BELOW", False),  # under the price, as confirmed
    ("falls", 1240.0, 1240.01, "BELOW", False),
    ("falls", 1240.0, 1240.0, "BELOW", True),  # at the price: it would sell at once
    ("falls", 1240.0, 1239.95, "BELOW", True),  # already through it: never "rises to 1,240"
    ("falls", 1240.0, 1100.0, "BELOW", True),
    ("rises", 1300.0, 1265.0, "ABOVE", False),
    ("rises", 1300.0, 1299.99, "ABOVE", False),
    ("rises", 1300.0, 1300.0, "ABOVE", True),
    ("rises", 1300.0, 1300.05, "ABOVE", True),  # never "falls to 1,300"
    ("rises", 1300.0, 1400.0, "ABOVE", True),
]


@pytest.mark.parametrize(("side", "trigger", "last", "direction", "refused"), VECTORS)
def test_side_vectors(side, trigger, last, direction, refused):
    data = protect(side, trigger)
    assert protect_check.direction(data) == direction
    said = protect_check.refusal(data, last)
    assert (said is not None) is refused
    if said:
        way = "below" if side == "falls" else "above"
        assert f"already at or {way} {trigger:,.2f}" in said and "would sell at once" in said
        assert not re.search(r"\b[1-5]\d\d\b(?![,.])|None|null|Exception", said)


@pytest.mark.parametrize(("side", "trigger", "last", "direction", "refused"), VECTORS)
def test_the_upstox_mapping_places_the_side_the_request_states(
    side, trigger, last, direction, refused
):
    """The mapping no longer reads the direction off the last price for a Protect: whatever the
    price is by now, the rule is the confirmed side. (A crossed one is refused before the map.)"""
    from broker.upstox.mapping import gtt_data

    body = gtt_data.transform_place_gtt(
        {**protect(side, trigger), "last_price": last, "instrument_token": "NSE_EQ|X"}
    )
    assert body["type"] == "SINGLE" and body["transaction_type"] == "SELL"
    assert body["rules"] == [
        {"strategy": "ENTRY", "trigger_type": direction, "trigger_price": trigger}
    ]


@pytest.mark.parametrize(
    ("last", "direction"), [(1265.0, "BELOW"), (1240.0, "IMMEDIATE"), (1200.0, "ABOVE")]
)
def test_any_other_single_gtt_is_mapped_as_before(last, direction):
    from broker.upstox.mapping import gtt_data

    other = {**protect("falls", 1240.0), "strategy": "GTT", "last_price": last,
             "instrument_token": "NSE_EQ|X"}  # fmt: skip
    assert gtt_data.transform_place_gtt(other)["rules"][0]["trigger_type"] == direction
    assert protect_check.refusal(other, last) is None and protect_check.direction(other) is None


@pytest.mark.parametrize(
    ("change", "said"),
    [
        ({"trigger_type": "OCO"}, protect_check.NOT_ONE_SELL),
        ({"action": "BUY"}, protect_check.NOT_ONE_SELL),
        ({"product": "MIS"}, protect_check.NOT_ONE_SELL),
        ({"triggerprice_sl": 0, "triggerprice_tg": 0}, protect_check.NO_SIDE),
        ({"triggerprice_sl": 1240, "triggerprice_tg": 1300}, protect_check.NO_SIDE),
        ({"triggerprice_sl": "abc"}, protect_check.NO_SIDE),
    ],
)
def test_a_protect_that_is_not_one_sell_on_one_side_is_refused(change, said):
    assert protect_check.refusal({**protect(), **change}, 1265.0) == said


@pytest.mark.parametrize("last", [None, 0, "", "abc", -5, float("nan")])
def test_with_no_last_price_nothing_is_placed_and_no_direction_is_guessed(last):
    assert protect_check.refusal(protect(), last) == protect_check.NO_PRICE


# ---- the Upstox module: a fresh price, then the refusal, before anything is sent ----------------


@pytest.fixture
def upstox(monkeypatch):
    from broker.upstox.api import gtt_api

    seen = {"last": 1265.0, "asked": 0, "posted": []}

    def last_price(symbol, exchange, auth):
        seen["asked"] += 1
        return seen["last"]

    class Client:
        def post(self, url, headers=None, content=None):
            seen["posted"].append(content)
            raise AssertionError("a refused Protect reached Upstox")

    monkeypatch.setattr(gtt_api, "_fetch_last_price", last_price)
    monkeypatch.setattr(gtt_api, "get_token", lambda symbol, exchange: "NSE_EQ|X")
    monkeypatch.setattr(gtt_api, "get_httpx_client", lambda: Client())
    return gtt_api, seen


def test_the_price_the_request_carried_is_never_the_one_judged(upstox):
    gtt_api, seen = upstox
    seen["last"] = 1239.0  # the market is already through the stop
    data = {**protect("falls", 1240.0), "last_price": 1300.0}  # what a stale phone believes
    said, status = gtt_api._prepare(data, "token")
    assert status == 409 and "already at or below 1,240.00" in said and seen["asked"] == 1
    seen["last"] = 1265.0
    assert gtt_api._prepare({**protect("falls", 1240.0), "last_price": 1.0}, "token") == (
        None,
        None,
    )


def test_a_crossed_protect_is_refused_before_anything_is_sent(upstox):
    gtt_api, seen = upstox
    seen["last"] = 1240.0
    response, body, trigger_id = gtt_api.place_gtt_order(protect("falls", 1240.0), "token")
    assert (response.status, trigger_id) == (409, None) and seen["posted"] == []
    assert body["message"].startswith("The last price is 1,240.00, already at or below 1,240.00")
    body, status = gtt_api.modify_gtt_order(
        {**protect("rises", 1200.0), "trigger_id": "GTT-1"}, "token"
    )
    assert status == 409 and "already at or above 1,200.00" in body["message"]
    assert seen["posted"] == []


def test_with_no_price_from_upstox_a_protect_is_not_placed(upstox):
    gtt_api, seen = upstox
    seen["last"] = None
    response, body, _ = gtt_api.place_gtt_order(protect("falls", 1240.0), "token")
    assert response.status == 409 and body["message"] == protect_check.NO_PRICE


# ---- the book: at stage sandbox, the sandbox whatever the app's mode ----------------------------


@pytest.fixture
def service(monkeypatch):
    """OpenAlgo's GTT services with their exits stood in: the sandbox, the broker's module, the
    Action Center's queue. The app's mode and the order mode are the test's to set."""
    import services.modify_gtt_order_service as modify
    import services.order_router_service as router
    import services.place_gtt_order_service as place
    import services.sandbox_service as sandbox

    seen = {"analyze": False, "semi_auto": False, "last": 1265.0, "sandbox": [], "broker": [],
            "queued": []}  # fmt: skip

    class Broker:
        @staticmethod
        def place_gtt_order(data, auth):
            seen["broker"].append(("place", dict(data)))
            return type("R", (), {"status": 200})(), {"status": "success"}, "UP-1"

        @staticmethod
        def modify_gtt_order(data, auth):
            seen["broker"].append(("modify", dict(data)))
            return {"status": "success", "trigger_id": data["trigger_id"]}, 200

    def sandbox_place(data, api_key, last_price=0):
        seen["sandbox"].append(("place", dict(data), api_key))
        return True, {"status": "success", "trigger_id": "SB-1", "mode": "analyze"}, 200

    def sandbox_modify(trigger_id, data, api_key):
        seen["sandbox"].append(("modify", dict(data), api_key))
        return True, {"status": "success", "trigger_id": trigger_id, "mode": "analyze"}, 200

    def queue(api_key, data, kind):
        seen["queued"].append(kind)
        return True, {"status": "success", "mode": "semi_auto"}, 200

    for module in (place, modify):
        monkeypatch.setattr(module, "get_analyze_mode", lambda: seen["analyze"])
        monkeypatch.setattr(module, "import_broker_gtt_module", lambda broker: Broker)
        monkeypatch.setattr(module, "get_auth_token_broker", lambda key: ("token", "upstox"))
    monkeypatch.setattr(place, "_fetch_last_price_for_sandbox", lambda *a: seen["last"])
    monkeypatch.setattr(sandbox, "sandbox_place_gtt_order", sandbox_place)
    monkeypatch.setattr(sandbox, "sandbox_modify_gtt_order", sandbox_modify)
    monkeypatch.setattr(router, "should_route_to_pending", lambda key, kind: seen["semi_auto"])
    monkeypatch.setattr(router, "queue_order", queue)
    monkeypatch.setattr(protect_check, "owner_api_key", lambda: "owner-key")
    seen["place"], seen["modify"] = place.place_gtt_order, modify.modify_gtt_order
    return seen


@pytest.mark.parametrize("analyze", [True, False], ids=["app in Analyze", "app Live"])
@pytest.mark.parametrize("semi_auto", [False, True], ids=["auto", "semi-auto"])
def test_at_stage_sandbox_a_protect_goes_to_the_sandbox_whatever_the_apps_mode(
    service, monkeypatch, analyze, semi_auto
):
    """The sheet was opened in Analyze mode; the app went Live (or to Semi-Auto) before Send."""
    monkeypatch.setenv(protect_flag.ENV, "sandbox")
    service["analyze"], service["semi_auto"] = analyze, semi_auto
    ok, body, status = service["place"](protect(), api_key="k")
    assert (ok, status, body["trigger_id"]) == (True, 200, "SB-1")
    assert [kind for kind, *_ in service["sandbox"]] == ["place"]
    assert service["broker"] == []
    if not analyze:
        assert service["queued"] == []  # never queued for an approval in another mode


def test_at_stage_sandbox_a_change_made_while_the_app_is_live_goes_to_the_sandbox(
    service, monkeypatch
):
    monkeypatch.setenv(protect_flag.ENV, "sandbox")
    service["analyze"] = False
    change = {**protect("falls", 1230.0), "trigger_id": "SB-1"}
    # as the page's own route calls it in Live mode: the session's token, no API key
    ok, body, status = service["modify"](change, api_key=None, auth_token="token", broker="upstox")
    assert (ok, status) == (True, 200) and service["broker"] == []
    assert [(kind, key) for kind, _data, key in service["sandbox"]] == [("modify", "owner-key")]


@pytest.mark.parametrize("stage", ["on", "off", None])
def test_at_any_other_stage_a_protect_follows_the_apps_mode_as_any_gtt_does(
    service, monkeypatch, stage
):
    if stage is None:
        monkeypatch.delenv(protect_flag.ENV, raising=False)
    else:
        monkeypatch.setenv(protect_flag.ENV, stage)
    service["analyze"] = False
    assert service["place"](protect(), api_key="k")[2] == 200
    assert [kind for kind, _ in service["broker"]] == ["place"] and service["sandbox"] == []
    service["analyze"] = True
    assert service["place"](protect(), api_key="k")[2] == 200
    assert [kind for kind, *_ in service["sandbox"]] == ["place"]


def test_a_gtt_that_is_not_a_protect_follows_the_apps_mode_at_stage_sandbox(service, monkeypatch):
    monkeypatch.setenv(protect_flag.ENV, "sandbox")
    service["analyze"] = False
    other = {**protect(), "strategy": "GTT"}
    assert service["place"](other, api_key="k")[2] == 200
    assert [kind for kind, _ in service["broker"]] == ["place"] and service["sandbox"] == []
    service["semi_auto"] = True
    service["place"](other, api_key="k")
    assert service["queued"] == ["placegttorder"]


@pytest.mark.parametrize("how", ["place", "modify"])
def test_in_the_sandbox_a_crossed_protect_is_refused_with_its_sentence(service, monkeypatch, how):
    monkeypatch.setenv(protect_flag.ENV, "sandbox")
    service["analyze"], service["last"] = True, 1239.0
    if how == "place":
        ok, body, status = service["place"](protect("falls", 1240.0), api_key="k")
    else:
        ok, body, status = service["modify"](
            {**protect("falls", 1240.0), "trigger_id": "SB-1"}, api_key="k"
        )
    assert (ok, status) == (False, 409) and service["sandbox"] == []
    assert body["message"].startswith("The last price is 1,239.00, already at or below 1,240.00")


def test_the_book_rule_can_only_move_a_request_to_the_sandbox(monkeypatch):
    for stage, expect in (("sandbox", True), ("on", False), ("off", False), ("ON", False)):
        monkeypatch.setenv(protect_flag.ENV, stage)
        assert protect_check.sandbox_only(protect()) is expect
        assert protect_check.sandbox_only({**protect(), "strategy": "GTT"}) is False
    # the only reader of the rule in the services sends to the sandbox when it is true
    for name in ("place_gtt_order_service.py", "modify_gtt_order_service.py"):
        text = (ROOT / "services" / name).read_text(encoding="utf-8")
        assert "get_analyze_mode() or protect" in text
