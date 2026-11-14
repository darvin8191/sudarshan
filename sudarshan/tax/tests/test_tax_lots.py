"""Tax lots against lots.json (T2): FIFO, intraday first, opening lots, bonus, split."""

from __future__ import annotations

import pytest
from tax_vector_tools import cases, picked, plain

from sudarshan.tax import lots
from sudarshan.tax.model import Action, OpeningLot, Trade, build


def replay(given: dict) -> lots.LotBook:
    return lots.replay(
        [build(Trade, row) for row in given["trades"]],
        [build(OpeningLot, row) for row in given.get("opening", ())],
        [build(Action, row) for row in given.get("actions", ())],
        frozenset(tuple(key) for key in given.get("all_delivery", ())),
        given.get("aliases"),
    )


@pytest.mark.parametrize("case", cases("lots.json"))
def test_replay(case):
    book, out = replay(case["in"]), case["out"]
    assert picked(book.disposals, out["disposals"]) == out["disposals"]
    assert picked(book.open_lots, out["open_lots"]) == out["open_lots"]
    assert picked(book.intraday, out["intraday"]) == out["intraday"]
    assert plain(book.fractions) == out["fractions"]
    assert len(book.notes) == out["notes"], book.notes


@pytest.mark.parametrize("case", cases("lots.json"))
def test_nothing_is_made_or_lost(case):
    """Units in equal units out or held (but for a split or bonus), and so does every paisa."""
    book, given = replay(case["in"]), case["in"]
    sale = sum(d.sale_paise for d in book.disposals) + sum(r.sell_paise for r in book.intraday)
    heads = [t for t in given["trades"] if t["segment"] in ("EQ", "MF")]
    heads = [t for t in heads if t["segment"] == "EQ" or t.get("asset_class") == "equity"]
    assert sale == sum(t["amount_paise"] for t in heads if t["side"] == "SELL")
    cost = sum(d.cost_paise or 0 for d in book.disposals) + sum(
        lot.cost_paise for lot in book.open_lots
    )
    paid = sum(t["amount_paise"] + (t.get("charges_ex_stt_paise") or 0)
               for t in heads if t["side"] == "BUY")  # fmt: skip
    opened = sum(row["cost_paise"] for row in given.get("opening", ()))
    assert cost + sum(r.buy_paise for r in book.intraday) == paid + opened


def test_a_bonus_lot_is_dated_its_ex_date_and_a_split_keeps_day_and_cost():
    case = next(c.values[0] for c in cases("lots.json") if c.id == "L1")
    days = {d.lot_source: d.acquired_day for d in replay(case["in"]).disposals}
    assert days == {"upstox_history": 20250601, "bonus": 20250910}


def test_an_alias_map_joins_lots_and_survives_a_loop():
    assert lots.canonical("A", {"A": "B", "B": "C"}) == "C"
    assert lots.canonical("A", {"A": "B", "B": "A"}) == "B"
    assert lots.canonical("A", {}) == "A"
    trades = [
        Trade(1, 1, 20250602, 1, "BUY", "EQ", "INE000A01010", 10**7, 10**6, 100_000),
        Trade(1, 1, 20260812, 2, "SELL", "EQ", "INE000A01028", 10**7, 10**6, 120_000),
    ]
    assert lots.replay(trades).disposals[0].cost_paise is None
    assert (
        lots.replay(trades, aliases={"INE000A01010": "INE000A01028"}).disposals[0].cost_paise
        == 100_000
    )


def test_an_unconfirmed_action_says_so_in_words():
    case = next(c.values[0] for c in cases("lots.json") if c.id == "L1-not-accepted")
    notes = replay(case["in"]).notes
    assert "bonus of INE000A01010 on 10 Sep 2025 is not confirmed" in notes[0]
    assert "split" in notes[1] and "section" not in " ".join(notes).lower()
