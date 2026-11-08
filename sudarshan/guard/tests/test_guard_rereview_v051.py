"""The re-review of v0.5.1 (e3e74bb), LOW-D: the kill switch's stop warning and exit-gaps' two new
reasons Exit all sends nothing for a position (M5.13).

After the switch's Exit all, the step tells the trader each position left with nothing sent and
no closing order working, whose stop-loss order the switch may just have cancelled ("open with its
stop-loss order cancelled"). Its list of such reasons knew "not sized" and "out of time" only.
Exit-gaps added two more ways Exit all sends nothing for a position Upstox's positions do not
list: one it cannot size from the fills, and an unlisted product of a listed instrument (V1, it
may be a conversion). Neither was counted, so such a position whose stop the switch cancelled was
left out of the warning.

Each test here fails on e3e74bb unless it says it is a control.
"""

from __future__ import annotations

from sudarshan.books import exit_guard
from sudarshan.guard import killswitch

CE = "NIFTY27OCT2625900CE"
PE = "NIFTY27OCT2625900PE"


def _item(symbol, product, reason, outcome="not_sent"):
    return {"exchange": "NFO", "symbol": symbol, "product": product, "outcome": outcome,
            "reason": reason}  # fmt: skip


def test_d_an_unlisted_position_exit_all_could_not_size_is_left_with_nothing_watching():
    said = exit_guard.unlisted_not_sized(("NFO", PE, "MIS"))
    left = killswitch._left_with_nothing_watching([_item(PE, "MIS", said)])
    assert [item["symbol"] for item in left] == [PE]


def test_d_an_unlisted_product_of_a_listed_instrument_is_left_with_nothing_watching():
    said = exit_guard.unlisted_in_another_product(("NFO", CE, "MIS"), ["NRML"])
    left = killswitch._left_with_nothing_watching([_item(CE, "MIS", said)])
    assert [item["symbol"] for item in left] == [CE]


def test_d_both_are_counted_together():
    """The re-review's probe: 0 of 2 were counted on e3e74bb."""
    each = [
        _item(PE, "MIS", exit_guard.unlisted_not_sized(("NFO", PE, "MIS"))),
        _item(CE, "MIS", exit_guard.unlisted_in_another_product(("NFO", CE, "MIS"), ["NRML"])),
    ]
    assert len(killswitch._left_with_nothing_watching(each)) == 2


def test_d_control_not_sized_is_counted_as_before():
    said = exit_guard.exit_not_sized(("NFO", PE, "MIS"), False)
    assert len(killswitch._left_with_nothing_watching([_item(PE, "MIS", said)])) == 1


def test_d_control_another_position_s_sentence_is_not_counted_for_this_one():
    """Control: the sentence must be this position's: another contract's, or the conversion
    sentence of another product, says nothing about this one."""
    for_ce = exit_guard.unlisted_in_another_product(("NFO", CE, "MIS"), ["NRML"])
    for_ce_unsized = exit_guard.unlisted_not_sized(("NFO", CE, "MIS"))
    for_pe_nrml = exit_guard.unlisted_in_another_product(("NFO", PE, "NRML"), ["MIS"])
    each = [
        _item(PE, "MIS", for_ce),
        _item(PE, "MIS", for_ce_unsized),
        _item(PE, "MIS", for_pe_nrml),
    ]
    assert killswitch._left_with_nothing_watching(each) == []


def test_d_control_a_position_whose_exit_is_held_or_working_is_not_counted():
    """Control: a position left out for a hold or an order working on its closing side has that
    order watching it; nor is a position whose exit was sent."""
    position = ("NFO", PE, "MIS")
    held = exit_guard.on_its_way(position, 1_761_541_925.0)
    each = [_item(PE, "MIS", held), _item(PE, "MIS", None, outcome="sent")]
    assert killswitch._left_with_nothing_watching(each) == []


def test_d_control_the_conversion_sentence_reads_as_before():
    """Control: the sentence Exit all writes for a possible conversion is unchanged."""
    said = exit_guard.unlisted_in_another_product(("NFO", CE, "MIS"), ["NRML", "CNC"])
    assert said == (
        "NIFTY 25900 CE was traded today as MIS, but Upstox's positions show it only as NRML and "
        "CNC: it may have been converted, so Exit all did not close it as MIS. Check Positions, "
        "and close it from the Upstox app if it is still held."
    )
