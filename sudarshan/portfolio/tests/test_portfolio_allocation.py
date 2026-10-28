"""core/allocation.py (PA6): how a tab's value divides, and the class AMFI's header gives a fund.

Four witnesses, as for the other cores. The golden vectors (vectors/allocation.json) are worked out
by hand and by an evaluator in make_pa6.py that imports nothing from sudarshan; the plain second
implementation in portfolio_oracle.py is run on the same rows and on hundreds of random tabs, and
identities that must hold whatever the numbers are close the gaps: the bars of class, sector, cap
size, account and member add up to the tab's value to the paisa, every share is the exact rounding
of its bar over the tab, tags overlap and nothing else does, and the order the holdings arrive in
never changes a bar.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import random
import re
from collections import Counter
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest
from portfolio_oracle import allocation as oracle_allocation
from portfolio_oracle import share_text
from portfolio_ts import interfaces, members
from test_portfolio_ledger import DAYS, DECIMALS, random_rows, repaired
from test_portfolio_vectors import load

from sudarshan.portfolio.core import kinds, words
from sudarshan.portfolio.core.allocation import (
    AllocationRow,
    Facts,
    Holding,
    Owner,
    allocate,
    allocate_all,
    mf_class,
)
from sudarshan.portfolio.core.daylines import lines_of
from sudarshan.portfolio.core.ledger import replay
from sudarshan.portfolio.core.model import Entry
from sudarshan.portfolio.core.valuation import Fixed, Row, tab_totals

VECTORS = Path(__file__).parent / "vectors"
FILE = json.loads((VECTORS / "allocation.json").read_text(encoding="utf-8"))
TYPES_TS = Path(__file__).resolve().parents[3] / "frontend/src/sudarshan/portfolio/types.ts"
GEN = load("make_pa6.py")  # the independent evaluator
U = kinds.QTY_SCALE
SUMS = ("class", "sector", "mcap", "account", "member")  # the kinds whose bars add up to the tab


def owners_of(accounts):
    return {int(number): Owner(a["name"], a["member_id"]) for number, a in accounts.items()}


def names_of(members_):
    return {int(number): name for number, name in members_.items()}


def bars(rows, accounts, members_):
    held = [Holding.of(row) for row in rows]
    return allocate_all(held, owners_of(accounts), names_of(members_))


def hold(account, qty, cost=0, opened=None):
    """A HoldingLine as the contract's JSON has it."""
    return {
        "account_id": account,
        "qty_u": qty,
        "open_qty_u": qty if opened is None else opened,
        "cost_paise": cost,
        "in_today_paise": 0,
        "out_today_paise": 0,
        "over_year_qty_u": 0,
    }


def holding_row(asset, price, *lines):
    return {
        "asset": asset,
        "today_mode": "ok",
        "price": {"price_u": price, "prev_close_u": None},
        "lines": list(lines),
    }


# -- the golden vectors ---------------------------------------------------------------------------


def test_the_file_has_what_the_design_needs():
    assert {f"AL{n}" for n in range(1, 13)} <= {case["id"] for case in FILE["allocations"]}
    assert {f"MF{n}" for n in range(1, 15)} <= {case["id"] for case in FILE["mf_classes"]}
    for case in (*FILE["allocations"], *FILE["mf_classes"]):
        assert case["title"] and len(case["why"]) > 40, case["id"]
    for case in FILE["allocations"]:
        assert list(case["expect"]) == list(kinds.ALLOCATION_BY), case["id"]


@pytest.mark.parametrize("case", FILE["allocations"], ids=lambda case: case["id"])
def test_the_bars_are_the_vectors(case):
    got = bars(case["rows"], case["accounts"], case["members"])
    assert got == case["expect"]
    assert list(got) == list(
        kinds.ALLOCATION_BY
    )  # always in the same order, as the phone lists them


@pytest.mark.parametrize("case", FILE["allocations"], ids=lambda case: case["id"])
def test_one_kind_of_bar_alone_is_what_all_of_them_say(case):
    held = [Holding.of(row) for row in case["rows"]]
    for by in kinds.ALLOCATION_BY:
        got = allocate(by, held, owners_of(case["accounts"]), names_of(case["members"]))
        assert [bar.as_dict() for bar in got] == case["expect"][by], by


@pytest.mark.parametrize("case", FILE["allocations"], ids=lambda case: case["id"])
def test_the_naive_oracle_gives_the_vectors_bars(case):
    for by in kinds.ALLOCATION_BY:
        got = oracle_allocation(by, case["rows"], case["accounts"], case["members"])
        assert got == case["expect"][by], by


@pytest.mark.parametrize("case", FILE["allocations"], ids=lambda case: case["id"])
def test_the_evaluator_gives_the_vectors_bars(case):
    for by in kinds.ALLOCATION_BY:
        got = GEN.bars_of(by, case["rows"], case["accounts"], case["members"])
        assert got == case["expect"][by], by


@pytest.mark.parametrize("case", FILE["mf_classes"], ids=lambda case: case["id"])
def test_the_class_of_a_fund_is_the_vectors(case):
    assert mf_class(case["header"]) == case["expect"]
    assert case["expect"] in kinds.ASSET_CLASSES
    assert GEN.class_of_header(case["header"]) == case["expect"]


# -- the class of a fund --------------------------------------------------------------------------

PIECES = [
    "Open Ended Schemes",
    "Close Ended Schemes",
    "Equity Scheme",
    "equity   scheme",
    "DEBT SCHEME",
    "Debt Scheme",
    "Hybrid Scheme",
    "Other Scheme",
    "Solution Oriented Scheme",
    " - ",
    "-",
    "(",
    ")",
    "((",
    "))",
    " ",
    "\t",
    "\n",
    chr(0xA0),  # a non-breaking space
    "Large Cap Fund",
    "Liquid Fund",
    "",
]


HEADLINES = [
    "Equity Scheme",
    "equity   scheme",
    " Equity Scheme ",
    "Equity\u00a0Scheme",
    "DEBT SCHEME",
    "Debt Scheme",
    "Hybrid Scheme",
    "Other Scheme",
    "Solution Oriented Scheme",
    "Equity Schemes",
    "",
]
TAILS = ["", " - Large Cap Fund", "-ELSS", " - Sectoral/Thematic (Banking)", " - ", "\n- Liquid"]


def random_header(rng):
    if rng.random() < 0.4:  # anything at all, brackets and dashes in any places
        return "".join(rng.choice(PIECES) for _ in range(rng.randint(0, 8)))
    prefix = rng.choice(["Open Ended Schemes", "Close Ended Schemes", "", "Interval Fund Schemes"])
    return f"{prefix}({rng.choice(HEADLINES)}{rng.choice(TAILS)})"


def test_the_class_of_a_fund_is_one_of_four_whatever_the_header_says():
    rng = random.Random(2026)
    seen = Counter()
    for _ in range(4000):
        header = random_header(rng)
        got = mf_class(header)
        assert got in ("mf_equity", "mf_debt", "mf_hybrid", "mf_other"), header
        assert got == GEN.class_of_header(header), repr(header)
        seen[got] += 1
    assert all(seen[kind] >= 100 for kind in ("mf_equity", "mf_debt", "mf_hybrid", "mf_other"))


@pytest.mark.parametrize("header", [None, 5, 5.5, b"(Equity Scheme - x)", ["(Equity Scheme)"]])
def test_a_header_that_is_not_text_is_mf_other(header):
    assert mf_class(header) == "mf_other"


def test_a_fund_is_in_a_class_the_platform_has():
    for header in ("Open Ended Schemes(Equity Scheme - Large Cap Fund)", "", "(Debt Scheme)"):
        assert mf_class(header) in kinds.ASSET_CLASSES


# -- facts and holdings ---------------------------------------------------------------------------


def test_the_facts_of_an_asset_are_made_plain():
    asset = {"id": 4, "asset_class": "equity", "sector": "", "mcap": "", "tags": ["b", "a", "b"]}
    assert Facts.of(asset) == Facts(4, "equity", None, None, ("b", "a"))
    assert Facts.of({"id": 5, "asset_class": "gold"}) == Facts(5, "gold")
    full = {"id": 6, "asset_class": "mf_debt", "sector": "Gilt", "mcap": "mid", "tags": ("x",)}
    assert Facts.of(full) == Facts(6, "mf_debt", "Gilt", "mid", ("x",))
    assert Facts.of({**full, "sector": None, "mcap": None, "tags": None}) == Facts(6, "mf_debt")


def test_an_asset_a_screen_can_hold_is_read_whatever_else_it_carries():
    asset = {"id": 7, "asset_class": "equity", "name": "ACME", "kind": "listed", "isin": "x"}
    assert Facts.of(asset) == Facts(7, "equity")


@pytest.mark.parametrize(
    "asset",
    [
        {"id": 1, "asset_class": "shares"},
        {"id": 1, "asset_class": ""},
        {"id": 1, "asset_class": "equity", "mcap": "giant"},
    ],
)
def test_a_class_or_a_cap_size_the_platform_does_not_have_is_refused(asset):
    with pytest.raises(ValueError):
        Facts.of(asset)


def test_facts_cannot_be_changed_after_they_are_made():
    facts = Facts.of({"id": 1, "asset_class": "equity"})
    with pytest.raises(dataclasses.FrozenInstanceError):
        facts.sector = "Banks"
    assert hash(facts) == hash(Facts(1, "equity"))


def test_a_holding_is_an_assets_facts_with_its_row():
    case = FILE["allocations"][0]
    holding = Holding.of(case["rows"][0])
    assert holding.facts == Facts.of(case["rows"][0]["asset"])
    assert holding.row == Row.of(case["rows"][0])
    facts, row = holding  # it is a pair, so allocate can take a list of plain pairs too
    assert (facts, row) == (holding.facts, holding.row)


def test_a_bar_has_the_fields_the_phone_reads():
    found = interfaces(TYPES_TS.read_text(encoding="utf-8"))
    assert list(AllocationRow("k", "l", 1, "0.0000", 1).as_dict()) == members(
        found["AllocationRow"]
    )
    assert tuple(kinds.ALLOCATION_BY) == ("class", "sector", "mcap", "account", "member", "tag")


def test_there_is_no_allocation_by_something_the_design_does_not_have():
    case = FILE["allocations"][0]
    held = [Holding.of(row) for row in case["rows"]]
    for by in ("", "Class", "currency", "tags", None):
        with pytest.raises(ValueError):
            allocate(by, held, owners_of(case["accounts"]), names_of(case["members"]))


def test_the_holdings_can_be_handed_over_once():
    case = FILE["allocations"][0]
    once = (Holding.of(row) for row in case["rows"])
    got = allocate_all(once, owners_of(case["accounts"]), names_of(case["members"]))
    assert got == case["expect"]


def test_the_names_of_the_bars_are_plain_words():
    nothing_here = re.compile(r"\b(arm|armed|arming|paper|virtual|http|exception|none|nan)\b", re.I)
    for text in (words.NO_SECTOR, words.NO_CAP, words.NO_TAG, *words.MCAP_LABELS.values()):
        assert text.isascii() and text == text.strip() and "  " not in text, text
        assert not nothing_here.search(text), text
    assert set(words.MCAP_LABELS) == set(kinds.MCAPS)
    assert GEN.CAP_LABELS == words.MCAP_LABELS
    assert GEN.CLASS_LABELS == kinds.ASSET_CLASS_LABELS
    assert list(GEN.CLASS_LABELS) == list(kinds.ASSET_CLASSES)


# -- a share is the exact rounding of its bar over the tab -----------------------------------------


def cost_only(kind, number, cost):
    """A holding with no price, so that it is worth exactly ``cost`` paise."""
    asset = {"id": number, "asset_class": kind, "sector": None, "mcap": None, "tags": []}
    return holding_row(asset, None, hold(1, 1, cost))


def shares_of(part, total):
    rows = [cost_only("equity", 1, part), cost_only("gold", 2, total - part)]
    return {
        bar["key"]: bar
        for bar in bars(rows, {"1": {"name": "A", "member_id": 1}}, {"1": "M"})["class"]
    }


def test_a_share_is_the_exact_rounding_of_its_bar_over_the_tab():
    rng = random.Random(4)
    cases = [(total, part) for total in range(1, 70) for part in range(total + 1)]
    for total in (127, 128, 129, 256, 384, 640, 1280, 12_800, 128_000, 10**9 + 7):
        for part in {0, 1, 2, 3, total - 1, total, *(rng.randint(0, total) for _ in range(60))}:
            cases.append((total, part))
    for total in (128, 256, 384):  # the ties: odd multiples of a 128th are exactly between
        cases += [(total, part) for part in range(total + 1)]
    ties = 0
    for total, part in cases:
        got = shares_of(part, total)
        assert got["equity"]["value_paise"] == part and got["gold"]["value_paise"] == total - part
        assert got["equity"]["pct"] == share_text(part, total) == GEN.pct4(part, total)
        assert (
            got["gold"]["pct"] == share_text(total - part, total) == GEN.pct4(total - part, total)
        )
        ties += (part * 10**6) % total * 2 == total
    assert ties >= 100  # a good many shares were exactly between two numbers


def test_a_share_that_rounds_to_nothing_is_still_a_number_with_four_decimals():
    got = shares_of(1, 10**9)
    assert got["equity"]["pct"] == "0.0000" and got["gold"]["pct"] == "100.0000"
    got = shares_of(0, 10)
    assert got["equity"]["pct"] == "0.0000" and got["equity"]["holdings"] == 1


# -- random tabs ----------------------------------------------------------------------------------

SECTORS = [None, None, "Banks", "IT", "Auto", "Pharma", "banks"]
CAPS = [None, "large", "mid", "small"]
TAGS = ["core", "income", "growth", "watch", "a b"]
QTYS = [1, 7, 500, 5_000, 50_000, 12_345, 250_000, U, 3 * U, 17 * U, 123 * U + 456_789]
PRICES = [1, 100, 1_000, 10_000, 12_345, 100_000, 5_000_000, 987_654_321]
MEMBER_NAMES = ["Me", "Member B", "Member C", "Me"]
ACCOUNT_NAMES = ["Demat", "Demat", "Fund folio", "Bank", "Demat A"]


def random_line(rng, account):
    if rng.random() < 0.12:
        return hold(account, 0, 0, opened=rng.choice([0, 3 * U]))  # sold out
    return hold(account, rng.choice(QTYS), rng.randint(0, 2_000_000))


def random_tab(rng):
    """Holdings, accounts and members as the contract's JSON has them; names repeat on purpose."""
    people = {str(n): rng.choice(MEMBER_NAMES) for n in range(1, rng.randint(2, 4))}
    accounts = {
        str(n): {"name": rng.choice(ACCOUNT_NAMES), "member_id": int(rng.choice(list(people)))}
        for n in range(1, rng.randint(2, 13))  # up to twelve: "10" sorts before "2" as text
    }
    rows = []
    for number in range(1, rng.randint(1, 15)):
        asset = {
            "id": number,
            "asset_class": rng.choice(kinds.ASSET_CLASSES),
            "sector": rng.choice(SECTORS),
            "mcap": rng.choice(CAPS),
            "tags": [rng.choice(TAGS) for _ in range(rng.choice([0, 0, 1, 2, 3, 5]))],
        }
        price = rng.choice(PRICES) if rng.random() < 0.85 else None
        chosen = rng.sample(sorted(accounts, key=int), rng.randint(1, min(4, len(accounts))))
        rows.append(
            holding_row(asset, price, *(random_line(rng, int(account)) for account in chosen))
        )
    return rows, accounts, people


def held_lines(rows):
    return [(row, line) for row in rows for line in row["lines"] if line["qty_u"]]


def value_of(row, line):
    price = row["price"]["price_u"]
    if price is None:
        return line["cost_paise"]
    numerator = line["qty_u"] * price
    return (2 * numerator + 10**8) // (2 * 10**8)  # a tie goes up; every value here is positive


def test_the_engine_the_evaluator_and_the_oracle_agree_on_random_tabs():
    rng = random.Random(20261029)
    seen = Counter()
    for _ in range(400):
        rows, accounts, people = random_tab(rng)
        got = bars(rows, accounts, people)
        for by in kinds.ALLOCATION_BY:
            assert got[by] == GEN.bars_of(by, rows, accounts, people), by
            assert got[by] == oracle_allocation(by, rows, accounts, people), by
            values = [bar["value_paise"] for bar in got[by]]
            seen["level"] += len(values) != len(set(values))
        for row, line in held_lines(rows):
            price = row["price"]["price_u"]
            seen["unpriced"] += price is None
            seen["half a paisa"] += price is not None and line["qty_u"] * price % 10**8 == 5 * 10**7
        seen["sold out"] += sum(1 for row in rows for line in row["lines"] if not line["qty_u"])
        seen["repeated tag"] += sum(
            len(row["asset"]["tags"]) != len(set(row["asset"]["tags"])) for row in rows
        )
        seen["same name"] += len({a["name"] for a in accounts.values()}) < len(accounts)
    # The generator must exercise what it is there for, or the comparison proves nothing.
    assert seen["level"] >= 200 and seen["unpriced"] >= 100 and seen["sold out"] >= 100
    assert seen["half a paisa"] >= 20 and seen["repeated tag"] >= 100 and seen["same name"] >= 300


def test_the_bars_of_a_kind_add_up_to_the_tabs_value_to_the_paisa():
    rng = random.Random(8)
    for _ in range(300):
        rows, accounts, people = random_tab(rng)
        got = bars(rows, accounts, people)
        value = tab_totals([Row.of(row) for row in rows], Fixed()).value_paise
        assert value == sum(value_of(row, line) for row, line in held_lines(rows))
        for by in SUMS:
            assert sum(bar["value_paise"] for bar in got[by]) == value, by


def test_every_share_is_its_bar_over_the_tab_and_the_shares_add_up_to_a_hundred_or_so():
    rng = random.Random(9)
    for _ in range(300):
        rows, accounts, people = random_tab(rng)
        got = bars(rows, accounts, people)
        total = sum(value_of(row, line) for row, line in held_lines(rows))
        for by, found in got.items():
            for bar in found:
                assert re.fullmatch(r"\d+\.\d{4}", bar["pct"]), bar
                assert bar["pct"] == share_text(bar["value_paise"], total)
                assert bar["pct"] == (GEN.pct4(bar["value_paise"], total) if total else "0.0000")
                if by != "tag":
                    assert Decimal(bar["pct"]) <= 100
            if by != "tag" and found and total:
                off = abs(sum(Decimal(bar["pct"]) for bar in found) - 100)
                assert off <= Decimal("0.00005") * len(found), (by, off)


def test_tags_overlap_and_nothing_else_does():
    rng = random.Random(10)
    for _ in range(300):
        rows, accounts, people = random_tab(rng)
        got = bars(rows, accounts, people)
        every = sum(
            value_of(row, line) * max(1, len(set(row["asset"]["tags"])))
            for row, line in held_lines(rows)
        )
        assert sum(bar["value_paise"] for bar in got["tag"]) == every
        total = sum(value_of(row, line) for row, line in held_lines(rows))
        assert all(bar["value_paise"] <= total for bar in got["tag"])
        by_tag = Counter()
        for row, line in held_lines(rows):
            for tag in set(row["asset"]["tags"]):
                by_tag[tag] += value_of(row, line)
        assert {bar["key"]: bar["value_paise"] for bar in got["tag"] if bar["key"]} == dict(by_tag)


def test_the_holdings_in_the_bars_are_the_assets_in_them():
    rng = random.Random(11)
    for _ in range(300):
        rows, accounts, people = random_tab(rng)
        got = bars(rows, accounts, people)
        assets = {row["asset"]["id"] for row, _ in held_lines(rows)}
        for by in ("class", "sector", "mcap"):  # an asset has one of each
            assert sum(bar["holdings"] for bar in got[by]) == len(assets), by
        for by, found in got.items():
            assert all(1 <= bar["holdings"] <= len(assets) for bar in found), by
        in_account = {}
        for row, line in held_lines(rows):
            in_account.setdefault(str(line["account_id"]), set()).add(row["asset"]["id"])
        assert {bar["key"]: bar["holdings"] for bar in got["account"]} == {
            key: len(found) for key, found in in_account.items()
        }


def test_bars_are_largest_first_and_of_equal_value_by_name_then_key():
    rng = random.Random(12)
    for _ in range(300):
        rows, accounts, people = random_tab(rng)
        for by, found in bars(rows, accounts, people).items():
            order = [(-bar["value_paise"], bar["label"], bar["key"]) for bar in found]
            assert order == sorted(order), by
            assert len({bar["key"] for bar in found}) == len(found), by


def test_the_order_the_holdings_arrive_in_never_changes_a_bar():
    rng = random.Random(13)
    for _ in range(150):
        rows, accounts, people = random_tab(rng)
        want = bars(rows, accounts, people)
        for _ in range(3):
            mixed = copy.deepcopy(rows)
            rng.shuffle(mixed)
            for row in mixed:
                rng.shuffle(row["lines"])
                rng.shuffle(row["asset"]["tags"])
            assert bars(mixed, accounts, people) == want


def test_lines_and_holdings_sold_out_change_no_bar():
    rng = random.Random(14)
    for _ in range(150):
        rows, accounts, people = random_tab(rng)
        want = bars(rows, accounts, people)
        more = copy.deepcopy(rows)
        for row in more:
            used = {line["account_id"] for line in row["lines"]}
            row["lines"] += [
                hold(int(account), 0, 0, opened=2 * U)
                for account in accounts
                if int(account) not in used
            ]
        gone = {
            "id": 99,
            "asset_class": "crypto",
            "sector": "Nothing here",
            "mcap": "large",
            "tags": ["gone"],
        }
        more.append(holding_row(gone, 10_000, *(hold(int(a), 0, 0, opened=U) for a in accounts)))
        assert bars(more, accounts, people) == want


def test_a_holding_with_no_price_is_worth_its_cost_in_every_bar():
    rng = random.Random(15)
    for _ in range(200):
        rows, accounts, people = random_tab(rng)
        for row in rows:
            row["price"]["price_u"] = None
        got = bars(rows, accounts, people)
        cost = sum(line["cost_paise"] for _, line in held_lines(rows))
        for by in SUMS:
            assert sum(bar["value_paise"] for bar in got[by]) == cost, by
        by_class = Counter()
        for row, line in held_lines(rows):
            by_class[row["asset"]["asset_class"]] += line["cost_paise"]
        assert {bar["key"]: bar["value_paise"] for bar in got["class"]} == dict(by_class)


def test_a_price_that_rises_never_lowers_a_bar():
    rng = random.Random(16)
    for _ in range(200):
        rows, accounts, people = random_tab(rng)
        priced = [row for row in rows if row["price"]["price_u"] is not None]
        if not priced:
            continue
        before = bars(rows, accounts, people)
        rng.choice(priced)["price"]["price_u"] += rng.randint(1, 5_000_000)
        after = bars(rows, accounts, people)
        for by in kinds.ALLOCATION_BY:
            now = {bar["key"]: bar["value_paise"] for bar in after[by]}
            for bar in before[by]:
                assert now[bar["key"]] >= bar["value_paise"], by


def test_a_tab_with_nothing_held_has_no_bars_and_a_tab_worth_nothing_has_no_share():
    asset = {"id": 1, "asset_class": "equity", "sector": "IT", "mcap": "mid", "tags": ["core"]}
    accounts, people = {"1": {"name": "Demat", "member_id": 1}}, {"1": "Me"}
    assert bars([], accounts, people) == {by: [] for by in kinds.ALLOCATION_BY}
    sold = [holding_row(asset, 10_000, hold(1, 0, 0, opened=U))]
    assert bars(sold, accounts, people) == {by: [] for by in kinds.ALLOCATION_BY}
    nothing = [holding_row(asset, 1, hold(1, 1, 0))]  # a millionth of a unit at 0.0001
    got = bars(nothing, accounts, people)
    assert all(
        [(bar["value_paise"], bar["pct"], bar["holdings"]) for bar in found] == [(0, "0.0000", 1)]
        for found in got.values()
    )


# -- through the ledger ---------------------------------------------------------------------------


def test_the_bars_of_a_replayed_tab_add_up_to_its_total():
    rng = random.Random(77)
    asset_classes = ["equity", "mf_equity", "gold"]
    counted = Counter()
    for _ in range(200):
        good = repaired(random_rows(rng, rng.randint(2, 40)))
        today = rng.choice(DAYS)
        ledger = replay([Entry.of(row) for row in good], as_of=today, decimals=DECIMALS)
        lines = lines_of(ledger, today, {})
        rows = []
        for asset, found in lines.items():
            info = {
                "id": asset,
                "asset_class": rng.choice(asset_classes),
                "sector": rng.choice(SECTORS),
                "mcap": rng.choice(CAPS),
                "tags": rng.sample(TAGS, rng.randint(0, 2)),
            }
            price = rng.choice(PRICES) if rng.random() < 0.8 else None
            rows.append(holding_row(info, price, *(line.as_dict() for line in found)))
        accounts = {n: {"name": f"Account {n}", "member_id": 1 + int(n) % 2} for n in ("1", "2")}
        people = {"1": "Me", "2": "Member B"}
        got = bars(rows, accounts, people)
        value = tab_totals([Row.of(row) for row in rows], Fixed()).value_paise
        for by in SUMS:
            assert sum(bar["value_paise"] for bar in got[by]) == value, by
        for by in kinds.ALLOCATION_BY:
            assert got[by] == oracle_allocation(by, rows, accounts, people), by
        counted["held"] += bool(got["class"])
        counted["both accounts"] += len(got["account"]) == 2
    assert counted["held"] >= 120 and counted["both accounts"] >= 40
