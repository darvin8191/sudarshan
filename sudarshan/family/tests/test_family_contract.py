"""F0: the family contract is well formed, agrees with the files it names, and types.ts says the same."""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

from sudarshan.family import contract as c
from sudarshan.family import gentypes
from sudarshan.vault import keys

FAMILY = Path(__file__).resolve().parents[1]
REPO = FAMILY.parents[1]


def test_every_type_resolves():
    for name, spec in c.RECORDS.items():
        for f in c.parse_fields(spec):
            assert c.known_type(f.type), (name, f)
    for op in (*c.VIEWER_OPS, *c.ADMIN_OPS, *c.NUDGE_OPS, *c.INTERNAL_OPS):
        assert c.base_type(op.res) in c.RECORDS, op
        for f in c.parse_fields(op.req):
            assert c.known_type(f.type), (op.name, f)


def test_names_are_unique_and_the_tables_agree():
    for table in (c.VIEWER_OP_NAMES, c.ADMIN_OP_NAMES, c.ERROR_CODES, c.WORD_KEYS):
        assert len(set(table)) == len(table), table
    assert c.VIEWER_OP_NAMES == tuple(op.name for op in c.VIEWER_OPS)
    assert set(c.ERROR_CODES) <= set(c.WORD_KEYS)  # every code has its sentence
    names = [*c.ENUMS, *c.RECORDS, "ErrorCode", "ViewerOpName", "AdminOpName", "ViewerOps"]
    names += [gentypes.pascal(op.name) + "Req" for op in c.VIEWER_OPS]
    names += ["Admin" + gentypes.pascal(op.name) + "Req" for op in (*c.ADMIN_OPS, *c.NUDGE_OPS)]
    names += ["Internal" + gentypes.pascal(op.name) + "Req" for op in c.INTERNAL_OPS]
    assert len(set(names)) == len(names), "two TypeScript names collide"


def test_routes_methods_and_their_fields():
    seen = set()
    for op in c.VIEWER_OPS:
        assert op.auth in c.AUTHS
        assert op.method in ("GET", "POST", "PUT", "PATCH", "DELETE")
        assert op.route.startswith("/") and not op.route.endswith("/")
        fields = {f.name: f for f in c.parse_fields(op.req)}
        for part in re.findall(r"<(\w+)>", op.route):
            assert part in fields and not fields[part].optional, (op.name, part)
        assert (op.method, op.route) not in seen, op
        seen.add((op.method, op.route))
        if op.method in ("GET", "DELETE"):  # read from the path and query: no list, no record
            for f in fields.values():
                assert c.base_type(f.type) in c.PRIMITIVES or c.base_type(f.type) in c.ENUMS
                assert not f.type.endswith("[]"), (op.name, f)
    assert {op.name for op in c.VIEWER_OPS if op.auth == "public"} == {
        "join_info",
        "join",
        "login",
    }


def test_the_matcher_finds_every_route_and_its_parts():
    for op in c.VIEWER_OPS:
        path = re.sub(r"<(\w+)>", lambda m: f"x-{m.group(1)}", op.route)
        found = c.viewer_op(op.method, path)
        assert found is not None and found[0] is op, op
        assert found[1] == {p: f"x-{p}" for p in re.findall(r"<(\w+)>", op.route)}
    assert c.viewer_op("GET", "/devices/abc/more") is None
    assert c.viewer_op("PUT", "/me") is None
    assert c.viewer_op("GET", "/nothing") is None
    assert c.viewer_op("GET", "/me/")[0].name == "me"


def test_the_writes_are_every_route_that_is_not_a_get():
    assert {m for m, _ in c.VIEWER_WRITES} <= {"POST", "PUT", "PATCH", "DELETE"}
    assert ("POST", "/family/api/login") in c.VIEWER_WRITES
    assert len(c.VIEWER_WRITES) == sum(op.method != "GET" for op in c.VIEWER_OPS)


def test_the_purposes_are_the_vaults():
    assert c.PURPOSES["admin"].encode() == keys.FAMILY_ADMIN_INFO
    assert c.PURPOSES["nudge"].encode() == keys.FAMILY_NUDGE_INFO
    assert c.PURPOSES["internal"].encode() == keys.FAMILY_INTERNAL_INFO
    assert c.ADMIN_PRINCIPAL == "owner" and c.NUDGE_PRINCIPAL == "sudarshan-alerts"


def test_the_one_call_out_is_the_internal_watchlist_route():
    """Lane FB's route as its blueprint serves it (feat/family-b): the path, the purpose, who the
    family service says it is, at most 4 KiB, 1 to 50 ids, and lists tied to their created_at."""
    assert c.INTERNAL_PATH == "/sudarshan/family/internal/watchlists"
    assert c.INTERNAL_PRINCIPAL == c.SERVICE == "sudarshan-family"
    assert (c.LIMITS["internal_bytes"], c.LIMITS["internal_lists"]) == (4096, 50)
    (op,) = c.INTERNAL_OPS
    # F7 (feat/family-c), review M3: and the member the lists are for
    assert (op.name, c.parse_fields(op.req)) == ("watchlists", (
        c.Field("list_ids", "int[]"), c.Field("member_id", "int"), c.Field("role", "Role"),
    ))  # fmt: skip
    assert [f.name for f in c.record("InternalWatchlists")] == ["lists", "gone"]
    assert [f.name for f in c.record("InternalList")] == ["id", "name", "created_at", "rows"]
    assert [f.name for f in c.record("InternalRow")] == [
        "symbol", "exchange", "instrument_key", "name", "tick_size", "lot_size",
    ]  # fmt: skip


def test_the_bridges_two_codes():
    assert (c.ERROR_STATUS["unavailable"], c.ERROR_STATUS["not_confirmed"]) == (503, 504)


def test_the_nudge_op_is_what_the_alerts_send():
    from sudarshan.alerts import nudge

    assert nudge.FAMILY_URL == f"http://127.0.0.1:{c.PORT}{c.NUDGE_PATH}"
    assert (nudge.OP, nudge.PRINCIPAL) == (c.NUDGE_OPS[0].name, c.NUDGE_PRINCIPAL)
    assert {nudge.ORDERS, nudge.HOLDINGS} == set(c.NUDGE_KINDS)
    assert [f.name for f in c.parse_fields(c.NUDGE_OPS[0].req)] == ["kind", "sandbox", "at"]


def test_the_categories_are_the_privacy_tables():
    table = json.loads((FAMILY / "privacy_fields.json").read_text(encoding="utf-8"))
    assert tuple(table["categories"]) == c.CATEGORIES
    for role, shown in c.PRIVACY_DEFAULTS.items():
        assert role in c.ROLES and tuple(shown) == c.CATEGORIES
        assert not (shown["pnl"] and not shown["qty"]), "P&L only with quantities (Y24)"
    assert c.PRIVACY_DEFAULTS["viewer"] == {"pnl": True, "qty": True, "funds": False,
                                            "orders": True}  # fmt: skip
    assert not any(c.PRIVACY_DEFAULTS["child"].values())


def test_the_numbers_the_design_names():
    assert (c.PORT, c.ACCOUNT, c.SWITCH_ENV) == (5585, "sudarshan-family", "SUDARSHAN_FAMILY")
    assert c.COOKIE == "__Host-sudarshan_family" and c.CSRF_COOKIE.startswith("__Host-")
    assert c.WS_CLOSE_SIGNED_OUT == 4401
    lim = c.LIMITS
    assert (lim["max_members"], lim["max_devices"]) == (4, 3)
    assert (lim["handle_min"], lim["handle_max"], lim["password_min"]) == (3, 20, 12)
    assert (lim["session_days"], lim["idle_days"], lim["invite_hours"]) == (30, 14, 2)
    assert (lim["admin_bytes"], lim["nudge_bytes"], lim["viewer_bytes"]) == (65536, 4096, 65536)
    assert c.LOCKOUT == {"after": 5, "first_s": 60, "max_s": 900}  # review M2: short back-off
    assert c.AREA_LIMIT == {"failures": 20, "window_s": 600}
    assert (c.PHONE_COOKIE, c.PHONE_DAYS) == ("__Host-sudarshan_family_phone", 90)
    for status in c.ERROR_STATUS.values():
        assert 400 <= status < 600


def test_the_port_is_the_readmes():
    rows = [r for r in (REPO / "sudarshan" / "README.md").read_text().splitlines() if "5585" in r]
    assert rows and rows[0].startswith(f"| {c.PORT} |") and c.SERVICE in rows[0]


def test_the_contract_is_pure():
    tree = ast.parse((FAMILY / "contract.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module]
            assert set(names) <= {"__future__", "typing"}, names


def test_types_ts_is_what_the_contract_makes():
    assert gentypes.TARGET.exists(), "run: python -m sudarshan.family.gentypes"
    assert gentypes.check(), "types.ts is out of date: run python -m sudarshan.family.gentypes"


def test_check_flag_fails_on_a_stale_file(tmp_path):
    stale = tmp_path / "types.ts"
    stale.write_text(gentypes.render() + "// edited by hand\n", encoding="utf-8")
    assert not gentypes.check(stale)
    ok = tmp_path / "ok.ts"
    ok.write_text(gentypes.render(), encoding="utf-8")
    assert gentypes.check(ok)
    assert not gentypes.check(tmp_path / "missing.ts")


def test_the_generated_lines_fit_the_formatter():
    for line in gentypes.render().splitlines():
        assert len(line) <= 100, line
        assert '"' not in line or line.startswith("//"), line  # single quotes, as the formatter


def test_check_command_exits_zero_when_current():
    done = subprocess.run(
        [sys.executable, "-m", "sudarshan.family.gentypes", "--check"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=REPO,
    )
    assert done.returncode == 0, done.stderr
