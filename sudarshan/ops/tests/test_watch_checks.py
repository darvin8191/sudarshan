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


#: Checks that count and tell news; they have no failing level.
COUNTED = {"assistant", "telegram_links"}


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
    assert dict(result.news) == vector.get("news", {})
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
        family = vector["check"] if vector["check"] in COUNTED else vector["check"].split("_")[0]
        by_check.setdefault(family, set()).add(vector["level"])
    for family, levels in by_check.items():
        assert {"fine", "unread"} <= levels, family
        assert family in COUNTED or levels & {"warning", "failing"}, family
    names = {result.name for result in checks.run_checks({}, {})}
    assert names == {f"service_{n}" for n in checks.SERVICES} | {
        "openalgo", "login", "feed", "instrument_list", "disk", "memory", "certificate",
        "posture_locks", "posture_debug", "posture_modes", "posture_ports", "posture_reboot",
        "posture_updates", "assistant", "telegram_links", "backup", "drill", "historify",
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


def test_a_certificate_unjudged_for_two_days_in_a_row_is_told_once():
    """v0.13.0 verify N6: no name for the site, or another site's certificate, is unread, and
    unread tells nobody. Two days of it in a row is a warning with the cause, told once, and
    told once more when a certificate is judged again."""
    day, told, memory, moved_at = 24 * 3600, {}, {}, []
    start = 1_800_000_000
    for cause in checks.CERT_CAUSES:
        told, memory, moved_at = {}, {}, []
        for step in range(0, 3 * day + 1, 120 * 30):  # an hour apart, for three days
            now = start + step
            (result,) = [checks.certificate({"now": now, "cert_hours_left": cause}, memory)]
            memory = dict(result.keep)
            assert dict(result.facts) == {"cause": cause}
            want = checks.WARNING if step >= checks.CERT_UNJUDGED_TELL_S else checks.UNREAD
            assert result.level == want, (cause, step)
            moved, told = checks.changes([result], told)
            moved_at += [step for _ in moved]
            assert all(checks.told_by(change) for change in moved)
        assert moved_at == [2 * day], "told once, at two days, and not again"
        assert memory == {"unjudged_since": start}
        # Judged again: fine, told once, and the count starts over.
        fine = checks.certificate({"now": start + 4 * day, "cert_hours_left": 900.0}, memory)
        moved, told = checks.changes([fine], told)
        assert fine.level == checks.FINE and dict(fine.keep) == {}
        assert [(m["level"], m["was"]) for m in moved] == [("fine", "warning")]


def test_the_two_days_are_in_a_row_and_a_failed_look_does_not_count_or_reset():
    start, day = 1_800_000_000, 24 * 3600
    first = checks.certificate({"now": start, "cert_hours_left": "no_name"}, {})
    # A look that failed (nginx not answering) keeps the count as it stands.
    blind = checks.certificate({"now": start + day, "cert_hours_left": None}, first.keep)
    assert blind.level == checks.UNREAD and dict(blind.keep) == {"unjudged_since": start}
    late = checks.certificate({"now": start + 2 * day, "cert_hours_left": "no_name"}, blind.keep)
    assert late.level == checks.WARNING
    # A judged certificate in between ends it: one more day is not two in a row.
    judged = checks.certificate({"now": start + day, "cert_hours_left": 500.0}, first.keep)
    again = checks.certificate({"now": start + 2 * day, "cert_hours_left": "no_name"}, judged.keep)
    assert judged.level == checks.FINE and again.level == checks.UNREAD
    assert dict(again.keep) == {"unjudged_since": start + 2 * day}
    # With nothing to count from, or a memory that is not a number, it is unread and never a crash.
    for memory in ({}, {"unjudged_since": "x"}, {"unjudged_since": True}):
        assert checks.certificate({"cert_hours_left": "no_name"}, memory).level == checks.UNREAD
    # A certificate running out is still failing, whatever was counted.
    assert checks.certificate({"now": start, "cert_hours_left": 3.0}, first.keep).level == (
        checks.FAILING
    )


def test_unread_and_off_never_change_what_was_told():
    failing = checks.Result("disk", checks.FAILING)
    moved, told = checks.changes([failing], {})
    assert [m["level"] for m in moved] == ["failing"] and told == {"disk": "failing"}
    for quiet in (checks.UNREAD, checks.OFF):
        moved, told = checks.changes([checks.Result("disk", quiet)], told)
        assert moved == [] and told == {"disk": "failing"}
    assert checks.changes([failing], told) == ([], told)


def test_the_readme_says_when_an_unchecked_certificate_is_told():
    readme = " ".join((HERE.parents[1] / "README.md").read_text(encoding="utf-8").split())
    assert checks.CERT_UNJUDGED_TELL_S == 2 * 24 * 3600
    for phrase in (
        "judged no certificate for two days in a row",
        "you are told once on Telegram, with the line to add to `/etc/sudarshan/watch.env`",
        'The System page then reads "Not being checked" beside Security certificate',
    ):
        assert phrase in readme, phrase


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


def test_what_the_watch_tells_and_what_it_only_shows():
    for name in checks.SHOWN_ONLY:
        assert not checks.told_by({"check": name, "level": "warning", "facts": {}})
    for cause, told in (("late", True), ("failed", False), ("never", False)):
        change = {"check": "drill", "level": "warning", "facts": {"cause": cause}}
        assert checks.told_by(change) is told, cause
    assert not checks.told_by({"check": "drill", "level": "fine", "facts": {}})
    assert checks.told_by({"check": "posture_locks", "level": "warning", "facts": {}})
    assert checks.told_by({"check": "disk", "level": "fine", "facts": {}})
