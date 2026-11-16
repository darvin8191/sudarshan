"""The count of alerts not looked at, and the mark that clears it (M3.8, gap audit GA7.6).

Before, the screener kept no mark of an alert being looked at, so ``today`` answered
``unseen_alerts: 0`` whatever had happened and the Dashboard card's "N new alerts" never showed.
Now a run that raised an alert counts until the owner opens it: by screen in the state, summed in
``today``, and cleared by ``alerts_seen`` up to the run he was shown.
"""

from __future__ import annotations

import pytest
from test_screener_results import call, judged, make, record
from test_screener_results import service as service  # noqa: F401  (the fixture)
from test_screener_runs import NOW

from sudarshan.screener import contract, words
from sudarshan.screener.ops import ApiError
from sudarshan.screener.store import OWNER


def unseen(svc) -> int:
    return call(svc, "today")["unseen_alerts"]


def of_screen(svc, screen) -> int:
    rows = {r["id"]: r for r in call(svc, "state")["screens"]}
    return rows[screen["id"]]["unseen_alerts"]


def test_the_contract_carries_the_op():
    ops = {s.name: s for s in contract.OPS}
    assert (ops["alerts_seen"].method, ops["alerts_seen"].route, ops["alerts_seen"].cls) == (
        "POST",
        "/alerts/seen",
        "write",
    )


def test_a_run_that_raised_an_alert_counts_until_it_is_opened(service):  # noqa: F811
    screen = make(service)
    assert unseen(service) == 0
    first = record(service, screen, 1, judged({"AAA": 100, "BBB": 200}))
    assert first.told is True
    assert unseen(service) == 1, "the service answers 0 whatever was raised"
    assert of_screen(service, screen) == 1
    # a run that raised nothing new adds nothing
    quiet = record(service, screen, 2, judged({"AAA": 100, "BBB": 200}))
    assert quiet.told is False and unseen(service) == 1
    got = call(service, "alerts_seen", screen_id=screen["id"], through=quiet.run_id)
    assert got == {"seen": 1, "unseen_alerts": 0}
    assert unseen(service) == 0 and of_screen(service, screen) == 0
    # opening again marks nothing, and the time of the first look is kept
    assert call(service, "alerts_seen", screen_id=screen["id"])["seen"] == 0
    row = service.store.read(OWNER, lambda tx: tx.run(first.run_id))
    assert row["alerted"] is True and row["alert_seen_at"] == NOW


def test_an_alert_that_came_after_what_he_was_shown_stays_new(service):  # noqa: F811
    screen = make(service)
    first = record(service, screen, 1, judged({"AAA": 100}))
    second = record(service, screen, 2, judged({"AAA": 100, "BBB": 200}))
    assert first.told and second.told and unseen(service) == 2
    got = call(service, "alerts_seen", screen_id=screen["id"], through=first.run_id)
    assert got == {"seen": 1, "unseen_alerts": 1}


def test_one_screen_s_alerts_or_every_screen_s(service):  # noqa: F811
    one, two = make(service), make(service, name="Other", target=None)
    record(service, one, 1, judged({"AAA": 100}))
    record(service, two, 1, judged({"BBB": 100}))
    assert unseen(service) == 2
    assert call(service, "alerts_seen", screen_id=one["id"]) == {"seen": 1, "unseen_alerts": 1}
    assert (of_screen(service, one), of_screen(service, two)) == (0, 1)
    assert call(service, "alerts_seen") == {"seen": 1, "unseen_alerts": 0}


def test_an_alert_that_was_never_sent_is_not_one_to_look_at(service):  # noqa: F811
    """A digest the alerts service refused for good: the run's line says no alert went."""
    screen = make(service)
    report = record(service, screen, 1, judged({"AAA": 100}))
    assert unseen(service) == 1
    service.store.write(
        OWNER, lambda tx: tx.run_alert_words(report.run_key, words.untold_words(409))
    )
    assert unseen(service) == 0


def test_a_deleted_screen_s_alerts_are_not_counted_and_a_gone_screen_is_said(service):  # noqa: F811
    screen = make(service)
    record(service, screen, 1, judged({"AAA": 100}))
    call(service, "delete", id=screen["id"])
    assert unseen(service) == 0
    with pytest.raises(ApiError) as caught:
        call(service, "alerts_seen", screen_id=screen["id"])
    assert caught.value.code == "not_found"


def test_the_run_s_push_says_whether_it_raised_an_alert(service):  # noqa: F811
    screen = make(service)
    record(service, screen, 1, judged({"AAA": 100}))
    record(service, screen, 2, judged({"AAA": 100}))
    rows = service.store.read(OWNER, lambda tx: tx.outbox_due(NOW + 10**9, 50))
    pushes = [r["payload"] for r in rows if r["kind"] == "push"]
    assert [p["alert"] for p in pushes] == [True, False]
