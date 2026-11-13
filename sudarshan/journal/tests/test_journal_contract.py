"""The contract (JF0): well formed, and the generated types.ts says the same."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

from sudarshan.journal import gentypes
from sudarshan.journal.core import kinds as k

CORE = Path(__file__).resolve().parents[1] / "core"


def test_every_type_resolves():
    for name, spec in k.RECORDS.items():
        for f in k.parse_fields(spec):
            assert k.known_type(f.type), (name, f)
    for op in (*k.OPS, *k.SERVICE_OPS):
        assert k.base_type(op.res) in k.RECORDS, op
        for f in k.parse_fields(op.req):
            assert k.known_type(f.type), (op.name, f)


def test_op_names_cover_the_tables_exactly():
    table = [op.name for op in (*k.OPS, *k.SERVICE_OPS)]
    assert len(set(table)) == len(table)
    assert set(table) == set(k.OP_NAMES)
    assert len(set(k.OP_NAMES)) == len(k.OP_NAMES)


def test_the_design_ops_are_there():
    wanted = {
        "state", "trips", "trip_get", "trip_update", "tags_list", "tag_create", "tag_update",
        "playbooks_list", "playbook_create", "playbook_update", "analytics", "reports_list",
        "report_get", "report_file", "report_build", "settings_get", "settings_set",
        "delivery", "reconcile_now", "family_day",
    }  # fmt: skip
    assert wanted <= set(k.OP_NAMES)


def test_classes_methods_and_paths():
    seen = set()
    for op in k.OPS:
        assert op.cls in k.LIMITS_PER_MIN
        assert op.method in ("GET", "POST", "PUT", "PATCH", "DELETE")
        fields = {f.name for f in k.parse_fields(op.req)}
        for p in re.findall(r"<(\w+)>", op.route):
            assert p in fields, (op.name, p)
        assert (op.method, op.route) not in seen
        seen.add((op.method, op.route))
        if op.method in ("GET", "DELETE"):
            for f in k.parse_fields(op.req):
                assert not f.type.endswith("[]"), (op.name, f)


def test_the_two_service_purposes_open_only_what_the_design_says():
    assert set(k.DELIVERY_OPS) == {"report_get", "report_file", "delivery"}
    assert set(k.FAMILY_OPS) == {"family_day"}
    assert len(set(k.PURPOSES.values())) == len(k.PURPOSES)
    assert k.PORT == 5586


def test_units_agree():
    assert k.VALUE_DIVISOR * k.PAISE_PER_RUPEE == k.PRICE_SCALE
    assert set(k.INTRADAY_PRODUCTS).isdisjoint(k.CARRY_PRODUCTS)
    assert set(k.HOLD_BUCKET_LABELS) == set(k.HOLD_BUCKETS)
    assert set(k.REPORT_TITLES) == set(k.REPORT_KINDS)


def test_no_money_field_is_a_float():
    for name, spec in k.RECORDS.items():
        for f in k.parse_fields(spec):
            if f.name.endswith(("_paise", "_u")):
                assert k.base_type(f.type) == "int", (name, f)


def test_the_screen_words_never_use_the_code_words():
    banned = ("scratch", "breakeven", "paper", "arm")
    for text in [*k.REPORT_TITLES.values(), *k.HOLD_BUCKET_LABELS.values()]:
        assert not any(w in text.lower().split() for w in banned), text


def test_the_core_is_pure():
    """No I/O, network, clock or logging import anywhere under core/ (tests excepted)."""
    banned = {
        "logging", "socket", "sqlite3", "subprocess", "requests", "httpx", "urllib", "asyncio",
        "threading", "time", "os", "sys", "random",
    }  # fmt: skip
    for path in sorted(CORE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module.split(".")[0]]
            assert not (set(names) & banned), (path.name, names)
            if isinstance(node, ast.Attribute) and node.attr in ("now", "utcnow", "today"):
                raise AssertionError(f"{path.name} reads the clock: {node.attr}")


def test_types_ts_is_what_the_contract_makes():
    assert gentypes.TARGET.exists(), "run: python -m sudarshan.journal.gentypes"
    assert gentypes.check(), "types.ts is out of date: run python -m sudarshan.journal.gentypes"


def test_check_flag_fails_on_a_stale_file(tmp_path):
    stale = tmp_path / "types.ts"
    stale.write_text(gentypes.render() + "// edited by hand\n", encoding="utf-8")
    assert not gentypes.check(stale)
    ok = tmp_path / "ok.ts"
    ok.write_text(gentypes.render(), encoding="utf-8")
    assert gentypes.check(ok)
    assert not gentypes.check(tmp_path / "missing.ts")


def test_check_command_exits_zero_when_current():
    done = subprocess.run(
        [sys.executable, "-m", "sudarshan.journal.gentypes", "--check"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
