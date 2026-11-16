"""AL2 (M13.10): the signals table in alerts.db, the once-only rule for TRINETRA's changes.

A claim is made when an item arrives and is never made twice; the item waits in the claim, so a
restart still sends it; one a restart finds on its way is never sent again (``unsure``).
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from alerts_fakes import ist

from sudarshan.alerts import store as store_module
from sudarshan.alerts.store import Store

NOW = ist(2026, 11, 16, 10, 42, 5).timestamp()


def body(item_id: str) -> str:
    return f'["change", {{"id": "{item_id}"}}]'


def test_an_item_is_claimed_once_across_a_restart(tmp_path):
    path = tmp_path / "state" / "alerts.db"
    first = Store(path)
    assert first.claim_signals([("c-1", NOW, body("c-1")), ("a-x", NOW, body("a-x"))], NOW) == [
        "c-1", "a-x",
    ]  # fmt: skip
    assert first.claim_signals([("c-1", NOW, body("c-1"))], NOW + 1) == []
    first.close()
    second = Store(path)  # a restart of sudarshan-alerts
    assert second.claim_signals([("c-1", NOW, body("c-1")), ("c-2", NOW, body("c-2"))], NOW) == [
        "c-2",
    ]  # fmt: skip
    assert [item for item, _, _ in second.queued_signals(NOW)] == ["c-1", "a-x", "c-2"]
    second.close()


def test_what_waits_comes_back_with_its_body_and_an_item_that_ended_keeps_none(tmp_path):
    path = tmp_path / "alerts.db"
    held = Store(path)
    held.claim_signals([("c-1", NOW + 60, body("c-1")), ("c-2", NOW, body("c-2"))], NOW)
    held.set_signals(["c-2"], "sent", NOW + 5)
    held.close()
    again = Store(path)
    assert again.queued_signals(NOW + 10) == [("c-1", NOW + 60, body("c-1"))]
    again.set_signals(["c-1"], "stale", NOW + 2000)
    assert again.queued_signals(NOW + 2001) == []
    again.close()
    rows = sqlite3.connect(path).execute("SELECT item_id, state, body FROM signals").fetchall()
    assert sorted(rows) == [("c-1", "stale", None), ("c-2", "sent", None)]


def test_an_item_a_restart_finds_on_its_way_is_settled_unsure_and_never_queued(tmp_path):
    path = tmp_path / "alerts.db"
    held = Store(path)
    held.claim_signals([("c-1", NOW, body("c-1")), ("c-2", NOW, body("c-2"))], NOW)
    held.set_signals(["c-1"], "sending", NOW + 1)  # the process stopped mid-send
    held.close()
    again = Store(path)
    assert [item for item, _, _ in again.queued_signals(NOW + 9)] == ["c-2"]
    assert again.claim_signals([("c-1", NOW, body("c-1"))], NOW + 10) == []
    again.close()
    state = sqlite3.connect(path).execute("SELECT state FROM signals WHERE item_id='c-1'")
    assert state.fetchone() == ("unsure",)


def test_a_file_that_fails_keeps_the_rule_in_memory_for_the_run(tmp_path):
    blocker = tmp_path / "state"
    blocker.write_text("not a folder")  # the file cannot be made: the store answers from memory
    held = Store(blocker / "alerts.db")
    assert held.in_memory
    assert held.claim_signals([("c-1", NOW, body("c-1"))], NOW) == ["c-1"]
    assert held.claim_signals([("c-1", NOW, body("c-1"))], NOW) == []
    assert [item for item, _, _ in held.queued_signals(NOW)] == ["c-1"]
    held.set_signals(["c-1"], "sending", NOW)
    assert held.queued_signals(NOW) == []  # it may have gone: never sent again
    assert held.claim_signals([("c-1", NOW, body("c-1"))], NOW) == []


def test_ended_items_are_forgotten_after_sixty_days_and_waiting_ones_never(tmp_path):
    held = Store(tmp_path / "alerts.db")
    held.claim_signals([("c-1", NOW, body("c-1")), ("c-2", NOW, body("c-2"))], NOW)
    held.set_signals(["c-1"], "sent", NOW)
    later = ist(2026, 11, 16, 10, 42, 5) + store_module.KEEP_NOTICES + timedelta(days=1)
    assert held.prune(later) == 1
    assert held.claim_signals([("c-1", NOW, body("c-1"))], NOW) == ["c-1"]  # its claim lapsed
    assert [item for item, _, _ in held.queued_signals(NOW)] == ["c-2", "c-1"]
    held.close()
