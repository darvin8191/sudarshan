"""An order went and its answer could not be written (M2). ORDER PATH.

The alert's row is claimed before the order is sent, and the answer is written after. When that
write fails the order has still gone: the write is tried again, the owner is told at once, the row
stays ``claimed`` with a sentence, and the next alert (holding the strategy's lock) settles it
before anything is judged: from the answer this process still remembers, or, after a restart,
from what the account holds. A bought option is never left with nothing on record to close it.
"""

from __future__ import annotations

import threading

import pytest
from automation_world import NOW, REFUSED_GATE, TV, FakeDesk, indicator_alert

from sudarshan.automation import contract, flag, intake, rules
from sudarshan.automation import model as m

OPTION = {
    "name": "OPT", "kind": "option", "underlying": "NIFTY", "product": "MIS", "lots": 1,
    "expiry_rule": "nearest", "strike_offset": "ATM",
}  # fmt: skip
FUTURE = {"name": "ORB", "kind": "future", "underlying": "NIFTY", "product": "MIS", "lots": 1}
CE, PE, FUT = "NIFTY17NOV2625000CE", "NIFTY17NOV2625000PE", "NIFTY24NOV26FUT"


@pytest.fixture
def desk():
    return FakeDesk()


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv(flag.ENV, "sandbox")
    intake._per_link.clear()
    intake._refused.clear()
    intake._unrecorded.clear()
    intake._unread_since.clear()
    yield
    intake._unrecorded.clear()
    intake._unread_since.clear()


def make(store, form=OPTION):
    strategy, token = store.create(contract.clean(dict(form)))
    return store.set_state(strategy.id, strategy.version, m.ACTIVE), token


def send(store, desk, token, position, ago=1.0):
    return intake.handle(token, indicator_alert(position, desk.clock - ago), TV, desk, store)


def lines(store, strategy_id):
    return [(r["verdict"], r["sentence"]) for r in reversed(store.alerts(strategy_id))]


def failing_writes(monkeypatch, store, times):
    """The next ``times`` writes of an order's answer fail, as a locked or full database does."""
    real, left = store.finish_alert, [times]

    def finish(*args, **kwargs):
        if left[0] > 0:
            left[0] -= 1
            raise RuntimeError("database is locked")
        return real(*args, **kwargs)

    monkeypatch.setattr(store, "finish_alert", finish)
    return left


def test_a_write_that_fails_once_is_tried_again_and_nothing_is_lost(store, desk, monkeypatch):
    strategy, token = make(store)
    failing_writes(monkeypatch, store, 1)
    assert send(store, desk, token, "long").verdict == "sent"
    state = store.state(strategy.id)
    assert (state.held_symbol, state.last_target) == (CE, 1)
    assert lines(store, strategy.id)[0][0] == "sent" and desk.told == []
    assert intake._unrecorded == {}


def test_the_owner_is_told_at_once_and_the_row_stays_claimed_with_a_sentence(
    store, desk, monkeypatch
):
    """The reviewer's probe: the option was bought and the answer could not be written."""
    strategy, token = make(store)
    failing_writes(monkeypatch, store, 2)
    got = send(store, desk, token, "long")
    assert got.status == 200 and desk.position(CE) == 65  # the option was bought
    verdict, said = lines(store, strategy.id)[0]
    assert verdict == "claimed"
    assert said == (
        "OPT: an alert's order was handled but SUDARSHAN could not record the result. OPT: order "
        "sent to buy 65 NIFTY 17 Nov 25000 CE. Check Orders. Before the next alert is applied, "
        "OPT's position is read again."
    )
    assert desk.told == [(strategy.id, "unrecorded", said)]  # at once, on Telegram


def test_the_next_alert_writes_the_remembered_answer_and_the_exit_closes_the_option(
    store, desk, monkeypatch
):
    strategy, token = make(store)
    failing_writes(monkeypatch, store, 2)
    send(store, desk, token, "long")
    assert store.state(strategy.id).held_symbol is None  # not on record yet
    desk.clock = NOW + 600
    assert send(store, desk, token, "flat").verdict == "sent"
    assert desk.position(CE) == 0 and not any(desk.positions.values())  # nothing is orphaned
    rows = lines(store, strategy.id)
    assert [v for v, _ in rows] == ["sent", "sent"]  # the first row has its real answer now
    assert rows[0][1] == "OPT: order sent to buy 65 NIFTY 17 Nov 25000 CE."
    assert desk.book_reads == [] and intake._unrecorded == {}  # written as it was, nothing read
    assert store.alerts(strategy.id)[-1]["order_id"]


@pytest.mark.parametrize("form", [OPTION, FUTURE], ids=["option", "future"])
def test_after_a_restart_the_state_is_settled_from_the_books(store, desk, monkeypatch, form):
    """The process that remembered the answer is gone. The next alert reads what the account
    holds of the contract the row names, and the strategy holds that."""
    strategy, token = make(store, form)
    held = CE if form is OPTION else FUT
    failing_writes(monkeypatch, store, 2)
    send(store, desk, token, "long")
    intake._unrecorded.clear()  # the restart
    desk.told.clear()
    desk.clock = NOW + 600
    assert send(store, desk, token, "flat").verdict == "sent"
    assert not any(desk.positions.values())
    rows = lines(store, strategy.id)
    name = strategy.name
    assert rows[0] == (
        "not_confirmed",
        f"{name}: the result of an earlier alert was not recorded, so Positions was read: {name} "
        f"holds long {'NIFTY 17 Nov 25000 CE' if form is OPTION else 'NIFTY 24 Nov FUT'}. Check "
        "Orders if that is not what you expect.",
    )
    assert desk.book_reads == [(True, "NFO", held, "MIS")]
    assert [t[1] for t in desk.told] == ["unrecorded"]
    state = store.state(strategy.id)
    assert (state.held_symbol, state.last_target) == (None, 0)


def test_a_server_that_stopped_before_the_order_went_is_settled_as_holding_nothing(store, desk):
    strategy, token = make(store)

    def dies(order):
        raise SystemExit("the worker was stopped")

    desk.before_place = dies
    with pytest.raises(SystemExit):
        send(store, desk, token, "long")
    desk.before_place = None
    desk.clock = NOW + 60
    assert send(store, desk, token, "long").verdict == "sent"  # judged afresh, and it goes
    rows = lines(store, strategy.id)
    assert rows[0][0] == "not_confirmed" and "OPT holds nothing." in rows[0][1]
    assert store.state(strategy.id).held_symbol == CE and desk.position(CE) == 65


def test_while_the_books_cannot_be_read_an_entry_waits_and_an_exit_still_goes(
    store, desk, monkeypatch
):
    strategy, token = make(store)
    failing_writes(monkeypatch, store, 2)
    send(store, desk, token, "long")
    intake._unrecorded.clear()
    desk.books_unreadable = True
    desk.clock = NOW + 60
    refused = send(store, desk, token, "short")
    assert refused.verdict == "order_refused" and len(desk.placed) == 1
    assert refused.sentence == (
        "OPT: the result of an earlier alert was not recorded and Positions could not be read, "
        "so this sell alert was not sent. Check Positions and Orders."
    )
    assert lines(store, strategy.id)[0][0] == "claimed"  # still unsettled
    # the exit goes to the contract the unsettled row names: the option is not orphaned
    desk.clock = NOW + 120
    assert send(store, desk, token, "flat").verdict == "sent"
    assert desk.placed[-1]["symbol"] == CE and desk.position(CE) == 0
    # the books answer again: the row is settled, and the strategy holds nothing
    desk.books_unreadable = False
    desk.clock = NOW + 180
    assert send(store, desk, token, "long").verdict == "sent"
    assert lines(store, strategy.id)[0][0] == "not_confirmed"


def test_a_row_the_books_never_answered_for_is_closed_after_sixteen_hours(store, desk, monkeypatch):
    """Sixteen hours of unreadable books, counted from the first read that failed."""
    strategy, token = make(store)
    failing_writes(monkeypatch, store, 2)
    send(store, desk, token, "long")
    intake._unrecorded.clear()
    desk.books_unreadable = True
    desk.clock = NOW + 60
    send(store, desk, token, "short")  # the first read that fails
    desk.clock = NOW + 60 + 16 * 3600 - 1
    send(store, desk, token, "short")
    assert lines(store, strategy.id)[0][0] == "claimed"  # one second short of the end
    desk.clock = NOW + 60 + 16 * 3600
    send(store, desk, token, "short")
    verdict, said = lines(store, strategy.id)[0]
    assert verdict == "not_confirmed" and "never recorded" in said
    assert intake._unread_since == {}


# ---- N1: the sixteen hours count from the first failed read, never from the alert -----------------

CARRY = {**OPTION, "product": "NRML"}


def test_n1_one_failed_read_long_after_the_alert_does_not_give_up(store, desk, monkeypatch):
    """The verifier's probe turned round. A carry option strategy: the entry's answer is lost,
    the server restarts, no alert comes for 16 h, and then the books fail once. The row stays
    unsettled (an entry waits, with its sentence), and the next good read settles it to the
    option held, so the flat alert closes it."""
    strategy, token = make(store, CARRY)
    failing_writes(monkeypatch, store, 2)
    send(store, desk, token, "long")
    assert desk.position(CE, product="NRML") == 65
    intake._unrecorded.clear()  # a restart
    desk.clock = NOW + 16 * 3600 + 60  # the next morning; the contract has not expired
    desk.books_unreadable = True  # one read fails
    refused = send(store, desk, token, "short")
    assert refused.verdict == "order_refused" and "Positions could not be read" in refused.sentence
    assert lines(store, strategy.id)[0][0] == "claimed"  # not given up
    assert not any("never recorded" in said for _verdict, said in lines(store, strategy.id))
    assert store.state(strategy.id).held_symbol is None and len(desk.placed) == 1
    desk.books_unreadable = False  # and every later one works
    desk.clock += 60
    assert send(store, desk, token, "flat").verdict == "sent"
    rows = lines(store, strategy.id)
    assert rows[0][0] == "not_confirmed" and "holds long NIFTY 17 Nov 25000 CE" in rows[0][1]
    assert desk.position(CE, product="NRML") == 0 and not any(desk.positions.values())
    assert intake._unread_since == {}


def test_n1_an_exit_still_goes_on_that_one_failed_read(store, desk, monkeypatch):
    strategy, token = make(store, CARRY)
    failing_writes(monkeypatch, store, 2)
    send(store, desk, token, "long")
    intake._unrecorded.clear()
    desk.clock = NOW + 16 * 3600 + 60
    desk.books_unreadable = True
    assert send(store, desk, token, "flat").verdict == "sent"  # to the contract the row names
    assert desk.position(CE, product="NRML") == 0
    assert lines(store, strategy.id)[0][0] == "claimed"


def test_n1_the_owner_is_told_again_when_a_further_day_has_gone(store, desk, monkeypatch):
    strategy, token = make(store, {**FUTURE, "product": "NRML"})
    failing_writes(monkeypatch, store, 2)
    send(store, desk, token, "long")
    intake._unrecorded.clear()
    desk.told.clear()
    desk.books_unreadable = True

    def still(at):
        desk.clock = NOW + at
        send(store, desk, token, "flat")  # an exit: it goes, and by itself tells nobody
        return [said for _id, reason, said in desk.told if reason == "still_unsettled"]

    assert still(23 * 3600) == []  # not a day yet
    assert len(still(25 * 3600)) == 1 and "from a day ago" in desk.told[-1][2]
    assert len(still(26 * 3600)) == 1  # the same day: said once
    assert lines(store, strategy.id)[0][0] == "claimed"  # 3 h of failed reads: far from the end
    intake._unread_since.clear()  # a restart: the count of failed reads starts again
    assert len(still(49 * 3600)) == 2 and "from 2 days ago" in desk.told[-1][2]
    assert lines(store, strategy.id)[0][0] == "claimed"


class FailsOnce(FakeDesk):
    """The books cannot be read exactly once, whoever asks first."""

    def __init__(self, *args):
        super().__init__(*args)
        self.failures, self._count = 1, threading.Lock()

    def held_quantity(self, exchange, symbol, product, api_key, analyze):
        with self._count:
            fail, self.failures = self.failures > 0, self.failures - 1
        if fail:
            self.book_reads.append((analyze, exchange, symbol, product))
            return None
        return super().held_quantity(exchange, symbol, product, api_key, analyze)


def together(calls):
    """Run the calls from threads released by one barrier; what each returned, by name."""
    barrier, out = threading.Barrier(len(calls)), {}

    def run(name, call):
        barrier.wait(10)
        out[name] = call()

    threads = [threading.Thread(target=run, args=item, daemon=True) for item in calls.items()]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert len(out) == len(calls)
    return out


@pytest.mark.parametrize("round_", range(6))
def test_n1_barrier_three_alerts_past_sixteen_hours_and_one_failed_read(
    store, monkeypatch, round_
):
    """Three alerts released together, 16 h after an answer was lost; the books fail for the
    first of them only. Whichever is first, nothing is given up, the row is settled by a later
    read, and the option is never left in the account with nothing on record to close it."""
    desk = FailsOnce()
    strategy, token = make(store, CARRY)
    failing_writes(monkeypatch, store, 2)
    send(store, desk, token, "long")
    intake._unrecorded.clear()  # a restart: the books decide
    desk.clock = NOW + 16 * 3600 + 60
    out = together({
        "flat": lambda: send(store, desk, token, "flat", ago=1.0),
        "flat2": lambda: send(store, desk, token, "flat", ago=2.0),
        "long": lambda: send(store, desk, token, "long", ago=3.0),
    })  # fmt: skip
    rows = lines(store, strategy.id)
    assert all(got.verdict for got in out.values())
    assert not any("never recorded" in said for _verdict, said in rows), rows
    assert "claimed" not in [verdict for verdict, _said in rows], rows
    held = desk.position(CE, product="NRML")
    assert held in (0, 65)
    assert (store.state(strategy.id).held_symbol == CE) == (held == 65)  # never orphaned
    assert len(desk.book_reads) >= 2 and intake._unread_since == {}


# ---- N2: a settle whose own write fails ----------------------------------------------------------


def test_n2_a_settle_that_cannot_write_tells_the_owner_and_the_exit_still_goes(
    store, desk, monkeypatch
):
    """The verifier's probe turned round: the write fails a third time, at the flat alert's
    settle. The owner is told at once, the exit has its own line, and it closes the option."""
    strategy, token = make(store)
    failing_writes(monkeypatch, store, 3)  # the two tries, and settle's own
    send(store, desk, token, "long")
    before = len(store.alerts(strategy.id))
    desk.told.clear()
    desk.clock = NOW + 600
    got = send(store, desk, token, "flat")
    assert got.verdict == "sent" and got.alert_id is not None
    assert len(store.alerts(strategy.id)) == before + 1  # the exit alert has its line
    assert desk.told == [(
        strategy.id,
        "settle_failed",
        "OPT: the result of an earlier alert could still not be recorded. Until it is, OPT "
        "sends no new entry; an exit alert still goes. Check Positions and Orders.",
    )]  # fmt: skip
    assert desk.placed[-1]["symbol"] == CE and desk.position(CE) == 0
    assert desk.book_reads == []  # judged on the answer still remembered, not on a late book
    assert store.state(strategy.id).held_symbol is None
    # the earlier row is settled from the books at the next alert, and the strategy goes on
    desk.clock = NOW + 660
    assert send(store, desk, token, "long").verdict == "sent"
    assert [verdict for verdict, _said in lines(store, strategy.id)] == [
        "not_confirmed", "sent", "sent",
    ]  # fmt: skip


def test_n2_an_entry_is_refused_with_its_sentence_while_the_answer_cannot_be_written(
    store, desk, monkeypatch
):
    strategy, token = make(store)
    failing_writes(monkeypatch, store, 3)
    send(store, desk, token, "long")
    desk.clock = NOW + 600
    got = send(store, desk, token, "short")
    assert got.verdict == "order_refused" and got.alert_id is not None
    assert got.sentence == (
        "OPT: the result of an earlier alert could still not be recorded, so this sell alert was "
        "not sent. Check Positions and Orders."
    )
    assert len(desk.placed) == 1 and desk.position(CE) == 65  # nothing opened, nothing closed
    assert "settle_failed" in [reason for _id, reason, _said in desk.told]
    # the write works again: the remembered answer is written, and the reversal goes
    desk.clock = NOW + 660
    assert send(store, desk, token, "short").verdict == "sent"
    assert (desk.position(CE), desk.position(PE)) == (0, 65)
    assert lines(store, strategy.id)[0] == ("sent", "OPT: order sent to buy 65 NIFTY 17 Nov 25000 CE.")


def test_n2_the_exit_goes_to_what_the_remembered_answer_says_is_held(store, desk, monkeypatch):
    """A two-step went and its answer was lost: the note still names the call, the account holds
    the put. With the write failing again, the flat alert's exit goes to the put."""
    strategy, token = make(store)
    assert send(store, desk, token, "long").verdict == "sent"
    failing_writes(monkeypatch, store, 3)
    desk.clock = NOW + 60
    send(store, desk, token, "short")
    assert (desk.position(CE), desk.position(PE)) == (0, 65)
    assert store.state(strategy.id).held_symbol == CE  # the note missed the two orders
    desk.clock = NOW + 120
    assert send(store, desk, token, "flat").verdict == "sent"
    assert desk.placed[-1]["symbol"] == PE and not any(desk.positions.values())


def test_n2_rows_that_cannot_even_be_read_refuse_an_entry_and_tell_the_owner(
    store, desk, monkeypatch
):
    strategy, token = make(store)

    def broken(strategy_id):
        raise RuntimeError("database disk image is malformed")

    monkeypatch.setattr(store, "unsettled", broken)
    got = send(store, desk, token, "long")
    assert got.verdict == "order_refused" and "could still not be recorded" in got.sentence
    assert desk.placed == [] and [reason for _id, reason, _said in desk.told] == [
        "settle_failed", "unrecorded",
    ]  # fmt: skip
    assert lines(store, strategy.id)[0][0] == "order_refused"  # the alert has its line


def test_n2_an_alert_that_cannot_be_claimed_sends_nothing_and_the_owner_is_told(
    store, desk, monkeypatch
):
    strategy, token = make(store)

    def broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(store, "claim_alert", broken)
    got = send(store, desk, token, "long")
    assert got.status == 200 and got.body["message"] == "handled"
    assert got.verdict == "order_refused" and desk.placed == []
    assert desk.told == [(
        strategy.id,
        "not_recorded",
        "OPT: an alert arrived but SUDARSHAN could not record it, so nothing was sent for it. If "
        "the chart and your position now differ, set the position by hand.",
    )]  # fmt: skip


@pytest.mark.parametrize("round_", range(6))
def test_n2_barrier_three_alerts_and_a_settle_that_cannot_write(store, monkeypatch, round_):
    """Three alerts released together after a lost answer; the write fails once more, for the
    settle of whichever alert is first. Every alert gets its own line and a verdict, the owner
    is told, no entry goes on a note that missed an order, and at most one option is open."""
    desk = FakeDesk()
    strategy, token = make(store)
    failing_writes(monkeypatch, store, 3)
    send(store, desk, token, "long")
    before = len(store.alerts(strategy.id))
    desk.clock = NOW + 600
    out = together({
        "flat": lambda: send(store, desk, token, "flat", ago=1.0),
        "flat2": lambda: send(store, desk, token, "flat", ago=2.0),
        "short": lambda: send(store, desk, token, "short", ago=3.0),
    })  # fmt: skip
    assert all(got.verdict and got.alert_id for got in out.values()), out
    assert len(store.alerts(strategy.id)) == before + 3  # a line each, none dropped
    assert [reason for _id, reason, _said in desk.told].count("settle_failed") == 1
    open_ = {key[2]: qty for key, qty in desk.positions.items() if qty}
    assert len(open_) <= 1 and all(qty == 65 for qty in open_.values())
    first = min(out.values(), key=lambda got: got.alert_id)
    if first is out["short"]:  # an entry judged while the answer was unwritten never goes
        assert first.verdict == "order_refused" and "could still not be recorded" in first.sentence
    # one more alert: nothing is left unsettled, and the note says what the account holds
    desk.clock = NOW + 700
    send(store, desk, token, "flat")
    assert "claimed" not in [verdict for verdict, _said in lines(store, strategy.id)]
    assert store.state(strategy.id).held_symbol is None and not any(desk.positions.values())


def test_the_two_step_settles_to_the_option_that_was_bought(store, desk, monkeypatch):
    strategy, token = make(store)
    assert send(store, desk, token, "long").verdict == "sent"
    failing_writes(monkeypatch, store, 2)
    desk.clock = NOW + 60
    send(store, desk, token, "short")  # the call sold, the put bought, the answer lost
    assert (desk.position(CE), desk.position(PE)) == (0, 65)
    assert store.alerts(strategy.id)[0]["symbol"] == PE  # the claimed row names the entry
    intake._unrecorded.clear()
    desk.clock = NOW + 120
    assert send(store, desk, token, "flat").verdict == "sent"
    assert desk.placed[-1]["symbol"] == PE and not any(desk.positions.values())


def test_the_two_step_whose_exit_was_refused_settles_to_the_option_still_held(
    store, desk, monkeypatch
):
    strategy, token = make(store)
    send(store, desk, token, "long")
    failing_writes(monkeypatch, store, 2)
    desk.script = [REFUSED_GATE]
    desk.clock = NOW + 60
    send(store, desk, token, "short")  # the exit was refused, so the put was never bought
    intake._unrecorded.clear()
    desk.clock = NOW + 120
    assert send(store, desk, token, "flat").verdict == "sent"
    assert desk.placed[-1]["symbol"] == CE and not any(desk.positions.values())


def test_any_failure_after_the_claim_is_told_and_the_row_stays_claimed(store, desk, monkeypatch):
    strategy, token = make(store)
    monkeypatch.setattr(rules, "read_answer", lambda *a: (_ for _ in ()).throw(KeyError("x")))
    got = send(store, desk, token, "long")
    assert got.status == 200 and got.verdict == "claimed"
    assert [t[1] for t in desk.told] == ["unrecorded"]
    assert lines(store, strategy.id)[0][0] == "claimed"
    monkeypatch.undo()
    monkeypatch.setenv(flag.ENV, "sandbox")
    desk.clock = NOW + 60
    assert send(store, desk, token, "flat").verdict == "sent"  # settled from the books first
    assert not any(desk.positions.values())


# ---- the pure rule ------------------------------------------------------------------------------

A, B = ("NFO", CE), ("NFO", PE)
HELD_A = m.State(1, last_target=1, last_fired_s=90, held_exchange="NFO", held_symbol=CE,
                 held_side="long", held_since_ms=5)  # fmt: skip
FLAT = m.State(1, last_target=0, last_fired_s=90)
SETTLED = [
    # name, books, row, bought, state -> (held symbol, side, last_target, last_fired_s)
    ("entry from flat went", rules.Books(A, None, {A: 65}), {"target": 1, "fired_s": 100}, True,
     FLAT, (CE, "long", 1, 100)),
    ("entry from flat did not go", rules.Books(A, None, {A: 0}), {"target": 1, "fired_s": 100},
     True, FLAT, (None, None, 0, 90)),
    ("a put bought on a short target is held long", rules.Books(B, None, {B: 65}),
     {"target": -1, "fired_s": 100}, True, FLAT, (PE, "long", -1, 100)),
    ("two-step went", rules.Books(B, A, {A: 0, B: 65}), {"target": -1, "fired_s": 100}, True,
     HELD_A, (PE, "long", -1, 100)),
    ("two-step's exit did not go", rules.Books(B, A, {A: 65, B: 0}),
     {"target": -1, "fired_s": 100}, True, HELD_A, (CE, "long", 1, 90)),
    ("two-step's exit went, the entry did not", rules.Books(B, A, {A: 0, B: 0}),
     {"target": -1, "fired_s": 100}, True, HELD_A, (None, None, 0, 90)),
    ("exit went", rules.Books(A, A, {A: 0}), {"target": 0, "fired_s": 100}, True, HELD_A,
     (None, None, 0, 100)),
    ("exit did not go", rules.Books(A, A, {A: 65}), {"target": 0, "fired_s": 100}, True, HELD_A,
     (CE, "long", 1, 90)),
    ("a future's reversal went", rules.Books(A, A, {A: -65}), {"target": -1, "fired_s": 100},
     False, HELD_A, (CE, "short", -1, 100)),
    ("a future's reversal did not go", rules.Books(A, A, {A: 65}), {"target": -1, "fired_s": 100},
     False, HELD_A, (CE, "long", 1, 90)),
    ("a future's short entry went", rules.Books(A, None, {A: -65}), {"target": -1, "fired_s": 100},
     False, FLAT, (CE, "short", -1, 100)),
    ("a long that shows short is not the alert's", rules.Books(A, None, {A: -65}),
     {"target": 1, "fired_s": 100}, False, FLAT, (None, None, 0, 90)),
    ("the mark never runs ahead of the clock", rules.Books(A, None, {A: 65}),
     {"target": 1, "fired_s": 10**10}, True, FLAT, (CE, "long", 1, 200)),
]  # fmt: skip


@pytest.mark.parametrize(
    ("books", "row", "bought", "state", "want"),
    [case[1:] for case in SETTLED],
    ids=[case[0] for case in SETTLED],
)
def test_state_settled(books, row, bought, state, want):
    got = rules.state_settled(books, row, bought, 200_000, state)
    assert (got["held_symbol"], got["held_side"], got["last_target"], got["last_fired_s"]) == want
    assert (got["held_exchange"] is None) == (got["held_symbol"] is None)


def test_the_page_reads_an_unrecorded_line_as_not_confirmed_with_its_own_sentence(
    store, desk, monkeypatch
):
    from sudarshan.automation import control, words

    strategy, token = make(store)
    failing_writes(monkeypatch, store, 2)
    send(store, desk, token, "long")
    now_ms = int(desk.now() * 1000)
    line = control.view(strategy, store, now_ms)["last_alert"]  # at once, not minutes later
    assert line["verdict"] == "not_confirmed" and "could not record the result" in line["sentence"]
    # a line claimed a moment ago with no sentence is an alert being handled: left as it is
    fresh = {"verdict": "claimed", "sentence": "", "received_ms": now_ms - 1000}
    assert control.alert_line(fresh, "OPT", now_ms)["verdict"] == "claimed"
    stale = {**fresh, "received_ms": now_ms - control.CLAIMED_STALE_MS - 1}
    assert control.alert_line(stale, "OPT", now_ms)["sentence"] == words.interrupted("OPT")
