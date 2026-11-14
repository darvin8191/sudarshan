"""Every rate, threshold and date the tax core uses, as dated rows (M7.27, M7.28).

A table is a tuple of ``Row`` in date order; ``at(table, day)`` is the row in force on a day (for
a yearly figure, the first day of the financial year). ``ref`` names the provision for builders
and is never printed on a screen or a report. ``verify`` marks a reading the sources leave open:
the default stands beside its ``alternatives``, a taxpayer's setting can pick one of them
(``choice``), and the pack says which was used. Read from secondary sources on ``checked``; the
whole table is for a working paper to verify with a chartered accountant.
"""

from __future__ import annotations

from typing import NamedTuple

RULES_VERSION = "2026.11.14-1"
CHECKED = 20261114
RUPEE = 100
LAKH = 100_000 * RUPEE
CRORE = 100 * LAKH


class Row(NamedTuple):
    from_day: int
    value: object
    ref: str
    checked: int = CHECKED
    verify: bool = False
    alternatives: tuple = ()


def row_at(table: tuple[Row, ...], day: int) -> Row:
    """The row in force on ``day``; LookupError for a day before the table starts."""
    chosen = None
    for row in table:
        if row.from_day <= day:
            chosen = row
    if chosen is None:
        raise LookupError(f"no rule row on {day}")
    return chosen


def at(table: tuple[Row, ...], day: int):
    return row_at(table, day).value


def choice(table: tuple[Row, ...], day: int, wanted=None):
    """A taxpayer's pick among a row's readings; anything the row does not offer is the default."""
    row = row_at(table, day)
    return wanted if wanted in row.alternatives else row.value


ACT = (
    Row(0, "1961", "Income-tax Act 1961"),
    Row(20260401, "2025", "Income-tax Act 2025, in force from 1 Apr 2026"),
)
#: Short-term gain on STT-paid listed shares and equity fund units, by transfer day (basis points).
STCG_RATE = (
    Row(0, 1500, "1961 Act s.111A"),
    Row(20240723, 2000, "1961 Act s.111A as amended from 23 Jul 2024; 2025 Act s.196"),
)
#: Long-term gain on the same, by transfer day. Before FY 2018-19 such a gain was exempt.
LTCG_RATE = (
    Row(0, 0, "1961 Act s.10(38): exempt before 1 Apr 2018"),
    Row(20180401, 1000, "1961 Act s.112A"),
    Row(20240723, 1250, "1961 Act s.112A as amended from 23 Jul 2024; 2025 Act s.198"),
)
#: The yearly long-term exemption per taxpayer, by the first day of the financial year (paise).
LTCG_EXEMPT = (
    Row(20180401, 1 * LAKH, "1961 Act s.112A"),
    Row(
        20240401,
        125_000 * RUPEE,
        "1961 Act s.112A as amended (whole of FY 2024-25); 2025 Act s.198",
    ),
)
#: Where one year has long-term gains at two rates (FY 2024-25), which the exemption and losses
#: reduce first. A utility convention, not a verified rule.
EXEMPT_ORDER = (
    Row(0, "highest_rate_first", "no provision fixes the order", verify=True,
        alternatives=("chronological",)),
)  # fmt: skip
LT_MONTHS = (Row(0, 12, "1961 Act s.2(42A) proviso; 2025 Act s.2(101)"),)
#: Whether the first anniversary of the purchase is still short-term (held "not more than" 12
#: months, the day of sale left out) or already long-term.
LT_BOUNDARY = (
    Row(0, "after_anniversary", "day count convention", verify=True,
        alternatives=("on_anniversary",)),
)  # fmt: skip
#: Cost of a share or unit bought before ``before``: the higher of the cost and the lower of the
#: fair market value on ``fmv_day`` (the highest quoted price that day) and the sale value.
GRANDFATHER = (
    Row(
        0,
        {"before": 20180201, "fmv_day": 20180131},
        "1961 Act s.55(2)(ac), for transfers from 1 Apr 2018; 2025 Act s.90(7), s.90(8)",
    ),
)
#: A buyback by the day of the buyback: exempt for the holder (the company paid); the whole
#: receipt a dividend and the cost a capital loss; an ordinary capital gain (promoters' extra tax
#: is not modelled).
BUYBACK = (
    Row(0, "exempt", "1961 Act s.10(34A), s.115QA"),
    Row(20241001, "dividend_and_loss", "1961 Act s.2(22)(f), s.46A from 1 Oct 2024"),
    Row(20260401, "capital_gain", "Finance Act 2026; 2025 Act", verify=True),
)
#: Years after the year of the loss in which it can still be set off, by bucket.
CARRY_YEARS = (
    Row(0, {"stcl": 8, "ltcl": 8, "business": 8, "speculative": 4},
        "1961 Act s.72, s.73, s.74; 2025 Act s.108 to s.113"),
)  # fmt: skip
#: Turnover of derivative trading for the audit limits: the sum of profits and losses of closed
#: trades (the ICAI guidance note's current reading), or that plus the premium on options sold.
TURNOVER_METHOD = (
    Row(0, "abs_pnl", "ICAI Guidance Note on Tax Audit (revised 2022, 2023)", verify=True,
        alternatives=("abs_pnl_plus_sale_premium",)),
)  # fmt: skip
#: Audit limits on turnover (paise): ``base``, and ``digital`` where cash dealings are 5% or less.
AUDIT = (
    Row(20200401, {"base": 1 * CRORE, "digital": 10 * CRORE}, "1961 Act s.44AB; 2025 Act s.63"),
)
#: A net below this share of turnover (basis points) is the "low margin" the flag speaks of.
LOW_MARGIN_BP = (Row(20200401, 600, "1961 Act s.44AD; 2025 Act s.58", verify=True),)
#: Cess on the tax, shown as its own line before any surcharge (basis points).
CESS_BP = (Row(20180401, 400, "health and education cess, Finance Act 2018"),)
#: Tax deducted by a company on dividends above ``threshold`` a year per payer (for dividends.py).
DIVIDEND_TDS = (
    Row(20200401, {"threshold": 5_000 * RUPEE, "rate_bp": 1000}, "1961 Act s.194"),
    Row(20250401, {"threshold": 10_000 * RUPEE, "rate_bp": 1000}, "1961 Act s.194; 2025 Act s.393",
        verify=True),
)  # fmt: skip
