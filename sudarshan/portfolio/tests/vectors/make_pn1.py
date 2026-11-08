"""Generator of the PN1 golden vectors: income.json, yield on cost and the income by year (M7.29).

Yield on cost is the last twelve months' income (dividends and interest, gross, the twelve
calendar months that end with the valuation day's) on what the current holdings cost, as a
percentage with four decimals, half away from zero, written with a plain minus and never -0.0000.
Nothing held at a cost gives no yield (null), never 0. The income by financial year (April to
March) has one row per year with any income, oldest first, labelled "FY 2026-27". Income dated
after the valuation day counts nowhere. Every figure here is worked out by hand in the case's why,
and the arithmetic below is Decimal, not the code's. Like the other generators it imports nothing
from sudarshan.

The cost of the current holdings is an input of each case: the ledger works it out (ledger.json
pins that), and this file pins only what is made of it.

Run: python3 make_pn1.py
"""

from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from portfolio_vector_tools import write_all

CASES = []


def e(i, kind, day, account, asset, amount, **more):
    row = {"id": i, "seq": i, "kind": kind, "trade_date": day, "account_id": account}
    if asset is not None:
        row["asset_id"] = asset
    row["amount_paise"] = amount
    row.update(more)
    return row


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


def case(ident, title, why, income_rows, valuation_day, cost_paise, trades=()):
    """``income_rows`` are (day, paise) credits; the expected figures are worked out here by a
    route of their own (calendar arithmetic on the day, Decimal for the percentage)."""
    year, month = valuation_day // 10000, valuation_day // 100 % 100
    first = year * 12 + month - 12  # the month number of the first of the twelve
    last_12m = 0
    years = {}
    entries = list(trades)
    for n, (day, paise) in enumerate(income_rows, start=len(entries) + 1):
        kind = "DIVIDEND" if n % 2 else "INTEREST"
        entries.append(e(n, kind, day, 1, 1 if kind == "DIVIDEND" else None, paise))
        if day > valuation_day:
            continue
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
            "cost_paise": cost_paise,
            "expect": {
                "last_12m_paise": last_12m,
                "yield_on_cost_pct": percent(last_12m, cost_paise),
                "by_fy": [
                    {"fy": fy, "label": label(fy), "paise": paise}
                    for fy, paise in sorted(years.items())
                    if paise
                ],
            },
        }
    )


BUY = (e(1, "BUY", 20250110, 1, 1, 1_000_000, qty_u=10_000_000),)  # 10 shares for 10,000.00

case(
    "Y1",
    "Twelve months of income on what the holdings cost",
    "Read on 27 Oct 2026 the twelve months are Nov 2025 to Oct 2026. Dividends of 100.00 on "
    "15 Nov 2025, 150.00 on 15 Feb 2026 and 200.00 on 15 Aug 2026: 450.00 (45000) in the twelve "
    "months, on holdings that cost 10,000.00 (1000000): 4.5000%. FY 2025-26 has 250.00 (the "
    "Nov and Feb credits) and FY 2026-27 has 200.00.",
    [(20251115, 10_000), (20260215, 15_000), (20260815, 20_000)],
    20261027,
    1_000_000,
    BUY,
)

case(
    "Y2",
    "Income a month too early is in its year but not in the twelve months",
    "Read on 27 Oct 2026 the first of the twelve months is Nov 2025. A dividend of 300.00 on "
    "31 Oct 2025 is a day too early for them and counts in FY 2025-26 only; 120.00 on 1 Nov 2025 "
    "is the first day of the twelve. Twelve months: 120.00 (12000) on a cost of 8,000.00: 1.5000%. "
    "FY 2025-26: 420.00.",
    [(20251031, 30_000), (20251101, 12_000)],
    20261027,
    800_000,
)

case(
    "Y3",
    "Income dated after the valuation day counts nowhere",
    "Read on 27 Oct 2026: a dividend of 500.00 dated 28 Oct 2026 (a draft the owner dated ahead, "
    "or a day in the future when the tab is read as of an earlier day) is in no month and no "
    "year. Only the 80.00 of 1 Oct 2026 counts: 0.8000% on 10,000.00.",
    [(20261001, 8_000), (20261028, 50_000)],
    20261027,
    1_000_000,
)

case(
    "Y4",
    "Nothing held at a cost gives no yield, not a zero",
    "Every share was sold, so the current holdings cost 0: the 60.00 of income in the twelve "
    "months has nothing to be a yield of, and the figure is null. The totals still stand: "
    "twelve months 60.00, FY 2026-27 60.00.",
    [(20260601, 6_000)],
    20261027,
    0,
)

case(
    "Y5",
    "The percentage is rounded half away from zero at the fourth decimal",
    "Income of 1.00 (100) on a cost of 3,000.00 (300000): 100 x 100 / 300000 = 0.033333...%, "
    "written 0.0333. Income of 2.00 on 300.00 is 0.666666...%, written 0.6667 (the vector Y5b).",
    [(20260601, 100)],
    20261027,
    300_000,
)

case(
    "Y5b",
    "The percentage is rounded half away from zero at the fourth decimal (up)",
    "Income of 2.00 (200) on a cost of 300.00 (30000): 0.666666...%, written 0.6667.",
    [(20260601, 200)],
    20261027,
    30_000,
)

case(
    "Y6",
    "Read in April: the twelve months straddle two financial years",
    "Read on 10 Apr 2026 the twelve months are May 2025 to Apr 2026. Interest of 1,000.00 on "
    "30 Apr 2025 is a month too early; 2,000.00 on 31 May 2025 and 3,000.00 on 31 Mar 2026 are "
    "FY 2025-26; 4,000.00 on 1 Apr 2026 is FY 2026-27. Twelve months: 9,000.00 (900000) on "
    "1,00,000.00: 9.0000%. By year: FY 2025-26 6,000.00, FY 2026-27 4,000.00.",
    [(20250430, 100_000), (20250531, 200_000), (20260331, 300_000), (20260401, 400_000)],
    20260410,
    10_000_000,
)

case(
    "Y7",
    "No income at all",
    "A tab with a purchase and no dividend or interest: twelve months 0, yield 0.0000% on its "
    "cost (there is a cost, and the income on it is nothing), and no year row at all.",
    [],
    20261027,
    1_000_000,
    BUY,
)

INCOME = {
    "about": (
        "Golden vectors for core/performance.income_summary (PN1, M7.29): the last twelve months' "
        "income, the yield on cost and the income by financial year. The cost of the current "
        "holdings is an input (the ledger's vectors pin it). Every figure is worked out by hand in "
        "the case's why; the generator's arithmetic is Decimal, not the code's."
    ),
    "cases": CASES,
}


def build() -> dict:
    return {"income.json": INCOME}


def main() -> None:
    write_all(build(), Path(__file__).parent)


if __name__ == "__main__":
    main()
