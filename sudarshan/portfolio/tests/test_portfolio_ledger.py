"""core/model.py and core/ledger.py (PA1, PA2): FIFO per account and asset, the day's order,
splits, bonus shares, transfers and the refusals.

Three witnesses to the same rules: the golden vectors (vectors/ledger.json and actions.json,
numbers derived by hand), a naive second implementation (portfolio_oracle.py, no code shared with
the engine) run on the same entries, and identities that must hold whatever the entries are.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pytest
from portfolio_oracle import simulate
from portfolio_snap import names_of, snapshot

from sudarshan.portfolio.core import kinds, words
from sudarshan.portfolio.core.days import from_date
from sudarshan.portfolio.core.ledger import (
    GROUP_ACQUIRE,
    GROUP_DISPOSE,
    GROUP_INCOME,
    GROUP_MOVE,
    GROUP_SPLITS,
    group_of,
    ordered,
    replay,
)
from sudarshan.portfolio.core.model import FIELDS, MAX_RATIO, BadEntry, Entry, Refusal

VECTOR_FILES = ("ledger.json", "actions.json")
CASES = [
    case
    for name in VECTOR_FILES
    for case in json.loads((Path(__file__).parent / "vectors" / name).read_text(encoding="utf-8"))[
        "cases"
    ]
]
U = kinds.QTY_SCALE
#: How many decimals a quantity of each asset may have in the random tabs: shares, then units.
DECIMALS = {1: 0, 2: 4}


GOOD = {
    "BUY": {"account_id": 1, "asset_id": 1, "qty_u": U, "amount_paise": 100},
    "SELL": {"account_id": 1, "asset_id": 1, "qty_u": U, "price_u": 10_000},
    "RIGHTS": {"account_id": 1, "asset_id": 1, "qty_u": U, "amount_paise": 100, "charges_paise": 5},
    "DIVIDEND": {"account_id": 1, "asset_id": 1, "amount_paise": 100, "tds_paise": 10},
    "INTEREST": {"account_id": 1, "amount_paise": 100},
    "FEE": {"account_id": 1, "asset_id": 1, "amount_paise": 100},
    "TRANSFER": {"asset_id": 1, "qty_u": U, "account_id": 1, "to_account_id": 2},
    "SPLIT": {"asset_id": 1, "ratio_num": 5, "ratio_den": 1},
    "BONUS": {"account_id": 1, "asset_id": 1, "qty_u": U},
}


def decimals_of(case):
    """The decimals a case gives each asset (JSON keys are text); shares unless it says."""
    return {int(asset): places for asset, places in case.get("decimals", {}).items()}


def run(case):
    entries = [Entry.of(row) for row in case["entries"]]
    return replay(
        entries,
        as_of=case.get("as_of"),
        names=names_of(case.get("names")),
        decimals=decimals_of(case),
    )


# -- the golden vectors ---------------------------------------------------------------------


def test_the_designs_vectors_are_all_there():
    ids = {case["id"] for case in CASES}
    assert {"F1", "F2", "F3", "F4", "F5", "F6", "F7"} <= ids
    assert {"C1", "C2", "C3", "C4", "C5"} <= ids
    assert len(ids) == len(CASES)
    for case in CASES:
        assert len(case["why"]) > 60, case["id"]  # each carries its derivation
        assert case["title"] and case["entries"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_the_engine_gives_the_vectors_numbers(case):
    expect = case["expect"]
    if "refused" in expect:
        with pytest.raises(Refusal) as caught:
            run(case)
        want = expect["refused"]
        assert caught.value.entry_id == want["entry"]
        assert caught.value.reason == want["reason"]
        assert caught.value.message == want["message"]
        assert caught.value.facts == want["facts"]
        assert str(caught.value) == caught.value.message
        return
    ledger = run(case)
    got = snapshot(ledger, case.get("over_year_on"))
    assert got["positions"] == expect["positions"]
    assert got["moves"] == expect["moves"]
    assert got["totals"] == expect["totals"]
    for entry_id, text in expect.get("words", {}).items():
        move = next(m for m in ledger.journal if m.entry.id == int(entry_id))
        assert words.describe(move) == text


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_the_naive_oracle_gives_the_same_numbers(case):
    got = simulate(case["entries"], case.get("as_of"), decimals_of(case), case.get("over_year_on"))
    expect = case["expect"]
    if "refused" in expect:
        assert got["refused"]["entry"] == expect["refused"]["entry"]
        assert got["refused"]["reason"] == expect["refused"]["reason"]
        return
    assert got["positions"] == expect["positions"]
    assert got["moves"] == expect["moves"]
    assert got["totals"] == expect["totals"]


# -- the engine against the oracle on many random tabs ---------------------------------------

DAYS = [from_date(date(2025, 1, 1) + timedelta(days=n)) for n in range(0, 140, 3)]
UNITS = [U, U, U, U, 250_000]
#: Days before every day above, for the first purchase of shares brought in from outside.
EARLIER = [20230615, 20240229, 20241105]
RATIOS = [(2, 1), (5, 1), (3, 2), (1, 2), (1, 5), (1, 10), (10, 1), (7, 3)]
KINDS = ["BUY", "RIGHTS", "SELL", "DIVIDEND", "INTEREST", "FEE", "TRANSFER", "SPLIT", "BONUS"]
WEIGHTS = [9, 2, 6, 2, 1, 1, 4, 2, 2]


def shape(row: dict) -> str:
    """What a row is, finer than its kind: the three ways to transfer are three different entries."""
    if row["kind"] != "TRANSFER":
        return row["kind"]
    if row.get("account_id") is None:
        return "TRANSFER_IN"
    return "TRANSFER_OUT" if row.get("to_account_id") is None else "TRANSFER_BETWEEN"


def transfer_row(rng: random.Random, row: dict) -> None:
    """Turn ``row`` (with an account) into a transfer between accounts, in or out."""
    row["asset_id"] = rng.choice([1, 2])
    row["qty_u"] = rng.randint(1, 6) * rng.choice(UNITS)
    way = rng.random()
    if way < 0.6:
        row["to_account_id"] = 3 - row["account_id"]
    elif way < 0.8:  # shares brought in from outside
        row["to_account_id"], row["account_id"] = row["account_id"], None
        row["amount_paise"] = rng.randint(100, 300_000)
        if rng.random() < 0.6:
            row["lot_date"] = rng.choice([*EARLIER, row["trade_date"]])
        if rng.random() < 0.5:
            row["value_paise"] = rng.randint(100, 400_000)
    elif rng.random() < 0.5:  # shares taken out to outside
        row["value_paise"] = rng.randint(100, 400_000)


def random_rows(rng: random.Random, count: int) -> list[dict]:
    rows = []
    for number in range(1, count + 1):
        kind = rng.choices(KINDS, WEIGHTS)[0]
        row = {"id": number, "seq": number, "kind": kind, "account_id": rng.choice([1, 2])}
        row["trade_date"] = rng.choice(DAYS)
        if kind in ("BUY", "RIGHTS", "SELL"):
            row["asset_id"] = rng.choice([1, 2])
            row["qty_u"] = rng.randint(1, 9) * rng.choice(UNITS)
            if rng.random() < 0.7:
                row["amount_paise"] = rng.randint(100, 500_000)
            else:
                row["price_u"] = rng.randint(10_000, 5_000_000)
            if rng.random() < 0.4:
                row["charges_paise"] = rng.randint(0, 3_000)
        elif kind == "TRANSFER":
            transfer_row(rng, row)
        elif kind == "SPLIT":
            row["asset_id"] = rng.choice([1, 2])
            row["ratio_num"], row["ratio_den"] = rng.choice(RATIOS)
            if rng.random() < 0.6:
                row["account_id"] = None  # every account that holds the asset
        elif kind == "BONUS":
            row["asset_id"] = rng.choice([1, 2])
            row["qty_u"] = rng.randint(1, 5) * U
        else:
            row["amount_paise"] = rng.randint(1, 100_000)
            if kind == "DIVIDEND":
                row["asset_id"] = rng.choice([1, 2])
                row["tds_paise"] = rng.randint(0, row["amount_paise"] // 5)
            elif rng.random() < 0.5:
                row["asset_id"] = rng.choice([1, 2])
        if rng.random() < 0.08:
            row["status"] = "draft"
        rows.append(row)
    rng.shuffle(rows)  # the list's order is not the entry order: seq is
    return rows


def compare(rows, as_of=None):
    """Run the engine and the oracle on ``rows``; return the oracle's answer."""
    want = simulate(rows, as_of, DECIMALS)
    entries = [Entry.of(row) for row in rows]
    if "refused" in want:
        with pytest.raises(Refusal) as caught:
            replay(entries, as_of=as_of, decimals=DECIMALS)
        refusal, said = caught.value, want["refused"]
        assert (refusal.entry_id, refusal.reason) == (said["entry"], said["reason"])
        if said["reason"] == "short":
            assert refusal.facts["have_qty_u"] == said["have"]
            assert refusal.facts["want_qty_u"] == said["want"]
        elif said["reason"] == "fraction":
            assert refusal.facts["have_qty_u"] == said["have"]
            assert refusal.facts["left_qty_u"] == said["left"]
            assert refusal.facts["account_id"] == said["account"]
        return want
    ledger = replay(entries, as_of=as_of, decimals=DECIMALS)
    assert snapshot(ledger) == want
    return want


def repaired(rows, keep=()):
    """``rows`` with every entry the ledger would refuse taken out, one at a time.

    A refusal for a reason in ``keep`` stays: the tab is returned with that entry in it, for a
    test of the refusal itself once everything before it is sound.
    """
    rows = list(rows)
    while True:
        answer = simulate(rows, None, DECIMALS)
        if "refused" not in answer or answer["refused"]["reason"] in keep:
            return rows
        rows = [row for row in rows if row["id"] != answer["refused"]["entry"]]


def test_the_engine_and_the_oracle_agree_on_random_tabs():
    rng = random.Random(20261027)
    refused, applied = Counter(), Counter()
    multi_lot = both_accounts = 0
    for _ in range(250):
        rows = random_rows(rng, rng.randint(2, 40))
        answer = compare(rows)
        if "refused" in answer:
            refused[answer["refused"]["reason"]] += 1
        answer = compare(repaired(rows, keep=("fraction",)))
        if "refused" in answer:
            refused["fraction_kept"] += 1
        good = repaired(rows)
        answer = compare(good)
        by_id = {row["id"]: row for row in good}
        for move in answer["moves"]:
            multi_lot += len(move["used"]) > 1
            applied[shape(by_id[move["entry"]])] += 1
        both_accounts += any(
            by_id[entry]["kind"] == "SPLIT"
            and by_id[entry].get("account_id") is None
            and {m["account"] for m in answer["moves"] if m["entry"] == entry} == {1, 2}
            for entry in {m["entry"] for m in answer["moves"]}
        )
        compare(good, as_of=rng.choice(DAYS))
    # The generator must exercise what it is there for, or the comparison proves nothing.
    assert sum(refused.values()) >= 100
    assert multi_lot >= 150
    assert all(refused[reason] >= 10 for reason in ("short", "not_held", "fraction_kept"))
    wanted = {
        "SPLIT": 100,
        "BONUS": 100,
        "TRANSFER_BETWEEN": 200,
        "TRANSFER_IN": 80,
        "TRANSFER_OUT": 40,
    }
    for name, at_least in wanted.items():
        assert applied[name] >= at_least, name
    assert both_accounts >= 20


def test_cost_and_units_are_conserved_whatever_the_entries():
    rng = random.Random(99)
    for _ in range(150):
        good = repaired(random_rows(rng, rng.randint(2, 40)))
        ledger = replay([Entry.of(row) for row in good], decimals=DECIMALS)
        for position in ledger.positions.values():
            moves = [
                m
                for m in ledger.journal
                if (m.account_id, m.asset_id) == (position.account_id, position.asset_id)
            ]
            assert sum(m.qty_u for m in moves) == position.qty_u
            assert sum(m.cost_paise for m in moves) == position.cost_paise
            assert sum(m.gain_paise for m in moves) == position.realised_paise
            assert all(lot.qty_u > 0 and lot.cost_paise >= 0 for lot in position.lots)
            # The running totals are what the lots add up to.
            assert position.qty_u == sum(lot.qty_u for lot in position.lots)
            assert position.cost_paise == sum(lot.cost_paise for lot in position.lots)
            assert [(lot.day, lot.order) for lot in position.lots] == sorted(
                (lot.day, lot.order) for lot in position.lots
            )
        for move in ledger.journal:
            if move.kind == "SELL":
                assert sum(piece.qty_u for piece in move.used) == -move.qty_u
                assert sum(piece.cost_paise for piece in move.used) == -move.cost_paise
                net = move.paise - move.entry.charges_paise
                assert move.gain_paise == net + move.cost_paise


def test_splits_and_transfers_never_make_or_lose_cost():
    rng = random.Random(7)
    seen = Counter()
    for _ in range(150):
        good = repaired(random_rows(rng, rng.randint(2, 40)))
        ledger = replay([Entry.of(row) for row in good], decimals=DECIMALS)
        running: dict = {}
        by_entry: dict = {}
        for move in ledger.journal:
            by_entry.setdefault(move.entry.id, []).append(move)
            key = (move.account_id, move.asset_id)
            qty, cost = running.get(key, (0, 0))
            running[key] = qty, cost = qty + move.qty_u, cost + move.cost_paise
            if move.kind == "SPLIT":
                # Cost and days stay: the lots after a split hold what the position held before.
                seen["split"] += 1
                assert move.cost_paise == 0
                assert move.gain_paise == 0
                assert sum(piece.qty_u for piece in move.used) == qty
                assert sum(piece.cost_paise for piece in move.used) == cost
                assert all(piece.qty_u > 0 for piece in move.used)
            if move.kind == "BONUS":
                seen["bonus"] += 1
                assert move.cost_paise == 0
                assert move.qty_u == move.entry.qty_u
        for moves in by_entry.values():
            entry = moves[0].entry
            if entry.kind != "TRANSFER" or None in (entry.account_id, entry.to_account_id):
                continue
            seen["between"] += 1
            out, into = moves
            # The same lots leave one account and reach the other, and no gain is made.
            assert (out.account_id, into.account_id) == (entry.account_id, entry.to_account_id)
            assert out.qty_u == -into.qty_u == -entry.qty_u
            assert out.cost_paise == -into.cost_paise
            assert out.used == into.used
            assert sum(piece.cost_paise for piece in out.used) == -out.cost_paise
            assert out.gain_paise == into.gain_paise == 0
            assert out.paise is None and into.paise is None
    assert seen["split"] >= 100 and seen["bonus"] >= 100 and seen["between"] >= 100


def test_replaying_the_same_entries_in_any_list_order_gives_the_same_ledger():
    rng = random.Random(5)
    for _ in range(40):
        good = repaired(random_rows(rng, 30))
        first = snapshot(replay([Entry.of(row) for row in good], decimals=DECIMALS))
        rng.shuffle(good)
        assert snapshot(replay([Entry.of(row) for row in good], decimals=DECIMALS)) == first


# -- the day's order -------------------------------------------------------------------------


def row(number, kind, day, **more):
    base = {"id": number, "seq": number, "kind": kind, "trade_date": day, "account_id": 1}
    return base | more


def test_a_day_is_applied_purchases_first_then_sales_then_income():
    rows = [
        row(1, "FEE", 20250106, amount_paise=100),
        row(2, "SELL", 20250106, asset_id=1, qty_u=U, amount_paise=1000),
        row(3, "DIVIDEND", 20250106, asset_id=1, amount_paise=50),
        row(4, "BUY", 20250106, asset_id=1, qty_u=U, amount_paise=900),
        row(5, "BUY", 20250105, asset_id=1, qty_u=U, amount_paise=900),
    ]
    # The earlier day first; on the 6th the purchase, the sale, then the fee and the dividend
    # (income and fees go last, in the order they were typed).
    assert [e.id for e in ordered(Entry.of(r) for r in rows)] == [5, 4, 2, 1, 3]


def test_inside_a_group_seq_decides_then_id():
    rows = [
        row(7, "BUY", 20250106, asset_id=1, qty_u=U, amount_paise=1, seq=2),
        row(3, "BUY", 20250106, asset_id=1, qty_u=U, amount_paise=1, seq=2),
        row(9, "BUY", 20250106, asset_id=1, qty_u=U, amount_paise=1, seq=1),
    ]
    assert [e.id for e in ordered(Entry.of(r) for r in rows)] == [9, 3, 7]


def test_drafts_and_later_days_are_left_out():
    rows = [
        row(1, "BUY", 20250106, asset_id=1, qty_u=U, amount_paise=1),
        row(2, "BUY", 20250107, asset_id=1, qty_u=U, amount_paise=1, status="draft"),
        row(3, "BUY", 20250120, asset_id=1, qty_u=U, amount_paise=1),
    ]
    entries = [Entry.of(r) for r in rows]
    assert [e.id for e in ordered(entries)] == [1, 3]
    assert [e.id for e in ordered(entries, as_of=20250106)] == [1]
    assert [e.id for e in ordered(entries, as_of=20250105)] == []


def test_every_kind_has_a_place_in_the_day():
    places = {
        kind: group_of(Entry.of({"kind": kind, "trade_date": 20250106, **GOOD[kind]}))
        for kind in kinds.ACTIVITY_KINDS
    }
    assert places["SPLIT"] == places["BONUS"] == GROUP_SPLITS
    assert places["BUY"] == places["RIGHTS"] == GROUP_ACQUIRE
    assert places["TRANSFER"] == GROUP_MOVE
    assert places["SELL"] == GROUP_DISPOSE
    assert places["DIVIDEND"] == places["INTEREST"] == places["FEE"] == GROUP_INCOME


def test_a_transfer_in_from_outside_is_an_acquisition_and_one_to_outside_a_disposal():
    inward = row(1, "TRANSFER", 20250106, asset_id=1, qty_u=U, amount_paise=5)
    inward |= {"account_id": None, "to_account_id": 2}
    outward = row(2, "TRANSFER", 20250106, asset_id=1, qty_u=U, to_account_id=None)
    assert group_of(Entry.of(inward)) == GROUP_ACQUIRE
    assert group_of(Entry.of(outward)) == GROUP_DISPOSE


# -- what a ledger holds ----------------------------------------------------------------------


def test_holdings_are_the_positions_that_still_hold_units():
    rows = [
        row(1, "BUY", 20250106, asset_id=1, qty_u=5 * U, amount_paise=50000),
        row(2, "BUY", 20250106, asset_id=2, qty_u=2 * U, amount_paise=20000),
        row(3, "SELL", 20250210, asset_id=2, qty_u=2 * U, amount_paise=21000),
        row(4, "INTEREST", 20250301, amount_paise=500),
    ]
    ledger = replay([Entry.of(r) for r in rows])
    # Asset 2 was sold out and the account's own interest belongs to no asset: neither is a
    # holding, though both stay in the ledger for the gain and the income they carry.
    assert [(p.account_id, p.asset_id) for p in ledger.holdings()] == [(1, 1)]
    assert set(ledger.positions) == {(1, 1), (1, 2), (1, None)}


def test_units_held_over_a_year_are_counted_lot_by_lot():
    rows = [
        row(1, "BUY", 20240105, asset_id=1, qty_u=4 * U, amount_paise=4000),
        row(2, "BUY", 20240229, asset_id=1, qty_u=3 * U, amount_paise=3000),
        row(3, "BUY", 20240630, asset_id=1, qty_u=2 * U, amount_paise=2000),
    ]
    position = replay([Entry.of(r) for r in rows]).position(1, 1)
    # More than a year is after the first anniversary, and 29 Feb 2024 has its on 28 Feb 2025.
    assert position.over_year_qty_u(20240105) == 0
    assert position.over_year_qty_u(20250105) == 0
    assert position.over_year_qty_u(20250106) == 4 * U
    assert position.over_year_qty_u(20250228) == 4 * U
    assert position.over_year_qty_u(20250301) == 7 * U
    assert position.over_year_qty_u(20250630) == 7 * U
    assert position.over_year_qty_u(20250701) == 9 * U
    # A sale takes the oldest lots first, so what stays is the newer, shorter held units.
    rows.append(row(4, "SELL", 20250302, asset_id=1, qty_u=5 * U, amount_paise=6000))
    after = replay([Entry.of(r) for r in rows]).position(1, 1)
    assert [(lot.day, lot.qty_u) for lot in after.lots] == [(20240229, 2 * U), (20240630, 2 * U)]
    assert after.over_year_qty_u(20250302) == 2 * U


# -- the refusal -----------------------------------------------------------------------------


def test_a_refusal_names_the_entry_and_keeps_its_numbers():
    rows = [
        row(1, "BUY", 20250110, asset_id=1, qty_u=10 * U, amount_paise=100000),
        row(2, "SELL", 20250312, asset_id=1, qty_u=12 * U, amount_paise=150000),
    ]
    with pytest.raises(Refusal) as caught:
        replay([Entry.of(r) for r in rows])
    refusal = caught.value
    assert (refusal.entry_id, refusal.reason) == (2, "short")
    assert refusal.facts == {
        "kind": "SELL",
        "day": 20250312,
        "account_id": 1,
        "asset_id": 1,
        "have_qty_u": 10 * U,
        "want_qty_u": 12 * U,
    }
    assert refusal.message.endswith("Add the missing purchase first.")


def test_a_refusal_belongs_to_the_account_and_asset_not_the_tab():
    rows = [
        row(1, "BUY", 20250110, asset_id=1, qty_u=10 * U, amount_paise=100000),
        row(2, "SELL", 20250312, asset_id=2, qty_u=U, amount_paise=1000),
    ]
    with pytest.raises(Refusal) as other_asset:
        replay([Entry.of(r) for r in rows])
    assert other_asset.value.entry_id == 2
    rows[1] = row(2, "SELL", 20250312, asset_id=1, qty_u=U, amount_paise=1000, account_id=2)
    with pytest.raises(Refusal) as other_account:
        replay([Entry.of(r) for r in rows])
    assert other_account.value.entry_id == 2


def test_the_first_entry_that_cannot_stand_is_the_one_named():
    rows = [
        row(1, "SELL", 20250110, asset_id=1, qty_u=U, amount_paise=1000),
        row(2, "SELL", 20250111, asset_id=1, qty_u=U, amount_paise=1000),
    ]
    with pytest.raises(Refusal) as caught:
        replay([Entry.of(r) for r in rows])
    assert caught.value.entry_id == 1


def test_an_entry_not_saved_yet_is_refused_without_an_id():
    sale = Entry.of(
        {"kind": "SELL", "trade_date": 20250110, "account_id": 1, "asset_id": 1}
        | {"qty_u": U, "amount_paise": 1000}
    )
    with pytest.raises(Refusal) as caught:
        replay([sale])
    assert caught.value.entry_id is None


# -- an entry that is not well formed ---------------------------------------------------------


@pytest.mark.parametrize("kind", kinds.ACTIVITY_KINDS)
def test_a_well_formed_entry_of_every_kind_is_accepted(kind):
    entry = Entry.of({"kind": kind, "trade_date": 20250106, **GOOD[kind]})
    assert entry.kind == kind and entry.status == "posted"
    assert FIELDS[kind][0]


def test_the_amount_of_a_trade_comes_from_the_price_when_none_is_typed():
    entry = Entry.of({"kind": "SELL", "trade_date": 20250106, **GOOD["SELL"]})
    assert entry.amount == 100  # 1 unit at 1.0000 rupees
    typed = Entry.of({"kind": "SELL", "trade_date": 20250106, **GOOD["SELL"], "amount_paise": 7})
    assert typed.amount == 7


def test_opening_and_outside_transfers_are_well_formed():
    opening = {"kind": "TRANSFER", "trade_date": 20250106, "asset_id": 1, "qty_u": U}
    opening |= {"to_account_id": 1, "amount_paise": 5000, "lot_date": 20200101}
    assert Entry.of(opening).lot_date == 20200101
    leaving = {"kind": "TRANSFER", "trade_date": 20250106, "asset_id": 1, "qty_u": U}
    leaving |= {"account_id": 1, "value_paise": 7000}
    assert Entry.of(leaving).to_account_id is None


BAD = [
    ({"kind": None}, "Choose what kind of entry this is."),
    ({"kind": "SWAP"}, "Choose what kind of entry this is."),
    ({"status": "pending"}, "An entry is posted or a draft."),
    ({"trade_date": 20250230}, "Enter a valid date."),
    ({"trade_date": None}, "Enter a valid date."),
    ({"trade_date": "2025-01-06"}, "Enter a valid date."),
    ({"account_id": None}, "A purchase needs an account."),
    ({"asset_id": None}, "A purchase needs an asset."),
    ({"qty_u": None}, "A purchase needs a quantity."),
    ({"amount_paise": None}, "A purchase needs an amount or a price."),
    ({"tds_paise": 5}, "A purchase does not take TDS."),
    ({"ratio_num": 5}, "A purchase does not take a ratio."),
    ({"qty_u": 0}, "The quantity must be above zero and at most 100 crore units."),
    (
        {"qty_u": kinds.MAX_QTY_U + 1},
        "The quantity must be above zero and at most 100 crore units.",
    ),
    ({"price_u": -1}, "That price is not valid."),
    ({"amount_paise": -1}, "An amount must be zero or more, and not absurdly large."),
    (
        {"charges_paise": kinds.MAX_PAISE + 1},
        "An amount must be zero or more, and not absurdly large.",
    ),
    ({"amount_paise": 1.5}, "Enter an amount as a whole number."),
    ({"qty_u": True}, "Enter a quantity as a whole number."),
    ({"qty_u": "10"}, "Enter a quantity as a whole number."),
]


@pytest.mark.parametrize(("change", "message"), BAD, ids=[f"bad{n}" for n in range(len(BAD))])
def test_a_malformed_purchase_says_what_to_fix(change, message):
    row_ = {"kind": "BUY", "trade_date": 20250106, **GOOD["BUY"], **change}
    with pytest.raises(BadEntry) as caught:
        Entry.of(row_)
    assert str(caught.value) == message


BAD_OTHER = [
    ("DIVIDEND", {"qty_u": U}, "A dividend does not take a quantity."),
    ("DIVIDEND", {"tds_paise": 101}, "TDS cannot be more than the amount."),
    ("FEE", {"qty_u": U}, "A fee does not take a quantity."),
    ("SELL", {"tds_paise": 5}, "A sale does not take TDS."),
    ("BONUS", {"price_u": 5}, "A bonus does not take a price."),
    ("BONUS", {"amount_paise": 5}, "A bonus does not take an amount."),
    ("BONUS", {"account_id": None}, "A bonus needs an account."),
    ("SPLIT", {"qty_u": U}, "A split does not take a quantity."),
    ("SPLIT", {"to_account_id": 2}, "A split does not take a second account."),
    ("SPLIT", {"asset_id": None}, "A split needs an asset."),
    ("SPLIT", {"ratio_num": 0}, "A split needs a ratio of whole numbers above zero."),
    ("SPLIT", {"ratio_den": MAX_RATIO + 1}, "A split needs a ratio of whole numbers above zero."),
    ("SPLIT", {"ratio_num": 1, "ratio_den": 1}, "A split must change the number of shares."),
    ("SPLIT", {"ratio_den": None}, "A split needs a ratio."),
    (
        "TRANSFER",
        {"account_id": None, "to_account_id": None},
        "A transfer needs an account to leave, an account to reach, or both.",
    ),
    ("TRANSFER", {"to_account_id": 1}, "A transfer needs two different accounts."),
    (
        "TRANSFER",
        {"account_id": None, "to_account_id": 2},
        "A transfer from outside needs the cost of the shares.",
    ),
    (
        "TRANSFER",
        {"account_id": None, "to_account_id": 2, "amount_paise": 5, "lot_date": 20250107},
        "The original purchase date must be a real day, not after the transfer.",
    ),
    (
        "TRANSFER",
        {"lot_date": 20200101},
        "Only a transfer from outside takes an original purchase date.",
    ),
    (
        "TRANSFER",
        {"amount_paise": 5},
        "Shares that move keep their own cost; a transfer takes no amount.",
    ),
    ("TRANSFER", {"value_paise": 5}, "A transfer between accounts takes no value."),
]


@pytest.mark.parametrize(
    ("kind", "change", "message"), BAD_OTHER, ids=[f"bad{n}" for n in range(len(BAD_OTHER))]
)
def test_a_malformed_entry_of_another_kind_says_what_to_fix(kind, change, message):
    with pytest.raises(BadEntry) as caught:
        Entry.of({"kind": kind, "trade_date": 20250106, **GOOD[kind], **change})
    assert str(caught.value) == message


def test_replay_checks_an_entry_built_by_hand_too():
    broken = Entry(
        kind="BUY", trade_date=20250106, account_id=1, asset_id=1, qty_u=0, amount_paise=1
    )
    with pytest.raises(BadEntry):
        replay([broken])


def test_fields_a_row_has_beyond_the_ledgers_are_left_alone():
    row_ = {"kind": "BUY", "trade_date": 20250106, **GOOD["BUY"]}
    row_ |= {"note": "n", "batch_id": 3, "created_at": 1, "charges_detail": {"stt_paise": 2}}
    assert Entry.of(row_).kind == "BUY"
