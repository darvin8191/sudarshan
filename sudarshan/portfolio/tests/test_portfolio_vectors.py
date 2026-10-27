"""The golden vectors are the output of their generator, and the generator is independent (PA1).

``vectors/make_pa1.py`` writes ``ledger.json`` and ``money.json``. It works the numbers out with
exact fractions and Decimal and imports nothing from ``sudarshan``, so a vector cannot be a copy of
what the code does. These tests keep the JSON and the generator from drifting apart (a number is
changed in the generator or not at all) and keep the generator independent.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

VECTORS = Path(__file__).parent / "vectors"
GENERATORS = sorted(path.name for path in VECTORS.glob("make_*.py"))
#: What the generators share; it is held to the same independence.
TOOLS = "portfolio_vector_tools.py"


def load(name: str):
    """Run a generator's file as a module (it finds its tools next to it, as it does as a script)."""
    spec = importlib.util.spec_from_file_location(f"portfolio_vectors_{name[:-3]}", VECTORS / name)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(VECTORS))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(VECTORS))
    return module


def test_there_is_a_generator_for_the_first_vectors():
    assert "make_pa1.py" in GENERATORS


@pytest.mark.parametrize("name", GENERATORS)
def test_the_committed_vectors_are_what_the_generator_writes(name):
    built = load(name).build()
    assert built
    for filename, data in built.items():
        written = json.loads((VECTORS / filename).read_text(encoding="utf-8"))
        assert written == data, f"{filename} is not what {name} writes: run it and commit both"


@pytest.mark.parametrize("name", [*GENERATORS, TOOLS])
def test_a_generator_imports_nothing_from_the_code_it_checks(name):
    tree = ast.parse((VECTORS / name).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    allowed = {
        "__future__",
        "json",
        "re",
        "decimal",
        "fractions",
        "pathlib",
        "portfolio_vector_tools",
    }
    assert imported <= allowed, imported


def test_every_vector_file_has_a_generator_that_writes_it():
    written = {filename for name in GENERATORS for filename in load(name).build()}
    on_disk = {path.name for path in VECTORS.glob("*.json")}
    assert on_disk == written
