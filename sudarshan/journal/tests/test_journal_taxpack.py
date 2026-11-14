"""The Tax pack on the tax working paper (P7 J5 and J6, M15.5): the pack the portfolio computes
(``sudarshan/tax/tests/vectors/pack.json``, the contract both sides load), through the journal's
link fake, into the archive row and the PDF. The journal works out none of it."""

from __future__ import annotations

import copy
import dataclasses
import json
import re
from pathlib import Path

import pytest
from render_pdfread import read_pdf
from test_journal_capture import files as files  # noqa: F401  (a fixture)
from test_journal_capture import ist_ms, scenario
from test_journal_capture import rig as rig  # noqa: F401  (a fixture)
from test_journal_period_reports import build, file_of, kept, pdf_lines
from test_journal_reports import call

import sudarshan.tax as tax_package
from sudarshan.journal import periods, taxpack
from sudarshan.journal.core import words
from sudarshan.journal.render import doc as D
from sudarshan.journal.store import Scope

OWNER = Scope.owner()
PACK = json.loads(
    (Path(tax_package.__file__).parent / "tests" / "vectors" / "pack.json").read_text("utf-8")
)[0]["out"]
SECTION = re.compile(r"(?i)\bsection\s*\d|§|\bsec\.\s*\d|\bu/s\b|income[- ]tax act")
MINUS = "−"


@pytest.fixture
def on(rig):  # noqa: F811
    rig.svc.settings = dataclasses.replace(rig.svc.settings, tax=True)
    rig.now = ist_ms(20261114, "22:30:00")
    rig.portfolio.answers["tax_pack"] = copy.deepcopy(PACK)
    return rig


def text_of(blob) -> str:
    return "\n".join(pdf_lines(blob))


def money_cells(blocks) -> list[str]:
    out = []
    for block in blocks:
        if isinstance(block, D.Table):
            for row in block.rows:
                out += [c.text if isinstance(c, D.Money) else str(getattr(c, "text", c))
                        for c in row]  # fmt: skip
    return out


# ---- the pack replay (design 11.2) -------------------------------------------------------------
def test_the_pack_goes_through_the_link_into_the_pdf_with_every_total(on):
    built = build(on, "fy", period="FY2627")
    assert built["words"] == "The tax pack is in the archive."
    asked = [c for c in on.portfolio.calls if c[0] == "tax_pack"]
    assert [(c[1], c[2]) for c in asked] == [({"fy": 2026}, "sudarshan-journal")]
    row = kept(on, "fy")
    text = text_of(file_of(on, row))
    assert words.FY_NOTE in text and words.FY_NOTE == PACK["note"]
    cg, b = PACK["capital_gains"], PACK["business"]
    # Every total of the capital-gains schedule, as the pack has it.
    for paise in (cg["st_gain_paise"], cg["lt_gain_paise"], cg["taxable"]["st_paise"],
                  cg["indicative_tax"]["tax_paise"], cg["indicative_tax"]["cess_paise"],
                  cg["exemption"]["limit_paise"], cg["carried"][0]["paise"]):  # fmt: skip
        assert words.rupees(paise) in text, paise
    assert f"{MINUS}{words.rupees(cg['lt_loss_paise'])}" in text  # a loss with its sign
    for sale in cg["schedule"]:
        assert sale["isin"] in text
        assert words.rupees(abs(sale["gain_paise"])) in text
    assert "Short-term" in text and "Long-term" in text and "12.5%" in text and "20%" in text
    for sale in cg["schedule"]:  # no date cut short by its column
        assert words.day_label_year(sale["sale_day"]) in text
    assert "..." not in text.split("Trading activity recorded")[0]
    # Business income: both blocks and the turnover with its method by name.
    for block in (b["non_speculative"], b["speculative"]):
        for key in ("gross_paise", "charges_paise", "net_paise", "turnover_paise"):
            assert words.rupees(abs(block[key])) in text, key
    assert words.rupees(b["turnover_paise"]) in text and b["turnover_method_label"] in text
    assert "1 position still open: in no total until closed." in text
    # The set-off sentences, the notes and the list of what is not computed, word for word.
    squeezed = re.sub(r"\s+", "", text)
    for sentence in (*cg["set_off_words"], *PACK["notes"], *PACK["not_computed"]):
        assert re.sub(r"\s+", "", sentence) in squeezed, sentence
    # Upstox's own charges per segment.
    for seg in PACK["charges"]["by_segment"].values():
        assert words.rupees(seg["total_paise"]) in text and words.rupees(seg["stt_paise"]) in text
    assert "to date" in text  # the year is running
    assert "Built from the tax working paper as of 14 Nov 2026, 22:00." in row["text"]
    assert "Capital gains taxable: short-term ₹24,966.53, long-term ₹0.00" in row["text"]
    assert not SECTION.search(text + row["text"])
    # The journal's own blocks follow, under their own heading.
    assert text.index("Tax working paper") < text.index("Trading activity recorded by the journal")
    assert "By kind of trade" in text and "held over 12 months" not in text


def test_the_printer_prints_the_packs_figures_and_adds_none(on):
    """Every money cell of the working paper's tables is a figure the pack holds (or one with
    its sign turned for a loss); nothing is summed, classified or rated here."""
    held: set[int] = set()

    def walk(value):
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, int) and not isinstance(value, bool):
            held.add(abs(value))

    walk(PACK)
    cells = money_cells(taxpack.pack_blocks(PACK))
    amounts = [c for c in cells if "₹" in c]
    assert len(amounts) > 30
    for cell in amounts:
        for found in re.findall(r"₹([\d,]+)\.(\d\d)", cell):
            paise = int(found[0].replace(",", "")) * 100 + int(found[1])
            assert paise in held, cell


def test_a_loss_is_printed_as_a_loss():
    """Mutation: a loss shown without its sign reads as a gain of the same size."""
    (table,) = [b for b in taxpack.capital_blocks(PACK["capital_gains"])
                if isinstance(b, D.Table) and b.rows and b.rows[0][0] == "Short-term gains"]  # fmt: skip
    cells = {row[0]: row[1] for row in table.rows}
    assert cells["Long-term losses"] == D.Money(-PACK["capital_gains"]["lt_loss_paise"])
    assert cells["Long-term losses"].result == "LOSS"
    assert cells["Long-term gains"] == D.Money(PACK["capital_gains"]["lt_gain_paise"])
    assert cells["Short-term losses"] == D.Money(0)


def test_a_pack_with_every_list_filled_prints_each_under_its_own_heading():
    pack = copy.deepcopy(PACK)
    sale = dict(pack["capital_gains"]["schedule"][0])
    pack["capital_gains"]["needs_fmv"] = [{**sale, "isin": "INE000N01011", "status": "needs_fmv"}]
    pack["capital_gains"]["unknown_cost"] = [
        {**sale, "isin": "INE000U01012", "status": "unknown_cost", "acquired_day": None,
         "cost_paise": None}]  # fmt: skip
    pack["capital_gains"]["exempt_buybacks"] = [
        {**sale, "isin": "INE000X01013", "status": "exempt_buyback", "buyback": "exempt"}
    ]
    pack["business"]["differs"] = [{"scrip": "NIFTY", "own_paise": 130000, "upstox_paise": None}]
    pack["business"]["flags"] = ["Turnover is above the limit. Ask your CA."]
    pack["business"]["unknown_settlement"] = [[1, 1, ["NFO", "ACME", 20260924, 0, "FUT"]]]
    pack["business"]["carried"] = [{"loss_fy": 2026, "bucket": "business", "paise": 15_500_000,
                                    "source": "computed", "last_fy": 2034}]  # fmt: skip
    pack["inputs"]["missing"] = ["Upstox's trade history for FY 2025-26 is not read yet."]
    pack["not_computed_sales"] = [{"instrument": "INF000D01014", "day": 20260810,
                                   "amount_paise": 5_000_000}]  # fmt: skip
    pack["capital_gains"]["schedule"][0]["fmv_used"] = True
    pack["running"] = False
    span = periods.fy_span(2026, 20270415)
    blob = periods.fy_pdf(span, "", [], ist_ms(20270415, "07:00:00"), pack)
    text = text_of(blob)
    for expected in (
        "Sales listed and in no total", "INE000N01011", "Needs the price of 31 Jan 2018",
        "INE000U01012", "No purchase record", "not given", "INE000X01013",
        "Buyback, exempt in your hands", "Differs from Upstox's own report", "nothing",
        "Turnover is above the limit. Ask your CA.", "1 future past expiry with no settlement",
        "F&O (business) loss", "FY 2034-35", "₹1,55,000.00",
        "Still needed from you or from Upstox", "Upstox's trade history for FY 2025-26",
        "INF000D01014", "₹50,000.00", "Long*", "* The highest price of 31 January 2018",
    ):  # fmt: skip
        assert expected in text, expected
    assert "The year is still running" not in text
    assert not SECTION.search(text)
    assert len(read_pdf(blob).pages) >= 2


def test_an_etf_a_reit_and_a_gold_bond_are_printed_as_not_computed_and_every_sale_is_named():
    """Delta review H1. The portfolio's paper now leaves these out of the gains and lists each
    sale with its value and name; the printed pack shows them under "Not computed here", never
    in the table of share sales, and a share's row has its name with its ISIN."""
    pack = copy.deepcopy(PACK)
    schedule = pack["capital_gains"]["schedule"]
    schedule[0]["name"] = "ACME INDUSTRIES"
    for other in schedule[1:]:
        other["name"] = None  # no record names it: the ISIN alone, as before
    units = [
        ("INF204KB17I5", "GOLD ETF UNITS", 5_000_000),
        ("INF732E01037", "LIQUID ETF UNITS", 6_100_000),
        ("INE041025011", "OFFICE PARKS REIT", 7_200_000),
        ("IN0020200013", "SOVEREIGN GOLD BOND 2028", 8_300_000),
        ("INF000D01015", None, 9_400_000),
    ]
    pack["not_computed_sales"] = [
        {"instrument": isin, "name": name, "day": 20260810 + n, "amount_paise": paise}
        for n, (isin, name, paise) in enumerate(units)
    ]
    pack["not_computed"] = ["5 sales of assets outside this working paper (sale value "
                            "Rs 3,60,000) are not computed.", *pack["not_computed"]]  # fmt: skip
    blocks = taxpack.pack_blocks(pack)
    tables_ = [b for b in blocks if isinstance(b, D.Table)]
    (outside,) = [t for t in tables_ if t.columns[0].title == "Sold, outside this paper"]
    assert [row[0] for row in outside.rows] == [
        "GOLD ETF UNITS (INF204KB17I5)", "LIQUID ETF UNITS (INF732E01037)",
        "OFFICE PARKS REIT (INE041025011)", "SOVEREIGN GOLD BOND 2028 (IN0020200013)",
        "INF000D01015",
    ]  # fmt: skip
    assert [row[2] for row in outside.rows] == [words.rupees(p) for _, _, p in units]
    (sales,) = [t for t in tables_ if t.columns[0].title == "ISIN" and len(t.columns) == 8]
    assert [row[0] for row in sales.rows] == [g["isin"] for g in schedule]  # shares only
    span = periods.fy_span(2026, 20270415)
    text = text_of(periods.fy_pdf(span, "", [], ist_ms(20270415, "07:00:00"), pack))
    paper = text.split("Trading activity recorded")[0]
    head, left_out = paper.split("Not computed here")
    for isin, name, paise in units:
        assert isin in left_out and isin not in head, isin
        assert words.rupees(paise) in left_out
        assert name is None or name in left_out, name
    assert "5 sales of assets outside this working paper" in left_out
    assert "Names of the shares and funds sold" in head
    assert f"{schedule[0]['isin']}: ACME INDUSTRIES" in head
    assert "..." not in paper, "no name or ISIN is cut short"


# ---- the archive keeps the paper it printed ----------------------------------------------------
def test_an_archived_pack_never_changes_until_it_is_built_again(on):
    """Mutation 15: a rule row or a late trade changes the portfolio's answer; the kept paper
    is the one printed, with its time and its rules edition, until the owner builds it again."""
    build(on, "fy", period="FY2627")
    first = kept(on, "fy")
    blob = file_of(on, first)
    assert first["rows"]["tax_pack"] == PACK and first["rows"]["tax_note"] is None
    assert first["rows"]["tax_pack"]["rules_version"] == PACK["rules_version"]
    later = copy.deepcopy(PACK)
    later["rules_version"] = "2027.02.01-1"
    later["capital_gains"]["taxable"]["st_paise"] = 99_999_900
    later["as_of_ms"] = ist_ms(20261120, "09:00:00")
    on.portfolio.answers["tax_pack"] = later
    served = call(on, "report_file", id=first["id"])
    assert served["file_sha256"] == first["file_sha256"]
    assert kept(on, "fy")["rows"]["tax_pack"] == PACK and file_of(on, kept(on, "fy")) == blob
    assert len([c for c in on.portfolio.calls if c[0] == "tax_pack"]) == 1

    on.now = ist_ms(20261120, "09:05:00")
    build(on, "fy", period="FY2627")
    again = kept(on, "fy")
    assert again["id"] == first["id"] and again["rows"]["tax_pack"]["rules_version"] == (
        "2027.02.01-1")  # fmt: skip
    assert "₹9,99,999.00" in text_of(file_of(on, again))
    assert "as of 20 Nov 2026, 09:00" in again["text"]


# ---- when the paper cannot be had --------------------------------------------------------------
@pytest.mark.parametrize(
    ("answer", "sentence"),
    [
        ((501, {"status": "error", "code": "not_ready"}), "is switched off on the server"),
        ((503, {"status": "error", "code": "unavailable"}), "could not be read from the portfolio"),
        ({"fy": 2026, "capital_gains": []}, "this release cannot read"),
        (None, "is switched off on the server"),
    ],
)
def test_without_the_paper_the_pack_says_why_and_holds_the_journals_blocks(on, answer, sentence):
    on.portfolio.answers["tax_pack"] = answer
    build(on, "fy", period="FY2627")
    row = kept(on, "fy")
    text = text_of(file_of(on, row))
    assert sentence in text and sentence in row["text"]
    assert "Capital gains: listed shares" not in text and "Every sale of the year" not in text
    assert words.FY_NOTE in text and "Trading activity recorded by the journal" in text
    assert row["rows"]["tax_pack"] is None and sentence in row["rows"]["tax_note"]


def test_the_portfolio_down_is_a_sentence_too(on):
    on.portfolio.down = True
    build(on, "fy", period="FY2627")
    assert "could not be read from the portfolio" in kept(on, "fy")["text"]


def test_the_family_never_gets_the_paper(on):
    build(on, "fy", period="FY2627")
    row = kept(on, "fy")
    seen = periods.family_variant("fy", row["period_key"], row["rows"])
    assert set(seen) == {"kind", "period_key", "line", "counts"}
    assert "tax" not in json.dumps(seen) and "paise" not in json.dumps(seen)


def test_small_formats():
    assert [taxpack.rate_text(bp) for bp in (1250, 2000, 1000, 400, 1575)] == [
        "12.5%", "20%", "10%", "4%", "15.75%"]  # fmt: skip
    assert taxpack.qty_text(100_000_000) == "100" and taxpack.qty_text(500_000) == "0.5"
    assert taxpack.qty_text(1_234_567_000_000) == "12,34,567"
    assert taxpack.fy_text(2034) == "FY 2034-35" and taxpack.fy_text(1999) == "FY 1999-00"
    assert taxpack.rs(None) == "not given" and taxpack.rs(0) == "₹0.00"
    assert not taxpack.usable({"fy": 2026}) and not taxpack.usable(None)
    assert taxpack.usable(PACK)


# ---- dividends and buybacks in the pack (J6) ---------------------------------------------------
def with_dividends():
    """PK1 with the year's dividends as the portfolio's ``dividends.for_pack`` gives them: two
    payers (one above the line where tax is deducted, one still expected), and what a buyback
    paid that counts as a dividend."""
    pack = copy.deepcopy(PACK)
    pack["dividends"] = {
        "by_payer": [
            {"isin": "INE000A01010", "name": "Acme Industries", "expected_paise": 165_000,
             "confirmed_paise": 900_000, "total_paise": 1_065_000, "tds_likely": True},
            {"isin": "INE000B01018", "name": None, "expected_paise": 42_000,
             "confirmed_paise": 0, "total_paise": 42_000, "tds_likely": False},
        ],
        "buyback_paise": 6_000_000,
        "total_paise": 7_107_000,
        "tds_expected_paise": 106_500,
        "unconfirmed_count": 2,
    }  # fmt: skip
    return pack


def test_dividends_by_payer_and_a_buybacks_receipt_are_in_the_paper(on):
    pack = with_dividends()
    on.portfolio.answers["tax_pack"] = pack
    build(on, "fy", period="FY2627")
    row = kept(on, "fy")
    text = text_of(file_of(on, row))
    part = text[text.index("Dividends") : text.index("Charges as Upstox reports them")]
    for expected in (
        "Acme Industries", "₹1,650.00", "₹9,000.00", "₹10,650.00", "Likely",
        "INE000B01018", "₹420.00", "Not expected",  # a payer with no name is shown by its ISIN
        "Received in a buyback, counted as a dividend", "₹60,000.00",
        "Dividends in all", "₹71,070.00",
        "Tax likely deducted at source, about", "₹1,065.00",
        "2 dividends here are expected, not yet confirmed by you",
    ):  # fmt: skip
        assert expected in part, expected
    assert "Dividends: ₹71,070.00" in row["text"]
    assert row["rows"]["tax_pack"]["dividends"] == pack["dividends"]
    assert not SECTION.search(text)


def test_a_year_with_no_dividend_says_so_and_one_dividend_is_one():
    blocks = taxpack.dividend_blocks(PACK["dividends"])
    assert [type(b) for b in blocks] == [D.Heading, D.Paragraph]
    assert blocks[1].lines == ("No dividend is recorded for this financial year.",)
    assert "Dividends:" not in "\n".join(taxpack.pack_lines(PACK))
    one = with_dividends()["dividends"] | {"unconfirmed_count": 1, "buyback_paise": 0,
                                           "tds_expected_paise": None}  # fmt: skip
    said = [ln for b in taxpack.dividend_blocks(one) if isinstance(b, D.Paragraph)
            for ln in b.lines]  # fmt: skip
    assert said[0].startswith("1 dividend here is expected, not yet confirmed by you")
    labels = [r[0] for b in taxpack.dividend_blocks(one) if isinstance(b, D.Table)
              for r in b.rows]  # fmt: skip
    assert "Received in a buyback, counted as a dividend" not in labels
    assert "Tax likely deducted at source, about" not in labels
    # A pack from before dividends were read (no such block at all) prints no Dividends heading.
    assert taxpack.dividend_blocks(None) == ()


def test_each_buyback_is_listed_by_the_rule_of_its_date():
    """K1's three regimes as the pack hands them over: an exempt tender is listed and in no
    total, one taxed as a dividend shows its nil sale value and its loss, and one from 1 April
    2026 is an ordinary sale marked as a buyback. The journal prints what the pack says."""
    pack = copy.deepcopy(PACK)
    sale = pack["capital_gains"]["schedule"][2]
    assert sale["buyback"]  # PK1's own buyback of 20 Jul 2026: an ordinary sale
    middle = {**sale, "isin": "INE000M01019", "sale_day": 20260210, "sale_paise": 0,
              "receipt_paise": 6_000_000, "gain_paise": -4_000_000, "buyback": "dividend",
              "dividend_paise": 6_000_000}  # fmt: skip
    exempt = {**sale, "isin": "INE000E01015", "sale_day": 20240915, "status": "exempt_buyback",
              "buyback": "exempt"}  # fmt: skip
    pack["capital_gains"]["schedule"].append(middle)
    pack["capital_gains"]["exempt_buybacks"] = [exempt]
    blocks = taxpack.capital_blocks(pack["capital_gains"])
    tables_ = [b for b in blocks if isinstance(b, D.Table)]
    sales = next(t for t in tables_ if t.columns[-1].title == "Term")
    by_isin = {r[0]: r for r in sales.rows}
    assert by_isin["INE000C01016"][-1] == "Long, buyback"
    assert by_isin["INE000M01019"][5] == "₹0.00"  # the sale value used is nil
    assert by_isin["INE000M01019"][6] == D.Money(-4_000_000)
    aside = next(t for t in tables_ if t.columns[-1].title == "Why")
    assert [(r[0], r[-1]) for r in aside.rows] == [
        ("INE000E01015", "Buyback, exempt in your hands")
    ]
    assert "INE000E01015" not in by_isin
