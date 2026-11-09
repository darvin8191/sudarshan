"""List alerts, the rules (M8.14): vectors/groups.json through groups.py. Pure: no store, no clock."""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

from sudarshan.alerts.market import groups
from sudarshan.alerts.market.groups import Member, Row

VECTORS = json.loads(Path(__file__).with_name("vectors").joinpath("groups.json").read_text("utf-8"))


def row(item) -> Row:
    if isinstance(item, str):
        return Row(item, item)
    return Row(item["key"], item["key"], valid_until=item.get("valid_until"))


def members(pairs) -> list[Member]:
    return [Member(i, key) for i, key in pairs]


@pytest.mark.parametrize("v", VECTORS["diff"], ids=lambda v: v["name"])
def test_the_diff(v):
    add, remove = groups.diff(members(v["members"]), [row(r) for r in v["rows"]])
    assert [r.instrument_key for r in add] == v["add"] and list(remove) == v["remove"]


@pytest.mark.parametrize("v", VECTORS["left_out"], ids=lambda v: str(len(v["items"])))
def test_rows_left_out_are_told_in_words(v):
    assert list(groups.left_out_words(v["items"])) == v["words"]


def _keys(v) -> tuple[list[str], int, int, list[str]]:
    used = v["in_use"]
    in_use = [f"U{n}" for n in range(used["keys"])]
    new = [f"U{n}" if used["same_keys"] else f"N{n}" for n in range(v["new"])]
    return in_use, used["alerts"], v["new"], new


@pytest.mark.parametrize("v", VECTORS["fit"], ids=lambda v: v["name"])
def test_the_fit_check_holds_both_limits_and_says_both_numbers(v):
    in_use, alerts, _, new = _keys(v)
    got = groups.check(new, in_use, alerts, 200, 500)
    assert (got.ok, got.words) == (v["ok"], v["words"])
    refusal = groups.fit_check(200, 500)(frozenset(in_use), alerts, new)
    assert refusal == (None if v["ok"] else v["words"])


def test_the_fit_check_is_the_limits_and_not_one_of_them():
    """Mutation target: a check of instruments alone passes three groups on 180 symbols."""
    third = next(v for v in VECTORS["fit"] if v["name"].startswith("the third"))
    in_use, alerts, _, new = _keys(third)
    assert len(set(new) - set(in_use)) == 0  # instruments alone would say it fits
    assert not groups.check(new, in_use, alerts, 200, 500).ok


@pytest.mark.parametrize("v", VECTORS["release"])
def test_a_claim_is_released_only_when_no_other_alert_holds_it(v):
    holders = dict(v["holders"])
    assert list(groups.release(holders, v["removed"])) == v["released"]


@pytest.mark.parametrize("v", VECTORS["refs"], ids=lambda v: v["ref"])
def test_a_ranked_ref_is_refused_and_the_others_are_not(v):
    assert groups.ref_refusal(v["ref"]) == v["refusal"]


@pytest.mark.parametrize("v", VECTORS["plan"], ids=lambda v: v["name"])
def test_the_plan(v):
    got = groups.plan(
        v["state"], [row(r) for r in v["rows"]], members(v["members"]), v["holders"],
        name="Swing", active=v["active"], max_instruments=v["limits"][0],
        max_active=v["limits"][1],
    )  # fmt: skip
    assert got.action == v["action"]
    assert [r.instrument_key for r in got.add] == v["add"] and list(got.remove) == v["remove"]
    assert list(got.release) == v["release"]
    assert (got.covered, got.total, got.note) == (v["covered"], v["total"], v["note"])
    assert got.ask_again_at == v.get("ask_again_at")


def test_the_plan_carries_the_left_out_words():
    got = groups.plan("ok", [row("A")], [], {}, name="Swing", active=True, max_instruments=200,
                      max_active=500, left_out=[{"why": "atm"}, {"why": "strike"}])  # fmt: skip
    assert got.left_out == (
        "ATM and strike rows change contract during the day and are left out: 2.",
    )


def test_a_row_is_read_from_the_routes_answer():
    assert groups.Row.of({"instrument_key": "K", "symbol": "S", "valid_until": 5}) == Row(
        "K", "S", "", "", None, 5)  # fmt: skip


def test_groups_does_no_io_and_reads_no_clock():
    """The pure core's rule: no imports of a store, a clock, the network, logging or files."""
    tree = ast.parse(inspect.getsource(groups))
    imported = {
        n.module if isinstance(n, ast.ImportFrom) else a.name
        for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in (n.names if isinstance(n, ast.Import) else [n])
    }  # fmt: skip
    assert imported <= {"__future__", "collections.abc", "typing", "sudarshan.alerts.messages"}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not names & {"open", "print", "time", "datetime", "logging"}
