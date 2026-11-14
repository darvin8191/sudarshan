"""The watch's checks over their vectors (M19.5, P8 10.8): fine, warning, failing, unreadable,
and "once per cause" over a sequence of runs."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from sudarshan.ops.watch import checks

HERE = Path(__file__).resolve().parent
VECTORS = json.loads((HERE / "watch_vectors.json").read_text(encoding="utf-8"))
SOURCE = HERE.parent / "watch" / "checks.py"


def _one(name: str, readings: dict, memory: dict) -> checks.Result:
    found = [r for r in checks.run_checks(readings, {name: memory}) if r.name == name]
    assert len(found) == 1, name
    return found[0]


@pytest.mark.parametrize("vector", VECTORS["checks"], ids=lambda v: f"{v['check']}: {v['name']}")
def test_a_check_over_its_vector(vector):
    result = _one(vector["check"], vector["readings"], vector["memory"])
    assert result.level == vector["level"]
    if "facts" in vector:
        assert dict(result.facts) == vector["facts"]
    if "keep" in vector:
        assert dict(result.keep) == vector["keep"]
    assert all(isinstance(value, str) for value in result.facts.values())


@pytest.mark.parametrize("sequence", VECTORS["sequences"], ids=lambda s: s["name"])
def test_once_per_cause_over_a_sequence_of_runs(sequence):
    name, memory, told = sequence["check"], sequence["memory"], {}
    for number, (readings, expected) in enumerate(sequence["runs"]):
        result = _one(name, readings, memory)
        memory = dict(result.keep)
        moved, told = checks.changes([result], told)
        said = [[m["level"], m["was"]] for m in moved]
        assert said == ([expected] if expected else []), f"run {number}: {result}"


def test_every_check_has_vectors_for_each_level_it_can_give():
    by_check: dict[str, set] = {}
    for vector in VECTORS["checks"]:
        by_check.setdefault(vector["check"].split("_")[0], set()).add(vector["level"])
    for family, levels in by_check.items():
        assert {"fine", "unread"} <= levels, family
        assert levels & {"warning", "failing"}, family
    names = {result.name for result in checks.run_checks({}, {})}
    assert names == {f"service_{n}" for n in checks.SERVICES} | {
        "openalgo", "login", "feed", "instrument_list", "disk", "memory", "certificate",
    }  # fmt: skip


def test_with_nothing_read_no_check_says_fine():
    """A reading that could not be read is "could not be read": never a crash, never fine."""
    for result in checks.run_checks({}, {}):
        assert result.level in (checks.UNREAD, checks.OFF), result
    garbage = {"health": 7, "enabled": "x", "disk": [], "ist_minute": "nine", "window_open": 3}
    for result in checks.run_checks(garbage, {"login": "x", "memory": {"low": "many"}}):
        assert result.level != checks.FINE, result


def test_a_check_that_raises_is_unread_and_keeps_its_memory(monkeypatch):
    def broke(readings, memory):
        raise RuntimeError("broke")

    monkeypatch.setattr(checks, "disk", broke)
    monkeypatch.setattr(checks, "openalgo", broke)
    results = {
        r.name: r for r in checks.run_checks({"openalgo": True}, {"openalgo": {"misses": 1}})
    }
    assert results["disk"].level == checks.UNREAD
    assert results["openalgo"].level == checks.UNREAD
    assert dict(results["openalgo"].keep) == {"misses": 1}


def test_unread_and_off_never_change_what_was_told():
    failing = checks.Result("disk", checks.FAILING)
    moved, told = checks.changes([failing], {})
    assert [m["level"] for m in moved] == ["failing"] and told == {"disk": "failing"}
    for quiet in (checks.UNREAD, checks.OFF):
        moved, told = checks.changes([checks.Result("disk", quiet)], told)
        assert moved == [] and told == {"disk": "failing"}
    assert checks.changes([failing], told) == ([], told)


def test_the_ports_are_the_ring_table_of_the_readme():
    readme = (HERE.parents[1] / "README.md").read_text(encoding="utf-8")
    for name, port in checks.SERVICES.items():
        row = [line for line in readme.splitlines() if line.startswith(f"| {port} |")]
        assert len(row) == 1 and f"`sudarshan-{name}`" in row[0], (name, port)


def test_the_checks_are_pure():
    """No I/O, no clock, no logging: only dataclasses and typing are imported."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    assert imported == {"__future__", "collections.abc", "dataclasses"}
    banned = {"open", "print", "exec", "eval", "__import__"}
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert not banned & called
