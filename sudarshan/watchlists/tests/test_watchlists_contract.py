"""The contract (W3): well formed, and the generated types.ts says the same."""

from __future__ import annotations

import re
import subprocess
import sys

from sudarshan.watchlists import contract as c
from sudarshan.watchlists import gentypes


def test_every_type_resolves():
    for name, spec in c.RECORDS.items():
        for f in c.parse_fields(spec):
            assert c.known_type(f.type), (name, f)
    for op in (*c.OPS, *c.INTERNAL_OPS):
        assert op.res in c.RECORDS, op
        for f in c.parse_fields(op.req):
            assert c.known_type(f.type), (op.name, f)


def test_op_names_unique_and_classes_known():
    assert len(set(c.OP_NAMES)) == len(c.OP_NAMES)
    for op in c.OPS:
        assert op.cls in c.LIMITS_PER_MIN
        assert op.method in ("GET", "POST", "PUT", "PATCH", "DELETE")


def test_the_design_table_is_all_there():
    wanted = {
        "state", "lists", "view", "symbol", "resolve", "strikes", "search", "ranked",
        "tv_preview", "section_add", "section_edit", "section_remove", "arrange", "copy",
        "favourites", "flags", "mark", "pref", "row_add", "row_edit", "row_remove", "tv_import",
    }  # fmt: skip
    assert wanted <= set(c.OP_NAMES)


def test_path_parameters_are_request_fields():
    for op in c.OPS:
        fields = {f.name for f in c.parse_fields(op.req)}
        for p in re.findall(r"<(\w+)>", op.route):
            assert p in fields, (op.name, p)


def test_routes_are_unique_per_method():
    seen = set()
    for op in c.OPS:
        key = (op.method, op.route)
        assert key not in seen
        seen.add(key)


def test_query_ops_use_primitives_only():
    for op in c.OPS:
        if op.method in ("GET", "DELETE"):
            for f in c.parse_fields(op.req):
                assert c.base_type(f.type) in c.PRIMITIVES or c.base_type(f.type) in c.ENUMS
                assert not f.type.endswith("[]"), (op.name, f)


def test_reads_never_write_and_writes_are_listed():
    for _method, route in c.WRITE_ROUTES:
        assert route.startswith(c.API_PREFIX)
    write = {op.name for op in c.OPS if op.cls == "write"}
    assert len(c.WRITE_ROUTES) == len(write)
    assert not any(op.cls == "write" and op.method == "GET" for op in c.OPS)


def test_error_codes_have_a_status():
    assert set(c.ERROR_CODES) == set(c.ERROR_STATUS)


def test_limits_match_the_design():
    assert c.LIMITS["list_items"] == 250 and c.LIMITS["window"] == 120
    assert c.LIMITS["sections_per_list"] == 20 and c.LIMITS["section_name"] == 40
    assert c.LIMITS["note"] == 500 and c.LIMITS["favourite_slots"] == 7
    assert c.LIMITS_PER_MIN == {"read": 120, "write": 60, "search": 300, "ranked": 30, "preview": 6}


def test_no_word_a_trader_must_not_read():
    text = gentypes.render() + open(c.__file__, encoding="utf-8").read()
    assert not re.search(r"\barm(ed|ing)?\b", text, re.I)
    assert "Live" not in text.replace("LIVE", "")


def test_parse_rejects_a_field_without_a_type():
    import pytest

    with pytest.raises(ValueError):
        c.parse_fields("a:int, b")


def test_types_ts_is_current():
    assert gentypes.check(), "run: python -m sudarshan.watchlists.gentypes"


def test_check_flag_fails_on_a_difference(tmp_path):
    assert gentypes.render() != gentypes.render().replace("export const ROOM", "export const ROOMX")
    assert not gentypes.check(tmp_path / "missing.ts")
    stale = tmp_path / "types.ts"
    stale.write_text(gentypes.render() + "// drift\n", encoding="utf-8")
    assert not gentypes.check(stale)


def test_cli_check_passes():
    r = subprocess.run(
        [sys.executable, "-m", "sudarshan.watchlists.gentypes", "--check"],
        capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr


def test_the_enum_golden_values():
    """Hand-typed from the design: a rename in the contract must be a deliberate one."""
    assert c.ENUMS["FollowKind"] == ("atm_option", "month_future", "strike_pair")
    assert c.ENUMS["ExpiryRule"] == ("nearest", "next", "month", "next_month")
    assert c.ENUMS["Sort"] == ("list", "change", "price", "name", "exchange")
    assert len(c.ENUMS["RankCategory"]) == 12
