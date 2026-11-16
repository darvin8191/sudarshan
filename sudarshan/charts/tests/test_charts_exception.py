"""The chart store runs inside OpenAlgo on the watchlists exception's conditions (README.md here):
it starts no thread, timer, task, loop or process, and opens its file through the shared engine
factory. These fail when a later change loosens one of them; do not relax them to land a feature.

The scanner is the watchlist exception's own (``sudarshan/watchlists/tests/test_exception.py``),
loaded from its file, so the two exceptions are held to one rule and it is written once."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WATCHED = sorted(p for p in (ROOT / "sudarshan" / "charts").rglob("*.py") if "tests" not in p.parts)
WATCHED.append(ROOT / "blueprints" / "sudarshan_charts.py")


def _scanner():
    path = ROOT / "sudarshan" / "watchlists" / "tests" / "test_exception.py"
    spec = importlib.util.spec_from_file_location("sudarshan_exception_scanner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.scan


def test_the_scanner_still_finds_what_it_must():
    scan = _scanner()
    assert scan("import threading\nthreading.Thread(target=print).start()")
    assert scan("import asyncio")
    assert scan("from apscheduler.schedulers.background import BackgroundScheduler")


def test_nothing_in_the_store_or_its_bridge_starts_work_off_the_request():
    scan = _scanner()
    assert {"schema.py", "store.py", "words.py", "sudarshan_charts.py"} <= {p.name for p in WATCHED}
    found = {p.relative_to(ROOT).as_posix(): scan(p.read_text()) for p in WATCHED}
    assert {name: hits for name, hits in found.items() if hits} == {}


def _names(path: Path) -> tuple[set[str], set[str]]:
    """The names a file imports and the names it calls."""
    tree = ast.parse(path.read_text())
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    called = {
        n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    }
    return imported, called


def test_the_file_is_opened_only_through_the_shared_engine_factory():
    for path in WATCHED:
        imported, called = _names(path)
        assert not {"StaticPool", "scoped_session", "create_engine"} & imported, path.name
        assert "create_engine" not in called, path.name
    imported, called = _names(ROOT / "sudarshan" / "charts" / "store.py")
    assert "create_db_engine" in imported and "create_db_engine" in called
