"""core/ledger_actions.py (PA2): splits, bonus shares and transfers, beyond the golden vectors.

The vectors (vectors/actions.json) pin the numbers of the cases the design names. These tests pin
the rules around them: the order inside a day, which account a refusal names, what a lot of one
millionth of a unit does to a split, how an asset's decimals decide what a split may leave, and
what each move records.
"""

from __future__ import annotations

import pytest

from sudarshan.portfolio.core import kinds
from sudarshan.portfolio.core.ledger import HANDLERS, ordered, replay
from sudarshan.portfolio.core.model import Entry, Ledger, Position, Refusal
from sudarshan.portfolio.core.words import Names

U = kinds.QTY_SCALE
NAMES = Names(
    accounts={1: "Zerodha", 2: "Account B"},
    assets={1: "RELIANCE", 2: "Example Fund"},
    units={2: "units"},
)


def row(number, kind, day, account=1, asset=1, **more):
    base = {"id": number, "seq": number, "kind": kind, "trade_date": day, "account_id": account}
    return base | ({} if asset is None else {"asset_id": asset}) | more


def buy(number, day, qty_u, amount, account=1, asset=1):
    return row(number, "BUY", day, account, asset, qty_u=qty_u, amount_paise=amount)


def split(number, day, num, den, account=None, asset=1):
    return row(number, "SPLIT", day, account, asset, ratio_num=num, ratio_den=den)


def move_lots(number, day, qty_u, to_account, account=1, asset=1, **more):
    return row(
        number, "TRANSFER", day, account, asset, qty_u=qty_u, to_account_id=to_account, **more
    )


def run(rows, **options):
    return replay([Entry.of(r) for r in rows], names=NAMES, **options)


def lots(ledger, account=1, asset=1):
    return [(lot.day, lot.qty_u, lot.cost_paise) for lot in ledger.position(account, asset).lots]


def refused(rows, **options):
    with pytest.raises(Refusal) as caught:
        run(rows, **options)
    return caught.value


# -- the day's order --------------------------------------------------------------------------


def test_a_day_with_every_kind_is_applied_in_the_documented_order():
    day = 20250310
    rows = [
        row(1, "FEE", day, asset=None, amount_paise=100),
        row(2, "SELL", day, qty_u=U, amount_paise=1000),
        move_lots(3, day, U, 2),
        row(4, "TRANSFER", day, None, to_account_id=2, qty_u=U, amount_paise=500),
        buy(5, day, U, 900),
        row(6, "BONUS", day, qty_u=U),
        split(7, day, 2, 1),
        row(8, "TRANSFER", day, qty_u=U),
        row(9, "DIVIDEND", day, amount_paise=50),
        row(10, "RIGHTS", day, qty_u=U, amount_paise=700),
    ]
    # splits and bonuses, what comes in, what moves between accounts, what goes out, income and
    # fees; inside a group the order the entries were made in.
    assert [e.id for e in ordered(Entry.of(r) for r in rows)] == [6, 7, 4, 5, 10, 3, 2, 8, 1, 9]


def test_a_bonus_and_a_split_on_one_day_go_in_the_order_they_were_entered():
    base = [buy(1, 20250106, 10 * U, 100000)]
    bonus_first = [*base, row(2, "BONUS", 20250310, qty_u=10 * U), split(3, 20250310, 2, 1)]
    # The bonus lot is split with the rest when the bonus was entered first ...
    assert lots(run(bonus_first)) == [(20250106, 20 * U, 100000), (20250310, 20 * U, 0)]
    split_first = [*base, split(2, 20250310, 2, 1), row(3, "BONUS", 20250310, qty_u=10 * U)]
    # ... and is not when the split was.
    assert lots(run(split_first)) == [(20250106, 20 * U, 100000), (20250310, 10 * U, 0)]


def test_a_sale_on_the_day_of_a_split_sees_the_shares_the_split_makes():
    rows = [
        buy(1, 20250106, 10 * U, 100000),
        row(2, "SELL", 20250310, qty_u=20 * U, amount_paise=150000),
        split(3, 20250310, 2, 1),
    ]
    ledger = run(rows, decimals={1: 0})
    assert lots(ledger) == []
    assert ledger.position(1, 1).realised_paise == 50000
    # Without the split the same sale is 10 short.
    assert refused(rows[:2]).facts["have_qty_u"] == 10 * U


def test_a_purchase_on_the_day_of_a_split_is_not_split():
    rows = [
        buy(1, 20250106, 10 * U, 100000),
        buy(2, 20250310, 4 * U, 60000),
        split(3, 20250310, 2, 1),
    ]
    assert lots(run(rows)) == [(20250106, 20 * U, 100000), (20250310, 4 * U, 60000)]


def test_a_draft_split_and_a_split_after_the_cut_do_nothing():
    rows = [buy(1, 20250106, 10 * U, 100000), split(2, 20250310, 2, 1) | {"status": "draft"}]
    assert lots(run(rows)) == [(20250106, 10 * U, 100000)]
    rows[1] = split(2, 20250310, 2, 1)
    assert lots(run(rows, as_of=20250309)) == [(20250106, 10 * U, 100000)]
    assert lots(run(rows, as_of=20250310)) == [(20250106, 20 * U, 100000)]


# -- what a split touches ---------------------------------------------------------------------


def test_a_split_leaves_other_assets_and_other_accounts_alone():
    rows = [
        buy(1, 20250106, 4 * U, 40000, account=1, asset=1),
        buy(2, 20250106, 4 * U, 41000, account=1, asset=2),
        buy(3, 20250106, 4 * U, 42000, account=2, asset=1),
        split(4, 20250310, 2, 1, account=1, asset=1),
    ]
    ledger = run(rows, decimals={1: 0, 2: 4})
    assert lots(ledger, 1, 1) == [(20250106, 8 * U, 40000)]
    assert lots(ledger, 1, 2) == [(20250106, 4 * U, 41000)]
    assert lots(ledger, 2, 1) == [(20250106, 4 * U, 42000)]
    rows[3] = split(4, 20250310, 2, 1, account=None, asset=1)
    ledger = run(rows, decimals={1: 0, 2: 4})
    assert lots(ledger, 1, 1) == [(20250106, 8 * U, 40000)]
    assert lots(ledger, 2, 1) == [(20250106, 8 * U, 42000)]
    assert lots(ledger, 1, 2) == [(20250106, 4 * U, 41000)]


def test_a_tab_wide_split_makes_one_move_per_account_lowest_first():
    rows = [
        buy(1, 20250106, 4 * U, 40000, account=2),
        buy(2, 20250106, 3 * U, 30000, account=1),
        split(3, 20250310, 2, 1),
    ]
    moves = [m for m in run(rows).journal if m.entry.id == 3]
    assert [(m.account_id, m.qty_u, m.cost_paise) for m in moves] == [(1, 3 * U, 0), (2, 4 * U, 0)]
    assert [sum(p.qty_u for p in m.used) for m in moves] == [6 * U, 8 * U]


def test_a_split_ignores_an_account_that_has_sold_out():
    rows = [
        buy(1, 20250106, 10 * U, 100000, account=1),
        row(2, "SELL", 20250210, 1, qty_u=10 * U, amount_paise=120000),
        buy(3, 20250211, 4 * U, 44000, account=2),
        split(4, 20250310, 2, 1),
    ]
    ledger = run(rows)
    assert [m.account_id for m in ledger.journal if m.entry.id == 4] == [2]
    assert lots(ledger, 1) == []
    assert lots(ledger, 2) == [(20250211, 8 * U, 44000)]


def test_a_tab_wide_split_names_the_lowest_account_that_cannot_take_it():
    rows = [
        buy(1, 20250106, 3 * U, 30000, account=1),
        buy(2, 20250106, 5 * U, 50000, account=2),
        split(3, 20250310, 1, 2),
    ]
    # Account 1's three shares halve to 1.5 and account 2's five to 2.5: the lower is named.
    refusal = refused(rows)
    assert refusal.facts["account_id"] == 1
    assert refusal.message == (
        "This split would leave 1.5 shares in Zerodha. "
        "Record the sale of the fraction first, then the split."
    )
    # With four shares account 1 can take it, and the other is the one named.
    rows[0] = buy(1, 20250106, 4 * U, 40000, account=1)
    refusal = refused(rows)
    assert refusal.facts["account_id"] == 2
    assert refusal.message.startswith("This split would leave 2.5 shares in Account B.")


def test_a_refused_split_changes_no_account():
    ledger = Ledger(NAMES, {1: 0})
    ledger.position(1, 1).add(20250106, ledger.next_order(), 4 * U, 40000)
    ledger.position(2, 1).add(20250106, ledger.next_order(), 3 * U, 30000)
    with pytest.raises(Refusal) as caught:
        HANDLERS["SPLIT"](ledger, Entry.of(split(3, 20250310, 1, 2)))
    # Account 1 could have taken the split (4 shares become 2); account 2 could not (1.5). The
    # split is all or nothing, so account 1 is as it was and the journal says nothing happened.
    assert caught.value.facts["account_id"] == 2
    assert lots(ledger, 1) == [(20250106, 4 * U, 40000)]
    assert ledger.position(1, 1).qty_u == 4 * U
    assert ledger.journal == []


# -- what a split may leave -------------------------------------------------------------------


def test_the_decimals_of_an_asset_decide_what_a_split_may_leave():
    rows = [buy(1, 20250106, 125 * U // 10, 125000, asset=2), split(2, 20250310, 1, 10, asset=2)]
    # 12.5 units become 1.25: two decimals.
    assert refused(rows, decimals={2: 0}).facts["left_qty_u"] == 1_250_000
    assert refused(rows, decimals={2: 1}).facts["left_qty_u"] == 1_250_000
    assert lots(run(rows, decimals={2: 2}), asset=2) == [(20250106, 1_250_000, 125000)]
    # An asset left out of the decimals is a whole-number asset: shares.
    assert refused(rows).reason == "fraction"
    assert refused(rows, decimals={1: 6}).reason == "fraction"


def test_the_sentence_says_units_for_an_asset_that_is_counted_in_units():
    rows = [buy(1, 20250106, 15 * U, 150000, asset=2), split(2, 20250310, 1, 10, asset=2)]
    assert refused(rows, decimals={2: 0}).message == (
        "This split would leave 1.5 units in Zerodha. "
        "Record the sale of the fraction first, then the split."
    )
    assert refused([buy(1, 20250106, 15 * U, 150000), split(2, 20250310, 1, 10)]).message == (
        "This split would leave 1.5 shares in Zerodha. "
        "Record the sale of the fraction first, then the split."
    )


def test_a_split_that_does_not_divide_evenly_is_refused_whatever_the_decimals():
    rows = [buy(1, 20250106, 10 * U, 100000, asset=2), split(2, 20250310, 1, 3, asset=2)]
    refusal = refused(rows, decimals={2: 6})
    # 10 x 1/3 is 3.333333..., which no number of decimals holds exactly.
    assert (refusal.reason, refusal.facts["left_qty_u"]) == ("fraction", 3_333_333)
    assert refusal.message.startswith("This split would leave 3.333333 units in Zerodha.")
    # What is left is said to the nearest millionth, half away from zero: 2 x 1/3 is 0.6666666...
    rows[0] = buy(1, 20250106, 2 * U, 20000, asset=2)
    assert refused(rows, decimals={2: 6}).facts["left_qty_u"] == 666_667
    # 9 x 1/3 is exact.
    rows[0] = buy(1, 20250106, 9 * U, 90000, asset=2)
    assert lots(run(rows, decimals={2: 0}), asset=2) == [(20250106, 3 * U, 90000)]


def test_a_split_that_would_leave_a_lot_with_nothing_is_refused():
    rows = [
        buy(1, 20250106, 1, 10, asset=2),
        buy(2, 20250107, 2, 20, asset=2),
        split(3, 20250310, 1, 3, asset=2),
    ]
    # Three millionths of a unit become one, which the position can hold; but the older lot
    # (one millionth) would round to nothing and take its cost with it.
    refusal = refused(rows, decimals={2: 6})
    assert (refusal.reason, refusal.facts["have_qty_u"], refusal.facts["left_qty_u"]) == (
        "fraction",
        3,
        1,
    )
    assert refusal.message.startswith("This split would leave 0.000001 units in Zerodha.")


def test_lots_are_rounded_on_the_running_total_so_they_add_up():
    rows = [
        buy(1, 20250106, 7 * U, 70000),
        buy(2, 20250107, 8 * U, 96000),
        buy(3, 20250108, 6 * U, 66000),
        split(4, 20250310, 1, 3),
    ]
    ledger = run(rows)
    assert [lot[1] for lot in lots(ledger)] == [2_333_333, 2_666_667, 2 * U]
    assert sum(lot[1] for lot in lots(ledger)) == 7 * U
    assert [lot[2] for lot in lots(ledger)] == [70000, 96000, 66000]


def test_a_split_move_records_the_change_and_the_lots_as_they_stand():
    rows = [buy(1, 20250106, 10 * U, 100000), split(2, 20250310, 5, 1)]
    move = run(rows).journal[-1]
    assert (move.kind, move.account_id, move.asset_id) == ("SPLIT", 1, 1)
    assert (move.qty_u, move.cost_paise, move.gain_paise, move.paise) == (40 * U, 0, 0, None)
    assert [tuple(piece) for piece in move.used] == [(20250106, 50 * U, 100000)]


# -- bonus shares -----------------------------------------------------------------------------


def test_a_bonus_needs_units_held_not_just_a_position():
    rows = [
        row(1, "DIVIDEND", 20250106, amount_paise=500),
        row(2, "BONUS", 20250310, qty_u=5 * U),
    ]
    refusal = refused(rows)
    # The account has earned from RELIANCE but holds none on the day.
    assert (refusal.entry_id, refusal.reason, refusal.facts["kind"]) == (2, "not_held", "BONUS")
    assert refusal.message == (
        "Zerodha holds no RELIANCE on 10 Mar 2025, so a bonus cannot be saved. "
        "Check the date, or add the missing purchase first."
    )


def test_a_bonus_is_a_lot_at_no_cost_dated_the_allotment_and_sold_after_older_lots():
    rows = [
        buy(1, 20250106, 4 * U, 40000),
        row(2, "BONUS", 20250310, qty_u=2 * U),
        buy(3, 20250410, 3 * U, 36000),
        row(4, "SELL", 20250510, qty_u=5 * U, amount_paise=70000),
    ]
    ledger = run(rows)
    bonus = ledger.journal[1]
    assert (bonus.qty_u, bonus.cost_paise, bonus.gain_paise, bonus.paise) == (2 * U, 0, 0, None)
    sale = ledger.journal[-1]
    # FIFO: the 4 bought on 6 Jan, then 1 of the 2 bonus shares, which cost nothing.
    assert [tuple(piece) for piece in sale.used] == [(20250106, 4 * U, 40000), (20250310, U, 0)]
    assert lots(ledger) == [(20250310, U, 0), (20250410, 3 * U, 36000)]
    assert ledger.position(1, 1).realised_paise == 70000 - 40000


# -- transfers --------------------------------------------------------------------------------


def test_shares_moved_in_queue_behind_the_lots_the_account_already_has_for_that_day():
    rows = [
        buy(1, 20250106, 2 * U, 20000, account=1),
        buy(2, 20250106, 2 * U, 30000, account=2),
        move_lots(3, 20250401, 2 * U, 2),
        row(4, "SELL", 20250415, 2, 1, qty_u=U, amount_paise=18000),
    ]
    ledger = run(rows)
    # Both lots are dated 6 Jan; the account's own came first, so a sale takes it first.
    assert lots(ledger, 2) == [(20250106, U, 15000), (20250106, 2 * U, 20000)]
    assert ledger.journal[-1].gain_paise == 18000 - 15000


def test_everything_moved_away_leaves_the_account_with_no_holding():
    rows = [buy(1, 20250106, 5 * U, 50000), move_lots(2, 20250310, 5 * U, 2)]
    ledger = run(rows)
    assert [(p.account_id, p.asset_id) for p in ledger.holdings()] == [(2, 1)]
    assert ledger.position(1, 1).qty_u == 0 and ledger.position(1, 1).cost_paise == 0


def test_a_transfer_between_accounts_makes_a_move_each_way_and_no_money():
    rows = [buy(1, 20250106, 4 * U, 40001), move_lots(2, 20250310, 1 * U, 2)]
    out, into = run(rows).journal[1:]
    # A quarter of 40001 paise is 10000.25, which goes to the paisa nearest.
    assert (out.account_id, out.qty_u, out.cost_paise) == (1, -U, -10000)
    assert (into.account_id, into.qty_u, into.cost_paise) == (2, U, 10000)
    assert out.used == into.used
    assert out.gain_paise == into.gain_paise == 0
    assert out.paise is None and into.paise is None


def test_shares_brought_in_from_outside_count_the_value_as_the_money_and_the_cost_as_the_cost():
    entry = row(
        1, "TRANSFER", 20260401, None, 1, qty_u=10 * U, to_account_id=1, amount_paise=100000
    )
    plain = run([entry]).journal[0]
    assert (plain.account_id, plain.qty_u, plain.cost_paise, plain.paise) == (
        1,
        10 * U,
        100000,
        100000,
    )
    valued = run([entry | {"value_paise": 150000, "lot_date": 20230815}])
    move = valued.journal[0]
    assert (move.cost_paise, move.paise) == (100000, 150000)
    assert lots(valued) == [(20230815, 10 * U, 100000)]
    # The lot counts as bought on its first day, so it is over a year old on the day it comes in.
    assert valued.position(1, 1).over_year_qty_u(20260401) == 10 * U


def test_shares_taken_out_leave_at_cost_and_the_value_is_only_the_money_counted():
    rows = [buy(1, 20250106, 4 * U, 40000), row(2, "TRANSFER", 20250310, qty_u=3 * U)]
    move = run(rows).journal[-1]
    assert (move.cost_paise, move.gain_paise, move.paise) == (-30000, 0, 30000)
    valued = run([*rows[:1], rows[1] | {"value_paise": 45000}])
    assert (valued.journal[-1].cost_paise, valued.journal[-1].paise) == (-30000, 45000)
    assert valued.position(1, 1).realised_paise == 0


def test_moving_more_than_the_account_holds_names_the_transfer_in_both_directions():
    held = [buy(1, 20250106, 4 * U, 40000)]
    for target in (2, None):
        entry = row(
            2,
            "TRANSFER",
            20250310,
            qty_u=5 * U,
            **({} if target is None else {"to_account_id": target}),
        )
        refusal = refused([*held, entry])
        assert (refusal.entry_id, refusal.reason) == (2, "short")
        assert refusal.facts["have_qty_u"] == 4 * U and refusal.facts["want_qty_u"] == 5 * U
        assert "a transfer of 5 cannot be saved" in refusal.message


def test_a_moved_lot_keeps_its_day_for_the_year():
    rows = [buy(1, 20240105, 10 * U, 100000), move_lots(2, 20250601, 10 * U, 2)]
    ledger = run(rows)
    assert lots(ledger, 2) == [(20240105, 10 * U, 100000)]
    assert ledger.position(2, 1).over_year_qty_u(20250601) == 10 * U
    assert ledger.position(2, 1).over_year_qty_u(20250105) == 0


# -- the pieces -------------------------------------------------------------------------------


def test_a_position_can_be_given_new_quantities_lot_by_lot():
    position = Position(1, 1)
    position.add(20250106, 1, 3 * U, 300)
    position.add(20250107, 2, 2 * U, 200)
    position.rescale([6 * U, 1])
    assert [(lot.day, lot.qty_u, lot.cost_paise) for lot in position.lots] == [
        (20250106, 6 * U, 300),
        (20250107, 1, 200),
    ]
    assert (position.qty_u, position.cost_paise) == (6 * U + 1, 500)
    with pytest.raises(ValueError, match="shorter|longer|argument"):
        position.rescale([U])


def test_the_decimals_of_a_ledger_default_to_whole_shares():
    assert Ledger().decimals_of(1) == 0
    assert Ledger(decimals={1: 4}).decimals_of(1) == 4
    assert Ledger(decimals={1: 4}).decimals_of(2) == 0
    assert Ledger().decimals_of(None) == 0
