"""AL2 (M13.10): the words of TRINETRA's changes on Telegram, and what a relay may carry.

Pure: the desk's words (``compose``) and its reading of a relay (``parse``) against the signal
contract's RelayChange and RelayAlert, and the end of the night hours. Every item is invented.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from alerts_fakes import ist
from signals_fakes import AT, WHY, WORDS_EXAMPLE, alert, change

from sudarshan.alerts.market.api import ApiError
from sudarshan.alerts.signals_desk import compose, night_end, parse, plain
from sudarshan.signals import contract as c


def test_a_change_is_told_in_the_designs_words_with_trinetras_sentence_quoted():
    text = compose("change", [change()], AT)
    assert text.split("\n") == ["सुदर्शन / SUDARSHAN", WORDS_EXAMPLE, f'"{WHY}"', "10:42:05 IST"]


def test_each_kind_of_change_and_an_alert_in_words():
    first = compose("change", [change(kind="first", from_label=None, confidence=None, why=None)],
                    AT).split("\n")  # fmt: skip
    assert first[1] == "TRINETRA · NIFTY regime: FII distribution (bearish)." and len(first) == 3
    bias = compose("change", [change(kind="bias", from_label="FII distribution", bias="neutral",
                                     module="swing_eod", instrument="BANKNIFTY")], AT)  # fmt: skip
    assert bias.split("\n")[1] == (
        "TRINETRA · BANKNIFTY end-of-day swing: FII distribution, bias now neutral, "
        "confidence 0.71."
    )
    gone = compose("change", [change(kind="no_data", module="gap_bias")], AT).split("\n")[1]
    assert gone == "TRINETRA · NIFTY gap bias: no data for this reading."
    other = compose("change", [change(module="vol_regime")], AT).split("\n")[1]
    assert other.startswith("TRINETRA · NIFTY vol regime: ")
    told = compose("alert", [alert()], AT).split("\n")
    assert told[1:] == [
        "TRINETRA · NIFTY regime · Act: FII selling quickens",
        '"Three sessions of net selling in index futures."',
        "10:42:05 IST",
    ]
    broad = compose("alert", [alert(instrument=None, level="watch", text="")], AT).split("\n")
    assert broad[1:] == ["TRINETRA · regime · Watch: FII selling quickens", "10:42:05 IST"]
    yesterday = compose("change", [change()], AT + timedelta(days=1)).split("\n")[-1]
    assert yesterday == "16 Nov 10:42:05 IST"


def test_trinetras_text_can_never_format_link_or_command_on_openalgos_markdown_path():
    """OpenAlgo's /notify sends with Markdown and does not escape (mutation target: TRINETRA's
    text sent as markup). Every Markdown character and every slash is replaced, control
    characters go, Indian scripts keep their joiners, and the sentence is cut at 300."""
    hostile = "*Exit* _now_ `x` [tap](https://x.example) /closeall\x07\nok"
    text = compose("change", [change(why=hostile, to_label="*bold*_")], AT)
    body = "\n".join(text.split("\n")[1:-1])
    for mark in ("*", "_", "`", "[", "]", "/", "\x07"):
        assert mark not in body, mark
    assert "\u2215closeall" in body and "ok" in body
    assert plain("क्\u200dष", 10) == "क्\u200dष"  # the joiner Indian scripts need is kept
    said = compose("change", [change(why="ठीक " * 200)], AT).split("\n")[2]
    assert said.startswith('"') and said.endswith('..."') and len(said) == 300 + 2


def test_a_burst_of_changes_to_one_reading_is_one_message_from_what_the_owner_last_knew():
    first, second = change(1), change(2, from_label="FII distribution", to_label="FII panic")
    text = compose("change", [first, second], AT).split("\n")[1]
    assert (
        text == "TRINETRA · NIFTY regime: FII accumulation → FII panic (bearish), confidence 0.71."
    )
    back = change(2, from_label="FII distribution", to_label="FII accumulation", bias="bull")
    text = compose("change", [first, back], AT).split("\n")[1]
    assert text == "TRINETRA · NIFTY regime: FII accumulation (bullish), confidence 0.71."


@pytest.mark.parametrize(
    ("args", "field"),
    [
        ({"changes": [change(id="7")]}, "changes.id"),
        ({"changes": [change(at_ms=True)]}, "changes.at_ms"),
        ({"changes": [change(confidence=1.5)]}, "changes.confidence"),
        ({"changes": [change(kind="sideways")]}, "changes.kind"),
        ({"changes": [change(instrument="SENSEX")]}, "changes.instrument"),
        ({"changes": [change(why="x" * 301)]}, "changes.why"),
        ({"alerts": [alert(level="loud")]}, "alerts.level"),
        ({"alerts": [alert(aid="c-1")]}, "alerts.id"),
        ({"changes": [change(n) for n in range(9)]}, "changes"),
        ({"changes": [change(1), change(1)]}, "id"),
        ({"changes": []}, "changes"),
        ({"changes": "c-1"}, "changes"),
        ("not a mapping", "args"),
    ],
)
def test_a_relay_that_breaks_the_contract_is_refused_whole_naming_the_field(args, field):
    with pytest.raises(ApiError) as refused:
        parse(args)
    assert (refused.value.status, refused.value.code, refused.value.field) == (
        400, "bad_request", field,
    )  # fmt: skip


def test_unknown_fields_are_left_out_and_every_contract_field_is_read():
    ((kind, fields),) = parse({"changes": [change(extra="ignored")]})
    assert kind == "change" and "extra" not in fields
    assert set(fields) == {f.name for f in c.record("RelayChange")}
    ((kind, fields),) = parse({"alerts": [alert()]})
    assert kind == "alert" and set(fields) == {f.name for f in c.record("RelayAlert")}


NIGHT = (22 * 60 + 30, 7 * 60)


@pytest.mark.parametrize(
    ("at", "hours", "end"),
    [
        (ist(2026, 11, 16, 23, 0), NIGHT, ist(2026, 11, 17, 7, 0)),
        (ist(2026, 11, 17, 6, 59), NIGHT, ist(2026, 11, 17, 7, 0)),
        (ist(2026, 11, 17, 7, 0), NIGHT, None),
        (ist(2026, 11, 16, 22, 29), NIGHT, None),
        (ist(2026, 11, 16, 13, 0), (12 * 60, 14 * 60), ist(2026, 11, 16, 14, 0)),
        (ist(2026, 11, 16, 23, 0), None, None),
        (ist(2026, 11, 16, 23, 0), (600, 600), None),
    ],
)
def test_the_end_of_the_night_hours(at, hours, end):
    assert night_end(at, hours) == end
