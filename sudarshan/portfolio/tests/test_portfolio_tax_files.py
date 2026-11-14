"""The tax phase's downloads (M7.7, P4; design p7 8): one schedule of the working paper for the CA,
and Upstox's report rows as the reader kept them. Numbers are written plain, signs kept; text a
spreadsheet would run as a formula gets an apostrophe; names are ASCII. Synthetic data only.
"""

from __future__ import annotations

import csv
import io

import pytest
from test_portfolio_tax_adapter import EQ_SALE, NOW, answered
from test_portfolio_tax_ops import call, loss_last_year, refused
from test_portfolio_tax_ops import ctx as ctx  # noqa: F401  (the store fixture)

from sudarshan.portfolio import reports
from sudarshan.portfolio import store as store_module
from sudarshan.portfolio.store import Scope

OWNER = Scope.owner()


@pytest.fixture(autouse=True)
def _stop():
    yield
    store_module.stop()


def table(answer: dict) -> list[list[str]]:
    return list(csv.reader(io.StringIO(answer["text"].removeprefix("\ufeff"))))


def test_a_schedule_of_gains_is_written_under_the_working_paper_sentence(ctx):
    answer = call(ctx, "tax_csv", schedule="capital_gains")
    assert answer["filename"] == "SUDARSHAN_Tax_FY2026-27_member1_capital_gains.csv"
    assert answer["text"].startswith("\ufeff")  # a spreadsheet reads the Devanagari name right
    rows = table(answer)
    assert rows[0] == [
        "सुदर्शन · SUDARSHAN · Tax working paper · FY 2026-27 · Me · year to date, as of 14 Nov 2026"
    ]
    assert rows[1][0].startswith("A working paper: verify with your CA.")
    head, sale = rows[3], dict(zip(rows[3], rows[4], strict=True))
    assert head[:4] == ["Sale day", "ISIN", "Name", "Account"]
    assert (sale["Sale day"], sale["Name"], sale["Account"], sale["Bought"]) == (
        "2026-10-27",
        "RELIANCE INDUSTRIES",
        "Upstox",
        "2024-01-10",
    )
    assert (sale["Quantity"], sale["Cost"], sale["Expenses"], sale["Gain"]) == (
        "10",
        "20000.00",
        "14.00",
        "5986.00",
    )
    assert (sale["Term"], sale["Rate %"], sale["Status"]) == ("Long-term", "12.5", "Counted")
    assert ["Exemption used", "5986.00"] in rows and ["Taxable long-term gain", "0.00"] in rows


def test_business_income_keeps_its_signs_plain(ctx):
    rows = table(call(ctx, "tax_csv", schedule="business"))
    label = "F&O and other derivatives"
    assert [label, "Options", "2", "-812.50", "4062.50"] in rows
    assert [label, "Charges", "", "154.23", ""] in rows and [
        label,
        "Net",
        "",
        "-966.73",
        "",
    ] in rows


def test_the_losses_name_each_set_off_and_its_year(ctx):
    loss_last_year(ctx)
    rows = table(call(ctx, "tax_csv", schedule="losses"))
    assert ["Capital gains", "Brought forward", "Short-term capital loss", "FY 2025-26",
            "Long-term gain at 12.5%", "", "2000.00", ""] in rows  # fmt: skip
    dividends = table(call(ctx, "tax_csv", schedule="dividends"))
    assert ["Buyback receipts taxed as a dividend", "0.00"] in dividends


def test_a_schedule_not_offered_is_refused_in_words(ctx):
    wrong = refused(ctx, "tax_csv", schedule="everything")
    assert (wrong.code, wrong.field, wrong.message) == (
        "bad_request",
        "schedule",
        "Choose a schedule to download.",
    )


# ---- Upstox's rows as read ---------------------------------------------------------------------
def test_upstox_trades_are_written_as_the_reader_kept_them(ctx):
    answer = call(ctx, "report_csv", kind="trades", fy=2026, segment="FO")
    assert answer["filename"] == "SUDARSHAN_Upstox_trades_FY2026-27_FO.csv"
    rows = table(answer)
    sold = dict(zip(rows[0], rows[1], strict=True))
    assert (sold["Trade date"], sold["Side"], sold["Strike"], sold["Expiry"]) == (
        "2026-10-28",
        "SELL",
        "25900.00",
        "2026-11-03",
    )
    assert (sold["Quantity"], sold["Price"], sold["Amount"]) == ("65", "125.00", "8125.00")


def test_upstox_pnl_keeps_a_loss_plain_and_says_a_charge_not_given(ctx):
    rows = table(call(ctx, "report_csv", kind="pnl", fy=2026, segment="FO"))
    put = dict(zip(rows[0], rows[2], strict=True))
    assert (put["Scrip"], put["Profit or loss"]) == ("NIFTY 26000 PE", "-2437.50")
    assert ["Total", "154.23"] in rows and ["IPFT", "not given"] in rows


def test_text_a_spreadsheet_would_run_is_written_as_text_and_a_partial_report_says_so(ctx):
    odd = reports.parse_trade(answered({**EQ_SALE, "scrip_name": "=HYPERLINK(1)"}))
    ctx.store.write(OWNER, lambda tx: tx.report_put("trades", 2025, "EQ", "partial", NOW, None,
                                                    {"rows": [odd]}, NOW))  # fmt: skip
    answer = call(ctx, "report_csv", kind="trades", fy=2025, segment="EQ")
    assert answer["filename"] == "SUDARSHAN_Upstox_trades_FY2025-26_EQ_partial.csv"
    assert table(answer)[1][5] == "'=HYPERLINK(1)"


@pytest.mark.parametrize(
    ("args", "code", "words"),
    [
        ({"kind": "trades", "fy": 2026, "segment": "CD"}, "not_found",
         "Upstox's report for this year and segment is not read yet. Read it on the Upstox tab, "
         "then download it."),
        ({"kind": "pnl", "fy": 2026, "segment": "MF"}, "bad_request", reports.WORDS["no_pnl_mf"]),
        ({"kind": "trades", "fy": 2022, "segment": "EQ"}, "bad_request", reports.WORDS["year"]),
    ],
)  # fmt: skip
def test_a_report_not_read_or_not_offered_is_refused_in_words(ctx, args, code, words):
    wrong = refused(ctx, "report_csv", **args)
    assert (wrong.code, wrong.message) == (code, words)
