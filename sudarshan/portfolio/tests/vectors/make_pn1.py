"""Generator of the PN1 golden vectors: income.json, yield on cost and the income by year (M7.29).

The twelve months are the twelve calendar months that end with the valuation day's. The yield on
cost is the income of those months that the lots *still held* earned, on what those lots cost, as
a percentage with four decimals, half away from zero, written with a plain minus and never
-0.0000. A dividend or interest paid on an asset is shared among the lots held that day by their
units; what a sale takes of a lot takes its share of the income with it (and of its cost, first
in first out); a transfer between accounts moves the income with the units; a split changes
units, not income. Income that went with units since sold, interest on no asset, and income
paid before any units were held are not in the yield, though they are in the twelve months'
total. A lot bought after a dividend counts at its cost, with none of the dividend. Nothing held
at a cost gives no yield (null), never 0. The income by financial year (April to March) has one
row per year with any income, oldest first, labelled "FY 2026-27". Income dated after the
valuation day counts nowhere.

Every case's ``held_income_paise`` and ``held_cost_paise`` are worked out by hand in its why and
written here as numbers, not computed: the lots, the shares of the income and the cost left after
a sale are done on paper. The generator derives only the twelve months and the years (calendar
arithmetic on the day) and the percentage (Decimal). Like the other generators it imports nothing
from sudarshan.

Quantities are shares times a million (10 shares = 10_000_000); money is paise.

Run: python3 make_pn1.py
"""

from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from portfolio_vector_tools import write_all

CASES = []
SH = 1_000_000  # one share


def buy(i, day, account, asset, shares, paise):
    return {
        "id": i, "seq": i, "kind": "BUY", "trade_date": day, "account_id": account,
        "asset_id": asset, "qty_u": shares * SH, "amount_paise": paise,
    }  # fmt: skip


def sell(i, day, account, asset, shares, paise):
    return {**buy(i, day, account, asset, shares, paise), "kind": "SELL"}


def dividend(i, day, account, asset, paise):
    return {
        "id": i, "seq": i, "kind": "DIVIDEND", "trade_date": day, "account_id": account,
        "asset_id": asset, "amount_paise": paise,
    }  # fmt: skip


def interest(i, day, account, paise, asset=None):
    row = {"id": i, "seq": i, "kind": "INTEREST", "trade_date": day, "account_id": account,
           "amount_paise": paise}  # fmt: skip
    if asset is not None:
        row["asset_id"] = asset
    return row


def transfer(i, day, source, target, asset, shares):
    return {
        "id": i, "seq": i, "kind": "TRANSFER", "trade_date": day, "account_id": source,
        "to_account_id": target, "asset_id": asset, "qty_u": shares * SH,
    }  # fmt: skip


def split(i, day, account, asset, num, den):
    return {
        "id": i, "seq": i, "kind": "SPLIT", "trade_date": day, "account_id": account,
        "asset_id": asset, "ratio_num": num, "ratio_den": den,
    }  # fmt: skip


def fy_of(day):
    return day // 10000 if day // 100 % 100 >= 4 else day // 10000 - 1


def label(fy):
    return f"FY {fy}-{(fy + 1) % 100:02d}"


def percent(numerator, denominator):
    """numerator / denominator as a percentage with four decimals, half away from zero."""
    if denominator == 0:
        return None
    scaled = (Decimal(numerator) * 100 / Decimal(denominator)).quantize(
        Decimal("0.0001"), ROUND_HALF_UP
    )
    text = f"{scaled:.4f}"
    return "0.0000" if text == "-0.0000" else text


def case(ident, title, why, entries, valuation_day, held_income, held_cost):
    """``held_income`` and ``held_cost`` (paise) are the hand-worked income of the twelve months
    on the lots still held, and those lots' cost. The twelve months' total and the years are
    worked out here from the income entries by calendar arithmetic of their own."""
    year, month = valuation_day // 10000, valuation_day // 100 % 100
    first = year * 12 + month - 12  # the month number of the first of the twelve
    last_12m = 0
    years = {}
    for row in entries:
        if row["kind"] not in ("DIVIDEND", "INTEREST") or row["trade_date"] > valuation_day:
            continue
        day, paise = row["trade_date"], row["amount_paise"]
        if day // 10000 * 12 + day // 100 % 100 - 1 >= first:
            last_12m += paise
        years[fy_of(day)] = years.get(fy_of(day), 0) + paise
    CASES.append(
        {
            "id": ident,
            "title": title,
            "why": why,
            "entries": entries,
            "valuation_day": valuation_day,
            "expect": {
                "last_12m_paise": last_12m,
                "held_income_paise": held_income,
                "held_cost_paise": held_cost,
                "yield_on_cost_pct": percent(held_income, held_cost),
                "by_fy": [
                    {"fy": fy, "label": label(fy), "paise": paise}
                    for fy, paise in sorted(years.items())
                    if paise
                ],
            },
        }
    )


TEN = buy(1, 20250110, 1, 1, 10, 1_000_000)  # 10 shares for 10,000.00, held throughout

case(
    "Y1",
    "Twelve months of income on what the holdings cost",
    "10 shares of one stock bought for 10,000.00 (1000000) on 10 Jan 2025 and never sold. Read on "
    "27 Oct 2026 the twelve months are Nov 2025 to Oct 2026. Dividends of 100.00 on 15 Nov 2025, "
    "150.00 on 15 Feb 2026 and 200.00 on 15 Aug 2026 are all earned by the one lot: 450.00 "
    "(45000) on its cost of 10,000.00: 4.5000%. FY 2025-26 has 250.00 (the Nov and Feb credits) "
    "and FY 2026-27 has 200.00.",
    [
        TEN,
        dividend(2, 20251115, 1, 1, 10_000),
        dividend(3, 20260215, 1, 1, 15_000),
        dividend(4, 20260815, 1, 1, 20_000),
    ],
    20261027,
    45_000,
    1_000_000,
)

case(
    "Y1b",
    "Interest on no asset is in the twelve months but not in the yield",
    "The same 10 shares (10,000.00). A dividend of 100.00 on 15 Nov 2025 is earned by the lot; "
    "interest of 300.00 on 1 Jun 2026 is on a bank balance, on no asset, so no lot earned it. "
    "Twelve months: 400.00 (40000), the total of everything received. Yield: the lot's 100.00 "
    "(10000) on 10,000.00: 1.0000%. FY 2025-26 100.00, FY 2026-27 300.00.",
    [TEN, dividend(2, 20251115, 1, 1, 10_000), interest(3, 20260601, 1, 30_000)],
    20261027,
    10_000,
    1_000_000,
)

case(
    "Y2",
    "Income a month too early is in its year but not in the twelve months",
    "8 shares bought for 8,000.00 (800000) on 10 Jan 2025. Read on 27 Oct 2026 the first of the "
    "twelve months is Nov 2025. A dividend of 300.00 on 31 Oct 2025 is a day too early for them "
    "and counts in FY 2025-26 only; 120.00 on 1 Nov 2025 is the first day of the twelve. Twelve "
    "months: 120.00 (12000) on a cost of 8,000.00: 1.5000%. FY 2025-26: 420.00.",
    [
        buy(1, 20250110, 1, 1, 8, 800_000),
        dividend(2, 20251031, 1, 1, 30_000),
        dividend(3, 20251101, 1, 1, 12_000),
    ],
    20261027,
    12_000,
    800_000,
)

case(
    "Y3",
    "Income dated after the valuation day counts nowhere",
    "10 shares (10,000.00). Read on 27 Oct 2026: a dividend of 500.00 dated 28 Oct 2026 (a draft "
    "the owner dated ahead, or a day in the future when the tab is read as of an earlier day) is "
    "in no month and no year, and no lot earned it yet. Only the 80.00 of 1 Oct 2026 counts: "
    "0.8000% on 10,000.00.",
    [TEN, dividend(2, 20261001, 1, 1, 8_000), dividend(3, 20261028, 1, 1, 50_000)],
    20261027,
    8_000,
    1_000_000,
)

case(
    "Y4",
    "Nothing held at a cost gives no yield, not a zero",
    "10 shares bought for 10,000.00, a dividend of 60.00 on 1 Jun 2026, then every share sold on "
    "1 Jul 2026 for 11,000.00. The lots held cost 0: the 60.00 in the twelve months has nothing "
    "to be a yield of, and the figure is null. The totals still stand: twelve months 60.00, "
    "FY 2026-27 60.00.",
    [TEN, dividend(2, 20260601, 1, 1, 6_000), sell(3, 20260701, 1, 1, 10, 1_100_000)],
    20261027,
    0,
    0,
)

case(
    "Y5",
    "The percentage is rounded half away from zero at the fourth decimal (down)",
    "30 shares for 3,000.00 (300000) and income of 1.00 (100): 100 x 100 / 300000 = 0.033333...%, "
    "written 0.0333. Y5b rounds up and Y5c is exactly on a half.",
    [buy(1, 20250110, 1, 1, 30, 300_000), dividend(2, 20260601, 1, 1, 100)],
    20261027,
    100,
    300_000,
)

case(
    "Y5b",
    "The percentage is rounded half away from zero at the fourth decimal (up)",
    "3 shares for 300.00 (30000) and income of 2.00 (200): 0.666666...%, written 0.6667.",
    [buy(1, 20250110, 1, 1, 3, 30_000), dividend(2, 20260601, 1, 1, 200)],
    20261027,
    200,
    30_000,
)

case(
    "Y5c",
    "Exactly on a half the percentage goes away from zero",
    "16 shares for 160.00 (16000) and income of 0.01 (1 paise): 1 x 100 / 16000 = 0.00625% exactly, "
    "which half away from zero writes as 0.0063 (not 0.0062).",
    [buy(1, 20250110, 1, 1, 16, 16_000), dividend(2, 20260601, 1, 1, 1)],
    20261027,
    1,
    16_000,
)

case(
    "Y6",
    "Read in April: the twelve months straddle two financial years",
    "100 shares for 1,00,000.00 (10000000). Read on 10 Apr 2026 the twelve months are May 2025 "
    "to Apr 2026. A dividend of 1,000.00 on 30 Apr 2025 is a month too early. 2,000.00 on 31 May "
    "2025 and 3,000.00 on 31 Mar 2026 are in the twelve months, and so is 4,000.00 on 1 Apr "
    "2026 (the first day of FY 2026-27). Twelve months: 9,000.00 (900000) on 1,00,000.00: "
    "9.0000%. By year: FY 2025-26 has 1,000.00 + 2,000.00 + 3,000.00 = 6,000.00 and FY 2026-27 "
    "has 4,000.00.",
    [
        buy(1, 20250110, 1, 1, 100, 10_000_000),
        dividend(2, 20250430, 1, 1, 100_000),
        dividend(3, 20250531, 1, 1, 200_000),
        dividend(4, 20260331, 1, 1, 300_000),
        dividend(5, 20260401, 1, 1, 400_000),
    ],
    20260410,
    900_000,
    10_000_000,
)

case(
    "Y7",
    "No income at all",
    "A tab with a purchase and no dividend or interest: twelve months 0, yield 0.0000% on its "
    "cost (there is a cost, and the income on it is nothing), and no year row at all.",
    [TEN],
    20261027,
    0,
    1_000_000,
)

case(
    "Y8",
    "Sold 90 of 100: the dividend on the 90 sold is not a yield on the 10 kept",
    "100 shares bought for 10,000.00 (1000000) on 10 Jan 2026. A dividend of Rs 5 a share, "
    "500.00 (50000), on 14 Aug 2026 is paid on all 100. On 1 Oct 2026 90 are sold for 9,900.00: "
    "first in first out, the one lot gives up 90/100 of its cost (900000) and 90/100 of its "
    "income (45000). Left: 10 shares costing 100000 with 5000 of the dividend, which is Rs 5 a "
    "share on Rs 100 a share: 5000 / 100000 = 5.0000%, not the 50% of 50000 on 100000. The twelve "
    "months still say 500.00 was received.",
    [
        buy(1, 20260110, 1, 1, 100, 1_000_000),
        dividend(2, 20260814, 1, 1, 50_000),
        sell(3, 20261001, 1, 1, 90, 990_000),
    ],
    20261027,
    5_000,
    100_000,
)

case(
    "Y9",
    "A dividend payer sold and gold bought: the gold pays nothing",
    "100 shares bought for 10,000.00 on 10 Jan 2026, a dividend of 800.00 (80000) on 14 Aug 2026, "
    "all 100 sold on 1 Oct 2026 for 10,500.00, and 10 g of gold bought for 1,000.00 (100000) on "
    "2 Oct 2026. The only lot held is the gold's: cost 100000, income 0: 0.0000%. The 800.00 is "
    "still in the twelve months' total.",
    [
        buy(1, 20260110, 1, 1, 100, 1_000_000),
        dividend(2, 20260814, 1, 1, 80_000),
        sell(3, 20261001, 1, 1, 100, 1_050_000),
        buy(4, 20261002, 1, 2, 10, 100_000),
    ],
    20261027,
    0,
    100_000,
)

case(
    "Y10",
    "A sale takes the oldest lot first and its income with it",
    "Lot A: 50 shares for 5,000.00 (500000) on 1 Feb 2026. Lot B: 50 shares for 7,000.00 "
    "(700000) on 1 Mar 2026. A dividend of 200.00 (20000), Rs 2 a share, on 14 Aug 2026 on all "
    "100: 10000 to A and 10000 to B. On 1 Oct 2026 70 shares are sold for 9,000.00: all of A and "
    "20 of B. B keeps 30 of its 50: cost 700000 x 30/50 = 420000, income 10000 x 30/50 = 6000. "
    "6000 / 420000 = 1.428571...%, written 1.4286.",
    [
        buy(1, 20260201, 1, 1, 50, 500_000),
        buy(2, 20260301, 1, 1, 50, 700_000),
        dividend(3, 20260814, 1, 1, 20_000),
        sell(4, 20261001, 1, 1, 70, 900_000),
    ],
    20261027,
    6_000,
    420_000,
)

case(
    "Y11",
    "A lot bought after the dividend counts at its cost with none of the income",
    "100 shares bought for 10,000.00 (1000000) on 10 Jan 2026 earn a dividend of 500.00 (50000) "
    "on 14 Aug 2026. Another 100 are bought for 12,000.00 (1200000) on 1 Oct 2026 and earned "
    "none of it. Both lots are still held: cost 2200000, income 50000: 50000 / 2200000 = "
    "2.272727...%, written 2.2727.",
    [
        buy(1, 20260110, 1, 1, 100, 1_000_000),
        dividend(2, 20260814, 1, 1, 50_000),
        buy(3, 20261001, 1, 1, 100, 1_200_000),
    ],
    20261027,
    50_000,
    2_200_000,
)

case(
    "Y12",
    "A transfer between accounts moves the income with the units",
    "100 shares in account 1 bought for 10,000.00 (1000000) on 10 Jan 2026; a dividend of 500.00 "
    "(50000) on 14 Aug 2026; on 1 Sep 2026 40 shares move to account 2. Account 1 keeps 60: cost "
    "600000, income 30000. Account 2 holds 40: cost 400000, income 20000. Together 50000 on "
    "1000000: 5.0000%.",
    [
        buy(1, 20260110, 1, 1, 100, 1_000_000),
        dividend(2, 20260814, 1, 1, 50_000),
        transfer(3, 20260901, 1, 2, 1, 40),
    ],
    20261027,
    50_000,
    1_000_000,
)

case(
    "Y13",
    "A split changes the units, not the income or the cost",
    "100 shares for 10,000.00 (1000000), a dividend of 500.00 (50000) on 14 Aug 2026, then a "
    "2-for-1 split on 1 Oct 2026: 200 shares at the same cost, and the lot's income unchanged: "
    "50000 / 1000000 = 5.0000%.",
    [
        buy(1, 20260110, 1, 1, 100, 1_000_000),
        dividend(2, 20260814, 1, 1, 50_000),
        split(3, 20261001, 1, 1, 2, 1),
    ],
    20261027,
    50_000,
    1_000_000,
)

case(
    "Y14",
    "Income shared among lots in whole paise, the leftover to the largest remainder",
    "Three lots of 1 share (cost 100.00, 10000, each) on 10, 11 and 12 Jan 2026. A dividend of "
    "1.00 (100 paise) on 14 Aug 2026 is shared 33, 33, 33 with 1 paise left; the three remainders "
    "tie, so the oldest lot takes it: 34, 33, 33. On 1 Oct 2026 1 share is sold for 120.00: the "
    "oldest lot, and its 34 with it. Held: two lots, income 33 + 33 = 66, cost 20000: "
    "66 x 100 / 20000 = 0.33%, written 0.3300. (Had the leftover gone to the newest lot it "
    "would be 67 and 0.3350.)",
    [
        buy(1, 20260110, 1, 1, 1, 10_000),
        buy(2, 20260111, 1, 1, 1, 10_000),
        buy(3, 20260112, 1, 1, 1, 10_000),
        dividend(4, 20260814, 1, 1, 100),
        sell(5, 20261001, 1, 1, 1, 12_000),
    ],
    20261027,
    66,
    20_000,
)

case(
    "Y15",
    "Income dated on the valuation day itself counts",
    "10 shares (10,000.00). Read on 27 Oct 2026 a dividend of 90.00 (9000) dated 27 Oct 2026 is "
    "in the twelve months (the day is not after the valuation day) and in FY 2026-27, and the lot "
    "held on it earned it: 9000 / 1000000 = 0.9000%.",
    [TEN, dividend(2, 20261027, 1, 1, 9_000)],
    20261027,
    9_000,
    1_000_000,
)

INCOME = {
    "about": (
        "Golden vectors for core/performance.income_summary (PN1, M7.29): the last twelve months' "
        "income, the yield on cost of the lots still held, and the income by financial year. "
        "Every figure is worked out by hand in the case's why; the generator derives only the "
        "twelve months, the years and the percentage."
    ),
    "cases": CASES,
}


def build() -> dict:
    return {"income.json": INCOME}


def main() -> None:
    write_all(build(), Path(__file__).parent)


if __name__ == "__main__":
    main()
