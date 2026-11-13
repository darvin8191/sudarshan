"""The TradingView export mapping (W20) against hand-typed vectors, and back through W18's
import."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sudarshan.watchlists import tvexport, tvimport
from sudarshan.watchlists.words import tvexport as words

HERE = Path(__file__).parent / "vectors"
V = json.loads((HERE / "tvexport.json").read_text(encoding="utf-8"))
IMPORT = json.loads((HERE / "tvimport.json").read_text(encoding="utf-8"))
KNOWN = {tuple(k.split(":", 1)) for k in IMPORT["known"]}


def known(exchange, symbol):
    return (exchange, symbol) in KNOWN


def entry(spec: str) -> tvexport.Entry:
    p = spec.split()
    if p[0] == "follow":
        ex, _, sym = p[1].partition(":")
        return tvexport.Entry("follow", ex, sym, p[2])
    if p[0] == "fut":
        ex, _, sym = p[1].partition(":")
        cont = None if p[3] == "none" else p[3]
        index = p[4:] == ["index"]  # the master lists its underlying as an index's
        return tvexport.Entry("row", ex, sym, None, "FUT", None, None, p[2], cont, index=index)
    if p[0] == "opt":
        ex, _, sym = p[1].partition(":")
        return tvexport.Entry(
            "row", ex, sym, None, p[5], p[3], float(p[4]), p[2], None, p[6] == "today"
        )
    ex, _, sym = p[0].partition(":")
    return tvexport.Entry("row", ex, sym)


def blocks(case):
    return [tvexport.Block(name, tuple(entry(s) for s in specs)) for name, specs in case["blocks"]]


def flat(item):
    return f"{item.kind} {item.exchange}:{item.symbol}" + (f" {item.rule}" if item.rule else "")


@pytest.mark.parametrize("c", V["cases"], ids=lambda c: c["name"])
def test_vectors(c):
    out = tvexport.build(blocks(c))
    assert out.text == c["text"]
    assert (out.symbols, out.sections) == (c["symbols"], c["sections"])
    assert out.commas == c.get("commas", 0) and out.repeats == c.get("repeats", 0)
    assert len(out.follows) == c.get("follows", 0)
    assert len(out.contracts) == c.get("contracts", 0)
    assert len(out.rolled) == c.get("rolled", 0)
    assert [list(x) for x in out.cuts] == c.get("cuts", [])


@pytest.mark.parametrize("c", [c for c in V["cases"] if "roundtrip" in c], ids=lambda c: c["name"])
def test_export_then_import_gives_the_same_symbols_and_sections(c):
    plan = tvimport.parse(tvexport.build(blocks(c)).text, known)
    assert plan.cuts == () and plan.repeats == 0
    assert [[s.name, [flat(i) for i in s.items]] for s in plan.sections] == c["roundtrip"]


def test_every_import_vector_that_matched_exports_back_to_itself():
    """The reverse of W18's own vectors: what an import made is what an export writes back."""
    for c in IMPORT["cases"]:
        if "follow_room" in c or c["matched"] == 0 or c["name"].startswith("a repeated section"):
            continue
        plan = tvimport.parse(c["text"], known)
        bl = []
        for sec in plan.sections:
            ents = []
            for i in sec.items:
                if i.kind == "follow":
                    ents.append(tvexport.Entry("follow", i.exchange, i.symbol, i.rule))
                else:
                    ents.append(tvexport.Entry("row", i.exchange, i.symbol))
            bl.append(tvexport.Block(sec.name, tuple(ents)))
        again = tvimport.parse(tvexport.build(bl).text, known)
        shape = lambda p: [[s.name, [flat(i) for i in s.items]] for s in p.sections]  # noqa: E731
        assert shape(again) == shape(plan), c["name"]


def test_a_section_name_is_never_split_by_the_text():
    out = tvexport.build([tvexport.Block("A,B,C", (tvexport.Entry("row", "NSE", "TCS"),))])
    assert out.text == "###ABC,NSE:TCS" and "," not in out.text.split(",")[0].lstrip("#")
    assert len(tvimport.parse(out.text, known).sections) == 1


def test_the_words():
    c = next(c for c in V["cases"] if c["name"].startswith("a comma"))
    out = tvexport.build(blocks(c))
    assert words.all_words("Swing", out) == [
        "Swing: 3 symbols in 3 sections, ready for TradingView.",
        "2 section names had a comma, which TradingView cannot read; it was left out.",
    ]
    one = tvexport.build([tvexport.Block("A,B", ())])
    assert words.commas(one.commas) == (
        "1 section name had a comma, which TradingView cannot read; it was left out."
    )
    c = next(c for c in V["cases"] if c["name"].startswith("rows that follow"))
    said = words.all_words("Swing", tvexport.build(blocks(c)))
    assert (
        said[1]
        == "NSE:NIFTY1! is the current-month future of NIFTY, and follows it on TradingView."
    )
    assert (
        said[2] == "NSE:NIFTY2! is the next-month future of NIFTY, and follows it on TradingView."
    )
    assert said[4] == "Left out until its TradingView form is checked: 1 index future (FINNIFTY)."
    c = next(c for c in V["cases"] if c["name"] == "a contract an ATM or strike row shows today")
    assert words.all_words("Swing", tvexport.build(blocks(c)))[1:] == [
        "2 at-the-money or strike rows were written as the contract they show today: "
        "NSE:NIFTY261124C25800, NSE:NIFTY261124P25800. "
        "They will not follow the price on TradingView.",
        "Left out until its TradingView form is checked: 1 BSE option (BFO:SENSEX26NOV2680000CE).",
    ]
    c = next(c for c in V["cases"] if c["name"].startswith("what has no"))
    said = words.all_words("Swing", tvexport.build(blocks(c)))
    assert said[1:] == [
        "These indices have no name TradingView reads: NSE_INDEX:NIFTYBANKX.",
        "These are on exchanges TradingView lists cannot take here, so they are left out: "
        "MCX:GOLDM26DEC26FUT, CDS:USDINR26NOV26FUT, GOLD.",
        "These are futures of one month. TradingView lists take only the rolling contract "
        "(NAME1! and NAME2!), so they are left out: NFO:NIFTY27JAN27FUT.",
        "Left out until its TradingView form is checked: 1 index future (NIFTYBANKX).",
    ]
    c = next(c for c in V["cases"] if c["name"].startswith("index futures other than"))
    said = words.all_words("Swing", tvexport.build(blocks(c)))
    assert said[-1] == (
        "Left out until their TradingView form is checked: 7 index futures (MIDCPNIFTY, "
        "NIFTYNXT50, SENSEX, BANKEX, NFO:FINNIFTY26NOV26FUT, NFO:NIFTYFPI26NOV26FUT, "
        "BFO:SENSEX26NOV26FUT) and 1 BSE option (BFO:BANKEX26NOV2660000PE)."
    )


def test_an_index_future_the_master_names_is_held_back_and_a_stock_future_is_not():
    """Review M3 with M1's rule: which futures are an index's is the master's answer (``index``),
    so an index future outside the alias table is held back too, and a stock's is written."""

    def fut(underlying: str, index: bool) -> tvexport.Entry:
        sym = f"{underlying}26NOV26FUT"
        return tvexport.Entry("row", "NFO", sym, None, "FUT", None, None, underlying, "month",
                              index=index)  # fmt: skip

    assert tvexport.tv_symbol(fut("NIFTYFPI", True)) == (None, "unchecked_future")
    assert tvexport.tv_symbol(fut("NIFTYFPI", False)) == ("NSE:NIFTYFPI1!", None)
    assert tvexport.tv_symbol(fut("NIFTY", True)) == ("NSE:NIFTY1!", None)
    assert tvexport.tv_symbol(fut("BANKNIFTY", True)) == ("NSE:BANKNIFTY1!", None)
    assert tvexport.tv_symbol(fut("BAJAJ-AUTO", False)) == ("NSE:BAJAJ_AUTO1!", None)


def test_the_import_holds_back_what_the_export_holds_back():
    """Review M3: the same futures and BSE options are left out on the way in, with the same
    sentence; an index with no futures at all is a follow row the op says has none."""
    text = "NSE:CNXFINANCE1!,BSE:SENSEX1!,BSE:SENSEX261126C80000,NSE:NIFTY1!"
    plan = tvimport.parse(text, known)
    assert [c.reason for c in plan.cuts] == ["unchecked_future", "unchecked_future",
                                             "unchecked_option"]  # fmt: skip
    assert [i.symbol for s in plan.sections for i in s.items] == ["NIFTY"]
    vix = tvimport.parse("NSE:INDIAVIX1!", known, futures=lambda exchange, symbol: False)
    assert vix.cuts == () and vix.sections[0].items[0].symbol == "INDIAVIX"
    nothing = tvexport.build(
        [tvexport.Block(None, (tvexport.Entry("row", "MCX", "GOLD", None, "FUT"),))]
    )
    assert (
        words.all_words("Swing", nothing)[0] == "Nothing in Swing can be exported to TradingView."
    )
    assert "—" not in json.dumps(V)
