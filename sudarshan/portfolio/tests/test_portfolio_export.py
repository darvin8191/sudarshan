"""M7.40 (P7 lane X, X1): a tab's CSV export per view, through the sealed API.

Every figure in a file is checked against the answer the phone is shown for that view
(``tab_get``, ``performance``), the entries read back as the same ledger, and the files of an
invented tab are kept as golden files (``golden/export``; set ``SUDARSHAN_GOLDEN=write`` to
write them again after a change that is meant).
"""

from __future__ import annotations

import csv
import dataclasses
import io
import os
from pathlib import Path

import httpx
from test_portfolio_import_ops import NOW, OWNER, Rig, scenario_of  # noqa: F401  (the rig)
from test_portfolio_purposes import FAM, JOURNAL, JRN
from test_portfolio_tab_ops import clean

from sudarshan.autologin.handoff import seal
from sudarshan.portfolio import export
from sudarshan.portfolio.core import kinds, money, valuation
from sudarshan.portfolio.imports.csvutil import BOM
from sudarshan.portfolio.ops import REGISTRY

GOLDEN = Path(__file__).parent / "golden" / "export"
FIGURES = ("holdings", "performance", "allocation", "income")
DAY = "2026-10-26"
LAKH_U = 100_000 * 10_000


def on(rig):
    rig.svc.settings = dataclasses.replace(rig.svc.settings, goals=True)
    return rig


def stocked(rig):
    """An invented tab: gold in two accounts (one of Member A's), a share with no price, a fund
    sold out of, income, a fee and a loan."""
    book, tab = rig.book, rig.tab

    def add(rid, **entry):
        book.activity_create(OWNER, tab["id"], rig.version(), entry, rid, now=NOW)

    book.tab_update(OWNER, tab["id"], rig.version(), {"name_alt": "લાંબા ગાળા"}, now=NOW)
    other = book.member_create(OWNER, "m-2", "Member A", now=NOW, relation="family")
    locker = book.account_create(OWNER, "a-2", other["id"], "Locker", "physical", now=NOW)
    gold = book.asset_create(OWNER, "g-1", "manual", now=NOW, name="Gold bar", asset_class="gold")
    plot = book.asset_create(OWNER, "g-2", "manual", now=NOW, name="Plot", asset_class="other")
    zerodha = rig.account["id"]
    add("e-1", kind="BUY", trade_date=20250901, account_id=zerodha, asset_id=gold["id"],
        qty_u=3_000_000, amount_paise=30_000_000, charges_paise=45_000)  # fmt: skip
    add("e-2", kind="BUY", trade_date=20260605, account_id=locker["id"], asset_id=gold["id"],
        qty_u=1_500_000, amount_paise=18_000_000)  # fmt: skip
    add("e-3", kind="BUY", trade_date=20260610, account_id=zerodha, asset_id=plot["id"],
        qty_u=1_000_000, amount_paise=50_000_000)  # fmt: skip
    add("e-4", kind="BUY", trade_date=20260701, account_id=zerodha, asset_id=rig.share["id"],
        qty_u=10_000_000, price_u=10_000_000, amount_paise=1_000_000, charges_paise=1_500)  # fmt: skip
    add("e-5", kind="SELL", trade_date=20260915, account_id=zerodha, asset_id=gold["id"],
        qty_u=1_000_000, amount_paise=12_500_000, charges_paise=20_000)  # fmt: skip
    add("e-6", kind="SELL", trade_date=20261001, account_id=zerodha, asset_id=plot["id"],
        qty_u=1_000_000, amount_paise=47_000_000)  # fmt: skip
    add("e-7", kind="DIVIDEND", trade_date=20260820, account_id=zerodha,
        asset_id=rig.share["id"], amount_paise=5_000)  # fmt: skip
    add("e-8", kind="INTEREST", trade_date=20260310, account_id=zerodha, amount_paise=120_000,
        note="=SUM(A1) from the bank")  # fmt: skip
    add("e-9", kind="FEE", trade_date=20261002, account_id=zerodha, amount_paise=59_000)
    points = ((gold, 20250901, 100_000), (gold, 20260630, 118_000), (gold, 20260930, 126_000),
              (gold, 20261023, 124_500), (plot, 20260610, 500_000), (plot, 20260930, 470_000))  # fmt: skip
    for asset, day, rupees in points:
        rig.svc.store.write(
            OWNER,
            lambda tx, a=asset, d=day, r=rupees: tx.set_price_point(a["id"], d, r * 10_000, NOW),
        )
    book.asset_create(
        OWNER, "l-1", "liability", now=NOW, name="Home loan", tab_id=tab["id"],
        version=rig.version(), first_value={"day": 20261001, "price_u": 2 * LAKH_U},
    )  # fmt: skip
    return on(rig)


async def file_of(rig, view, name="export_csv", **more):
    answer = clean(await rig.call(name, id=rig.tab["id"], view=view, **more))
    assert answer["status"] == "success", answer
    return answer["data"]


def table(text: str) -> list[list[str]]:
    assert text.startswith(BOM) and "\n" not in text.replace("\r\n", "")
    return list(csv.reader(io.StringIO(text[1:], newline="")))


def block(rows: list[list[str]], title: str) -> list[dict[str, str]]:
    """The table under ``title`` as one dict per row (its own header row names the cells)."""
    at = rows.index([title])
    head, found = rows[at + 1], []
    for row in rows[at + 2 :]:
        if not row:
            break
        found.append(dict(zip(head, row, strict=False)))
    return found


def paise(text: str) -> int:
    sign = -1 if text.startswith("-") else 1
    whole, _, part = text.lstrip("-").partition(".")
    assert len(part) == 2, text
    return sign * (int(whole) * 100 + int(part))


def golden(name: str, text: str) -> None:
    path = GOLDEN / name
    if os.environ.get("SUDARSHAN_GOLDEN") == "write":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode())
    assert path.read_bytes().decode() == text, f"{name} is not the golden file"


# ---- the contract ----------------------------------------------------------------------------------
def test_the_exports_are_built_owner_only_and_the_pdf_waits_for_the_goals_switch():
    assert "export_csv" in REGISTRY
    assert kinds.SWITCH_OF["export_pdf"] == "goals" and "export_csv" not in kinds.SWITCH_OF
    others = {*kinds.FAMILY_OPS, *kinds.JOURNAL_OPS, *kinds.SHARING_OPS}
    assert not {"export_csv", "export_pdf"} & others
    assert set(export.VIEW_TITLES) == set(kinds.EXPORT_VIEWS)
    assert set(export.ALLOCATION_TITLES) == set(kinds.ALLOCATION_BY)


@scenario_of
async def test_while_the_switch_is_off_a_view_of_figures_is_not_ready_and_the_entries_still_come(
    rig,
):
    for view in FIGURES:
        answer = clean(await rig.call("export_csv", id=rig.tab["id"], view=view))
        assert (answer["code"], answer["message"]) == ("not_ready", kinds.WORDS["not_ready"])
    old = (await rig.call("export_csv", id=rig.tab["id"]))["data"]
    assert old["filename"] == f"SUDARSHAN_Portfolio_{rig.tab['id']}_{DAY}.csv"
    named = await file_of(rig, "activity")
    assert named["filename"] == f"SUDARSHAN_Portfolio_Long-term_activity_{DAY}.csv"
    assert named["text"] == BOM + old["text"]


@scenario_of
async def test_no_family_member_and_no_other_service_is_given_an_export(rig):
    stocked(rig)
    rig.svc.api.purposes += [("family", FAM), ("journal", JRN)]
    async with httpx.AsyncClient(trust_env=False) as client:
        for fernet, who in ((FAM, "viewer:1"), (FAM, "viewer:2"), (JRN, JOURNAL)):
            for name in ("export_csv", "export_pdf"):
                for view in (None, *kinds.EXPORT_VIEWS):
                    args = {"id": rig.tab["id"], **({"view": view} if view else {})}
                    answer = await client.post(
                        f"http://127.0.0.1:{rig.svc.api.port}/v1/{name}",
                        content=seal(fernet, name, principal=who, args=args), timeout=20,
                    )  # fmt: skip
                    assert answer.status_code == 403, (who, name, view)
                    assert "filename" not in answer.text and "Gold" not in answer.text


# ---- every figure against the view it is made from -----------------------------------------------
@scenario_of
async def test_holdings_are_the_tabs_rows_and_totals_to_the_paisa(rig):
    stocked(rig)
    shown = (await rig.call("tab_get", id=rig.tab["id"], accounts=[]))["data"]
    file = await file_of(rig, "holdings")
    assert file["filename"] == f"SUDARSHAN_Portfolio_Long-term_holdings_{DAY}.csv"
    rows = table(file["text"])
    assert rows[0] == ["SUDARSHAN / सुदर्शन", "Holdings"]
    assert rows[1] == ["Long term", "લાંબા ગાળા", "As of 26 Oct 2026, 13:03 IST"]
    totals, fixed = shown["totals"], shown["fixed"]
    figures = dict(row for row in rows[3:7])
    assert {k: paise(v) for k, v in figures.items()} == {
        "Value": totals["value_paise"], "Cost": totals["cost_paise"],
        "Unrealised gain": totals["unrealised_paise"], "Overall gain": totals["overall_gain_paise"],
    }  # fmt: skip
    lines = block(rows, "Holdings")
    total = lines.pop()
    assert total["Holding"] == "Total"
    assert (paise(total["Cost"]), paise(total["Value"]), paise(total["Gain"])) == (
        totals["cost_paise"], totals["value_paise"], totals["unrealised_paise"])  # fmt: skip
    assert sum(paise(r["Value"]) for r in lines) == totals["value_paise"]
    assert sum(paise(r["Cost"]) for r in lines) == totals["cost_paise"]
    assert sum(paise(r["Gain"]) for r in lines) == totals["unrealised_paise"]
    assert [paise(r["Value"]) for r in lines] == sorted(
        (paise(r["Value"]) for r in lines), reverse=True
    )
    # Each holding's lines add up to what the core says of the row the phone is sent.
    assert len(lines) == sum(len(row["lines"]) for row in shown["rows"]) == 3
    for row in shown["rows"]:
        mine = [r for r in lines if r["Holding"] == row["asset"]["name"]]
        want = valuation.row_figures(valuation.Row.of(row))
        assert sum(paise(r["Value"]) for r in mine) == want.value_paise
        assert sum(paise(r["Cost"]) for r in mine) == want.cost_paise
        assert sum(paise(r["Gain"]) for r in mine) == want.unrealised_paise
        for got, line in zip(sorted(mine, key=lambda r: r["Account"]),
                             sorted(row["lines"], key=lambda li: -li["account_id"]), strict=True):  # fmt: skip
            assert got["Quantity"] == export.csv_cell(export.Qty(line["qty_u"]))
            assert got["Price"] == export.csv_cell(export.Price(row["price"]["price_u"]))
            assert got["Weight %"] == money.pct(paise(got["Value"]), totals["value_paise"])
    reliance = next(r for r in lines if r["Symbol"] == "RELIANCE")
    assert (reliance["Price"], reliance["Gain %"], reliance["Note"]) == ("", "", "No price yet")
    assert reliance["Value"] == reliance["Cost"] == "10015.00" and reliance["Gain"] == "0.00"
    assert {r["Account"] for r in lines} == {"Zerodha", "Locker"}
    sums = {r["Figure"]: r["Value"] for r in block(rows, "Totals")}
    assert paise(sums["Realised gain"]) == fixed["realised_paise"] < 0  # the plot sold at a loss
    assert sums["Realised gain"].startswith("-") and not sums["Realised gain"].startswith("'")
    assert paise(sums["Income"]) == fixed["income_paise"]
    assert paise(sums["Fees"]) == fixed["fees_paise"]
    assert sums["Absolute return %"] == totals["absolute_return_pct"]
    assert paise(sums["Loans outstanding"]) == fixed["liabilities_paise"] == 20_000_000
    assert paise(sums["Value after loans"]) == totals["value_paise"] - 20_000_000
    loans = block(rows, "Loans")
    assert [(r["Loan"], paise(r["Outstanding"]), r["Balance of"]) for r in loans] == [
        (each["name"], each["outstanding_paise"], "2026-10-01") for each in shown["liabilities"]
    ]
    assert ["1 holding has no price yet and is counted at its cost."] in rows
    golden("holdings.csv.golden", file["text"])


@scenario_of
async def test_performance_is_the_performance_views_figures_and_every_day_of_its_series(rig):
    stocked(rig)
    report = (await rig.call("performance", id=rig.tab["id"]))["data"]
    file = await file_of(rig, "performance")
    rows = table(file["text"])
    facts = {r["Figure"]: (r["Value"], r["Note"]) for r in block(rows, "Figures")}
    for label, key in (("Value", "value_paise"), ("Money in", "money_in_paise"),
                       ("Money out", "money_out_paise"), ("Fees", "fee_paise"),
                       ("Net put in", "net_put_in_paise"), ("Total gain", "total_gain_paise")):  # fmt: skip
        assert paise(facts[label][0]) == report[key], label
    assert facts["Valued on"][0] == DAY and facts["First entry"][0] == "2025-09-01"
    assert facts["Absolute return %"][0] == report["absolute_return_pct"]
    assert facts["XIRR %"] == (report["xirr"]["pct"] or "", report["xirr"]["words"] or "")
    assert facts["TWR %"] == (report["twr"]["pct"] or "", report["twr"]["words"] or "")
    assert facts["TWR a year %"][0] == (report["twr"]["annualised_pct"] or "")
    assert report["xirr"]["pct"] and report["twr"]["pct"]  # both are shown for this tab
    index = block(rows, "Against the index")
    assert [r["Index"] for r in index] == [b["label"] for b in report["benchmarks"]]
    for got, want in zip(index, report["benchmarks"], strict=True):
        assert got["XIRR %"] == (want["xirr_pct"] or "") and got["Beta"] == (want["beta"] or "")
        assert all(word in got["Note"] for word in [*want["words"], want["stat_note"]])
    years = block(rows, "By financial year")
    assert [
        (r["Year"], paise(r["Realised gain"]), paise(r["Income"]), paise(r["Fees"])) for r in years
    ] == [
        (fy["label"], fy["realised_paise"], fy["income_paise"], fy["fees_paise"])
        for fy in report["by_fy"]
    ]
    days = block(rows, "Daily values")
    assert len(days) == len(report["series"]) > 300
    assert [
        (r["Day"].replace("-", ""), paise(r["Value"]), paise(r["Net put in"])) for r in days
    ] == [(str(p["day"]), p["value_paise"], p["net_put_in_paise"]) for p in report["series"]]
    assert ["Month-end values"] not in rows  # that table is the paper's
    assert report["series_words"] is None  # the history starts with the first entry
    golden("performance.csv.golden", file["text"])


@scenario_of
async def test_allocation_and_income_are_the_bars_and_the_income_the_phone_is_shown(rig):
    stocked(rig)
    shown = (await rig.call("tab_get", id=rig.tab["id"], accounts=[]))["data"]
    report = (await rig.call("performance", id=rig.tab["id"]))["data"]
    file = await file_of(rig, "allocation")
    rows = table(file["text"])
    assert rows[3:5] == [["Value", export.csv_cell(export.Amount(shown["totals"]["value_paise"]))],
                         ["Holdings", "2"]]  # fmt: skip
    for by, title in export.ALLOCATION_TITLES.items():
        got = [(r["Name"], paise(r["Value"]), r["Share %"], int(r["Holdings"]))
               for r in block(rows, title)]  # fmt: skip
        assert got == [(b["label"], b["value_paise"], b["pct"], b["holdings"])
                       for b in shown["allocation"][by]], by  # fmt: skip
    assert {r["Name"] for r in block(rows, "By family member")} == {"Me", "Member A"}
    golden("allocation.csv.golden", file["text"])

    file = await file_of(rig, "income")
    rows, found = table(file["text"]), report["income"]
    facts = {r["Figure"]: r["Value"] for r in block(rows, "Figures")}
    assert paise(facts["This financial year"]) == found["this_fy_paise"] == 5_000
    assert paise(facts["Last 12 months"]) == found["last_12m_paise"] == 125_000
    assert paise(facts["Earned by holdings still held"]) == found["held_income_paise"]
    assert paise(facts["Cost of those holdings"]) == found["held_cost_paise"]
    assert facts["Yield on cost %"] == found["yield_on_cost_pct"] != ""
    months = block(rows, "By month")
    total = months.pop()
    assert (total["Period"], paise(total["Income"])) == ("Last 12 months", found["last_12m_paise"])
    assert [paise(r["Income"]) for r in months] == [m["paise"] for m in found["by_month"]]
    assert (months[0]["Period"], months[-1]["Period"]) == ("Nov 2025", "Oct 2026")
    assert [(r["Period"], paise(r["Income"])) for r in block(rows, "By financial year")] == [
        (fy["label"], fy["paise"]) for fy in found["by_fy"]
    ]
    golden("income.csv.golden", file["text"])


# ---- the entries read back -----------------------------------------------------------------------
@scenario_of
async def test_the_entries_read_back_into_an_empty_tab_as_the_same_ledger(rig):
    book, tab, share, acc = rig.book, rig.tab, rig.share["id"], rig.account["id"]
    entries = (
        {"kind": "BUY", "trade_date": 20250901, "qty_u": 10_000_000, "price_u": 10_000_000,
             "amount_paise": 1_000_000, "charges_paise": 1_500},
        {"kind": "BUY", "trade_date": 20260605, "qty_u": 5_000_000, "price_u": 11_000_000,
             "amount_paise": 550_000},
        {"kind": "SPLIT", "trade_date": 20260701, "ratio_num": 2, "ratio_den": 1},
        {"kind": "SELL", "trade_date": 20260915, "qty_u": 8_000_000, "price_u": 6_000_000,
             "amount_paise": 480_000, "charges_paise": 900},
        {"kind": "DIVIDEND", "trade_date": 20260920, "amount_paise": 5_000, "tds_paise": 500},
    )  # fmt: skip
    for n, entry in enumerate(entries):
        more = {} if entry["kind"] == "SPLIT" else {"account_id": acc}
        book.activity_create(OWNER, tab["id"], rig.version(), {**entry, **more, "asset_id": share},
                             f"e-{n}", now=NOW)  # fmt: skip
    for n, (kind, note) in enumerate((("INTEREST", "=SUM(A1) from the bank"), ("FEE", None))):
        book.activity_create(
            OWNER, tab["id"], rig.version(),
            {"kind": kind, "trade_date": 20261002, "account_id": acc, "amount_paise": 59_000,
             "note": note}, f"f-{n}", now=NOW,
        )  # fmt: skip
    file = await file_of(rig, "activity")
    assert file["filename"] == f"SUDARSHAN_Portfolio_Long-term_activity_{DAY}.csv"
    assert file["filename"].isascii() and file["text"].startswith(BOM + "date,action,symbol,")
    assert "'=SUM(A1) from the bank" in file["text"]  # a note that would run is guarded
    golden("activity.csv.golden", file["text"])
    copy = book.tab_create(OWNER, "t-9", "Copy", "mixed", now=NOW)
    shown = (
        await rig.call("import_preview", id=copy["id"], format="generic", filename="x.csv",
                       text=file["text"])
    )["data"]  # fmt: skip
    assert (shown["rows_total"], shown["rows_new"], shown["rows_bad"]) == (7, 7, 0), shown
    done = await rig.call("import_commit", id=shown["batch_id"], version=shown["tab_version"])
    assert done["data"]["added"] == 7
    again = (await rig.call("export_csv", id=copy["id"], view="activity"))["data"]
    assert again["text"] == file["text"]
    was = (await rig.call("tab_get", id=tab["id"], accounts=[]))["data"]
    now = (await rig.call("tab_get", id=copy["id"], accounts=[]))["data"]
    assert now["totals"] == was["totals"] and now["fixed"] == was["fixed"]
    assert [r["lines"] for r in now["rows"]] == [r["lines"] for r in was["rows"]]
    assert was["rows"][0]["lines"][0]["qty_u"] == 22_000_000  # 15 split 2 for 1, less 8


# ---- names, refusals and what a spreadsheet would run -------------------------------------------
def test_a_file_name_is_ascii_whatever_the_tab_is_called():
    def name(tab):
        return export.file_name(tab, "holdings", NOW, "csv")

    assert name({"id": 7, "name": "Kids' education / 2040!"}) == (
        f"SUDARSHAN_Portfolio_Kids-education-2040_holdings_{DAY}.csv"
    )
    assert name({"id": 7, "name": "લાંબા ગાળા"}) == f"SUDARSHAN_Portfolio_tab7_holdings_{DAY}.csv"
    assert name({"id": 7, "name": "../../etc"}) == f"SUDARSHAN_Portfolio_etc_holdings_{DAY}.csv"
    long = export.slug({"id": 7, "name": "A very long name of a tab - for the retirement years"})
    assert long == "A-very-long-name-of-a-ta" and len(long) == export.SLUG_MAX
    assert export.slug({"id": 7, "name": "Twenty-three characters - x"}).endswith("characters")


def test_text_that_a_spreadsheet_would_run_is_guarded_and_a_loss_stays_a_number():
    cell = export.csv_cell
    assert [cell(t) for t in ("=1+1", "+SUM(A1)", "@cmd", "-2+3", "Plain")] == [
        "'=1+1", "'+SUM(A1)", "'@cmd", "'-2+3", "Plain"]  # fmt: skip
    assert cell(export.Gain(-105_025)) == "-1050.25" and cell(export.Amount(0)) == "0.00"
    assert cell(export.Qty(109_459_000)) == "109.459" and cell(export.Qty(10_000_000)) == "10"
    assert cell(export.Price(456_789)) == "45.6789" and cell(export.Price(10_000_000)) == "1000.00"
    assert (
        cell(export.Pct("-1.2000", True)) == "-1.2000"
        and cell(export.Day(20260105)) == "2026-01-05"
    )
    assert [cell(c) for c in (None, export.Amount(None), export.Pct(None), export.Day(None))] == [
        ""
    ] * 4


@scenario_of
async def test_a_name_that_would_run_in_a_spreadsheet_is_written_as_text(rig):
    stocked(rig)
    rig.book.tab_update(OWNER, rig.tab["id"], rig.version(), {"name": "=HYPERLINK(1)"}, now=NOW)
    file = await file_of(rig, "holdings")
    assert file["filename"] == f"SUDARSHAN_Portfolio_HYPERLINK-1_holdings_{DAY}.csv"
    assert table(file["text"])[1][0] == "'=HYPERLINK(1)"


@scenario_of
async def test_a_saved_default_view_does_not_narrow_the_file(rig):
    stocked(rig)
    spec = {"v": 1, "filters": {"classes": ["equity"]}}
    made = await rig.call("view_create", id=rig.tab["id"], request_id="v-1", name="Shares",
                          spec=spec, is_default=True)  # fmt: skip
    assert made["status"] == "success", made
    narrowed = (await rig.call("tab_get", id=rig.tab["id"]))["data"]
    whole = (await rig.call("tab_get", id=rig.tab["id"], accounts=[]))["data"]
    assert len(narrowed["rows"]) == 1 and len(whole["rows"]) == 2
    rows = table((await file_of(rig, "holdings"))["text"])
    assert len(block(rows, "Holdings")) == 3 + 1  # three lines and the total
    assert paise(rows[3][1]) == whole["totals"]["value_paise"]
    assert ["The whole tab, with no filter."] in rows


@scenario_of
async def test_what_cannot_be_exported_is_said_in_words(rig):
    on(rig)
    system = next(t for t in rig.book.tabs(OWNER) if t["system"])
    for view in kinds.EXPORT_VIEWS:
        answer = clean(await rig.call("export_csv", id=system["id"], view=view))
        assert answer["code"] == "bad_request" and "Reports" in answer["message"], view
        gone = clean(await rig.call("export_csv", id=999, view=view))
        assert gone["code"] == "not_found", view
    bad = clean(await rig.call("export_csv", id=rig.tab["id"], view="everything"))
    assert (bad["code"], bad["field"]) == ("bad_request", "view")
    assert bad["message"] == export.WORDS["view"]
    # A tab with nothing in it still gives a file that says so.
    rows = table((await file_of(rig, "holdings"))["text"])
    assert ["No holdings in this tab."] in rows and block(rows, "Holdings")[-1]["Value"] == "0.00"
