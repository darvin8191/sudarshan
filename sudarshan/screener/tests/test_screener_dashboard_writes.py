"""The dashboard's writes end to end (M9.16, dashboards design 3.4, 3.5, 3.9 and 7).

Every write names the version it was decided on; the first change (version 0) keeps the suggestion
it was looking at; a stale version writes nothing and says so in the dashboard's words; adding from
a preset makes the screen and the widget in one transaction; every write queues the push that tells
the owner's other screens.
"""

from __future__ import annotations

import dataclasses

import pytest
from test_screener_dashboard_ops import add, named
from test_screener_dashboard_ops import service as ops_service  # noqa: F401  (a fixture)
from test_screener_results import call, make
from test_screener_runs import NOW

from sudarshan.screener import contract, presets, words
from sudarshan.screener.ops import ApiError
from sudarshan.screener.store import OWNER


@pytest.fixture
def service(ops_service):  # noqa: F811
    return ops_service


DAY_ONLY = {
    "where": {
        "id": "c1",
        "left": {"field": "day_open"},
        "op": "eq",
        "right": {"field": "day_high"},
    },
    "rank": {"by": {"field": "change_pct", "tf": "D"}, "order": "desc"},
}


def refused(svc, op, code, **args) -> ApiError:
    with pytest.raises(ApiError) as caught:
        call(svc, op, **args)
    assert caught.value.code == code, caught.value.message
    return caught.value


def keys(svc) -> list[tuple[int, str]]:
    got = call(svc, "dashboard")
    return [(w["screen_id"], w["kind"]) for w in got["widgets"]]


def pushes(svc) -> list[dict]:
    rows = svc.store.read(OWNER, lambda tx: tx.outbox_due(NOW + 10**9, 500))
    return [r["payload"] for r in rows if r["payload"].get("_event") == contract.EVENT_DASHBOARD]


def test_the_contract_names_the_writes_the_event_and_every_push_event():
    ops = {s.name: s for s in contract.OPS}
    assert (ops["widget_add"].method, ops["widget_add"].route) == ("POST", "/dashboard/widgets")
    assert (ops["widget_update"].method, ops["widget_update"].route) == (
        "PATCH",
        "/dashboard/widget",
    )
    assert (ops["widget_remove"].method, ops["widget_remove"].route) == (
        "DELETE",
        "/dashboard/widget",
    )
    assert ops["widget_remove"].query == (("screen_id", "int"), ("kind", "str"), ("version", "int"))
    assert (ops["widget_order"].method, ops["widget_order"].route) == ("POST", "/dashboard/order")
    assert contract.EVENT_DASHBOARD == "sudarshan_screener_dashboard"
    assert contract.PUSH_EVENTS == (
        contract.EVENT_RUN, contract.EVENT_LISTS, contract.EVENT_STATE, contract.EVENT_DASHBOARD,
    )  # fmt: skip


def test_the_first_change_at_version_zero_keeps_the_suggestion_and_moves_to_two(service):
    one, two = make(service), named(service, "Two")
    got = call(service, "widget_update", version=0, screen_id=two["id"], kind="table", rows=5)
    assert got == {"version": 2}
    after = call(service, "dashboard")
    assert after["suggested"] is False and after["version"] == 2
    assert keys(service) == [(two["id"], "table"), (one["id"], "table")]
    assert after["widgets"][0]["rows"] == 5 and after["widgets"][1]["rows"] == 10


def test_a_stale_version_writes_nothing_and_says_so(service):
    screen = make(service)
    call(service, "widget_update", version=0, screen_id=screen["id"], kind="table", rows=5)
    for op, args in (
        ("widget_update", {"rows": 20}),
        ("widget_remove", {}),
        ("widget_order", {"order": [[screen["id"], "table"]]}),
        ("widget_add", {"screen_id": screen["id"], "kind": "count"}),
    ):
        extra = (
            {"screen_id": screen["id"], "kind": "table"}
            if op in ("widget_update", "widget_remove")
            else {}
        )
        err = refused(service, op, "version_conflict", version=1, **extra, **args)
        assert err.message == words.DASH_CONFLICT
    assert call(service, "dashboard")["widgets"][0]["rows"] == 5 and keys(service) == [
        (screen["id"], "table")
    ]


def test_update_changes_rows_sort_and_values_and_can_clear_them(service):
    screen = make(service)
    addr = {"screen_id": screen["id"], "kind": "table"}
    call(service, "widget_update", version=0, **addr, rows=20, value_clauses=["c1"],
         sort_by={"by": "value", "order": "desc", "clause": "c1"})  # fmt: skip
    card = call(service, "widget", **addr)
    assert (card["rows"], card["value_clauses"], card["sort_by"]) == (
        20, ["c1"], {"by": "value", "order": "desc", "clause": "c1"},
    )  # fmt: skip
    call(service, "widget_update", version=2, **addr, sort_by=None, value_clauses=None)
    card = call(service, "widget", **addr)
    assert card["sort_by"] is None and card["value_clauses"] is None and card["rows"] == 20


def test_update_can_turn_a_table_into_a_count_but_not_onto_one_that_exists(service):
    screen = make(service)
    addr = {"screen_id": screen["id"], "kind": "table"}
    version = call(service, "widget_add", version=0, screen_id=screen["id"], kind="count")[
        "version"
    ]
    twice = refused(service, "widget_update", "refused", version=version, **addr, to_kind="count")
    assert "already on the dashboard as a count" in twice.message
    call(service, "widget_remove", version=version, screen_id=screen["id"], kind="count")
    call(service, "widget_update", version=version + 1, **addr, to_kind="count")
    assert keys(service) == [(screen["id"], "count")]


@pytest.mark.parametrize(
    ("fields", "sentence"),
    [
        ({"rows": 7}, words.DASH_ROWS),
        ({"to_kind": "pie"}, words.DASH_KIND),
        ({"value_clauses": ["a", "b", "c"]}, words.DASH_VALUES),
        ({"value_clauses": ["a", "a"]}, words.DASH_VALUES),
        ({"sort_by": {"by": "value", "order": "up", "clause": "a"}}, words.DASH_SORT),
        ({"sort_by": {"by": "value", "order": "asc"}}, words.DASH_SORT),
        ({"sort_by": {"by": "ltp", "order": "asc", "clause": "a"}}, words.DASH_SORT),
        ({"sort_by": {"by": "colour", "order": "asc"}}, words.DASH_SORT),
    ],
)
def test_update_refuses_what_a_widget_cannot_be_in_words(service, fields, sentence):
    screen = make(service)
    err = refused(
        service,
        "widget_update",
        "refused",
        version=0,
        screen_id=screen["id"],
        kind="table",
        **fields,
    )
    assert err.message == sentence
    assert call(service, "dashboard")["suggested"] is True, "a refused change writes nothing"


def test_update_with_nothing_to_change_and_a_widget_that_is_not_there(service):
    screen = make(service)
    refused(
        service, "widget_update", "bad_request", version=0, screen_id=screen["id"], kind="table"
    )
    err = refused(
        service,
        "widget_update",
        "not_found",
        version=0,
        screen_id=screen["id"],
        kind="count",
        rows=5,
    )
    assert err.message == words.DASH_GONE
    refused(service, "widget_remove", "bad_request", version=0, screen_id=screen["id"], kind="pie")


def test_remove_closes_the_gap_and_says_the_screen_is_kept(service):
    a, b, c = make(service), named(service, "B"), named(service, "C")
    got = call(service, "widget_remove", version=0, screen_id=b["id"], kind="table")
    assert got == {"version": 2, "notice": words.DASH_REMOVED}
    assert keys(service) == [(c["id"], "table"), (a["id"], "table")]
    assert [w["position"] for w in call(service, "dashboard")["widgets"]] == [0, 1]
    assert call(service, "state")["screens"][1]["name"] == "B", "the screen itself is kept"


def test_order_moves_the_whole_layout_by_screen_and_kind(service):
    a, b = make(service), named(service, "B")
    version = call(service, "widget_add", version=0, screen_id=a["id"], kind="count")["version"]
    wanted = [[a["id"], "count"], [a["id"], "table"], [b["id"], "table"]]
    assert call(service, "widget_order", version=version, order=wanted) == {"version": version + 1}
    assert keys(service) == [(a["id"], "count"), (a["id"], "table"), (b["id"], "table")]
    cases = [
        ([[a["id"], "count"]], "version_conflict", words.DASH_CONFLICT),  # not the whole layout
        ("x", "bad_request", words.DASH_ORDER),
        ([], "bad_request", words.DASH_ORDER),
        ([[a["id"]]], "bad_request", words.DASH_ORDER),
        ([["1", "table"]], "bad_request", words.DASH_ORDER),
        ([[a["id"], "pie"]], "bad_request", words.DASH_ORDER),
    ]
    for bad, code, sentence in cases:
        assert (
            refused(service, "widget_order", code, version=version + 1, order=bad).message
            == sentence
        )


def test_order_at_version_zero_moves_the_suggestion_he_was_looking_at(service):
    a, b = make(service), named(service, "B")
    got = call(service, "widget_order", version=0, order=[[a["id"], "table"], [b["id"], "table"]])
    assert got == {"version": 2} and keys(service) == [(a["id"], "table"), (b["id"], "table")]


def test_add_an_existing_screen_as_a_count_and_starts_a_paused_one(service):
    active, idle = make(service), named(service, "Idle", status="paused")
    got = call(service, "widget_add", version=0, screen_id=active["id"], kind="count", rows=5)
    assert got["version"] == 2 and got["kind"] == "count" and got["notice"] is None
    got = call(service, "widget_add", version=2, screen_id=idle["id"])
    assert got["screen"]["status"] == "active" and got["screen"]["version"] == idle["version"] + 1
    kept = call(
        service,
        "widget_add",
        version=3,
        screen_id=named(service, "Kept", status="paused")["id"],
        start=False,
    )
    assert kept["screen"]["status"] == "paused" and kept["notice"] is None
    refused(service, "widget_add", "not_found", version=4, screen_id=9999)
    refused(service, "widget_add", "bad_request", version=4)
    refused(service, "widget_add", "bad_request", version=4, screen_id=active["id"], preset="x")


def test_a_screen_added_at_the_active_limit_stays_paused_and_says_why(service):
    service.settings = dataclasses.replace(service.settings, max_active=1)
    make(service)
    idle = named(service, "Idle", status="paused")
    got = call(service, "widget_add", version=0, screen_id=idle["id"])
    assert got["screen"]["status"] == "paused"
    assert got["notice"] == words.paused_add_words(1, 1)
    assert got["notice"] == (
        "Added Paused: 1 of 1 screens already check through the day. Pause one to start this."
    )


def test_a_preset_makes_the_screen_and_the_widget_in_one_step(service):
    key = next(p.key for p in presets.PRESETS if p.spec is not None and p.waits is None)
    got = call(service, "widget_add", version=0, preset=key, kind="count")
    assert got["version"] == 1 + 0 + 1 and got["screen"]["name"]
    assert got["screen"]["status"] == "active" and got["kind"] == "count"
    assert keys(service) == [(got["screen"]["id"], "count")]
    # the same preset again is another screen, never a second widget for the first
    again = call(service, "widget_add", version=got["version"], preset=key, start=False)
    assert again["screen"]["id"] != got["screen"]["id"] and again["screen"]["status"] == "paused"


def test_a_preset_that_cannot_be_added_is_refused_in_its_words(service):
    waiting = next((p for p in presets.PRESETS if p.spec is not None and p.waits), None)
    if waiting is not None:
        assert (
            refused(service, "widget_add", "refused", version=0, preset=waiting.key).message
            == waiting.waits
        )
    assert (
        refused(service, "widget_add", "refused", version=0, preset="nope").message
        == presets.NOT_IN_LIBRARY
    )
    assert call(service, "state")["screens"] == []


def test_a_refused_add_leaves_no_screen_behind(service):
    key = next(p.key for p in presets.PRESETS if p.spec is not None and p.waits is None)
    call(service, "widget_add", version=0, preset=key)
    before = len(call(service, "state")["screens"])
    refused(service, "widget_add", "version_conflict", version=0, preset=key)
    refused(service, "widget_add", "refused", version=2, preset=key, rows=7)
    assert len(call(service, "state")["screens"]) == before


def test_a_day_only_screen_may_choose_when_it_checks_and_no_other_may(service):
    day = named(service, "Day", **DAY_ONLY)
    got = call(
        service, "widget_add", version=0, screen_id=day["id"], kind="count", when="after_close"
    )
    assert got["screen"]["where"] == "F&O stocks · after the close"
    card = call(service, "widget", screen_id=day["id"], kind="count")
    assert card["mode"] == "after_close" and card["day_only"] is True
    late = named(service, "Late", status="paused", run={"on": "after_close"}, **DAY_ONLY)
    back = call(service, "widget_add", version=2, screen_id=late["id"], when="during_day")
    assert back["screen"]["where"] == "F&O stocks · 15-min"
    minute = make(service)  # reads a 15-minute candle: not day-only
    err = refused(
        service,
        "widget_add",
        "refused",
        version=3,
        screen_id=minute["id"],
        kind="count",
        when="after_close",
    )
    assert err.message == words.DASH_WHEN_DAY_ONLY and err.field == "run"
    refused(
        service,
        "widget_add",
        "bad_request",
        version=3,
        screen_id=minute["id"],
        kind="count",
        when="never",
    )


def test_every_write_queues_the_push_and_only_the_newest_is_kept(service):
    a, b = make(service), named(service, "B")
    assert pushes(service) == []
    call(service, "widget_update", version=0, screen_id=a["id"], kind="table", rows=5)
    assert pushes(service) == [{"_event": contract.EVENT_DASHBOARD, "version": 2}]
    call(service, "widget_order", version=2, order=[[b["id"], "table"], [a["id"], "table"]])
    call(service, "widget_remove", version=3, screen_id=b["id"], kind="table")
    call(service, "widget_add", version=4, screen_id=a["id"], kind="count")
    assert pushes(service) == [{"_event": contract.EVENT_DASHBOARD, "version": 5}]


def test_a_refused_write_queues_no_push(service):
    screen = make(service)
    refused(
        service, "widget_update", "refused", version=0, screen_id=screen["id"], kind="table", rows=7
    )
    refused(
        service,
        "widget_remove",
        "version_conflict",
        version=3,
        screen_id=screen["id"],
        kind="table",
    )
    assert pushes(service) == []


def test_deleting_a_screen_with_widgets_queues_the_push_too(service):
    a, b = make(service), named(service, "B")
    call(service, "widget_update", version=0, screen_id=a["id"], kind="table", rows=5)
    call(service, "delete", id=b["id"])
    assert keys(service) == [(a["id"], "table")]
    assert pushes(service) == [{"_event": contract.EVENT_DASHBOARD, "version": 3}]


def test_the_writes_work_through_the_real_sealed_api(tmp_path):
    import asyncio

    from test_screener_ops import served

    async def scenario():
        svc, task, client = await served(tmp_path)
        try:
            status, made = await client.op("create", spec=_spec(), request_id="r1")
            assert status == 200
            screen = made["data"]["id"]
            status, body = await client.op(
                "widget_update", version=0, screen_id=screen, kind="table", rows=5
            )
            assert (status, body["data"]) == (200, {"version": 2})
            status, body = await client.op(
                "widget_update", version=1, screen_id=screen, kind="table", rows=20
            )
            assert status == 409 and body["error"]["message"] == words.DASH_CONFLICT
            status, body = await client.op(
                "widget_remove", version=2, screen_id=screen, kind="table"
            )
            assert status == 200 and body["data"]["notice"] == words.DASH_REMOVED
            status, body = await client.op("dashboard")
            assert status == 200 and body["data"]["widgets"] == [] and body["data"]["version"] == 3
        finally:
            svc.stop("test over")
            await task

    asyncio.run(scenario())


def _spec() -> dict:
    from test_screener_runs import SPEC

    return SPEC
