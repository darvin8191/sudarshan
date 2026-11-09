"""The review's round-4 follow-up on the dashboard service (M9.16, B1-1, B2-1 to B2-4, B2-7, B2-8).

Each test fails on 4be7ff2: the version-0 suggestion answered "not found" for a widget its page
still showed, a card named another screen's next check, a count card said "Real-time", a widget
removed elsewhere read as a screen that is gone, ``widget_add`` ignored the limit of screens
checking through the day, a 30 or 60-minute screen was marked as waiting for history it did not
need, and a changed screen's repeated check showed as two points on a count card.
"""

from __future__ import annotations

import pytest
from screener_helpers import ms
from test_screener_dashboard_ops import add, named
from test_screener_dashboard_ops import service as ops_service  # noqa: F401  (a fixture)
from test_screener_results import call, judged, record
from test_screener_runs import DAY, NOW

from sudarshan.screener import dashboard as dash
from sudarshan.screener import words
from sudarshan.screener.ops import ApiError
from sudarshan.screener.store import OWNER

service = ops_service

DAY_ONLY = {
    "where": {
        "id": "c1",
        "left": {"field": "day_open"},
        "op": "eq",
        "right": {"field": "day_high"},
    },
    "rank": {"by": {"field": "change_pct", "tf": "D"}, "order": "desc"},
}


class RealTime:
    def engine_facts(self, now):
        return {"trading": True, "feed": "ok"}


def _fails(svc, op, **args) -> ApiError:
    with pytest.raises(ApiError) as caught:
        call(svc, op, **args)
    return caught.value


# ---- B1-1 and B2-3: a widget that is missing while its screen is there -----------------------------------
def test_a_suggested_widget_whose_screen_was_paused_elsewhere_is_a_changed_dashboard(service):
    screen = named(service, "Soon paused")
    assert [w["screen_id"] for w in call(service, "dashboard")["widgets"]] == [screen["id"]]
    service.store.write(
        OWNER, lambda tx: tx.screen_change(screen["id"], 1, {"status": "paused"}, ms(DAY, "10:16"))
    )
    err = _fails(service, "widget_remove", id=screen["id"], kind="table", version=0)
    assert (err.code, err.message) == ("version_conflict", words.DASH_CONFLICT)


def test_a_widget_removed_elsewhere_is_not_a_screen_that_is_gone(service):
    screen = named(service, "Kept")
    version = add(service, 0, screen, "count")["version"]
    call(service, "widget_remove", id=screen["id"], kind="count", version=version)  # the tablet
    for err in (
        _fails(service, "widget", id=screen["id"], kind="count"),  # a run push reads the card
        _fails(service, "widget_remove", id=screen["id"], kind="count", version=version + 1),
        _fails(
            service,
            "widget_update",
            id=screen["id"],
            kind="count",
            version=version + 1,
            set={"rows": 5},
        ),  # fmt: skip
    ):
        assert (err.code, err.message) == ("version_conflict", words.DASH_CONFLICT)


def test_a_screen_that_is_gone_still_says_so(service):
    err = _fails(service, "widget", id=987654, kind="count")
    assert (err.code, err.message) == ("not_found", words.DASH_GONE)


# ---- B2-1 and B2-2: what the card's line says ------------------------------------------------------------
def test_a_card_names_its_own_screens_next_check(service):
    service.clock = lambda: ms(DAY, "10:30:30")
    service.parts["engine"] = RealTime()
    fifteen = named(service, "Fifteen")
    named(service, "One minute", run={"on": "close", "tf": 1})
    card = call(service, "widget", id=fifteen["id"], kind="table")
    assert card["next_at"] == ms(DAY, "10:45:05")
    assert card["line"]["kind"] == "real_time"
    assert card["line"]["words"] == "Real-time · next check 10:45"


def test_a_closed_card_gives_its_own_next_check_too(service):
    service.clock = lambda: ms("2026-10-31", "11:00")  # a Saturday

    class Shut:
        def engine_facts(self, now):
            return {"trading": False, "feed": "ok"}

    service.parts["engine"] = Shut()
    fifteen = named(service, "Fifteen")
    named(service, "One minute", run={"on": "close", "tf": 1})
    card = call(service, "widget", id=fifteen["id"], kind="table")
    assert card["line"]["kind"] == "closed"
    assert " · next check Mon 09:30" in card["line"]["words"], card["line"]["words"]
    assert card["not_live_words"] == "Market closed · next check Mon 09:30"


def test_a_count_card_never_says_real_time(service):
    service.clock = lambda: ms(DAY, "10:30:30")
    service.parts["engine"] = RealTime()
    screen = named(service, "Counted")
    add(service, 0, screen, "count")
    line = call(service, "widget", id=screen["id"], kind="count")["line"]
    assert line == {
        "kind": "checking",
        "words": "Checking · next check 10:45",
        "live": True,
        "state": line["state"],
    }
    assert "Real-time" not in line["words"]
    assert "checking" in dash.LINE_KINDS


# ---- B2-4: the limit of screens checking through the day -------------------------------------------------
def test_adding_a_widget_cannot_move_a_screen_past_the_limit_of_screens_checking_by_day(service):
    limit = service.settings.max_active
    for n in range(limit):
        named(service, f"Busy {n}")  # 15-min closes, Active
    evening = named(service, "Evening", run={"on": "after_close"}, **DAY_ONLY)
    err = _fails(
        service, "widget_add", version=0, screen_id=evening["id"], kind="count", when="during_day"
    )
    assert err.code == "refused" and err.message.startswith(f"Up to {limit} screens can check")
    stored = service.store.read(OWNER, lambda tx: tx.screen(evening["id"]))
    assert stored["spec"]["run"] == {"on": "after_close"}, "nothing was changed"
    assert service.store.read(OWNER, lambda tx: tx.dashboard()) is None, "and no widget was added"


def test_adding_a_widget_may_move_a_screen_to_the_day_below_the_limit(service):
    evening = named(service, "Evening", run={"on": "after_close"}, **DAY_ONLY)
    call(service, "widget_add", version=0, screen_id=evening["id"], kind="count", when="during_day")
    stored = service.store.read(OWNER, lambda tx: tx.screen(evening["id"]))
    assert stored["spec"]["run"]["on"] == "close"


# ---- B2-7: how many candles a day holds -----------------------------------------------------------------
@pytest.mark.parametrize(
    "tf, need, most", [(30, 13, 13), (60, 7, 7), (5, 75, 75), (15, 25, 25), (3, 125, 125)]
)
def test_a_screen_needing_what_a_day_holds_does_not_wait(tf, need, most):
    assert dash.waits_of({tf: need}) is None
    assert dash.waits_of({tf: need + 1}) == words.waits_words(tf, need + 1, most)


# ---- B2-8: one point for each check --------------------------------------------------------------------
def test_a_boundary_run_twice_is_one_point_the_newest_runs():
    runs = [
        {"id": 7, "candle_end": 2_000, "matched": 2},
        {"id": 9, "candle_end": 2_000, "matched": 1},
        {"id": 8, "candle_end": 1_000, "matched": 4},
    ]
    assert dash.newest_per_boundary(runs) == [(1_000, 4), (2_000, 1)]
    assert dash.newest_per_boundary(list(reversed(runs))) == [(1_000, 4), (2_000, 1)]


def test_a_count_card_keeps_the_newest_run_of_a_boundary(service):
    screen = named(service, "Changed")
    add(service, 0, screen, "count")
    record(service, screen, 1, judged({"AAA": 100, "BBB": 200, "CCC": 300}))
    changed = service.store.write(
        OWNER, lambda tx: tx.screen_change(screen["id"], screen["version"], {}, NOW + 1)
    )  # a new version of the screen runs the latest boundary again
    record(service, changed, 1, judged({"AAA": 100}))
    series = call(service, "widget", id=screen["id"], kind="count")["counts"]["series"]
    assert [p["matches"] for p in series] == [1]
