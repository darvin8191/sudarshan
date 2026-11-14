"""An order went and its answer could not be written (M2). ORDER PATH.

The alert's row is claimed before the order is sent, and the answer is written after. When that
write fails the order has still gone: the write is tried again, the owner is told at once, the row
stays ``claimed`` with a sentence, and the next alert (holding the strategy's lock) settles it
before anything is judged: from the answer this process still remembers, or, after a restart,
from what the account holds. A bought option is never left with nothing on record to close it.
"""

from __future__ import annotations

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
    yield
    intake._unrecorded.clear()


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
    strategy, token = make(store)
    failing_writes(monkeypatch, store, 2)
    send(store, desk, token, "long")
    intake._unrecorded.clear()
    desk.books_unreadable = True
    desk.clock = NOW + 16 * 3600 + 1
    send(store, desk, token, "flat")
    verdict, said = lines(store, strategy.id)[0]
    assert verdict == "not_confirmed" and "never recorded" in said


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
