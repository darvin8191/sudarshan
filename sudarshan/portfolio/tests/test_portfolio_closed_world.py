"""The closed world of the family masks (phase P7; design p7 section 10, mutation target 20).

A family member is given the answers of ``FAMILY_OPS``, passed through the one mask table
(``sudarshan/family/privacy_fields.json``). A field that table does not name reaches the member. So
every key of every answer a viewer can be given, and of every answer of phase P7 (none of whose ops
is a family op, and whose fields the table hides anyway), must be one of:

* hidden by a category of the table, or by its ``cost`` list (which follows P&L): its value,
  and everything under it, goes;
* in the table's ``never_hidden``: the live price's anchors and the public facts beside them;
* in ``STRUCTURAL`` below: a key that carries no figure of the owner's (a name, an id, a day, a
  state, a container whose members are judged one by one).

Two walks: the answer types the contract declares (``types.ts``, from each op's ``data`` type down
through interfaces, inline objects and unions), and the service's real answers on a store that
holds entries, a shared tab and an Upstox book. A new field fails here until somebody decides which
of the three it is, and that decision is one line, which the reviewer reads.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from portfolio_ts import (
    aliases,
    exported_types,
    interfaces,
    member_types,
    ops_map,
    parents,
    type_refs,
)
from test_portfolio_purposes import ALL, scenario
from test_portfolio_purposes import fake as fake  # noqa: F401  (the fake Upstox fixture)

from sudarshan.portfolio import privacy, serialize
from sudarshan.portfolio.core import kinds

REPO = Path(__file__).resolve().parents[3]
TYPES_TS = REPO / "frontend" / "src" / "sudarshan" / "portfolio" / "types.ts"
TABLE = json.loads(privacy.FIELDS_FILE.read_text(encoding="utf-8"))
#: Everything some choice of the owner's hides: the categories, and ``cost``, which follows P&L.
MASK = frozenset(
    entry for entries in (*TABLE["categories"].values(), TABLE["cost"]) for entry in entries
)
NEVER = frozenset(TABLE["never_hidden"])

#: Keys that carry no figure of the owner's, reviewed one by one. A figure (money, a quantity, a
#: return, a price the owner paid) never goes here: it goes into a category of the table.
STRUCTURAL = frozenset(
    {
        # who and what: names, ids, instruments, kinds and labels
        "id", "name", "name_alt", "kind", "relation", "broker", "icon", "colour", "symbol",
        "exchange", "isin", "instrument_key", "amfi_code", "asset_class", "sector", "mcap", "tags",
        "unit_label", "product", "multiplier", "collateral_type", "cas", "system", "member_id",
        "account_id", "asset_id", "tab_id", "event_id", "activity_id", "trade_id", "taxpayer",
        "segment", "scope", "source", "basis", "mark", "treat", "bucket", "filename", "member",
        "level", "hides", "insights", "instrument", "tax_mode",
        # when: days, instants and years
        "day", "days", "at", "now", "read_at", "as_of", "as_of_day", "as_of_ms", "due_at",
        "resolved_at", "price_day", "valuation_day", "first_flow_day", "nav_day", "ex_day",
        "acquired_day", "credited_day", "gone_at", "expires_at", "from", "to", "fy", "fy_label",
        "loss_fy",
        "auction_from", "auction_until", "auction_close",
        # states, flags and versions
        "state", "status", "detail", "version", "schema_version", "service_version",
        "rules_version", "v", "on", "late", "provisional", "not_read", "archived", "pinned",
        "position", "is_default", "today_mode", "held", "running", "listed_future", "switches",
        "loans_hidden", "manual_old_days", "allocate_expenses", "exempt_order", "lt_boundary",
        "turnover_method", "risk_free_bp", "months_target",
        # sentences with no figure in them
        "missing", "not_computed", "notes", "series_words",
        # containers: what is under them is judged key by key
        "tabs", "members", "accounts", "jobs", "amfi", "entries", "tab", "live", "upstox",
        "holdings", "positions", "mtf", "data", "rows", "row", "asset", "assets", "aliases",
        "value", "layout", "spec", "defaults", "widgets", "columns", "sort", "group", "filters",
        "classes", "views", "loose_loans", "inputs", "sources", "opening_lots", "fmvs", "carries",
        "day_overrides", "trade_marks", "settings", "events", "to_confirm", "reads", "decisions",
        "details", "not_computed_sales",
    }
)  # fmt: skip

#: What the phase added to the table (design p7 section 10, and the defensive few beside it), in
#: version 3: the rupee figures among them follow P&L in ``cost`` as well (review H1's rule).
V3_PNL = (
    "beta", "correlation", "stat_days", "stat_note", "risk", "sharpe", "sortino", "calmar",
    "volatility", "drawdown", "tax", "capital_gains", "business", "carried", "goals", "progress",
    "required_monthly",
)  # fmt: skip
V3_QTY = ("dividends", "expected", "calendar", "turnover", "charges", "fmv_u", "text", "base64")


def judged(key: str) -> bool:
    """Whether a key is accounted for (hidden, never hidden or structural)."""
    return serialize.hidden(key, MASK) or key in NEVER or key in STRUCTURAL


def answer_misses(data, path: tuple[str, ...] = ()) -> set[str]:
    """The paths of the keys of a real answer that nothing accounts for; a hidden key's value is
    gone with it and is not walked."""
    out: set[str] = set()
    if isinstance(data, dict):
        for key, value in data.items():
            if serialize.hidden(key, MASK):
                continue
            if not judged(key):
                out.add(".".join((*path, key)))
            out |= answer_misses(value, (*path, key))
    elif isinstance(data, list):
        for item in data:
            out |= answer_misses(item, path)
    return out


class Declared:
    """The answer types of one types.ts text, walked from a type expression down."""

    def __init__(self, text: str):
        self.interfaces, self.aliases = interfaces(text), aliases(text)
        self.parents, self.exported = parents(text), exported_types(text)
        self.ops = ops_map(text)

    def misses(self, expr: str, path: tuple[str, ...]) -> set[str]:
        out: set[str] = set()
        self._expr(expr, path, frozenset(), out)
        return out

    def keys(self) -> set[str]:
        """Every member name any interface or inline object of the text declares."""
        found: set[str] = set()

        def members(body: str) -> None:
            for key, expr in member_types(body):
                found.add(key)
                for inline in type_refs(expr, self.exported)[1]:
                    members(inline)

        for body in self.interfaces.values():
            members(body)
        for expr in self.aliases.values():
            for inline in type_refs(expr, self.exported)[1]:
                members(inline)
        return found

    def _type(self, name: str, path, seen, out) -> None:
        if name in seen:
            return
        seen = seen | {name}
        for base in self.parents.get(name, ()):
            self._type(base, path, seen, out)
        if name in self.interfaces:
            self._members(member_types(self.interfaces[name]), path, seen, out)
        elif name in self.aliases:
            self._expr(self.aliases[name], path, seen, out)

    def _expr(self, expr: str, path, seen, out) -> None:
        names, inline = type_refs(expr, self.exported)
        for name in sorted(names):
            self._type(name, path, seen, out)
        for body in inline:
            self._members(member_types(body), path, seen, out)

    def _members(self, members, path, seen, out) -> None:
        for key, expr in members:
            if serialize.hidden(key, MASK):
                continue
            if not judged(key):
                out.add(".".join((*path, key)))
            self._expr(expr, (*path, key), seen, out)


DECLARED = Declared(TYPES_TS.read_text(encoding="utf-8"))
#: The ops whose declared answers are walked: what a viewer can be given, and the phase's own.
WALKED = [op for op in DECLARED.ops if op in kinds.FAMILY_OPS or op in kinds.SWITCH_OF]


# ---- the declared answers ------------------------------------------------------------------------
@pytest.mark.parametrize("op", WALKED)
def test_every_key_of_a_declared_answer_is_accounted_for(op):
    assert DECLARED.misses(DECLARED.ops[op][1], (op,)) == set()


def test_the_walk_covers_the_family_ops_and_every_op_of_the_phase():
    routed_family = set(kinds.FAMILY_OPS) & set(kinds.OP_NAMES)
    assert routed_family | (set(kinds.SWITCH_OF) & set(kinds.OP_NAMES)) == set(WALKED)
    assert {"tax_get", "risk", "goals_list", "calendar", "dividends", "ca_list"} <= set(WALKED)


# ---- the real answers ----------------------------------------------------------------------------
@scenario
async def test_every_key_of_a_real_family_answer_is_accounted_for(rig):
    await rig.grant("7", [rig.tab["id"], rig.other["id"], rig.upstox_tab])
    found: dict[str, set[str]] = {}
    for op, args in (
        ("state", {}),
        ("summary", {}),
        ("tab_get", {"id": rig.tab["id"]}),
        ("performance", {"id": rig.tab["id"]}),
        ("upstox_history", {}),
        ("upstox_book", {"parts": list(kinds.UPSTOX_PAGE_PARTS)}),
    ):
        found[op] = answer_misses(await rig.owner(op, **args))
    for op, args in (
        ("upstox_book", {"parts": ["orders", "trades"]}),
        ("shares_for_viewer", {}),
    ):
        status, body = await rig.family("7", op, **args)
        assert status == 200, body
        found[f"family {op}"] = answer_misses(body["data"])
    assert {op: misses for op, misses in found.items() if misses} == {}


@scenario
async def test_the_phase_hides_nothing_a_family_screen_shows_today(rig):
    """Version 2 only adds fields no family answer carries yet: every family screen of today
    shows what it showed."""
    await rig.grant("7", [rig.tab["id"], rig.upstox_tab])
    seen: set[str] = set()

    def keys(data) -> None:
        if isinstance(data, dict):
            for key, value in data.items():
                seen.add(key)
                keys(value)
        elif isinstance(data, list):
            for item in data:
                keys(item)

    for op, args in (("state", {}), ("summary", {}), ("tab_get", {"id": rig.tab["id"]}),
                     ("performance", {"id": rig.tab["id"]}), ("upstox_book", {})):  # fmt: skip
        status, body = await rig.family("7", op, **args)
        assert status == 200, (op, body)
        keys(body["data"])
        if op == "performance":
            body_of_performance = body["data"]
    added = frozenset((*V3_PNL, *V3_QTY))
    # The one thing of the phase a family screen may carry (design p7 section 10): the benchmark
    # statistics, which sit inside ``benchmarks`` and so show only where the member's P&L does.
    assert {key for key in seen if serialize.hidden(key, added)} == set(BENCHMARK_STATS)
    for each in body_of_performance["benchmarks"]:
        assert set(BENCHMARK_STATS) < set(each)
    assert not set(BENCHMARK_STATS) & (set(body_of_performance) | set(body_of_performance["twr"]))


BENCHMARK_STATS = ("beta", "correlation", "stat_days", "stat_note")


# ---- the table and the list ----------------------------------------------------------------------
def test_the_table_hides_the_phase_fields_from_version_3():
    assert TABLE["version"] >= 3
    assert set(V3_PNL) <= set(TABLE["categories"]["pnl"])
    assert set(V3_QTY) <= set(TABLE["categories"]["qty"])
    assert set(V3_QTY) <= set(TABLE["cost"])  # every one of them is a rupee figure or holds one
    assert "amount_u" in NEVER


def test_no_op_of_the_phase_is_a_family_op():
    """Mutation target 20: a tax, risk, goal or calendar op added to FAMILY_OPS fails here."""
    assert not set(kinds.SWITCH_OF) & set(kinds.FAMILY_OPS)
    assert not set(kinds.SWITCH_OF) & set(kinds.SHARING_OPS)


def test_a_structural_key_is_no_figure_and_names_a_declared_field():
    assert {key for key in STRUCTURAL if serialize.hidden(key, MASK)} == set()
    assert STRUCTURAL & NEVER == set()
    assert STRUCTURAL - DECLARED.keys() - RUNTIME_ONLY == set()
    for key in STRUCTURAL:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", key), key


#: Structural keys only the service's answers carry (types.ts does not declare them): the share
#: list of ``shares_for_viewer`` (a family op with no owner route) and a switch's name.
RUNTIME_ONLY = frozenset({"level", "hides", "insights"})


def test_the_walks_see_a_forgotten_key():
    assert answer_misses({"name": "n", "rows": [{"id": 1, "hush": 5}], "qty_u": 3}) == {"rows.hush"}
    assert answer_misses({"capital_gains": {"hush": 1}}) == set()  # gone with its parent
    text = """
export interface PortfolioOps {
  x: { args: NoArgs; data: Outer }
}
export type NoArgs = Record<string, never>
export interface Inner {
  hush: number
  qty_u: number
}
export interface Outer {
  name: string
  rows: Inner[]
  more: { secret: string; pnl_paise: number }
  tax: Inner
}
"""
    walked = Declared(text)
    assert walked.misses(walked.ops["x"][1], ("x",)) == {"x.rows.hush", "x.more", "x.more.secret"}
