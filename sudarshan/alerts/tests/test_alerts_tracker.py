"""Which updates become alerts, once only, in order; across a restart too."""

from __future__ import annotations

from datetime import timedelta

import pytest
from alerts_fakes import ist, update

from sudarshan.alerts.orders import TRIGGER_SETTLE_S, OrderTracker, parse, state_of
from sudarshan.alerts.store import Store

T0 = ist(2026, 10, 21, 10, 0, 0)


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "state" / "alerts.db")
    yield store
    store.close()


def kinds(alerts):
    return [(a.key, a.kind, a.filled) for a in alerts]


def feed(tracker, *payloads, at=T0):
    out = []
    for n, payload in enumerate(payloads):
        out += tracker.on_update(payload, at + timedelta(milliseconds=n))
    return out


def test_dedupe_survives_a_restart(tmp_path):
    path = tmp_path / "state" / "alerts.db"
    day = [
        update("1", "open"),
        update("1", "open", filled_quantity=20, average_price=100),
        update("1", "complete", average_price=100.5),
        update("2", "rejected", rejection_reason="Insufficient funds"),
        update("3", "cancelled", filled_quantity=10, average_price=99),
    ]
    first = Store(path)
    assert kinds(feed(OrderTracker(first), *day)) == [
        ("1", "part", 20), ("1", "filled", 65), ("2", "rejected", 0), ("3", "part_cancelled", 10),
    ]  # fmt: skip
    first.close()
    # A new process on the same file, and the proxy (or a webhook) replays the whole day.
    second = Store(path)
    again = OrderTracker(second)
    assert feed(again, *day, at=T0 + timedelta(hours=1)) == []
    # Something new still gets through.
    assert kinds(again.on_update(update("4", "complete"), T0 + timedelta(hours=2))) == [
        ("4", "filled", 65)
    ]
    second.close()


def test_part_fills_only_move_forward(store):
    tracker = OrderTracker(store)
    got = feed(
        tracker,
        update("7", "open", quantity=1800, filled_quantity=600, average_price=50),
        update("7", "open", quantity=1800, filled_quantity=600, average_price=50),  # repeated
        update("7", "open", quantity=1800, filled_quantity=400, average_price=50),  # late
        update("7", "open", quantity=1800, filled_quantity=1200, average_price=50.2),
        update("7", "open", quantity=1800, filled_quantity=1800, average_price=50.3),  # all
        update("7", "complete", quantity=1800, filled_quantity=1800, average_price=50.3),
        update("7", "open", quantity=1800, filled_quantity=1200, average_price=50.2),  # stale
    )
    assert kinds(got) == [("7", "part", 600), ("7", "part", 1200), ("7", "filled", 1800)]


def test_nothing_after_a_final_state(store):
    tracker = OrderTracker(store)
    assert kinds(feed(tracker, update("8", "rejected"))) == [("8", "rejected", 0)]
    assert feed(tracker, update("8", "complete"), update("8", "cancelled")) == []


def test_cancellations_by_setting(tmp_path):
    def run(setting, payload, name):
        store = Store(tmp_path / name / "alerts.db")
        try:
            return kinds(OrderTracker(store, cancelled=setting).on_update(payload, T0))
        finally:
            store.close()

    none_filled = update("9", "cancelled")
    part_filled = update("9", "cancelled", filled_quantity=25, average_price=10)
    assert run("all", none_filled, "a") == [("9", "cancelled", 0)]
    assert run("part-filled", none_filled, "b") == []
    # A cancel after a part fill is always told: the position is not what was ordered.
    assert run("part-filled", part_filled, "c") == [("9", "part_cancelled", 25)]
    assert run("all", update("9", "cancelled after market order"), "d") == [("9", "cancelled", 0)]


def _stop(order_id, status, **fields):
    return update(order_id, status, action="SELL", pricetype="SL", trigger_price=120, **fields)


def test_a_stop_that_triggers_and_stays_unfilled_is_told_once(store):
    tracker = OrderTracker(store)
    assert feed(tracker, _stop("s1", "trigger pending"), _stop("s1", "open")) == []
    assert tracker.due(T0 + timedelta(seconds=TRIGGER_SETTLE_S - 0.5)) == []
    (alert,) = tracker.due(T0 + timedelta(seconds=TRIGGER_SETTLE_S + 0.1))
    assert (alert.kind, alert.at) == ("triggered", T0 + timedelta(milliseconds=1))
    assert tracker.due(T0 + timedelta(seconds=60)) == []
    # The fill that follows is still told.
    later = tracker.on_update(_stop("s1", "complete", average_price=119.5), T0 + timedelta(1))
    assert kinds(later) == [("s1", "filled", 65)]


def test_a_stop_modify_or_cancel_is_not_a_trigger(store):
    tracker = OrderTracker(store)
    # Modify: pending and modified both arrive as "open", then trigger pending again.
    feed(tracker, _stop("m1", "trigger pending"), _stop("m1", "open"), _stop("m1", "open"),
         _stop("m1", "trigger pending"))  # fmt: skip
    # Cancel: "cancel pending" arrives as "open", then cancelled (told as a cancellation).
    cancelled = feed(tracker, _stop("c1", "trigger pending"), _stop("c1", "open"),
                     _stop("c1", "cancelled"))  # fmt: skip
    # Changed into a plain limit order: not a stop any more.
    feed(tracker, _stop("l1", "trigger pending"), update("l1", "open", pricetype="LIMIT"))
    assert tracker.due(T0 + timedelta(seconds=60)) == []
    assert kinds(cancelled) == [("c1", "cancelled", 0)]


def test_a_stop_that_fills_at_once_is_one_fill(store):
    tracker = OrderTracker(store)
    got = feed(tracker, _stop("f1", "trigger pending"), _stop("f1", "open"),
               _stop("f1", "complete", average_price=119.8))  # fmt: skip
    assert kinds(got) == [("f1", "filled", 65)]
    assert tracker.due(T0 + timedelta(seconds=60)) == []
    assert "(SL, MIS, NFO)" in got[0].line


def test_trigger_pending_is_remembered_across_a_restart(tmp_path):
    path = tmp_path / "alerts.db"
    before = Store(path)
    OrderTracker(before).on_update(_stop("r1", "trigger_pending"), T0)  # the sandbox spelling
    before.close()
    after = Store(path)
    tracker = OrderTracker(after)
    tracker.on_update(_stop("r1", "open"), T0 + timedelta(minutes=5))
    assert kinds(tracker.due(T0 + timedelta(minutes=6))) == [("r1", "triggered", 0)]
    after.close()


def test_sandbox_updates_are_skipped_unless_asked_for(tmp_path):
    payload = update("x1", "complete", mode="analyze", broker="sandbox")
    off, on = Store(tmp_path / "a" / "alerts.db"), Store(tmp_path / "b" / "alerts.db")
    assert OrderTracker(off).on_update(payload, T0) == []
    (alert,) = OrderTracker(on, sandbox=True).on_update(payload, T0)
    assert alert.key == "sandbox:x1" and alert.line.startswith("Sandbox filled: ")
    off.close()
    on.close()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"orderid": ""},
        {"orderid": None, "order_status": "complete"},
        {"orderid": "z" * 65, "order_status": "complete"},
        {"orderid": "z1", "order_status": "open", "quantity": "lots", "filled_quantity": None},
        {"orderid": "z2", "order_status": "open", "quantity": float("nan"), "filled_quantity": -5},
    ],
)
def test_junk_is_ignored(store, payload):
    assert OrderTracker(store).on_update(payload, T0) == []


def test_status_words():
    assert state_of("complete") == "complete"
    assert state_of("Rejected") == "rejected"
    assert state_of("canceled") == "cancelled"
    assert state_of("trigger_pending") == state_of("trigger pending") == "trigger_pending"
    # A failed cancel request leaves the order working: never read as cancelled.
    for working in ("not cancelled", "cancel pending", "open pending", "after market order req "
                    "received", "", None):  # fmt: skip
        assert state_of(working) == "working"
    assert parse(update("1", "open", quantity="65", filled_quantity="20.0")).filled == 20


def test_waiting_triggers_are_bounded(store, monkeypatch):
    from sudarshan.alerts import orders

    monkeypatch.setattr(orders, "MAX_WAITING_TRIGGERS", 3)
    tracker = OrderTracker(store)
    for n in range(5):
        feed(tracker, _stop(f"b{n}", "trigger pending"), _stop(f"b{n}", "open"))
        assert len(tracker._waiting) <= 3
    assert sorted(a.key for a in tracker.due(T0 + timedelta(seconds=60))) == ["b2", "b3", "b4"]


def test_old_records_are_pruned(store):
    tracker = OrderTracker(store)
    tracker.on_update(update("p1", "complete"), T0)
    assert store.prune(T0 + timedelta(days=8)) == 2  # its sent row and its order row
    # Forgotten after a week: a replay that old would be told again, which nothing sends.
    assert kinds(tracker.on_update(update("p1", "complete"), T0 + timedelta(days=8))) == [
        ("p1", "filled", 65)
    ]


# -- the 3 s rule, pinned ------------------------------------------------------------------------


def test_a_modify_that_takes_2_5_s_is_not_a_trigger(store):
    tracker = OrderTracker(store)  # the shipped settle time
    tracker.on_update(_stop("p1", "trigger pending"), T0)
    tracker.on_update(_stop("p1", "open"), T0 + timedelta(seconds=0.1))  # modify pending
    tracker.on_update(_stop("p1", "trigger pending"), T0 + timedelta(seconds=2.6))  # done
    assert tracker.due(T0 + timedelta(seconds=30)) == []


def test_an_unfilled_trigger_is_told_at_3_1_s_and_not_at_2_9_s(store):
    tracker = OrderTracker(store)
    tracker.on_update(_stop("p2", "trigger pending"), T0)
    tracker.on_update(_stop("p2", "open"), T0)
    assert tracker.due(T0 + timedelta(seconds=2.9)) == []
    assert kinds(tracker.due(T0 + timedelta(seconds=3.1))) == [("p2", "triggered", 0)]


def test_a_slow_modify_does_not_use_up_the_real_trigger(store):
    """A modify slower than 3 s is told as a trigger (nothing tells them apart); the real one
    that follows, in the next trigger-pending episode, is still told."""
    tracker = OrderTracker(store)
    tracker.on_update(_stop("p3", "trigger pending"), T0)
    tracker.on_update(_stop("p3", "open"), T0 + timedelta(seconds=1))  # modify pending
    assert len(tracker.due(T0 + timedelta(seconds=4.5))) == 1  # slow: told
    tracker.on_update(_stop("p3", "trigger pending"), T0 + timedelta(seconds=5))  # modified
    tracker.on_update(_stop("p3", "open"), T0 + timedelta(seconds=60))  # the real trigger
    assert kinds(tracker.due(T0 + timedelta(seconds=64))) == [("p3", "triggered", 0)]
    assert tracker.due(T0 + timedelta(seconds=120)) == []


# Two guards stand between a modify or a cancel and a false "Triggered": the update that ends the
# wait drops it, and due() reads the record again. Each test below holds one of them alone.


def test_an_update_that_ends_the_wait_drops_it_at_once(store):
    tracker = OrderTracker(store)
    for order_id, end in (("w1", "trigger pending"), ("w2", "cancelled"), ("w3", "complete")):
        tracker.on_update(_stop(order_id, "trigger pending"), T0)
        tracker.on_update(_stop(order_id, "open"), T0 + timedelta(seconds=0.1))
        assert order_id in tracker._waiting
        tracker.on_update(_stop(order_id, end), T0 + timedelta(seconds=0.2))
        assert order_id not in tracker._waiting  # nothing lingers for the rest of the settle time


def test_due_goes_by_the_record_not_by_the_wait(store):
    tracker = OrderTracker(store)
    record = {
        "d1": ("trigger_pending", 0, False),  # modified back to waiting for its trigger
        "d2": ("cancelled", 0, True),
        "d3": ("working", 20, False),  # part filled: told as a fill, not as a trigger
    }
    for order_id, (state, filled, final) in record.items():
        tracker.on_update(_stop(order_id, "trigger pending"), T0)
        tracker.on_update(_stop(order_id, "open"), T0)
        with store.transaction() as tx:  # the record moved on; the wait was not told
            tx.save_order(order_id, state, filled, final, tx.order(order_id).pending_seen, T0)
    assert tracker.due(T0 + timedelta(seconds=60)) == []


# -- rows found in the order book after a pause ------------------------------------------------


def test_book_rows_are_marked_and_never_start_a_trigger_check(store):
    tracker = OrderTracker(store)
    tracker.on_update(_stop("b1", "trigger pending"), T0)
    # The book reads a stop order waiting for its trigger as open: no trigger, no state change.
    assert tracker.on_update(_stop("b1", "open"), T0, found_later=True) == []
    assert tracker._waiting == {}
    with store.transaction() as tx:
        assert tx.order("b1").state == "trigger_pending"
    # A real trigger afterwards is still seen.
    tracker.on_update(_stop("b1", "open"), T0 + timedelta(seconds=10))
    assert len(tracker.due(T0 + timedelta(seconds=20))) == 1
    (found,) = tracker.on_update(update("b2", "complete"), T0, found_later=True)
    assert found.line.endswith(" (found after a pause in order alerts)")


def test_book_rows_repeat_nothing_already_told(store):
    tracker = OrderTracker(store)
    feed(tracker, update("b3", "open", filled_quantity=20, average_price=9),
         update("b4", "complete"), update("b5", "rejected", rejection_reason="No margin"))  # fmt: skip
    again = [
        update("b3", "open", filled_quantity=20, average_price=9),
        update("b3", "open", filled_quantity=10, average_price=9),  # a lagging trade book
        update("b4", "complete"),
        update("b5", "rejected"),
    ]
    assert [a for p in again for a in tracker.on_update(p, T0, found_later=True)] == []
    more = tracker.on_update(update("b3", "open", filled_quantity=30), T0, found_later=True)
    assert kinds(more) == [("b3", "part", 30)]


def test_a_rejection_found_later_does_not_claim_the_broker_gave_no_reason(store):
    (alert,) = OrderTracker(store).on_update(update("b6", "rejected"), T0, found_later=True)
    assert "The reason is in the Upstox app's order book." in alert.line
    assert "gave no reason" not in alert.line


def test_a_rejection_after_a_part_fill_says_what_was_filled(store):
    payload = update("b7", "rejected", filled_quantity=40, average_price=121.5,
                     rejection_reason="Circuit limit")  # fmt: skip
    (alert,) = OrderTracker(store).on_update(payload, T0)
    assert alert.line == (
        "Rejected: BUY 65 NIFTY 21 OCT 25900 CE (MIS, NFO); 40 were filled at ₹121.50. "
        "Broker says: Circuit limit"
    )


# -- the record failing ------------------------------------------------------------------------


class _Refusing:
    """An engine whose every transaction fails, as a full disk would."""

    def begin(self):
        from sqlalchemy.exc import OperationalError

        raise OperationalError("COMMIT", {}, Exception("database or disk is full"))

    def dispose(self):
        pass


def test_a_failing_record_still_queues_and_holds_back_repeats(tmp_path, caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger="sudarshan.alerts")
    store = Store(tmp_path / "alerts.db")
    tracker = OrderTracker(store)
    assert kinds(tracker.on_update(update("f1", "complete"), T0)) == [("f1", "filled", 65)]
    store.engine = _Refusing()
    assert kinds(tracker.on_update(update("f2", "complete"), T0)) == [("f2", "filled", 65)]
    assert store.in_memory
    assert tracker.on_update(update("f2", "complete"), T0) == []  # held back in memory
    assert tracker.on_update(update("f1", "complete"), T0) == []  # told before the failure
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "database or disk is full" in errors[0].getMessage()


def test_a_commit_that_fails_does_not_lose_the_alert(tmp_path):
    """The claim was written, then the commit failed: memory must not believe it was made."""
    store = Store(tmp_path / "alerts.db")
    tracker = OrderTracker(store)
    real_begin = store.engine.begin

    class FailAtCommit:
        def __init__(self):
            self.inner = real_begin()

        def __enter__(self):
            return self.inner.__enter__()

        def __exit__(self, *exc):
            from sqlalchemy.exc import OperationalError

            self.inner.__exit__(ValueError, ValueError("rolled back"), None)
            raise OperationalError("COMMIT", {}, Exception("disk I/O error"))

    class Engine:
        begin = staticmethod(FailAtCommit)

        def dispose(self):
            pass

    store.engine = Engine()
    assert kinds(tracker.on_update(update("f3", "complete"), T0)) == [("f3", "filled", 65)]


def test_the_sent_table_admits_each_claim_once(tmp_path):
    path = tmp_path / "alerts.db"
    store = Store(path)
    with store.transaction() as tx:
        assert tx.claim("o1", "part", 20, T0) is True
        assert tx.claim("o1", "part", 20, T0) is False
        assert tx.claim("o1", "part", 40, T0) is True
        assert tx.claim("o1", "filled", 20, T0) is True
        assert tx.claim("o2", "part", 20, T0) is True
    store.close()
    again = Store(path)
    with again.transaction() as tx:
        assert tx.claim("o1", "part", 20, T0) is False
        assert tx.most_filled("o1", "part") == 40
    again.close()


def test_claims_held_in_memory_are_admitted_once_too(tmp_path):
    blocked = tmp_path / "a-file-not-a-directory"
    blocked.write_text("")
    store = Store(blocked / "alerts.db")  # cannot be created: the alerts run from memory
    assert store.in_memory
    with store.transaction() as tx:
        assert tx.claim("o1", "part", 20, T0) is True
        assert tx.claim("o1", "part", 20, T0) is False
        assert tx.claim("o1", "part", 40, T0) is True
        assert tx.most_filled("o1", "part") == 40
    store.close()
