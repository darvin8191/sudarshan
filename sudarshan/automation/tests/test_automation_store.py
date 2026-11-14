"""The automation store: strategies, versions, tokens, and the two claims (A1)."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing

import pytest

from sudarshan.automation import contract
from sudarshan.automation import model as m
from sudarshan.automation import store as store_mod


def _make(store, **change):
    form = {"name": "ORB", "kind": "future", "underlying": "NIFTY", "product": "MIS", **change}
    return store.create(contract.clean(form))


def test_a_strategy_is_born_sandbox_and_stopped_with_a_state_row(store):
    strategy, token = _make(store)
    assert (strategy.mode, strategy.state, strategy.version) == ("sandbox", "stopped", 1)
    assert strategy.token_tail == token[-4:] and strategy.contract_key == "NFO:NIFTY:FUT"
    state = store.state(strategy.id)
    assert (state.last_target, state.alerts_today, state.held_symbol) == (0, 0, None)
    assert store.strategies() == [strategy] and store.count() == 1


def test_the_token_is_kept_only_as_its_hash(store):
    strategy, token = _make(store)
    with closing(sqlite3.connect(store.path)) as db:
        dump = "\n".join(db.iterdump())
    assert token not in dump and token[4:] not in dump
    assert store_mod.token_hash(token) in dump
    assert store.by_token(token) == strategy and store.by_token(token + "x") is None


def test_a_new_link_ends_the_old_one(store):
    strategy, old = _make(store)
    changed, new = store.replace_token(strategy.id, strategy.version)
    assert new != old and changed.version == 2 and changed.token_tail == new[-4:]
    assert store.by_token(old) is None and store.by_token(new).id == strategy.id


def test_a_second_strategy_of_the_same_name_is_refused(store):
    _make(store)
    with pytest.raises(contract.Refused, match="already has this name"):
        _make(store, underlying="BANKNIFTY")
    other, _ = _make(store, name="Second", underlying="BANKNIFTY")
    with pytest.raises(contract.Refused, match="already has this name"):
        store.update(other.id, other.version, {"name": "ORB"})


def test_a_write_carries_the_version_it_read(store):
    strategy, _ = _make(store)
    changed = store.update(strategy.id, strategy.version, {"max_age_s": 60})
    assert (changed.max_age_s, changed.version) == (60, 2)
    with pytest.raises(store_mod.Conflict):
        store.update(strategy.id, strategy.version, {"max_age_s": 90})  # the page's old copy
    assert store.strategy(strategy.id).max_age_s == 60
    with pytest.raises(store_mod.Missing):
        store.update(999, 1, {"max_age_s": 90})


def test_one_strategy_owns_a_contract_and_product_in_a_mode(store):
    first, _ = _make(store)
    second, _ = _make(store, name="Second")
    store.set_state(first.id, first.version, m.ACTIVE)
    for state in (m.ACTIVE, m.EXITS_ONLY):
        with pytest.raises(store_mod.Taken) as taken:
            store.set_state(second.id, second.version, state)
        assert taken.value.other == "ORB"
    assert store.strategy(second.id).state == m.STOPPED
    # another product, or another underlying, is another position
    third, _ = _make(store, name="Third", product="NRML")
    store.set_state(third.id, third.version, m.ACTIVE)
    # once the first is Stopped the contract is free
    store.set_state(first.id, None, m.STOPPED, "by the owner")
    assert store.set_state(second.id, second.version, m.ACTIVE).state == m.ACTIVE


def test_an_edit_cannot_move_an_active_strategy_onto_a_taken_contract(store):
    first, _ = _make(store)
    second, _ = _make(store, name="Second", underlying="BANKNIFTY")
    store.set_state(first.id, first.version, m.ACTIVE)
    second = store.set_state(second.id, second.version, m.ACTIVE)
    moved = contract.clean({**contract.form_of(second), "underlying": "NIFTY"})
    with pytest.raises(store_mod.Taken):
        store.update(second.id, second.version, moved)


def test_two_strategies_started_together_on_one_contract_one_is_active(store):
    """Barrier 4. Two starts released together: the index lets one in."""
    first, _ = _make(store)
    second, _ = _make(store, name="Second")
    barrier, results = threading.Barrier(2), {}

    def start(strategy):
        barrier.wait(timeout=10)
        try:
            results[strategy.name] = store.set_state(strategy.id, strategy.version, m.ACTIVE).state
        except store_mod.Taken as taken:
            results[strategy.name] = f"taken by {taken.other}"

    threads = [threading.Thread(target=start, args=(s,)) for s in (first, second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(results.values(), key=len) in (
        ["active", "taken by ORB"],
        ["active", "taken by Second"],
    ), results
    active = [s for s in store.strategies() if s.state == m.ACTIVE]
    assert len(active) == 1


def _columns(claim="100|-|long:", verdict="claimed", **more):
    return {"claim": claim, "verdict": verdict, "received_ms": 1, "counts": True, **more}


def test_an_alert_is_claimed_once(store):
    strategy, _ = _make(store)
    first = store.claim_alert(strategy.id, lambda s, st: _columns(), "2026-11-16")
    again = store.claim_alert(strategy.id, lambda s, st: _columns(), "2026-11-16")
    assert first[2] is not None and again[2] is None and again[3] is not None
    assert store.state(strategy.id).alerts_today == 1  # the repeat did not count
    assert len(store.alerts(strategy.id)) == 1
    other, _ = _make(store, name="Second", underlying="BANKNIFTY")
    assert store.claim_alert(other.id, lambda s, st: _columns(), "2026-11-16")[2] is not None


def test_the_claim_rests_on_the_unique_index(store):
    """Mutation 1: without the index a second delivery would be claimed too."""
    strategy, _ = _make(store)
    store.claim_alert(strategy.id, lambda s, st: _columns(), "2026-11-16")
    with closing(sqlite3.connect(store.path)) as db, db:
        (sql,) = db.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'ux_auto_alert_claim'"
        ).fetchone()
        assert "UNIQUE" in sql and "strategy_id, claim" in sql
        db.execute("DROP INDEX ux_auto_alert_claim")
    assert store.claim_alert(strategy.id, lambda s, st: _columns(), "2026-11-16")[2] is not None


def test_the_claim_reads_and_writes_in_one_go(store):
    strategy, _ = _make(store)
    seen = []

    def decide(s, st):
        seen.append((s.state, st.alerts_on("2026-11-16")))
        return _columns(
            claim=f"{len(seen)}|-|flat:",
            state_values={"told_stopped_day": "2026-11-16"},
            strategy_values={"state": m.EXITS_ONLY, "stopped_why": "too many alerts today"},
        )

    store.claim_alert(strategy.id, decide, "2026-11-16")
    store.claim_alert(strategy.id, decide, "2026-11-16")
    assert seen == [("stopped", 0), ("exits_only", 1)]
    state = store.state(strategy.id)
    assert (state.day, state.alerts_today, state.told_stopped_day) == (
        "2026-11-16",
        2,
        "2026-11-16",
    )
    assert store.strategy(strategy.id).stopped_why == "too many alerts today"
    store.claim_alert(strategy.id, decide, "2026-11-17")  # a new day counts from one
    assert store.state(strategy.id).alerts_today == 1
    assert store.claim_alert(strategy.id, lambda s, st: None, "2026-11-17")[2:] == (None, None)
    assert store.claim_alert(999, decide, "2026-11-17") is None


def test_the_answer_is_written_once_on_the_row_and_the_state_moves_with_it(store):
    strategy, _ = _make(store)
    _, _, alert_id, _ = store.claim_alert(
        strategy.id, lambda s, st: _columns(payload="x" * 5000, ip="52.89.214.238"), "2026-11-16"
    )
    store.finish_alert(
        alert_id,
        verdict="sent",
        sentence="Bought 65 NIFTY 24 Nov FUT.",
        order_id="251116000001",
        answer_ms=42,
        state_values={"last_target": 1, "last_fired_s": 100, "held_symbol": "NIFTY24NOV26FUT"},
    )
    row = store.alert(alert_id)
    assert (row["verdict"], row["order_id"], row["answer_ms"]) == ("sent", "251116000001", 42)
    assert len(row["payload"]) == store_mod.PAYLOAD_KEEP
    state = store.state(strategy.id)
    assert (state.last_target, state.last_fired_s, state.held_symbol) == (1, 100, "NIFTY24NOV26FUT")
    line = store.last_alert(strategy.id)
    assert "payload" not in line and "ip" not in line and "claim" not in line  # not for the page


def test_notes_are_lines_but_not_claims(store):
    strategy, _ = _make(store)
    for _ in range(3):
        assert store.note_alert(strategy.id, verdict="repeat", received_ms=5, sentence="Repeat.")
    assert [a["verdict"] for a in store.alerts(strategy.id)] == ["repeat"] * 3
    assert store.state(strategy.id).alerts_today == 0


def test_the_log_is_read_newest_first_in_pages(store):
    strategy, _ = _make(store)
    for n in range(5):
        store.claim_alert(strategy.id, lambda s, st, n=n: _columns(claim=f"{n}|-|flat:"), "d")
    page = store.alerts(strategy.id, limit=2)
    assert len(page) == 2 and page[0]["id"] > page[1]["id"]
    older = store.alerts(strategy.id, before=page[-1]["id"], limit=10)
    assert len(older) == 3 and all(a["id"] < page[-1]["id"] for a in older)


def test_the_sandbox_run_counts_what_the_go_live_sheet_shows(store):
    strategy, _ = _make(store)
    day = 86_400_000
    rows = [("sent", 0), ("no_action", 10), ("late", 20), ("repeat", 30), ("sent", 3 * day)]
    for n, (verdict, at) in enumerate(rows):
        store.note_alert(strategy.id, verdict=verdict, received_ms=at, mode="sandbox", claim=str(n))
    store.note_alert(strategy.id, verdict="sent", received_ms=9 * day, mode="live", claim="live")
    assert store.sandbox_run(strategy.id) == {"alerts": 4, "sessions": 2, "sent": 3, "refused": 1}


def test_the_store_opens_no_file_until_it_is_used(tmp_path):
    made = store_mod.AutomationStore(tmp_path / "db" / "automation.db")
    assert not (tmp_path / "db").exists()
    made.strategies()
    assert (tmp_path / "db" / "automation.db").exists()
    made.dispose()


# ---- L2: the alert log is pruned -------------------------------------------------------------------


def test_l2_lines_older_than_ninety_days_go_and_the_rest_stay(store):
    strategy, _ = _make(store)
    day, now = 86_400_000, 200 * 86_400_000
    for n, age in enumerate((0, 1, 89, 90, 91, 400)):
        store.note_alert(strategy.id, verdict="sent", received_ms=now - age * day - 1, claim=str(n))
    assert store.prune(now) == 3
    left = sorted((now - 1 - row["received_ms"]) // day for row in store.alerts(strategy.id))
    assert left == [0, 1, 89]
    assert store.prune(now) == 0


def test_l2_a_strategy_keeps_only_its_newest_lines_and_another_strategy_is_untouched(store):
    first, _ = _make(store)
    second, _ = _make(store, name="Second", underlying="BANKNIFTY")
    now = 200 * 86_400_000
    for n in range(12):
        store.note_alert(first.id, verdict="late", received_ms=now - 1000 + n, claim=f"a{n}")
    for n in range(4):
        store.note_alert(second.id, verdict="late", received_ms=now - 1000 + n, claim=f"b{n}")
    assert store.prune(now, keep_rows=5) == 7
    kept = [row["received_ms"] - (now - 1000) for row in store.alerts(first.id)]
    assert kept == [11, 10, 9, 8, 7]  # the newest five
    assert len(store.alerts(second.id)) == 4


def test_l2_the_page_read_prunes_once_a_day_and_never_fails_for_it(store, monkeypatch):
    from automation_world import NOW, FakeDesk

    from sudarshan.automation import control

    monkeypatch.setattr(control, "_pruned_on", {})
    strategy, _ = _make(store)
    desk = FakeDesk()
    old = int(NOW * 1000) - 91 * 86_400_000
    store.note_alert(strategy.id, verdict="sent", received_ms=old, claim="old")
    calls = []
    real = store.prune
    monkeypatch.setattr(store, "prune", lambda now_ms: calls.append(now_ms) or real(now_ms))
    control.listing(desk=desk, store=store)
    control.listing(desk=desk, store=store)
    assert len(calls) == 1 and store.alerts(strategy.id) == []
    desk.clock = NOW + 86_400  # the next day in India
    monkeypatch.setattr(store, "prune", lambda now_ms: 1 / 0)
    assert control.listing(desk=desk, store=store)["status"] == "success"


def test_l2_the_intake_never_prunes():
    """The design: pruned from the owner's read, never from an alert's request."""
    from pathlib import Path

    source = (Path(__file__).parents[1] / "intake.py").read_text()
    assert "prune" not in source
