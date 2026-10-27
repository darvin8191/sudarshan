"""The contract (PF0): kinds.py, types.ts and the README say one thing.

Three places state the same contract: core/kinds.py (the server), frontend/src/sudarshan/
portfolio/types.ts (the phone) and the README. Each is compared with the others here, so a change
to one that the rest do not follow fails the build. The core's purity (no I/O, no clock) is held
here too, and the rules that apply to every word the portfolio says.
"""

from __future__ import annotations

import ast
import base64
import json
import re
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from portfolio_ts import (
    TsError,
    exported_consts,
    exported_types,
    includes,
    ops_map,
    read_const,
)

from sudarshan.portfolio.core import kinds
from sudarshan.vault.keys import PORTFOLIO_API_INFO

REPO = Path(__file__).resolve().parents[3]
PACKAGE = REPO / "sudarshan" / "portfolio"
CORE = PACKAGE / "core"
TYPES_TS = REPO / "frontend" / "src" / "sudarshan" / "portfolio" / "types.ts"
README = PACKAGE / "README.md"


@pytest.fixture(scope="module")
def ts_text() -> str:
    return TYPES_TS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def readme() -> str:
    return README.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """The text with every run of whitespace one space, so a line break never splits a phrase."""
    return " ".join(text.split())


# -- types.ts says what kinds.py says ------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(kinds.SHARED_NUMBERS))
def test_a_number_is_the_same_on_the_phone(ts_text, name):
    assert read_const(ts_text, name) == kinds.SHARED_NUMBERS[name]


@pytest.mark.parametrize("name", sorted(kinds.SHARED_LISTS))
def test_a_list_has_the_same_members_in_the_same_order_on_the_phone(ts_text, name):
    assert read_const(ts_text, name) == list(kinds.SHARED_LISTS[name])


@pytest.mark.parametrize("name", sorted(kinds.SHARED_LABELS))
def test_labels_and_sentences_are_the_same_on_the_phone(ts_text, name):
    assert read_const(ts_text, name) == kinds.SHARED_LABELS[name]


@pytest.mark.parametrize("name", sorted(kinds.SHARED_STRINGS))
def test_a_string_is_the_same_on_the_phone(ts_text, name):
    assert read_const(ts_text, name) == kinds.SHARED_STRINGS[name]


def test_the_phone_has_no_constant_the_server_does_not_know(ts_text):
    known = (
        set(kinds.SHARED_NUMBERS)
        | set(kinds.SHARED_LISTS)
        | set(kinds.SHARED_LABELS)
        | set(kinds.SHARED_STRINGS)
        | {"OPS"}
    )
    assert set(exported_consts(ts_text)) == known


def test_every_list_has_its_union_type_on_the_phone(ts_text):
    unions = dict(re.findall(r"^export type (\w+) = \(typeof (\w+)\)\[number\]", ts_text, re.M))
    by_list = {const: type_name for type_name, const in unions.items()}
    assert set(by_list) == set(kinds.SHARED_LISTS)
    assert set(by_list.values()) <= exported_types(ts_text)


def test_the_ops_table_is_the_same_on_the_phone(ts_text):
    phone = read_const(ts_text, "OPS")
    server = []
    for op in kinds.OPS:
        row = {"name": op.name, "method": op.method, "route": op.route, "cls": op.cls}
        if op.query:
            row["query"] = [list(pair) for pair in op.query]
        if op.upload:
            row["upload"] = True
        if op.download:
            row["download"] = True
        server.append(row)
    assert phone == server


def test_the_ops_map_has_exactly_the_ops_and_real_types(ts_text):
    mapped = ops_map(ts_text)
    assert list(mapped) == list(kinds.OP_NAMES)
    exported = exported_types(ts_text)
    for op, (args, data) in mapped.items():
        for part in args.split("&"):
            assert part.strip() in exported, f"{op}: {part.strip()} is not exported"
        assert data in exported, f"{op}: {data} is not exported"


def test_a_route_with_an_id_takes_the_id(ts_text):
    mapped = ops_map(ts_text)
    for op in kinds.OPS:
        args = mapped[op.name][0]
        takes_id = any(includes(ts_text, part.strip(), "Targets") for part in args.split("&"))
        assert takes_id == ("<id>" in op.route), op.name


# -- the README says what kinds.py says ----------------------------------------------------------


def _table_rows(readme: str, first_cell: re.Pattern[str]) -> list[list[str]]:
    rows = []
    for line in readme.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and first_cell.fullmatch(cells[0]):
            rows.append(cells)
    return rows


def test_the_readme_table_lists_the_ops_the_server_has(readme, ts_text):
    rows = _table_rows(readme, re.compile(r"`[a-z_]+`"))
    ops = [row for row in rows if len(row) == 8]
    assert [row[0].strip("`") for row in ops] == list(kinds.OP_NAMES)
    mapped = ops_map(ts_text)
    for row, op in zip(ops, kinds.OPS, strict=True):
        name, method, route, limiter, query, request, answer, notes = row
        assert (method, route.strip("`"), limiter) == (op.method, op.route, op.cls), op.name
        pairs = [tuple(part.split(":")) for part in query.strip("`").split(", ") if part]
        assert pairs == list(op.query), op.name
        assert (request.strip("`"), answer.strip("`")) == mapped[op.name], op.name
        assert ("upload" in notes) == op.upload, op.name
        assert ("download" in notes) == op.download, op.name


def test_the_readme_error_table_gives_the_statuses_the_bridge_gives(readme):
    rows = _table_rows(readme, re.compile(r"`[a-z_]+`"))
    errors = {row[0].strip("`"): int(row[1]) for row in rows if len(row) == 4 and row[1].isdigit()}
    assert errors == kinds.ERROR_STATUS
    assert set(errors) == set(kinds.ERROR_CODES)


def test_the_readme_states_the_limits_the_bridge_enforces(readme):
    text = _flat(readme)
    limits, timeouts = kinds.LIMITS_PER_MIN, kinds.TIMEOUTS_S
    assert (
        f"reads {limits['read']}/min, writes {limits['write']}/min, imports {limits['import']}/min, "
        f"dry runs {limits['dry_run']}/min"
    ) in text
    assert (
        f"waits {timeouts['read']} s for a read, {timeouts['write']} s for a write, "
        f"{timeouts['import']} s for an import and {timeouts['upstox']} s for the Upstox ops"
    ) in text
    assert f"at most {kinds.API_MAX_BYTES // 1024} KiB" in text
    assert f"{kinds.IMPORT_MAX_BYTES / 2**20:g} MiB" in text
    assert f"{kinds.LOCAL_HOST}:{kinds.DEFAULT_HTTP_PORT}" in text
    assert PORTFOLIO_API_INFO.decode() in text
    assert (
        f"at most {kinds.IMPORT_MAX_FILE_BYTES:,} bytes and {kinds.IMPORT_MAX_ROWS:,} rows" in text
    )
    assert "/sudarshan/portfolio/api/*" in text
    assert kinds.API_PREFIX == "/sudarshan/portfolio/api"


# -- kinds.py agrees with itself -----------------------------------------------------------------


def test_the_ops_are_well_formed():
    names = [op.name for op in kinds.OPS]
    assert len(names) == len(set(names)), "an op is listed twice"
    routes = [(op.method, op.route) for op in kinds.OPS]
    assert len(routes) == len(set(routes)), "a method and route is used twice"
    classes = {"read", "write", "import", "upstox"}
    for op in kinds.OPS:
        assert op.method in {"GET", "POST", "PATCH", "PUT", "DELETE"}, op.name
        assert op.cls in classes, op.name
        assert op.route.startswith("/"), op.name
        assert set(re.findall(r"<(\w+)>", op.route)) <= {"id"}, op.name
        assert (op.cls in {"read", "upstox"}) == (op.method == "GET"), op.name
        if op.query:
            assert op.method == "GET", op.name
        arguments = [name for name, _ in op.query]
        assert len(arguments) == len(set(arguments)), op.name
        assert all(kind in kinds.QUERY_TYPES for _, kind in op.query), op.name
        assert not op.upload or op.method == "POST", op.name
        assert not op.download or op.method == "GET", op.name
    assert set(kinds.DRY_RUN_OPS) <= set(kinds.OP_NAMES)
    assert classes <= set(kinds.TIMEOUTS_S)
    assert {"read", "write", "import", "dry_run"} == set(kinds.LIMITS_PER_MIN)
    assert tuple(kinds.OP_NAMES) == tuple(op.name for op in kinds.OPS)


def test_every_error_code_has_a_status_and_the_ones_the_bridge_makes_have_words():
    assert set(kinds.ERROR_STATUS) == set(kinds.ERROR_CODES)
    for code in ("not_ready", "not_confirmed", "unauthorized", "forbidden", "rate_limited"):
        assert code in kinds.WORDS, code
    assert "unavailable" in kinds.ERROR_CODES and "not_answering" in kinds.WORDS
    assert all(400 <= status <= 599 for status in kinds.ERROR_STATUS.values())


def test_labels_cover_exactly_their_lists():
    pairs = {
        "ACTIVITY_KIND_LABELS": "ACTIVITY_KINDS",
        "ACCOUNT_KIND_LABELS": "ACCOUNT_KINDS",
        "TAB_KIND_LABELS": "TAB_KINDS",
        "ASSET_CLASS_LABELS": "ASSET_CLASSES",
        "BENCHMARK_LABELS": "BENCHMARKS",
    }
    for labels, listed in pairs.items():
        assert list(kinds.SHARED_LABELS[labels]) == list(kinds.SHARED_LISTS[listed]), labels


def test_a_list_has_no_member_twice():
    for name, members in kinds.SHARED_LISTS.items():
        assert len(members) == len(set(members)), name


def test_holding_kinds_are_the_asset_kinds_that_hold_value():
    assert set(kinds.HOLDING_KINDS) == set(kinds.ASSET_KINDS) - {"liability"}
    assert set(kinds.TODAY_MODES) == set(kinds.TODAY_STATES) - {"unknown"}
    assert set(kinds.UPSTOX_PARTS) == {"holdings", "positions", "mtf", "funds"}
    assert set(kinds.IMPORT_ACTIONS) >= {"BUY", "SELL", "TRANSFER_IN", "TRANSFER_OUT"}
    assert set(kinds.ACTIVITY_KINDS) >= {"BUY", "SELL", "SPLIT", "BONUS", "RIGHTS"}


# -- the units ------------------------------------------------------------------------------------


def test_the_scales_agree_and_stay_inside_a_browser_number():
    assert kinds.VALUE_DIVISOR * kinds.PAISE_PER_RUPEE == kinds.QTY_SCALE * kinds.PRICE_SCALE
    # One unit at one rupee is a hundred paise.
    assert kinds.QTY_SCALE * kinds.PRICE_SCALE // kinds.VALUE_DIVISOR == kinds.PAISE_PER_RUPEE
    assert kinds.MAX_QTY_U == kinds.MAX_UNITS * kinds.QTY_SCALE
    for limit in (kinds.MAX_QTY_U, kinds.MAX_PRICE_U, kinds.MAX_PAISE):
        assert limit < 2**53
    # Their product is what needs BigInt on the phone.
    assert kinds.MAX_QTY_U * kinds.MAX_PRICE_U > 2**53
    assert kinds.PCT_DECIMALS == 4


def _sealed_size(args: dict) -> int:
    body = {"op": "import_preview", "nonce": "0" * 32, "args": args}
    plain = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return len(Fernet(Fernet.generate_key()).encrypt(plain))


def test_the_largest_import_fits_its_seal_as_text_and_would_not_as_base64():
    row = '2026-03-12,BUY,RELIANCE,NSE,INE002A01018,10,1428.25,14282.50,20.00,"Demat",,,"note"\r\n'
    text = (row * (kinds.IMPORT_MAX_FILE_BYTES // len(row) + 1)).encode("ascii")
    text = text[: kinds.IMPORT_MAX_FILE_BYTES].decode("ascii")
    assert len(text.encode("utf-8")) == kinds.IMPORT_MAX_FILE_BYTES
    args = {"id": 1, "format": "generic", "filename": "t.csv", "account_id": 1}
    assert _sealed_size({**args, "text": text}) <= kinds.IMPORT_MAX_BYTES
    as_base64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    assert _sealed_size({**args, "data": as_base64}) > kinds.IMPORT_MAX_BYTES


def test_an_ordinary_request_is_far_below_the_api_limit():
    entry = {"kind": "BUY", "trade_date": 20261108, "account_id": 1, "note": "n" * kinds.NOTE_MAX}
    assert _sealed_size({"id": 1, "version": 1, "request_id": "u" * 36, "entry": entry}) < (
        kinds.API_MAX_BYTES // 4
    )


# -- the words ------------------------------------------------------------------------------------

_FORBIDDEN_WORDS = re.compile(
    r"\b(arm|armed|arming|paper trading|virtual trading|http|exception|traceback|status code)\b",
    re.IGNORECASE,
)
_A_STATUS_CODE = re.compile(r"\b[45]\d\d\b")


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def test_no_word_a_trader_reads_is_a_developer_word():
    for label in (*_strings(kinds.WORDS), *_strings(kinds.SHARED_LABELS)):
        assert label.isascii(), label
        assert not _FORBIDDEN_WORDS.search(label), label
    for sentence in kinds.WORDS.values():  # "NIFTY 500" is a name; a sentence has no status code
        assert not _A_STATUS_CODE.search(sentence), sentence


def test_the_sentences_the_design_spells_out_are_kept_word_for_word():
    assert kinds.WORDS["off"] == "The portfolio is switched off on the server."
    assert (
        kinds.WORDS["not_confirmed"] == "Not confirmed: open the tab to see whether it was saved."
    )
    assert kinds.WORDS["version_conflict"].startswith("This tab was changed on another screen. ")
    assert str(kinds.MAX_TABS) in kinds.WORDS["tab_limit"]
    assert f"{kinds.IMPORT_MAX_FILE_BYTES // 1000} KB" in kinds.WORDS["import_too_big"]


#: Emoji, dingbats, symbols and arrows-as-icons, by code point: this file holds none itself.
_ICON_RANGES = (
    (0x1F000, 0x1FAFF),
    (0x2600, 0x27BF),
    (0x2B00, 0x2BFF),
    (0xFE0F, 0xFE0F),
    (0x200D, 0x200D),
)


def icons_in(text: str) -> list[str]:
    return [ch for ch in text if any(low <= ord(ch) <= high for low, high in _ICON_RANGES)]


def test_no_icon_or_emoji_in_any_portfolio_file():
    checked = 0
    for root in (PACKAGE, TYPES_TS.parent):
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".md", ".ts", ".tsx", ".json"} or not path.is_file():
                continue
            checked += 1
            found = icons_in(path.read_text(encoding="utf-8"))
            assert not found, f"{path.relative_to(REPO)} has an icon or emoji: {found[:3]}"
    assert checked >= 5


def test_the_icon_check_sees_an_icon():
    assert icons_in("plain ASCII and the rupee sign " + chr(0x20B9)) == []
    for code in (0x1F600, 0x2705, 0x2713, 0x2B50, 0xFE0F, 0x200D):
        assert icons_in("a" + chr(code) + "b") == [chr(code)]


# -- the core does no I/O ------------------------------------------------------------------------

PURE_STDLIB = frozenset(
    {
        "__future__",
        "bisect",
        "collections",
        "dataclasses",
        "datetime",
        "decimal",
        "enum",
        "fractions",
        "functools",
        "itertools",
        "math",
        "operator",
        "re",
        "typing",
    }
)
BANNED_CALLS = frozenset({"open", "print", "input", "exec", "eval", "compile", "__import__"})
BANNED_ATTRIBUTES = frozenset(
    {"now", "today", "utcnow", "read_text", "write_text", "read_bytes", "write_bytes", "unlink"}
)


def impurities(source: str) -> list[str]:
    """Why this source is not pure: an import outside the allowed few, a clock, a file, a print."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.partition(".")[0]
                if top not in PURE_STDLIB and not alias.name.startswith("sudarshan.portfolio.core"):
                    found.append(f"imports {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:  # a relative import stays inside the package
                continue
            if module.partition(".")[0] not in PURE_STDLIB and not module.startswith(
                "sudarshan.portfolio.core"
            ):
                found.append(f"imports from {module}")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in BANNED_CALLS:
                found.append(f"calls {func.id}()")
            if isinstance(func, ast.Attribute) and func.attr in BANNED_ATTRIBUTES:
                found.append(f"calls .{func.attr}()")
        elif isinstance(node, ast.Global):
            found.append("uses global state")
    return found


@pytest.mark.parametrize("path", sorted(CORE.glob("*.py")), ids=lambda path: path.name)
def test_a_core_module_does_no_io(path):
    assert impurities(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "source",
    [
        "import os",
        "import logging",
        "import time",
        "from threading import Lock",
        "import sqlite3",
        "from sudarshan.alerts.market import grain",
        "from datetime import datetime\nx = datetime.now()",
        "from datetime import date\nx = date.today()",
        "print('x')",
        "open('f')",
        "from pathlib import Path\nPath('x').read_text()",
        "def f():\n    global X\n    X = 1",
    ],
)
def test_the_purity_check_catches_what_it_is_there_for(source):
    assert impurities(source) != []


def test_the_purity_check_lets_the_pure_imports_through():
    assert impurities("from decimal import Decimal\nfrom fractions import Fraction\n") == []
    assert impurities("from sudarshan.portfolio.core.kinds import QTY_SCALE") == []
    assert impurities("from .kinds import QTY_SCALE") == []


# -- the reader of types.ts is itself honest -----------------------------------------------------


def test_the_reader_reads_what_biome_may_write():
    text = """
// a comment
export const ONE = 1_000
export const TWO: Record<A, string> = {
  a: 'x',
  'b c': "it's",
  d: ['p', 'q'],
} as const
export const THREE = ['u', 'v',
  'w'] as const /* tail */
export const FOUR = [{ k: 1, l: [true, null] }]
"""
    assert read_const(text, "ONE") == 1000
    assert read_const(text, "TWO") == {"a": "x", "b c": "it's", "d": ["p", "q"]}
    assert read_const(text, "THREE") == ["u", "v", "w"]
    assert read_const(text, "FOUR") == [{"k": 1, "l": [True, None]}]
    assert exported_consts(text) == ["ONE", "TWO", "THREE", "FOUR"]


def test_the_reader_refuses_what_it_does_not_understand():
    with pytest.raises(TsError):
        read_const("export const X = 1 + 2 * foo()\n", "X")
    with pytest.raises(TsError):
        read_const("export const X = `template`\n", "X")
    with pytest.raises(TsError):
        read_const("export const Y = 1\n", "X")
