"""Generator of the PA2 golden vectors: actions.json, written next to this file.

Splits, bonus shares, rights and transfers (the design's F7 and C1-C5, and the cases around them).
Like make_pa1.py it imports nothing from sudarshan: every number is worked out by hand in the
case's ``why``, and the tests hold the engine and the naive second implementation to it.

Run: python3 make_pa2.py
"""

from pathlib import Path

from portfolio_vector_tools import (
    DOT,
    NAMES,
    U,
    add_case,
    day_text,
    e,
    inr,
    mv,
    pos,
    price_text,
    qty_text,
    rd,
    refused,
    totals,
    write_all,
)

CASES = []
FUND = {"accounts": {"1": "Folio"}, "assets": {"1": "Example Fund"}, "units": {"1": "units"}}
KEPT = f"{DOT}cost and dates kept"


def case(*args, **kwargs):
    add_case(CASES, *args, **kwargs)


# F7 ---------------------------------------------------------------------------------------
F7 = [
    e(1, "BUY", 20240105, 1, 1, 10 * U, 100000),
    e(2, "TRANSFER", 20250601, 1, 1, 10 * U, to_account_id=2),
    e(3, "SELL", 20250701, 2, 1, 10 * U, 150000),
]
F7_MOVES = [
    mv(1, 10 * U, 100000, 0, 100000),
    mv(2, -10 * U, -100000, 0, None, [(20240105, 10 * U, 100000)], account=1),
    mv(2, 10 * U, 100000, 0, None, [(20240105, 10 * U, 100000)], account=2),
]
case(
    "F7",
    "A transfer between accounts keeps the lot's cost and day",
    "Account 1 buys 10 on 5 Jan 2024 for 1,000.00 (100000 paise). On 1 Jun 2025 all 10 move to "
    "account 2: the one lot goes whole, so it takes its whole cost 100000 and keeps its day "
    "(5 Jan 2024). Account 2 sells 10 on 1 Jul 2025 for 1,500.00 (150000): that lot costs 100000, "
    "gain 150000 - 100000 = 50000 (500.00). A transfer is no sale: it makes no gain.",
    F7,
    {
        "positions": {
            "1/1": pos(0, 0, []),
            "2/1": pos(0, 0, [], realised=50000),
        },
        "moves": [
            *F7_MOVES,
            mv(3, -10 * U, -100000, 50000, 150000, [(20240105, 10 * U, 100000)], account=2),
        ],
        "totals": totals(realised=50000),
    },
    names=NAMES,
    words={
        2: f"From 5 Jan 2024: 10 at {price_text(1_000_000)}{KEPT}",
        3: f"From 5 Jan 2024: 10 at {price_text(1_000_000)}{DOT}gain {inr(50000)}",
    },
)
case(
    "F7b",
    "A lot moved to another account is still held for more than a year from its first day",
    "The same entries up to the transfer (as of 1 Jun 2025). The lot is account 2's now, dated "
    "5 Jan 2024. Its first anniversary was 5 Jan 2025, and 1 Jun 2025 is after it: all 10 units "
    "have been held for more than a year. Account 1 holds nothing.",
    F7[:2],
    {
        "positions": {
            "1/1": pos(0, 0, [], over_year=0),
            "2/1": pos(10 * U, 100000, [(20240105, 10 * U, 100000)], over_year=10 * U),
        },
        "moves": F7_MOVES,
        "totals": totals(),
    },
    names=NAMES,
    as_of=20250601,
    over_year_on=20250601,
)
case(
    "F7c",
    "On the first anniversary itself a lot is not yet held for more than a year",
    "The same position looked at on 5 Jan 2025, the first anniversary of the 5 Jan 2024 purchase: "
    "it has been held for exactly a year, not more than a year, so 0 units count.",
    F7[:2],
    {
        "positions": {
            "1/1": pos(0, 0, [], over_year=0),
            "2/1": pos(10 * U, 100000, [(20240105, 10 * U, 100000)], over_year=0),
        },
        "moves": F7_MOVES,
        "totals": totals(),
    },
    names=NAMES,
    as_of=20250601,
    over_year_on=20250105,
)

# C1, C9 ----------------------------------------------------------------------------------
C1 = [
    e(1, "BUY", 20230501, 1, 1, 10 * U, 1_000_000),
    e(2, "SPLIT", 20250310, None, 1, ratio_num=5, ratio_den=1),
]
case(
    "C1",
    "A split keeps the cost and the day: each share becomes 5",
    "Buy 10 on 1 May 2023 for 10,000.00 (1,000,000 paise). On 10 Mar 2025 each share becomes 5: "
    "10 x 5 = 50 units. The lot keeps its cost 1,000,000 and its day, so a unit now costs "
    "1,000,000 / 50 = 20,000 paise (200.00).",
    C1,
    {
        "positions": {"1/1": pos(50 * U, 1_000_000, [(20230501, 50 * U, 1_000_000)])},
        "moves": [
            mv(1, 10 * U, 1_000_000, 0, 1_000_000),
            mv(2, 40 * U, 0, 0, None, [(20230501, 50 * U, 1_000_000)], account=1),
        ],
        "totals": totals(),
    },
    names=NAMES,
    decimals={"1": 0},
    words={2: f"Each share becomes 5{DOT}10 become 50{KEPT}"},
)
case(
    "C9",
    "A sale after a split takes its cost at the new unit cost",
    "After C1 the lot is 50 units costing 1,000,000. A sale of 20 on 1 Apr 2025 for 4,500.00 "
    "(450000) takes 1,000,000 x 20/50 = 400,000 of the cost (200.00 a unit); gain 450000 - "
    "400000 = 50000 (500.00). Left: 30 units costing 600,000, still dated 1 May 2023.",
    [*C1, e(3, "SELL", 20250401, 1, 1, 20 * U, 450000)],
    {
        "positions": {
            "1/1": pos(30 * U, 600_000, [(20230501, 30 * U, 600_000)], realised=50000),
        },
        "moves": [
            mv(1, 10 * U, 1_000_000, 0, 1_000_000),
            mv(2, 40 * U, 0, 0, None, [(20230501, 50 * U, 1_000_000)], account=1),
            mv(3, -20 * U, -400_000, 50000, 450000, [(20230501, 20 * U, 400_000)]),
        ],
        "totals": totals(realised=50000),
    },
    names=NAMES,
    decimals={"1": 0},
    words={3: f"From 1 May 2023: 20 at {price_text(2_000_000)}{DOT}gain {inr(50000)}"},
)

# C2 ---------------------------------------------------------------------------------------
case(
    "C2",
    "A consolidation that would leave part of a share is refused",
    "Buy 15 shares. A split of 1 new for every 10 old would leave 15 x 1/10 = 1.5 shares, and "
    "shares are whole numbers (0 decimals). The split is entry 2 and is the one named, with the "
    "account it fails in.",
    [
        e(1, "BUY", 20250110, 1, 1, 15 * U, 150000),
        e(2, "SPLIT", 20250312, None, 1, ratio_num=1, ratio_den=10),
    ],
    refused(
        2,
        "fraction",
        "This split would leave 1.5 shares in Zerodha. Record the sale of the fraction first, "
        "then the split.",
        kind="SPLIT",
        day=20250312,
        account_id=1,
        asset_id=1,
        have_qty_u=15 * U,
        left_qty_u=1_500_000,
        ratio_num=1,
        ratio_den=10,
    ),
    names=NAMES,
    decimals={"1": 0},
)
case(
    "C2b",
    "The same consolidation is fine for fund units, which have four decimals",
    "15 units, 1 new for every 10 old: 1.5 units, which four decimals can hold (1.5000). The "
    "lot keeps its cost 150000 and its day.",
    [
        e(1, "BUY", 20250106, 1, 1, 15 * U, 150000),
        e(2, "SPLIT", 20250312, None, 1, ratio_num=1, ratio_den=10),
    ],
    {
        "positions": {"1/1": pos(1_500_000, 150000, [(20250106, 1_500_000, 150000)])},
        "moves": [
            mv(1, 15 * U, 150000, 0, 150000),
            mv(2, -13_500_000, 0, 0, None, [(20250106, 1_500_000, 150000)], account=1),
        ],
        "totals": totals(),
    },
    names=FUND,
    decimals={"1": 4},
    words={2: f"1 new for every 10 old{DOT}15 become 1.5{KEPT}"},
)
case(
    "C2d",
    "Two lots of 5 consolidated 1 for 10 are two lots of half a share that make one share",
    "Buy 5 for 500.00 (50000) on 6 Jan 2025 and 5 for 600.00 (60000) on 10 Feb. A split of 1 new "
    "for every 10 old: 10 x 1/10 = 1 share, whole. The lots become 0.5 and 0.5 (500000 each) "
    "with their cost and day. Selling the 1 share for 200.00 (20000) takes both: cost 50000 + "
    "60000 = 110000, loss 20000 - 110000 = -90000 (900.00).",
    [
        e(1, "BUY", 20250106, 1, 1, 5 * U, 50000),
        e(2, "BUY", 20250210, 1, 1, 5 * U, 60000),
        e(3, "SPLIT", 20250310, None, 1, ratio_num=1, ratio_den=10),
        e(4, "SELL", 20250401, 1, 1, 1 * U, 20000),
    ],
    {
        "positions": {"1/1": pos(0, 0, [], realised=-90000)},
        "moves": [
            mv(1, 5 * U, 50000, 0, 50000),
            mv(2, 5 * U, 60000, 0, 60000),
            mv(
                3,
                -9 * U,
                0,
                0,
                None,
                [(20250106, 500_000, 50000), (20250210, 500_000, 60000)],
                account=1,
            ),
            mv(
                4,
                -U,
                -110000,
                -90000,
                20000,
                [(20250106, 500_000, 50000), (20250210, 500_000, 60000)],
            ),
        ],
        "totals": totals(realised=-90000),
    },
    names=NAMES,
    decimals={"1": 0},
    words={
        3: f"1 new for every 10 old{DOT}10 become 1{KEPT}",
        4: f"From 6 Jan 2025: 0.5 at {price_text(10_000_000)}; from 10 Feb 2025: 0.5 at "
        f"{price_text(12_000_000)}{DOT}loss {inr(90000)}",
    },
)
case(
    "C2e",
    "Lots that do not divide are rounded on the running total, so the share count is exact",
    "Buy 7 (70000) and 8 (96000). A split of 1 new for every 3 old: 15 x 1/3 = 5 shares, whole. "
    "The first lot alone would be 7/3 = 2.333333 (2,333,333 in millionths, rounded); the two "
    "together are exactly 5,000,000, so the second lot is 5,000,000 - 2,333,333 = 2,666,667. "
    "Lots 2,333,333 + 2,666,667 = 5,000,000, cost kept: 70000 and 96000.",
    [
        e(1, "BUY", 20250106, 1, 1, 7 * U, 70000),
        e(2, "BUY", 20250210, 1, 1, 8 * U, 96000),
        e(3, "SPLIT", 20250310, None, 1, ratio_num=1, ratio_den=3),
    ],
    {
        "positions": {
            "1/1": pos(
                5 * U,
                166000,
                [(20250106, 2_333_333, 70000), (20250210, 2_666_667, 96000)],
            )
        },
        "moves": [
            mv(1, 7 * U, 70000, 0, 70000),
            mv(2, 8 * U, 96000, 0, 96000),
            mv(
                3,
                -10 * U,
                0,
                0,
                None,
                [(20250106, 2_333_333, 70000), (20250210, 2_666_667, 96000)],
                account=1,
            ),
        ],
        "totals": totals(),
    },
    names=NAMES,
    decimals={"1": 0},
    words={3: f"1 new for every 3 old{DOT}15 become 5{KEPT}"},
)

THIRDS = [(20250106, 333_333, 10000), (20250107, 333_334, 11000), (20250108, 333_333, 12000)]
case(
    "C2f",
    "Three lots that each round down would make less than a whole share, so the running total is used",
    "Three lots of 1 share cost 100.00, 110.00 and 120.00 (10000, 11000, 12000). One new share "
    "for every 3 old: 3 x 1/3 = 1 share, whole. Each lot alone would be 1/3 = 0.333333 "
    "(333,333 millionths), and three of those make 999,999, not 1,000,000. Rounding the running "
    "total instead: 1,000,000 x 1/3 = 333,333 after the first lot, 2,000,000 x 1/3 = 666,667 "
    "after the second (so the second takes 333,334), 3,000,000 x 1/3 = 1,000,000 after the "
    "third (333,333). The lots add up to exactly 1 share and keep their cost and day. Selling "
    "that share for 400.00 (40000) takes all three: cost 10000 + 11000 + 12000 = 33000, gain "
    "40000 - 33000 = 7000.",
    [
        e(1, "BUY", 20250106, 1, 1, U, 10000),
        e(2, "BUY", 20250107, 1, 1, U, 11000),
        e(3, "BUY", 20250108, 1, 1, U, 12000),
        e(4, "SPLIT", 20250310, None, 1, ratio_num=1, ratio_den=3),
        e(5, "SELL", 20250401, 1, 1, U, 40000),
    ],
    {
        "positions": {"1/1": pos(0, 0, [], realised=7000)},
        "moves": [
            mv(1, U, 10000, 0, 10000),
            mv(2, U, 11000, 0, 11000),
            mv(3, U, 12000, 0, 12000),
            mv(4, -2 * U, 0, 0, None, THIRDS, account=1),
            mv(5, -U, -33000, 7000, 40000, THIRDS),
        ],
        "totals": totals(realised=7000),
    },
    names=NAMES,
    decimals={"1": 0},
    words={
        4: f"1 new for every 3 old{DOT}3 become 1{KEPT}",
        5: "; ".join(
            f"{'from' if n else 'From'} {day_text(day)}: {qty_text(qty)} at "
            f"{price_text(rd(cost * 10**8, qty))}"
            for n, (day, qty, cost) in enumerate(THIRDS)
        )
        + f"{DOT}gain {inr(7000)}",
    },
)

# C6 ---------------------------------------------------------------------------------------
C6 = [
    e(1, "BUY", 20250106, 1, 1, 10 * U, 100000),
    e(2, "BUY", 20250107, 2, 1, 4 * U, 44000),
]
case(
    "C6",
    "A split without an account applies to every account that holds the asset",
    "Account 1 holds 10 (100000), account 2 holds 4 (44000). Each share becomes 2 on 10 Mar "
    "2025, for every account: 20 and 8 units, each lot keeping its cost and day. One move for "
    "each account, the lower account number first.",
    [*C6, e(3, "SPLIT", 20250310, None, 1, ratio_num=2, ratio_den=1)],
    {
        "positions": {
            "1/1": pos(20 * U, 100000, [(20250106, 20 * U, 100000)]),
            "2/1": pos(8 * U, 44000, [(20250107, 8 * U, 44000)]),
        },
        "moves": [
            mv(1, 10 * U, 100000, 0, 100000),
            mv(2, 4 * U, 44000, 0, 44000),
            mv(3, 10 * U, 0, 0, None, [(20250106, 20 * U, 100000)], account=1),
            mv(3, 4 * U, 0, 0, None, [(20250107, 8 * U, 44000)], account=2),
        ],
        "totals": totals(),
    },
    names=NAMES,
    decimals={"1": 0},
    words={3: f"Each share becomes 2{DOT}10 become 20{KEPT}"},
)
case(
    "C6b",
    "A split for one account leaves the others alone",
    "The same holdings, but the split names account 1: it goes to 20 units, account 2 keeps its "
    "4 units and 44000.",
    [*C6, e(3, "SPLIT", 20250310, 1, 1, ratio_num=2, ratio_den=1)],
    {
        "positions": {
            "1/1": pos(20 * U, 100000, [(20250106, 20 * U, 100000)]),
            "2/1": pos(4 * U, 44000, [(20250107, 4 * U, 44000)]),
        },
        "moves": [
            mv(1, 10 * U, 100000, 0, 100000),
            mv(2, 4 * U, 44000, 0, 44000),
            mv(3, 10 * U, 0, 0, None, [(20250106, 20 * U, 100000)], account=1),
        ],
        "totals": totals(),
    },
    names=NAMES,
    decimals={"1": 0},
)

case(
    "C6c",
    "A split for every account is refused for the account that cannot take it",
    "Account 1 holds 10 shares, account 2 holds 3. One new share for every 5 old: account 1 "
    "would hold 10 x 1/5 = 2, which is whole, but account 2 would hold 3 x 1/5 = 0.6. The split "
    "is refused for the account it fails in (Account B), and no account changes.",
    [
        e(1, "BUY", 20250106, 1, 1, 10 * U, 100000),
        e(2, "BUY", 20250107, 2, 1, 3 * U, 33000),
        e(3, "SPLIT", 20250310, None, 1, ratio_num=1, ratio_den=5),
    ],
    refused(
        3,
        "fraction",
        "This split would leave 0.6 shares in Account B. Record the sale of the fraction first, "
        "then the split.",
        kind="SPLIT",
        day=20250310,
        account_id=2,
        asset_id=1,
        have_qty_u=3 * U,
        left_qty_u=600_000,
        ratio_num=1,
        ratio_den=5,
    ),
    names=NAMES,
    decimals={"1": 0},
)

# C7 ---------------------------------------------------------------------------------------
NOT_HELD = "{who} on 12 Mar 2025, so a {what} cannot be saved. Check the date, or add the missing purchase first."
case(
    "C7a",
    "A split in an account that holds none of the asset is refused",
    "Only account 2 bought. A split named for account 1 on 12 Mar 2025 has nothing to split.",
    [
        e(1, "BUY", 20250110, 2, 1, 10 * U, 100000),
        e(2, "SPLIT", 20250312, 1, 1, ratio_num=2, ratio_den=1),
    ],
    refused(
        2,
        "not_held",
        NOT_HELD.format(who="Zerodha holds no RELIANCE", what="split"),
        kind="SPLIT",
        day=20250312,
        account_id=1,
        asset_id=1,
    ),
    names=NAMES,
)
case(
    "C7b",
    "A split for every account is refused when no account holds the asset",
    "Nothing was bought, so a split for every account has nothing to split.",
    [e(1, "SPLIT", 20250312, None, 1, ratio_num=2, ratio_den=1)],
    refused(
        1,
        "not_held",
        NOT_HELD.format(who="No account holds RELIANCE", what="split"),
        kind="SPLIT",
        day=20250312,
        account_id=None,
        asset_id=1,
    ),
    names=NAMES,
)
case(
    "C7c",
    "A bonus in an account that holds none of the asset is refused",
    "Only account 2 bought; account 1 cannot receive bonus shares on shares it never held.",
    [
        e(1, "BUY", 20250110, 2, 1, 10 * U, 100000),
        e(2, "BONUS", 20250312, 1, 1, 5 * U),
    ],
    refused(
        2,
        "not_held",
        NOT_HELD.format(who="Zerodha holds no RELIANCE", what="bonus"),
        kind="BONUS",
        day=20250312,
        account_id=1,
        asset_id=1,
    ),
    names=NAMES,
)
case(
    "C7d",
    "A bonus dated after the account sold out is refused",
    "Buy 10 on 10 Jan, sell all 10 on 1 Mar: on 12 Mar the account holds none, so a bonus dated "
    "12 Mar is refused (the sentence asks the owner to check the date).",
    [
        e(1, "BUY", 20250110, 1, 1, 10 * U, 100000),
        e(2, "SELL", 20250301, 1, 1, 10 * U, 120000),
        e(3, "BONUS", 20250312, 1, 1, 5 * U),
    ],
    refused(
        3,
        "not_held",
        NOT_HELD.format(who="Zerodha holds no RELIANCE", what="bonus"),
        kind="BONUS",
        day=20250312,
        account_id=1,
        asset_id=1,
    ),
    names=NAMES,
)

# C3, C4, C5, C8 ---------------------------------------------------------------------------
C3 = [
    e(1, "BUY", 20230501, 1, 1, 10 * U, 100000),
    e(2, "BONUS", 20250915, 1, 1, 10 * U),
]
case(
    "C3",
    "Bonus shares are a new lot at nil cost, dated the allotment",
    "Buy 10 on 1 May 2023 for 1,000.00 (100000). Bonus of 10 allotted on 15 Sep 2025: a new "
    "lot of 10 costing 0, dated 15 Sep 2025. 20 units, cost still 100000.",
    C3,
    {
        "positions": {
            "1/1": pos(20 * U, 100000, [(20230501, 10 * U, 100000), (20250915, 10 * U, 0)])
        },
        "moves": [mv(1, 10 * U, 100000, 0, 100000), mv(2, 10 * U, 0, 0, None)],
        "totals": totals(),
    },
    names=NAMES,
    words={2: "Bonus 10 at no cost"},
)
case(
    "C5",
    "A sale after a bonus takes the older lot first, then the nil-cost bonus lot",
    "After C3, sell 15 on 20 Oct 2025 for 1,050.00 (105000). FIFO: the whole 1 May 2023 lot "
    "(10 units, 100000), then 5 of the bonus lot, whose cost is 0 x 5/10 = 0. Cost 100000, gain "
    "105000 - 100000 = 5000 (50.00). Left: 5 bonus units at no cost.",
    [*C3, e(3, "SELL", 20251020, 1, 1, 15 * U, 105000)],
    {
        "positions": {"1/1": pos(5 * U, 0, [(20250915, 5 * U, 0)], realised=5000)},
        "moves": [
            mv(1, 10 * U, 100000, 0, 100000),
            mv(2, 10 * U, 0, 0, None),
            mv(
                3,
                -15 * U,
                -100000,
                5000,
                105000,
                [(20230501, 10 * U, 100000), (20250915, 5 * U, 0)],
            ),
        ],
        "totals": totals(realised=5000),
    },
    names=NAMES,
    words={
        3: f"From 1 May 2023: 10 at {price_text(1_000_000)}; from 15 Sep 2025: 5 at "
        f"{price_text(0)}{DOT}gain {inr(5000)}"
    },
)
case(
    "C4",
    "A rights issue is a purchase: a new lot at the price paid",
    "Buy 10 (100000). Rights of 2 at 80.00 on 1 Jun 2025 cost 160.00 (16000): a new lot of 2 "
    "costing 16000, dated 1 Jun 2025. 12 units, cost 116000.",
    [
        e(1, "BUY", 20230501, 1, 1, 10 * U, 100000),
        e(2, "RIGHTS", 20250601, 1, 1, 2 * U, 16000),
    ],
    {
        "positions": {
            "1/1": pos(12 * U, 116000, [(20230501, 10 * U, 100000), (20250601, 2 * U, 16000)])
        },
        "moves": [mv(1, 10 * U, 100000, 0, 100000), mv(2, 2 * U, 16000, 0, 16000)],
        "totals": totals(),
    },
    names=NAMES,
    words={2: f"Cost {inr(16000)}"},
)
case(
    "C8",
    "On the day of a split the split is applied first, whatever was typed first",
    "Buy 10 on 6 Jan (100000). On 10 Mar a purchase of 4 for 600.00 (60000) is typed before the "
    "split (each share becomes 2) that takes effect that morning. The split goes first: 10 -> "
    "20, then the 4 are bought: 24 units, cost 160000. Applying the split after the purchase "
    "would double the 4 as well (28).",
    [
        e(1, "BUY", 20250106, 1, 1, 10 * U, 100000),
        e(2, "BUY", 20250310, 1, 1, 4 * U, 60000),
        e(3, "SPLIT", 20250310, None, 1, ratio_num=2, ratio_den=1),
    ],
    {
        "positions": {
            "1/1": pos(24 * U, 160000, [(20250106, 20 * U, 100000), (20250310, 4 * U, 60000)])
        },
        "moves": [
            mv(1, 10 * U, 100000, 0, 100000),
            mv(3, 10 * U, 0, 0, None, [(20250106, 20 * U, 100000)], account=1),
            mv(2, 4 * U, 60000, 0, 60000),
        ],
        "totals": totals(),
    },
    names=NAMES,
    decimals={"1": 0},
)

# TF ---------------------------------------------------------------------------------------
case(
    "TF1",
    "A lot moved in parts gives the new account its share of the cost, rounded as a sale is",
    "Account 1 buys 3 for 100.00 (10000) on 6 Jan 2025. Moving 1 unit takes 10000 x 1/3 = 3333.33 "
    "-> 3333 (account 1 keeps 2 units and 6667). Moving 1 more takes 6667 x 1/2 = 3333.5 -> 3334 "
    "(half away from zero; account 1 keeps 1 unit and 3333). Account 2 now holds two lots dated "
    "6 Jan, 3333 and 3334, in the order they arrived. Selling both for 90.00 (9000) uses 3333 + "
    "3334 = 6667; gain 9000 - 6667 = 2333. Cost is never lost: 3333 + 6667 = 10000.",
    [
        e(1, "BUY", 20250106, 1, 1, 3 * U, 10000),
        e(2, "TRANSFER", 20250210, 1, 1, U, to_account_id=2),
        e(3, "TRANSFER", 20250211, 1, 1, U, to_account_id=2),
        e(4, "SELL", 20250310, 2, 1, 2 * U, 9000),
    ],
    {
        "positions": {
            "1/1": pos(U, 3333, [(20250106, U, 3333)]),
            "2/1": pos(0, 0, [], realised=2333),
        },
        "moves": [
            mv(1, 3 * U, 10000, 0, 10000),
            mv(2, -U, -3333, 0, None, [(20250106, U, 3333)], account=1),
            mv(2, U, 3333, 0, None, [(20250106, U, 3333)], account=2),
            mv(3, -U, -3334, 0, None, [(20250106, U, 3334)], account=1),
            mv(3, U, 3334, 0, None, [(20250106, U, 3334)], account=2),
            mv(
                4,
                -2 * U,
                -6667,
                2333,
                9000,
                [(20250106, U, 3333), (20250106, U, 3334)],
                account=2,
            ),
        ],
        "totals": totals(realised=2333),
    },
    names=NAMES,
    words={
        2: f"From 6 Jan 2025: 1 at {price_text(333_300)}{KEPT}",
        4: f"From 6 Jan 2025: 1 at {price_text(333_300)}; from 6 Jan 2025: 1 at "
        f"{price_text(333_400)}{DOT}gain {inr(2333)}",
    },
)
case(
    "TF2",
    "Shares brought in from outside open a lot at the cost entered, dated their first purchase",
    "Account 1 is given 10 shares from outside on 1 Apr 2026: cost 1,000.00 (100000), first "
    "bought 15 Aug 2023, worth 1,500.00 that day (150000, which is what counts as money in; the "
    "lot's cost stays 100000). Selling 4 on 15 Sep 2026 for 700.00 (70000) takes 100000 x 4/10 = "
    "40000; gain 70000 - 40000 = 30000 (300.00). Left: 6 units, 60000, still dated 15 Aug 2023.",
    [
        e(
            1,
            "TRANSFER",
            20260401,
            None,
            1,
            10 * U,
            100000,
            to_account_id=1,
            lot_date=20230815,
            value_paise=150000,
        ),
        e(2, "SELL", 20260915, 1, 1, 4 * U, 70000),
    ],
    {
        "positions": {
            "1/1": pos(6 * U, 60000, [(20230815, 6 * U, 60000)], realised=30000),
        },
        "moves": [
            mv(1, 10 * U, 100000, 0, 150000, account=1),
            mv(2, -4 * U, -40000, 30000, 70000, [(20230815, 4 * U, 40000)]),
        ],
        "totals": totals(realised=30000),
    },
    names=NAMES,
    words={
        1: f"Cost {inr(100000)}{DOT}bought 15 Aug 2023{DOT}valued at {inr(150000)}",
        2: f"From 15 Aug 2023: 4 at {price_text(1_000_000)}{DOT}gain {inr(30000)}",
    },
)
case(
    "TF2b",
    "Without a first-purchase date or a value, the lot is dated the transfer and worth its cost",
    "10 shares come in from outside on 1 Apr 2026 at a cost of 1,000.00 (100000), with no "
    "original date and no value: the lot is dated 1 Apr 2026 and the money counted in is its "
    "cost, 100000.",
    [e(1, "TRANSFER", 20260401, None, 1, 10 * U, 100000, to_account_id=1)],
    {
        "positions": {"1/1": pos(10 * U, 100000, [(20260401, 10 * U, 100000)])},
        "moves": [mv(1, 10 * U, 100000, 0, 100000, account=1)],
        "totals": totals(),
    },
    names=NAMES,
    words={1: f"Cost {inr(100000)}"},
)
case(
    "TF8",
    "An opening lot with an earlier first-purchase date is sold before a lot bought after it",
    "Account 1 buys 5 on 1 Mar 2025 (50000). On 1 Apr 2025 5 more come in from outside at a "
    "cost of 400.00 (40000), first bought 15 Jan 2024. FIFO goes by the day a lot was bought, so "
    "a sale of 5 on 15 Apr 2025 for 700.00 (70000) takes the 15 Jan 2024 lot: cost 40000, gain "
    "30000. The 1 Mar lot is left whole.",
    [
        e(1, "BUY", 20250301, 1, 1, 5 * U, 50000),
        e(2, "TRANSFER", 20250401, None, 1, 5 * U, 40000, to_account_id=1, lot_date=20240115),
        e(3, "SELL", 20250415, 1, 1, 5 * U, 70000),
    ],
    {
        "positions": {"1/1": pos(5 * U, 50000, [(20250301, 5 * U, 50000)], realised=30000)},
        "moves": [
            mv(1, 5 * U, 50000, 0, 50000),
            mv(2, 5 * U, 40000, 0, 40000, account=1),
            mv(3, -5 * U, -40000, 30000, 70000, [(20240115, 5 * U, 40000)]),
        ],
        "totals": totals(realised=30000),
    },
    names=NAMES,
    words={3: f"From 15 Jan 2024: 5 at {price_text(800_000)}{DOT}gain {inr(30000)}"},
)
TF3 = [
    e(1, "BUY", 20250110, 1, 1, 10 * U, 100000),
    e(2, "BUY", 20250210, 1, 1, 10 * U, 120000),
]
TF3_USED = [(20250110, 10 * U, 100000), (20250210, 5 * U, 60000)]
case(
    "TF3",
    "Shares taken out to outside leave at their cost, with no gain or loss",
    "Buy 10 (100000) on 10 Jan and 10 (120000) on 10 Feb 2025. On 10 Mar 15 shares leave the "
    "tab: the whole 10 Jan lot (100000) and 5 of the 10 Feb lot (120000 x 5/10 = 60000). Cost "
    "160000 leaves with them; there is no gain or loss, and with no value entered the money "
    "counted out is that cost. Left: 5 units, 60000.",
    [*TF3, e(3, "TRANSFER", 20250310, 1, 1, 15 * U)],
    {
        "positions": {"1/1": pos(5 * U, 60000, [(20250210, 5 * U, 60000)])},
        "moves": [
            mv(1, 10 * U, 100000, 0, 100000),
            mv(2, 10 * U, 120000, 0, 120000),
            mv(3, -15 * U, -160000, 0, 160000, TF3_USED),
        ],
        "totals": totals(),
    },
    names=NAMES,
    words={
        3: f"From 10 Jan 2025: 10 at {price_text(1_000_000)}; from 10 Feb 2025: 5 at "
        f"{price_text(1_200_000)}{DOT}left at cost, no gain or loss"
    },
)
case(
    "TF3b",
    "A value entered for shares taken out changes the money counted out, not the gain",
    "The same transfer out, entered with a value of 1,950.00 (195000): that is the money counted "
    "out, but the lots still leave at their cost 160000 and the gain stays 0.",
    [*TF3, e(3, "TRANSFER", 20250310, 1, 1, 15 * U, value_paise=195000)],
    {
        "positions": {"1/1": pos(5 * U, 60000, [(20250210, 5 * U, 60000)])},
        "moves": [
            mv(1, 10 * U, 100000, 0, 100000),
            mv(2, 10 * U, 120000, 0, 120000),
            mv(3, -15 * U, -160000, 0, 195000, TF3_USED),
        ],
        "totals": totals(),
    },
    names=NAMES,
)
case(
    "TF4",
    "Moving more than the account holds is refused, in words",
    "Account 1 holds 10 on 12 Mar 2025; a transfer of 12 is 2 short. The transfer is entry 2.",
    [
        e(1, "BUY", 20250110, 1, 1, 10 * U, 100000),
        e(2, "TRANSFER", 20250312, 1, 1, 12 * U, to_account_id=2),
    ],
    refused(
        2,
        "short",
        "Zerodha holds 10 RELIANCE on 12 Mar 2025, so a transfer of 12 cannot be saved. "
        "Add the missing purchase first.",
        kind="TRANSFER",
        day=20250312,
        account_id=1,
        asset_id=1,
        have_qty_u=10 * U,
        want_qty_u=12 * U,
    ),
    names=NAMES,
)
case(
    "TF5",
    "On one day a purchase, then the move, then the sale of the moved shares",
    "On 10 Mar 2025, typed in the opposite order: sell 5 in account 2 (600.00, 60000), move 10 "
    "from account 1 to 2, buy 10 in account 1 (1,000.00, 100000). The day applies the purchase, "
    "then the move, then the sale, so it stands: account 2 holds the 10 at 100000 and the sale "
    "takes 100000 x 5/10 = 50000; gain 60000 - 50000 = 10000. Account 2 is left with 5 units "
    "and 50000.",
    [
        e(1, "SELL", 20250310, 2, 1, 5 * U, 60000),
        e(2, "TRANSFER", 20250310, 1, 1, 10 * U, to_account_id=2),
        e(3, "BUY", 20250310, 1, 1, 10 * U, 100000),
    ],
    {
        "positions": {
            "1/1": pos(0, 0, []),
            "2/1": pos(5 * U, 50000, [(20250310, 5 * U, 50000)], realised=10000),
        },
        "moves": [
            mv(3, 10 * U, 100000, 0, 100000),
            mv(2, -10 * U, -100000, 0, None, [(20250310, 10 * U, 100000)], account=1),
            mv(2, 10 * U, 100000, 0, None, [(20250310, 10 * U, 100000)], account=2),
            mv(1, -5 * U, -50000, 10000, 60000, [(20250310, 5 * U, 50000)]),
        ],
        "totals": totals(realised=10000),
    },
    names=NAMES,
)
case(
    "TF6",
    "A moved lot takes its place in the new account by the day it was bought",
    "Account 1 buys 2 on 6 Jan 2025 (20000); account 2 buys 2 on 1 Mar 2025 (30000). On 1 Apr "
    "the 6 Jan lot moves to account 2, where it goes ahead of the 1 Mar lot because it is older. "
    "A sale of 1 on 15 Apr for 180.00 (18000) takes the 6 Jan lot: 20000 x 1/2 = 10000, gain "
    "8000. Left in account 2: 1 unit at 10000 (6 Jan) and 2 at 30000 (1 Mar).",
    [
        e(1, "BUY", 20250106, 1, 1, 2 * U, 20000),
        e(2, "BUY", 20250301, 2, 1, 2 * U, 30000),
        e(3, "TRANSFER", 20250401, 1, 1, 2 * U, to_account_id=2),
        e(4, "SELL", 20250415, 2, 1, U, 18000),
    ],
    {
        "positions": {
            "1/1": pos(0, 0, []),
            "2/1": pos(
                3 * U,
                40000,
                [(20250106, U, 10000), (20250301, 2 * U, 30000)],
                realised=8000,
            ),
        },
        "moves": [
            mv(1, 2 * U, 20000, 0, 20000),
            mv(2, 2 * U, 30000, 0, 30000),
            mv(3, -2 * U, -20000, 0, None, [(20250106, 2 * U, 20000)], account=1),
            mv(3, 2 * U, 20000, 0, None, [(20250106, 2 * U, 20000)], account=2),
            mv(4, -U, -10000, 8000, 18000, [(20250106, U, 10000)]),
        ],
        "totals": totals(realised=8000),
    },
    names=NAMES,
    words={4: f"From 6 Jan 2025: 1 at {price_text(1_000_000)}{DOT}gain {inr(8000)}"},
)

ABOUT = (
    "Golden vectors for splits, bonus shares, rights and transfers (PA2: F7, C1-C9, TF1-TF8). The "
    "numbers are derived by hand in each case's 'why'; make_pa2.py writes this file, imports "
    "nothing from sudarshan, and the tests hold the engine and a naive second implementation to "
    "it. Synthetic data only. A case's 'decimals' says how many decimals a quantity of the asset "
    "may have (shares 0) and 'over_year_on' asks each position for the units held for more than a "
    "year on that day."
)


def build() -> dict:
    """The file name and contents of every vector file this generator writes."""
    return {"actions.json": {"about": ABOUT, "names": NAMES, "cases": CASES}}


def main() -> None:
    write_all(build(), Path(__file__).parent)


if __name__ == "__main__":
    main()
