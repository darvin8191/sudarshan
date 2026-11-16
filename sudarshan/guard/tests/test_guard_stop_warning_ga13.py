"""The kill switch's stop warning over an exit that did not go (M5.13, GA1.3).

Turned on with Exit all, the switch cancels every open order, stop-loss orders too, and then
sends Exit all. Its step warns of each position left open with nothing sent and no closing order
working, whose stop it may just have cancelled. Three gaps, each closed here:

- a position whose exit Upstox itself refused (or the order pacer, or the risk guard) was not
  counted, so the step ended without the warning while the position stood open with no stop;
- a position Upstox's positions do not list, which could not be sized or matched to a contract,
  was passed over as if nothing were held, unless a cancel had been unsure;
- the positions left open were counted, never named, and nothing in the step said they were open
  when no stop of theirs had been cancelled.

The step is never a success over a position left open: it is named, kept in the report as open,
and the lead says a step needs a look. Tests marked DEFECT fail on 21511c32.
"""

from __future__ import annotations

import pytest
from test_guard_killswitch_book import Multi, _steps
from test_guard_killswitch_book import upstox as book_world  # noqa: F401 - the fixture
from test_guard_killswitch_upstox import CE, OWNER, world_module

from sudarshan.books import exit_guard
from sudarshan.guard import killswitch, readers, words
from utils.broker_backpressure import BusyResponse

PE = "NIFTY27OCT2625900PE"
TOKEN = world_module.INSTRUMENTS[CE][0]
NAME = "NIFTY 25900 CE"


def _refusing(broker: Multi) -> None:
    """Upstox refuses every order from now (a margin or price band rule, say)."""

    def post(url, headers=None, content=None, **kwargs):
        return world_module.FakeResponse(
            400,
            {"status": "error",
             "errors": [{"errorCode": "UDAPI100050", "message": "Order rejected by the exchange"}]},
        )  # fmt: skip

    broker.post = post


def _item(symbol, product, reason, **more):
    return {"exchange": "NFO", "symbol": symbol, "product": product, "outcome": "not_sent",
            "reason": reason, **more}  # fmt: skip


# -- the whole switch over a simulated Upstox ------------------------------------------------------


def test_defect_a_position_whose_exit_upstox_refused_is_open_with_its_stop_cancelled(book_world):  # noqa: F811
    """DEFECT. The stop is cancelled, then Upstox refuses the exit. On 21511c32 the step said
    how many exits were refused and stopped there: no warning that the position is open with its
    stop gone."""
    broker = book_world.broker
    broker.rest(CE, "SELL", 65)  # the trader's stop-loss order
    _refusing(broker)
    answer = killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    step = _steps(answer)["live_exit_all"]
    assert broker.working == [] and broker.true_qty(CE) == 65  # stop gone, position held
    assert step["ok"] is False
    assert step["detail"]["left_open"] == [NAME]
    assert step["detail"]["left_without_stops"] == 1
    said = step["words"]
    assert words.ks_left_open([NAME]) in said
    assert said.endswith(words.ks_left_without_stops(1))
    assert "UDAPI" not in said  # Upstox's words without its code
    # Never reported as success: the switch stays on, the report keeps the position as open,
    # and the first line sends the trader to it.
    assert answer.state.on is True
    assert "step needs a look" in answer.message or "steps need a look" in answer.message


def test_defect_a_refused_exit_with_no_stop_cancelled_is_still_named_as_open(book_world):  # noqa: F811
    """DEFECT. Nothing was cancelled for it, so no stop sentence, but the position is open and
    the step says which one."""
    broker = book_world.broker
    _refusing(broker)
    answer = killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    step = _steps(answer)["live_exit_all"]
    assert broker.true_qty(CE) == 65 and step["ok"] is False
    assert step["detail"]["left_open"] == [NAME]
    assert "left_without_stops" not in step["detail"]
    assert step["words"].endswith(words.ks_left_open([NAME]))


def test_control_an_exit_that_went_leaves_nothing_named(book_world):  # noqa: F811
    broker = book_world.broker
    broker.rest(CE, "SELL", 65)
    answer = killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    step = _steps(answer)["live_exit_all"]
    assert broker.true_qty(CE) == 0 and step["ok"] is True
    assert "left_open" not in step["detail"] and "Still open" not in step["words"]


def test_the_step_is_never_a_success_over_a_position_left_open(book_world, monkeypatch):  # noqa: F811
    """Whatever Exit all's own answer says: one that answered success and listed a refused exit
    is still a step that needs a look."""
    body = {
        "status": "success",
        "message": "All Open Positions SquaredOff",
        "positions": [_item(CE, "MIS", "Upstox refused its exit.", refused=True)],
    }
    monkeypatch.setattr(killswitch, "_close_all", lambda owner: (True, body, 200))
    ok, said, detail = killswitch._exit_all_live(OWNER, [], {})
    assert ok is False and detail["left_open"] == [NAME] and "Still open: " + NAME in said


# -- which positions are left with nothing watching them -------------------------------------------


@pytest.mark.parametrize(
    "reason",
    [
        "Upstox refused its exit: Order rejected by the exchange",
        "Upstox refused its exit.",
        "The risk guard refused its exit.",
        "The kill switch is on: new orders are refused.",  # any words the risk guard gives
    ],
)
def test_defect_an_exit_sent_for_and_refused_is_left_with_nothing_watching(reason):
    left = killswitch._left_with_nothing_watching([_item(CE, "MIS", reason, refused=True)])
    assert [item["symbol"] for item in left] == [CE]


def test_control_a_position_left_to_a_working_order_is_not_counted():
    """Control: one left out for an order on its closing side, a hold or a claim has that order
    watching it, and carries no mark."""
    position = ("NFO", CE, "MIS")
    each = [
        _item(CE, "MIS", exit_guard.on_its_way(position, 0.0)),
        _item(CE, "MIS", exit_guard.exit_working(position, 0.0, ("261027000001",))),
    ]
    assert killswitch._left_with_nothing_watching(each) == []


def test_exit_all_marks_only_an_exit_that_was_sent_for_and_refused():
    """The mark the warning reads is put on by Exit all itself, for each kind of refusal."""
    import broker.upstox.api.order_api as upstox_api
    from sudarshan.guard import gate

    held = ("NFO", CE, "MIS")
    refused = world_module.FakeResponse(400, {})
    by_upstox = upstox_api._exit_outcome(
        held, "refused", refused, {"message": "UDAPI100050: Order rejected"}, None
    )
    assert by_upstox["refused"] is True and by_upstox["outcome"] == "not_sent"
    assert by_upstox["reason"] == "Upstox refused its exit: Order rejected"
    paced = upstox_api._exit_outcome(held, "refused", BusyResponse(), {}, None)
    assert paced["refused"] is True and "limited number of orders" in paced["reason"]
    guarded = upstox_api._exit_outcome(held, "refused", gate.RefusedResponse(), {}, None)
    assert guarded["refused"] is True
    sent = upstox_api._exit_outcome(held, "sent", refused, {}, "261027000001")
    unknown = upstox_api._exit_outcome(held, "unknown", refused, {}, None)
    assert "refused" not in sent and "refused" not in unknown


# -- what became of their stops ---------------------------------------------------------------------


def _cancelled_stop(product="I"):
    seen = {"S1": {"instrument_token": TOKEN, "product": product, "order_type": "SL-M"}}
    return killswitch._Cancels(ids=["S1"]), seen


def test_defect_an_unlisted_position_that_cannot_be_matched_is_not_passed_over(monkeypatch):
    """DEFECT. Upstox's positions do not list it and its contract was not found, so it is named
    by Upstox's own trading symbol. It cannot be matched to the cancelled orders: any of them may
    have been its stop. On 21511c32 it was counted only when a cancel had been unsure."""
    monkeypatch.setattr(readers, "instrument_token", lambda symbol, exchange: None)
    raw = "NIFTY 25900 PE 27 OCT 26"
    item = _item(raw, "I", exit_guard.unlisted_not_sized(("NFO", raw, "I")))
    assert killswitch._left_with_nothing_watching([item]) == [item]
    cancels, seen = _cancelled_stop()
    assert killswitch._stops_gone([item], cancels, seen) == (0, 1)


def test_defect_an_unlisted_position_is_matched_by_its_instrument_alone(monkeypatch):
    """DEFECT. Its product is the order rows' word or could not be worked out; the stop the
    switch cancelled was on the same instrument in another product's letter."""
    monkeypatch.setattr(readers, "instrument_token", lambda symbol, exchange: TOKEN)
    item = _item(CE, "MIS", exit_guard.unlisted_not_sized(("NFO", CE, "MIS")))
    cancels, seen = _cancelled_stop(product="D")
    assert killswitch._stops_gone([item], cancels, seen) == (1, 0)


def test_control_a_listed_position_is_matched_by_instrument_and_product(monkeypatch):
    monkeypatch.setattr(readers, "instrument_token", lambda symbol, exchange: TOKEN)
    listed = _item(CE, "MIS", "Upstox refused its exit.", refused=True)
    cancels, seen = _cancelled_stop(product="D")  # a stop of the NRML position, not this one
    assert killswitch._stops_gone([listed], cancels, seen) == (0, 0)
    cancels, seen = _cancelled_stop(product="I")
    assert killswitch._stops_gone([listed], cancels, seen) == (1, 0)


def test_control_with_nothing_cancelled_no_stop_is_said_to_be_gone(monkeypatch):
    monkeypatch.setattr(readers, "instrument_token", lambda symbol, exchange: None)
    item = _item("X", "I", exit_guard.unlisted_not_sized(("NFO", "X", "I")))
    assert killswitch._stops_gone([item], killswitch._Cancels(), {}) == (0, 0)


# -- the words ------------------------------------------------------------------------------------------


def test_the_open_positions_are_named_for_a_trader():
    one = words.ks_left_open([NAME])
    assert one.startswith(f"Still open: {NAME}.") and "close it from the Upstox app" in one
    two = words.ks_left_open([NAME, "NIFTY 25900 PE"])
    assert f"Still open: {NAME} and NIFTY 25900 PE." in two and "close them" in two
    many = words.ks_left_open([f"S{n}" for n in range(13)])
    assert "S9 and 3 more." in many and "S10" not in many
    for said in (one, two, many):
        assert "Error" not in said and "refused" not in said.lower()
