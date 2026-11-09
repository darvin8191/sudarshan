"""The TradingView mapping (W18) against hand-typed vectors."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from sudarshan.watchlists import tvimport
from sudarshan.watchlists.words import tvimport as words

V = json.loads((Path(__file__).parent / "vectors" / "tvimport.json").read_text(encoding="utf-8"))
KNOWN = {tuple(k.split(":", 1)) for k in V["known"]}


def known(exchange, symbol):
    return (exchange, symbol) in KNOWN


def flat(item):
    base = f"{item.kind} {item.exchange}:{item.symbol}"
    return base + (f" {item.rule}" if item.rule else "")


@pytest.mark.parametrize("c", V["cases"], ids=lambda c: c["name"])
def test_vectors(c):
    plan = tvimport.parse(c["text"], known, c.get("follow_room", tvimport.FOLLOW_ROWS))
    assert [[s.name, [flat(i) for i in s.items]] for s in plan.sections] == c["sections"]
    assert [list(x) for x in plan.cuts] == c["cuts"]
    assert plan.found_sections == c["found_sections"]
    assert plan.found_symbols == c["found_symbols"]
    assert plan.matched == c["matched"]
    assert plan.repeats == c.get("repeats", 0)
    assert plan.follow == c.get("follow", 0)
    assert plan.not_rolling == c.get("not_rolling", 0)
    if "preview" in c:
        assert words.preview(plan) == c["preview"]


def make_plan(spec):
    secs = []
    for n, (name, count) in enumerate(spec):
        items = tuple(tvimport.Item("fixed", "NSE", f"S{n}_{i}") for i in range(count))
        secs.append(tvimport.Sec(name, items))
    return tvimport.Plan(tuple(secs), (), 0, 0, 0, 0, 0)


@pytest.mark.parametrize("c", V["split"], ids=lambda c: c["name"])
def test_split(c):
    chunks = tvimport.split(make_plan(c["sections"]))
    sizes = [sum(len(s.items) for s in ch) for ch in chunks]
    assert sizes == c["sizes"]
    assert all(n <= tvimport.LIST_ITEMS for n in sizes)
    if "chunk_sections" in c:
        assert [[s.name for s in ch] for ch in chunks] == c["chunk_sections"]
    if "line" in c:
        assert words.split_line("Swing", sizes) == c["line"]
    sent = [i for ch in chunks for s in ch for i in s.items]
    assert len(sent) == sum(n for _, n in c["sections"]) and len(set(sent)) == len(sent)


def test_follow_rows_do_not_count_against_a_lists_size():
    items = tuple(tvimport.Item("fixed", "NSE", f"S{i}") for i in range(250))
    items += tuple(tvimport.Item("follow", "NSE", f"F{i}", "month") for i in range(5))
    plan = tvimport.Plan((tvimport.Sec("A", items),), (), 0, 0, 0, 5, 0)
    assert len(tvimport.split(plan)) == 1


def test_hash_and_size_limit():
    assert tvimport.text_hash("a") == tvimport.text_hash("a") != tvimport.text_hash("b")
    assert not tvimport.too_big("x" * 65536) and tvimport.too_big("x" * 65537)


def test_words():
    assert words.room_line("Swing", 203) == "Swing · room for 203"
    assert words.done("Swing", 45, 2) == "Swing: 45 symbols in 2 sections."
    assert words.done("Swing", 1, 0) == "Swing: 1 symbol."
    plan = tvimport.parse("NSE:NIFTY1!,NSE:NIFTY2!,NSE:RELIANCE1!", known, 2)
    out = words.preview(plan)
    assert "NIFTY1! becomes a current-month FUT row that follows NIFTY." in out
    assert any(
        s.startswith("20 follow rows is the most a list holds: 1 future was added") for s in out
    )


def test_the_module_is_pure():
    for mod in (tvimport, words):
        tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert names <= {
            "__future__",
            "re",
            "hashlib",
            "typing",
            "collections.abc",
            "sudarshan.watchlists",
            "sudarshan.watchlists.tvimport",
        }
