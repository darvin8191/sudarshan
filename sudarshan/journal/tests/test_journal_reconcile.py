"""Estimate against actual, month by month (P7 J4, M7.42): the pure core's vectors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sudarshan.journal.core import reconcile as R

DATA = json.loads(
    (Path(__file__).parent / "vectors" / "reconcile.json").read_text(encoding="utf-8")
)
SEG = {v["id"]: v for v in DATA["segments"]}
MON = {v["id"]: v for v in DATA["months"]}


def seg(vec):
    return R.segment_row(vec["segment"], vec["estimate"], vec["orders"], vec["modelled"],
                         vec["actual"], vec["actual_total"], vec.get("waiting"))  # fmt: skip


def month(vec):
    return R.month_row(202610, "October 2026", [seg(SEG[s]) for s in vec["segments"]])


@pytest.mark.parametrize("vec", DATA["segments"], ids=[v["id"] for v in DATA["segments"]])
def test_segment_vector(vec):
    got = seg(vec)
    want = dict(vec["expect"])
    parts = [[p["part"], p["estimate_paise"], p["actual_paise"], p["diff_paise"]]
             for p in got["parts"]]  # fmt: skip
    assert parts == want.pop("parts")
    assert {k: got[k] for k in want} == want
    assert got["orders"] == vec["orders"] and got["segment"] == vec["segment"]


@pytest.mark.parametrize("vec", DATA["months"], ids=[v["id"] for v in DATA["months"]])
def test_month_vector(vec):
    got = month(vec)
    assert {k: got[k] for k in vec["expect"]} == vec["expect"]
    assert len(got["segments"]) == len(vec["segments"])


def test_year_vector():
    got = R.year_row([month(MON[m]) for m in DATA["year"]["months"]])
    assert got == DATA["year"]["expect"]
    assert R.year_row([month(MON["M2"])])["actual_paise"] is None


def test_the_vectors_are_what_their_maker_writes():
    import importlib.util

    path = Path(__file__).parent / "vectors" / "make_reconcile.py"
    spec = importlib.util.spec_from_file_location("make_reconcile", path)
    made = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(made)
    assert (made.SEGMENTS, made.MONTHS, made.YEAR) == (
        DATA["segments"], DATA["months"], DATA["year"])  # fmt: skip
    assert "sudarshan" not in path.read_text(encoding="utf-8").replace(
        "python sudarshan/journal", "").replace("``core/reconcile.py``", "")  # fmt: skip


def test_the_sign_says_who_charged_more():
    """Mutation: the estimate's own gap (estimate less actual) shown as the difference."""
    more = seg(SEG["R2-over-5"])
    assert more["diff_paise"] > 0 and not more["diff_pct"].startswith("-")
    assert "above the estimate" in more["words"]
    less = seg(SEG["R1"])
    assert less["diff_paise"] < 0 and less["diff_pct"].startswith("-")
    assert "below the estimate" in less["words"]
    same = R.segment_row("FO", {"brokerage": 100, "total": 100}, 1, True, {"brokerage": 100}, 100)
    assert (same["diff_paise"], same["diff_pct"], same["flagged"], same["words"]) == (
        0, "0.00", False, None)  # fmt: skip


def test_an_empty_part_is_not_compared_as_zero():
    """Mutation: a part Upstox left empty read as 0 would say STT is Rs 6.25 below."""
    got = R.parts_compared(SEG["R1"]["estimate"], {"brokerage": 6000, "stt": None})
    assert [p["part"] for p in got] == ["brokerage"]
    zero = R.parts_compared(SEG["R1"]["estimate"], {"brokerage": 6000, "stt": 0})
    assert [(p["part"], p["diff_paise"]) for p in zero] == [("brokerage", 0), ("stt", -625)]


def test_no_message_shows_a_code_or_a_section():
    for vec in DATA["segments"]:
        text = seg(vec)["words"] or ""
        assert "None" not in text and "_" not in text and "paise" not in text


# ---- the op: a year of the journal's own fills against the kept month rows ---------------------
import dataclasses  # noqa: E402

from test_journal_actual import day_trade, put  # noqa: E402
from test_journal_capture import files as files  # noqa: E402, F401  (a fixture)
from test_journal_capture import ist_ms, scenario  # noqa: E402
from test_journal_capture import rig as rig  # noqa: E402, F401  (a fixture)

from sudarshan.journal.core import charges as charges_core  # noqa: E402
from sudarshan.journal.errors import ApiError  # noqa: E402
from sudarshan.journal.jobs.charges import charges_row  # noqa: E402
from sudarshan.journal.ops import REGISTRY, charges_ops  # noqa: E402
from sudarshan.journal.store import Scope  # noqa: E402

OWNER = Scope.owner()


def ask(rig, today, **args):  # noqa: F811
    rig.now = ist_ms(today, "21:00:00")

    async def go():
        return await REGISTRY["charges_reconcile"](rig.svc, args)

    return scenario(rig, go)


def month_answer(rig, segment, first, last, **parts):  # noqa: F811
    row = charges_row(parts, segment, first, last, 1, rig.now)
    rig.svc.store.write(OWNER, lambda tx: tx.upstox_charges_put(row, rig.now))


def test_switched_off_the_comparison_is_not_ready(rig):  # noqa: F811
    with pytest.raises(ApiError) as caught:
        ask(rig, 20261111)
    assert (caught.value.status, caught.value.code) == (501, "not_ready")
    assert "switched off on the server" in caught.value.message


def test_a_year_of_months_against_the_kept_rows(rig):  # noqa: F811
    """October has a month row, November has not ended: the estimate is the journal's own for
    each order, the actual is Upstox's month row, and the difference is the actual less it."""
    rig.svc.settings = dataclasses.replace(rig.svc.settings, tax=True)
    october = day_trade(20261029) + day_trade(20261030)
    put(rig, october + day_trade(20261109))
    est = charges_core.estimate(october)["parts"]
    assert est["brokerage_paise"] == 12_000  # four orders on the Plus plan
    read_at = rig.now
    month_answer(rig, "FO", 20261001, 20261031, total_paise=est["total_paise"] - 4_000 - 720,
                 brokerage_paise=8_000, gst_paise=est["gst_paise"] - 720,
                 stt_paise=est["stt_paise"], stamp_duty_paise=est["stamp_paise"],
                 transaction_paise=est["exchange_paise"], sebi_turnover_paise=est["sebi_paise"])  # fmt: skip
    got = ask(rig, 20261111)
    assert (got["fy"], got["fy_label"], got["running"], got["note"]) == (
        2026, "FY 2026-27", True, None)  # fmt: skip
    assert [m["month"] for m in got["months"]] == [202604, 202605, 202606, 202607, 202608,
                                                   202609, 202610, 202611]  # fmt: skip
    assert all(m["orders"] == 0 and m["segments"] == [] for m in got["months"][:6])
    october_row, november = got["months"][6], got["months"][7]
    assert (october_row["label"], october_row["orders"]) == ("October 2026", 4)
    assert october_row["estimate_paise"] == est["total_paise"]
    assert october_row["actual_paise"] == est["total_paise"] - 4_720
    assert october_row["diff_paise"] == -4_720 and october_row["flagged"] is True
    assert october_row["words"] == (
        "Brokerage is ₹40.00 below the estimate: check your plan under Reports > Settings."
    )
    (fo,) = october_row["segments"]
    assert (fo["segment"], fo["label"]) == ("FO", "Futures and options")
    assert fo["read_at"] == read_at  # when the month's row was kept, not when it is shown
    assert [(p["part"], p["diff_paise"]) for p in fo["parts"]] == [
        ("brokerage", -4_000), ("stt", 0), ("exchange", 0), ("sebi", 0), ("stamp", 0),
        ("gst", -720),
    ]  # fmt: skip
    assert (november["orders"], november["actual_paise"], november["flagged"]) == (2, None, False)
    assert (
        november["words"] == "Upstox's charges for a month are read from the 3rd of the next month."
    )
    assert november["estimate_paise"] == charges_core.estimate(day_trade(20261109))["parts"][
        "total_paise"]  # fmt: skip
    assert got["year"] == {"months": 1, "estimate_paise": est["total_paise"],
                           "actual_paise": est["total_paise"] - 4_720, "diff_paise": -4_720,
                           "diff_pct": october_row["diff_pct"], "flagged": True}  # fmt: skip
    assert got["rate_card"]["version"] == charges_core.CARD_VERSION
    # A month that has ended and is due, but whose row is all zero: not read yet, never Rs 0.
    put(rig, day_trade(20260915))
    month_answer(rig, "FO", 20260901, 20260930, total_paise=0, brokerage_paise=0)
    september = ask(rig, 20261111)["months"][5]
    assert (september["orders"], september["actual_paise"]) == (2, None)
    assert september["words"] == "Upstox's charges for this month are not read yet."


def test_a_year_with_no_trades_and_a_year_that_is_over(rig):  # noqa: F811
    rig.svc.settings = dataclasses.replace(rig.svc.settings, tax=True)
    got = ask(rig, 20261111, fy=2025)
    assert (got["fy_label"], got["running"], len(got["months"])) == ("FY 2025-26", False, 12)
    assert got["months"][-1]["month"] == 202603 and got["note"] == charges_ops.NOTHING
    assert got["year"]["actual_paise"] is None
    for bad in ({"fy": 2027}, {"fy": 1999}, {"fy": "2026"}, {"fy": True}):
        with pytest.raises(ApiError) as caught:
            ask(rig, 20261111, **bad)
        assert caught.value.code == "bad_request"


def test_the_months_of_a_year():
    assert [m[0] for m in charges_ops.months_of(2026, 20260401)] == [202604]
    year = charges_ops.months_of(2027, 20290101)
    assert [m[0] for m in year][8:] == [202712, 202801, 202802, 202803]
    assert year[10][1:] == (20280201, 20280229, "February 2028")  # a leap February
