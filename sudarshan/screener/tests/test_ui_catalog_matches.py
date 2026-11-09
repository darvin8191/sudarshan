"""The phone's catalogue (frontend/src/sudarshan/screener/catalog.ts) names what catalog.py names.

The service checks every screen against catalog.py, so a builder that offers a name the checker does
not know sends the owner a refusal, and one that lacks a name hides something the service can do.
The two tables are written apart; this fails when their names differ.
"""

from __future__ import annotations

import re
from pathlib import Path

from sudarshan.screener import catalog as cat

TS = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "src"
    / "sudarshan"
    / "screener"
    / "catalog.ts"
).read_text(encoding="utf-8")


def _block(name: str) -> str:
    """The text of one exported table, however the formatter has laid it out."""
    start = TS.index(f"export const {name}")
    return TS[start : TS.index("\n]\n", start)]


def _ids(block: str) -> list[str]:
    return re.findall(r"\bid: '([^']+)'", _block(block))


def _chunks(block: str) -> dict[str, str]:
    """Each entry's text by its id."""
    parts = re.split(r"(?=\bid: ')", _block(block))[1:]
    return {re.match(r"id: '([^']+)'", part).group(1): part for part in parts}


def test_fields_match():
    assert _ids("FIELDS: FieldInfo[]") == list(cat.FIELDS)


def test_indicators_match_with_their_parameters_and_outputs():
    assert _ids("INDICATORS: IndicatorInfo[]") == list(cat.IND)
    chunks = _chunks("INDICATORS: IndicatorInfo[]")
    for key, spec in cat.IND.items():
        assert re.findall(r"\bname: '([^']+)'", chunks[key]) == [p.name for p in spec.params], key
        outs = re.search(r"outs: \[([^\]]*)\]", chunks[key]).group(1)
        assert re.findall(r"'([^']+)'", outs) == list(spec.outs), key


def test_operators_and_options_match():
    assert _ids("OPERATORS: OperatorInfo[]") == list(cat.OPS)
    assert _ids("OPTIONS: OptionInfo[]") == list(cat.OPT)


def test_states_lists_and_limits_match():
    patterns = re.search(r"PATTERNS = \[([^\]]*)\]", TS, re.S).group(1)
    assert re.findall(r"'([^']+)'", patterns) == list(cat.PATTERNS)
    states = re.search(r"BUILDUP_STATES = \[([^\]]*)\]", TS, re.S).group(1)
    assert re.findall(r"'([^']+)'", states) == list(cat.BUILDUP_STATES)
    lists = re.search(r"INDEX_LISTS: [^=]*= \[(.*?)\n\]", TS, re.S).group(1)
    assert re.findall(r"id: '([^']+)'", lists) == list(cat.INDEX_LISTS)
    for name, value in (
        ("MAX_CLAUSES", cat.MAX_CLAUSES),
        ("MAX_DEPTH", cat.MAX_DEPTH),
        ("MAX_INSTANCES", cat.MAX_INSTANCES),
        ("NAME_MAX", cat.NAME_MAX),
    ):
        assert re.search(rf"export const {name} = {value}\b", TS), name
