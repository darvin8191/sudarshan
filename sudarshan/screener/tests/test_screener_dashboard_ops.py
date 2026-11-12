"""The dashboard's reads end to end (M9.16, dashboards design 3.4 to 3.8, 7 and the review's LOW-B8
and LOW-B10): a real store and the registered ops.

The suggestion before the owner's first change, the stored layout after it, a card matching what
``results`` ranks, the widget's own sort and value columns, a count card's series, the line that
says why a card is not live, the budget on an injected clock and a day of twelve one-minute screens
under the bridge's three-second read timeout.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from screener_helpers import ms
from test_screener_results import call, judged, make, record  # noqa: F401  (record: fixtures)
from test_screener_runs import DAY, NOW, SESSION, SPEC

from sudarshan.screener import contract, spec, words
from sudarshan.screener import store as store_module
from sudarshan.screener.ops import REGISTRY, ApiError
from sudarshan.screener.runs import ENGINE_VERSION
from sudarshan.screener.service import Service
from sudarshan.screener.store import OWNER, ScreenerStore
from sudarshan.screener.tests.test_screener_service import FERNET, settings_for


@pytest.fixture
def service(tmp_path):
    svc = Service(settings_for(1, SUDARSHAN_SCREENER_STATE_DIR=str(tmp_path)), FERNET, jobs={})
    svc.store = ScreenerStore(tmp_path / "screener.db")
    svc.clock = lambda: NOW
    yield svc
    svc.store.close()
    store_module.stop()


def named(svc, name, status="active", **extra) -> dict:
    stored = spec.check(dict(SPEC, name=name, **extra))
    return svc.store.write(OWNER, lambda tx: tx.screen_insert(name, stored, status, NOW - 1))


def add(svc, version, screen, kind="table", **fields):
    return svc.store.write(
        OWNER,
        lambda tx: tx.widget_insert(
            version, {"screen_id": screen["id"], "kind": kind, **fields}, NOW
        ),
    )


def change(svc, version, screen, **fields):
    return svc.store.write(
        OWNER, lambda tx: tx.widget_change(version, screen["id"], "table", fields, NOW)
    )


def test_the_dashboard_ops_are_in_the_contract_with_widgets_addressed_by_screen_and_kind():
    ops = {s.name: s for s in contract.OPS}
    assert ops["dashboard"].route == "/dashboard" and ops["dashboard"].method == "GET"
    # the route's one <id> is the screen; the kind rides in the query (reads, remove) or the body
    assert ops["widget"].route == "/dashboard/widgets/<id>"
    assert ops["widget"].query == (("kind", "str"),)


def test_before_a_change_the_dashboard_is_a_suggestion_of_the_active_screens(service):
    first, second = named(service, "First"), named(service, "Second")
    named(service, "Paused one", status="paused")
    got = call(service, "dashboard")
    assert (got["version"], got["suggested"], got["name"]) == (0, True, "Dashboard")
    assert [w["screen_id"] for w in got["widgets"]] == [second["id"], first["id"]]
    card = got["widgets"][0]
    assert (card["kind"], card["rows"], card["position"], card["rows_pending"]) == (
        "table",
        10,
        0,
        False,
    )
    assert (
        card["name"] == "Second"
        and card["status"] == "active"
        and card["where"] == "F&O stocks · 15-min"
    )
    assert card["run"] is None and card["matches"] == 0 and card["rows_shown"] == []
    assert got["limits"] == {"widgets": 20} and got["server_time"] == NOW
    assert "id" not in card and "id" not in got


def test_after_the_first_change_the_stored_layout_is_read_in_its_order(service):
    one, two = named(service, "One"), named(service, "Two")
    version = add(service, 0, one, "count")["version"]  # the first change keeps the suggestion
    got = call(service, "dashboard")
    assert got["suggested"] is False and got["version"] == version == 2
    assert [(w["screen_id"], w["kind"]) for w in got["widgets"]] == [
        (two["id"], "table"),
        (one["id"], "table"),
        (one["id"], "count"),
    ]
    assert [w["position"] for w in got["widgets"]] == [0, 1, 2]


def test_a_card_matches_what_results_ranks(service):
    screen = make(service)
    record(service, screen, 1, judged({"AAA": 100, "BBB": 300, "CCC": 200}))
    card = call(service, "dashboard")["widgets"][0]
    shown = call(service, "results", id=screen["id"])
    assert card["rows_shown"] == shown["rows"][:10] and card["more"] == 0
    assert card["run"] == shown["run"] and card["matches"] == 3
    assert [r["symbol"] for r in card["rows_shown"]] == ["BBB", "CCC", "AAA"]
    assert card["not_checked"] == shown["not_checked"]


def test_rows_past_the_widgets_count_are_counted_not_shown(service):
    screen = make(service)
    record(service, screen, 1, judged({c * 2: 100 + i for i, c in enumerate("ABCDEFG")}))
    version = change(service, 0, screen, rows=5)["version"]
    card = call(service, "dashboard")["widgets"][0]
    assert (
        version == 2 and len(card["rows_shown"]) == 5 and card["more"] == 2 and card["matches"] == 7
    )


def test_the_widgets_own_sort_overrides_the_screens_rank_and_ranks_follow_it(service):
    screen = make(service)
    record(service, screen, 1, judged({"AAA": 100, "BBB": 300, "CCC": 200}))
    change(service, 0, screen, sort_by={"by": "ltp", "order": "asc"})
    card = call(service, "widget", id=screen["id"], kind="table")
    assert [r["symbol"] for r in card["rows_shown"]] == ["AAA", "CCC", "BBB"]
    assert [r["rank"] for r in card["rows_shown"]] == [1, 2, 3]
    assert card["sort_by"] == {"by": "ltp", "order": "asc"}


def test_a_sort_or_value_column_the_screen_lacks_falls_back(service):
    screen = make(service)
    record(service, screen, 1, judged({"AAA": 100, "BBB": 300}))
    change(
        service, 0, screen,
        sort_by={"by": "value", "order": "asc", "clause": "gone"}, value_clauses=["gone"],
    )  # fmt: skip
    card = call(service, "widget", id=screen["id"], kind="table")
    assert card["sort_by"] is None and card["value_clauses"] is None
    assert [r["symbol"] for r in card["rows_shown"]] == ["BBB", "AAA"]


def test_a_suggested_widget_is_read_by_its_screen_and_kind(service):
    screen = make(service)
    record(service, screen, 1, judged({"AAA": 100}))
    card = call(service, "widget", id=screen["id"], kind="table")
    assert card == call(service, "dashboard")["widgets"][0]


def test_a_widget_that_is_not_there_says_so_in_words(service):
    screen = make(service)
    with pytest.raises(ApiError) as gone:
        call(service, "widget", id=screen["id"] + 99, kind="count")  # no such screen
    assert gone.value.code == "not_found" and gone.value.message == words.DASH_GONE
    for bad in (
        {"screen_id": screen["id"], "kind": "pie"},
        {"screen_id": "x", "kind": "table"},
        {},
    ):
        with pytest.raises(ApiError) as refused:
            call(service, "widget", **bad)
        assert refused.value.code == "bad_request"


def test_a_count_card_carries_the_days_checks_and_how_a_screen_reader_says_them(service):
    screen = make(service)
    for k, found in enumerate(
        [
            judged({"AAA": 100, "BBB": 200, "CCC": 300}),
            judged({"AAA": 100, "BBB": 200}),
            judged({"AAA": 100}),
        ],
        start=1,
    ):
        record(service, screen, k, found)
    add(service, 0, screen, "count")
    card = call(service, "widget", id=screen["id"], kind="count")
    counts = card["counts"]
    assert [p["matches"] for p in counts["series"]] == [3, 2, 1]
    day = call(service, "today")["screens"][0]  # the same figures the home card shows
    assert (counts["entered_today"], counts["exited_today"]) == (3, day["exited"])
    assert day["entered"] == 3
    assert counts["series_words"].startswith("Matches at each check today: 3, 2, 1; latest 1 at ")
    assert card["rows_shown"] == [] and card["matches"] == 1 and card["list"]["count"] >= 0


def test_a_card_that_is_not_live_says_why_in_the_engine_lines_own_words(service):
    screen = make(service)
    card = call(service, "widget", id=screen["id"], kind="table")
    line = card["line"]
    assert line["live"] is False and line["state"] == "not_linked" or line["words"]
    assert not line["words"].startswith("Real-time")

    class Facts:
        def engine_facts(self, now):
            return {"trading": True, "feed": "token_rejected", "feed_since": NOW - 60_000}

    service.parts["engine"] = Facts()
    line = call(service, "widget", id=screen["id"], kind="table")["line"]
    assert line["kind"] == "feed_down" and line["live"] is False
    assert (
        call(service, "widget", id=screen["id"], kind="table")["not_live_words"] == (line["words"])
    )
    assert "Upstox refused the price feed's token" in line["words"]
    paused = named(service, "Paused", status="paused")
    add(service, 0, paused)
    line = call(service, "widget", id=paused["id"], kind="table")["line"]
    assert (line["kind"], line["words"]) == ("paused", "Paused · not checking")


def test_a_screen_that_needs_history_says_what_it_waits_for(service):
    needs_days = {
        "where": {
            "id": "c1", "left": {"field": "close", "tf": "D", "at": {"ago": 2}}, "op": "gt",
            "right": {"num": "100"},
        },
        "rank": {"by": {"field": "change_pct", "tf": "D"}, "order": "desc"},
    }  # fmt: skip
    screen = named(service, "Needs days", **needs_days)
    card = call(service, "widget", id=screen["id"], kind="table")
    assert (
        card["waits"] == "Waits for the history download: needs 3 day candles, and today gives 1."
    )
    assert card["line"]["kind"] == "waits" and card["day_only"] is True
    assert card["mode"] == "forming_day" and card["mode_words"].startswith("Forming day candle")


def test_cards_past_the_budget_answer_rows_pending_on_an_injected_clock(service):
    screens = [named(service, f"S{i}") for i in range(3)]
    record(service, screens[0], 1, judged({"AAA": 100}))
    ticks = iter(range(100))
    service.monotonic = lambda: float(next(ticks))  # one second a look at the clock
    got = call(service, "dashboard")
    assert [w["rows_pending"] for w in got["widgets"]] == [False, False, True]
    late = got["widgets"][2]
    assert late["name"] == "S0" and late["status"] == "active" and late["line"]["words"]
    assert late["rows_shown"] == [] and late["run"] is None and late["matches"] is None
    assert late["list"] is None and late["not_checked"] is None
    whole = call(service, "widget", id=late["screen_id"], kind="table")
    assert whole["rows_pending"] is False and whole["matches"] == 1


def test_a_day_of_twelve_one_minute_screens_reads_under_the_bridges_timeout(service):
    one_minute = {"run": {"on": "close", "tf": 1}}
    screens = [named(service, f"Minute {i}", **one_minute) for i in range(12)]
    start = ms(DAY, "09:15")

    def fill(tx):
        for screen in screens:
            for k in range(1, 376):
                tx.run_insert(
                    {
                        "screen_id": screen["id"], "screen_version": screen["version"],
                        "run_key": f"{screen['id']}:v{screen['version']}:{SESSION}:1:{k}",
                        "session": SESSION, "candle_start": start + (k - 1) * 60_000,
                        "candle_end": start + k * 60_000, "ran_at": start + k * 60_000 + 5000,
                        "matched": k % 9, "entered": 1, "exited": 1, "status": "done",
                        "engine_version": ENGINE_VERSION,
                    }
                )  # fmt: skip

    service.store.write(OWNER, fill)
    version = 0  # the suggestion is a table for each of the twelve; eight counts make twenty
    for screen in screens[:8]:
        version = add(service, version, screen, "count")["version"]
    began = time.monotonic()
    got = call(service, "dashboard")
    took = time.monotonic() - began
    assert len(got["widgets"]) == 20 and not any(w["rows_pending"] for w in got["widgets"])
    counted = [w for w in got["widgets"] if w["kind"] == "count"]
    assert len(counted) == 8 and len(counted[0]["counts"]["series"]) == 75
    assert took < 1.5, f"the dashboard read took {took:.2f}s"


def test_a_cards_read_loads_runs_only_for_a_count_card_and_only_for_its_screen(
    service, monkeypatch
):
    """Review L2: a table card's read loaded every run of the day of every screen as soon as any
    count card was on the layout, on the service's one event loop."""
    screens = [named(service, f"Minute {i}", run={"on": "close", "tf": 1}) for i in range(3)]
    for screen in screens:
        for k in range(1, 4):
            record(service, screen, k, judged({"AAA": 100}))
    version = add(service, 0, screens[0], "count")["version"]  # a count card on the layout
    reads: list[tuple] = []
    real_all, real_of = store_module.Tx.runs_session, store_module.Tx.runs_session_of
    monkeypatch.setattr(
        store_module.Tx,
        "runs_session",
        lambda self, s: reads.append(("all", s)) or real_all(self, s),
    )
    monkeypatch.setattr(
        store_module.Tx,
        "runs_session_of",
        lambda self, sid, s: reads.append(("one", sid)) or real_of(self, sid, s),
    )
    call(service, "widget", id=screens[1]["id"], kind="table")
    assert reads == [], "a table card needs none of the day's runs"
    card = call(service, "widget", id=screens[0]["id"], kind="count")
    assert reads == [("one", screens[0]["id"])], "a count card reads its own screen's day only"
    assert [p["matches"] for p in card["counts"]["series"]] == [1, 1, 1]
    got = call(service, "dashboard")
    assert [r for r in reads if r[0] == "all"] == []
    assert version >= 1 and len(got["widgets"]) == 4
