"""core/daylines.py (PA3): a replayed ledger turned into the lines a screen prices.

Three witnesses, as for the ledger: the golden vectors (vectors/daylines.json, every line worked
out by hand), a naive second implementation (portfolio_oracle.day_lines, which reads the open from
a replay without today's trades and the money from today's entries one by one) run on the same
entries and on hundreds of random tabs, and identities that must hold whatever the entries are.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from fractions import Fraction
from pathlib import Path

import pytest
from portfolio_oracle import day_lines, half_away
from test_portfolio_ledger import DAYS, DECIMALS, random_rows, repaired

from sudarshan.portfolio.core import kinds
from sudarshan.portfolio.core.daylines import lines_of
from sudarshan.portfolio.core.ledger import replay
from sudarshan.portfolio.core.model import Entry
from sudarshan.portfolio.core.valuation import (
    Fixed,
    Row,
    closed_today,
    row_figures,
    tab_totals,
)

FILE = json.loads((Path(__file__).parent / "vectors" / "daylines.json").read_text(encoding="utf-8"))
CASES = FILE["cases"]
U = kinds.QTY_SCALE


def prices_of(case, key):
    return {int(asset): value for asset, value in case.get(key, {}).items()}


def engine_lines(rows, today, prev, decimals=None):
    ledger = replay([Entry.of(r) for r in rows], as_of=today, decimals=decimals)
    return lines_of(ledger, today, prev)


def as_dicts(lines):
    return {asset: [line.as_dict() for line in found] for asset, found in lines.items()}


# -- the golden vectors ---------------------------------------------------------------------------


def test_the_file_has_what_the_design_needs():
    ids = {case["id"] for case in CASES}
    assert {"DL1", "DL2", "DL3", "DL5", "DL6", "DL7", "DL10"} <= ids
    assert all(case["why"] and case["title"] for case in CASES)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_the_engine_gives_the_vectors_lines(case):
    lines = engine_lines(case["entries"], case["today"], prices_of(case, "prev_close_u"))
    assert as_dicts(lines) == {int(a): ls for a, ls in case["expect"]["lines"].items()}
    assert list(lines) == sorted(lines)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_the_vectors_lines_give_the_vectors_figures(case):
    prev = prices_of(case, "prev_close_u")
    lines = engine_lines(case["entries"], case["today"], prev)
    for asset, price in prices_of(case, "price_u").items():
        figures = row_figures(Row("ok", price, prev.get(asset), tuple(lines[asset])))
        assert figures.as_dict() == case["expect"]["rows"][str(asset)]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_the_naive_oracle_gives_the_same_lines(case):
    got = day_lines(case["entries"], case["today"], prices_of(case, "prev_close_u"))
    assert got == {int(a): ls for a, ls in case["expect"]["lines"].items()}


# -- the rules, one at a time ---------------------------------------------------------------------


def buy(number, day, qty, amount, account=1, asset=1, **more):
    return {
        "id": number,
        "seq": number,
        "kind": "BUY",
        "trade_date": day,
        "account_id": account,
        "asset_id": asset,
        "qty_u": qty * U,
        "amount_paise": amount,
        **more,
    }


def test_a_ledger_with_nothing_held_has_no_lines():
    assert lines_of(replay([]), 20261027, {}) == {}


def test_income_and_fees_with_no_asset_have_no_line():
    rows = [
        {
            "id": 1,
            "seq": 1,
            "kind": "FEE",
            "trade_date": 20261027,
            "account_id": 1,
            "amount_paise": 5,
        },
        {
            "id": 2,
            "seq": 2,
            "kind": "INTEREST",
            "trade_date": 20261027,
            "account_id": 1,
            "amount_paise": 9,
        },
    ]
    assert engine_lines(rows, 20261027, {}) == {}


def test_a_lot_moved_between_accounts_keeps_its_day_for_the_over_a_year_count():
    rows = [
        buy(1, 20250101, 10, 1000),
        {
            "id": 2,
            "seq": 2,
            "kind": "TRANSFER",
            "trade_date": 20261027,
            "account_id": 1,
            "to_account_id": 2,
            "asset_id": 1,
            "qty_u": 4 * U,
        },
    ]
    lines = engine_lines(rows, 20261027, {1: 10_000_000})
    assert [(line.account_id, line.qty_u, line.over_year_qty_u) for line in lines[1]] == [
        (1, 6 * U, 6 * U),
        (2, 4 * U, 4 * U),
    ]


def test_the_previous_close_of_another_asset_is_not_used():
    rows = [
        buy(1, 20260105, 10, 1000),
        {
            "id": 2,
            "seq": 2,
            "kind": "TRANSFER",
            "trade_date": 20261027,
            "account_id": 1,
            "to_account_id": 2,
            "asset_id": 1,
            "qty_u": U,
        },
    ]
    lines = engine_lines(rows, 20261027, {2: 99_999_999})
    assert [(line.in_today_paise, line.out_today_paise) for line in lines[1]] == [(0, 0), (0, 0)]


def test_a_transfer_between_accounts_is_valued_with_the_rounding_of_a_holding():
    # 1 share at 0.0050 is half a paisa: one paisa each way, as a holding of it would be valued.
    rows = [
        buy(1, 20260105, 10, 1000),
        {
            "id": 2,
            "seq": 2,
            "kind": "TRANSFER",
            "trade_date": 20261027,
            "account_id": 1,
            "to_account_id": 2,
            "asset_id": 1,
            "qty_u": U,
        },
    ]
    lines = engine_lines(rows, 20261027, {1: 50})
    assert [(line.in_today_paise, line.out_today_paise) for line in lines[1]] == [(0, 1), (1, 0)]


# -- random tabs: the engine and the oracle agree --------------------------------------------------


def prev_closes(rng):
    """A previous close per asset in whole paise, or none for some."""
    return {asset: rng.randint(10, 5000) * 100 if rng.random() < 0.85 else None for asset in (1, 2)}


def sells_out(rng, rows, today):
    """``rows`` and, for some positions held at the end of ``today``, a sale of all that is left."""
    held = replay([Entry.of(row) for row in rows], as_of=today, decimals=DECIMALS)
    number = max((row["id"] for row in rows), default=0)
    more = list(rows)
    for account, asset in sorted(key for key in held.positions if key[1] is not None):
        position = held.positions[account, asset]
        if position.qty_u and rng.random() < 0.35:
            number += 1
            more.append(
                {
                    "id": number,
                    "seq": number,
                    "kind": "SELL",
                    "trade_date": today,
                    "account_id": account,
                    "asset_id": asset,
                    "qty_u": position.qty_u,
                    "amount_paise": rng.randint(100, 500_000),
                }
            )
    return more


def crowded(rng):
    """A random tab with a good share of its entries on one day, and that day."""
    rows = random_rows(rng, rng.randint(4, 40))
    today = rng.choice(DAYS[20:])
    for row in rows:
        if rng.random() < 0.45:
            row["trade_date"] = today
        if row.get("lot_date", 0) > row["trade_date"]:
            row["lot_date"] = row["trade_date"]  # shares cannot have been bought after the move
    return repaired(sells_out(rng, repaired(rows), today)), today


def shape_of(row):
    if row["kind"] != "TRANSFER":
        return row["kind"]
    if row.get("account_id") is None:
        return "TRANSFER_IN"
    return "TRANSFER_OUT" if row.get("to_account_id") is None else "TRANSFER_BETWEEN"


def test_the_engine_and_the_oracle_agree_on_the_lines_of_random_tabs():
    rng = random.Random(20261028)
    seen = Counter()
    for _ in range(400):
        good, today = crowded(rng)
        prev = prev_closes(rng)
        want = day_lines(good, today, prev, DECIMALS)
        assert as_dicts(engine_lines(good, today, prev, DECIMALS)) == want
        for found in want.values():
            for line in found:
                seen["in"] += line["in_today_paise"] > 0
                seen["out"] += line["out_today_paise"] > 0
                seen["sold_out"] += line["qty_u"] == 0 and line["open_qty_u"] > 0
                seen["in_and_out"] += line["qty_u"] == 0 and line["open_qty_u"] == 0
                seen["bought_only"] += line["open_qty_u"] == 0 and line["qty_u"] > 0
                seen["over_year"] += line["over_year_qty_u"] > 0
        for row in good:
            if row["trade_date"] == today and row.get("status") != "draft":
                seen[shape_of(row)] += 1
    # The generator must exercise what it is there for, or the comparison proves nothing.
    wanted = {
        "in": 500,
        "out": 400,
        "sold_out": 120,
        "in_and_out": 80,
        "bought_only": 120,
        "over_year": 15,
        "SELL": 400,
        "BUY": 500,
        "RIGHTS": 120,
        "SPLIT": 60,
        "BONUS": 60,
        "TRANSFER_BETWEEN": 100,
        "TRANSFER_IN": 60,
        "TRANSFER_OUT": 40,
    }
    for name, at_least in wanted.items():
        assert seen[name] >= at_least, name


# -- identities --------------------------------------------------------------------------------------


def flat_today(lines, prices, prevs):
    """Today of every line of every holding, sold-out ones too, added up the long way."""
    paise = base = 0
    for asset, found in lines.items():
        for line in found:
            now = half_away(Fraction(line["qty_u"] * prices[asset], 10**8))
            opened = half_away(Fraction(line["open_qty_u"] * prevs[asset], 10**8))
            paise += now - (opened + line["in_today_paise"] - line["out_today_paise"])
            base += opened + line["in_today_paise"]
    return paise, base


def tab_today_of(lines, prices, prevs):
    """The tab's Today as the screen gets it: the list's holdings plus the sold-out ones."""
    rows = [Row("ok", prices[a], prevs[a], tuple(found)) for a, found in lines.items()]
    closed, closed_base = closed_today(rows)
    return tab_totals(rows, Fixed(closed_today_paise=closed, closed_today_base_paise=closed_base))


def test_a_tabs_today_is_the_sum_of_every_line_sold_out_ones_included():
    rng = random.Random(41)
    checked = 0
    for _ in range(200):
        good = repaired(random_rows(rng, rng.randint(2, 40)))
        if not good:
            continue
        today = rng.choice(good)["trade_date"]
        prevs = {a: rng.randint(10, 5000) * 100 for a in (1, 2)}
        prices = {a: rng.randint(10, 5000) * 100 for a in (1, 2)}
        lines = engine_lines(good, today, prevs, DECIMALS)
        totals = tab_today_of(lines, prices, prevs)
        paise, base = flat_today(as_dicts(lines), prices, prevs)
        figure = totals.today
        assert figure.unknown == 0 and figure.pending == 0
        if figure.state == "ok":
            assert (figure.paise, figure.base_paise) == (paise, base)
            checked += 1
        else:
            assert lines == {} or (paise, base) == (0, 0)
    assert checked >= 150


def whole_shares_tab(rng):
    """Shares in whole numbers, bought on earlier days in two accounts."""
    rows = []
    for number in range(1, rng.randint(3, 6)):
        rows.append(
            buy(
                number,
                20260105 + number,
                rng.randint(2, 9),
                rng.randint(100, 9999),
                rng.choice([1, 2]),
                1,
            )
        )
    return rows


def test_a_transfer_between_accounts_changes_nothing_in_the_tabs_today():
    rng = random.Random(8)
    moved = 0
    for _ in range(150):
        rows = whole_shares_tab(rng)
        held = {}
        for row in rows:
            held[row["account_id"]] = held.get(row["account_id"], 0) + row["qty_u"]
        source = rng.choice(sorted(held))
        count = rng.randint(1, held[source] // U)
        transfer = {
            "id": 99,
            "seq": 99,
            "kind": "TRANSFER",
            "trade_date": 20261027,
            "account_id": source,
            "to_account_id": 3 - source,
            "asset_id": 1,
            "qty_u": count * U,
        }
        prevs = {1: rng.randint(10, 5000) * 100}
        prices = {1: rng.randint(10, 5000) * 100}
        # Whole shares at whole paise: every value is exact, so no rounding can tell the two apart.
        without = tab_today_of(engine_lines(rows, 20261027, prevs), prices, prevs)
        with_it = tab_today_of(engine_lines(rows + [transfer], 20261027, prevs), prices, prevs)
        assert with_it.today.paise == without.today.paise
        assert with_it.value_paise == without.value_paise
        # The percentage's base is the open value plus money in, and the receiving account's
        # line has the moved shares as money in: the base counts them once more. The rupee figure
        # is exact; the percentage is a little lower on a day shares move (README, "Today").
        assert with_it.today.base_paise == without.today.base_paise + count * prevs[1] // 100
        moved += 1
    assert moved == 150


def test_with_no_trades_today_a_holdings_today_is_its_units_times_the_change():
    rng = random.Random(12)
    for _ in range(150):
        rows = whole_shares_tab(rng)
        prevs = {1: rng.randint(10, 5000) * 100}
        prices = {1: rng.randint(10, 5000) * 100}
        totals = tab_today_of(engine_lines(rows, 20261027, prevs), prices, prevs)
        units = sum(row["qty_u"] for row in rows) // U
        change = (prices[1] - prevs[1]) // 100  # paise per share
        assert totals.today.paise == units * change
        assert totals.today.base_paise == units * prevs[1] // 100
