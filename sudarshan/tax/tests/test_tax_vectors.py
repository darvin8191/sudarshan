"""The vector files are their generators' output, and no generator uses the code under test."""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import pytest

VECTORS = Path(__file__).parent / "vectors"
GENERATORS = sorted(path.name for path in VECTORS.glob("make_*.py"))
ALLOWED = {"json", "pathlib", "fractions", "decimal"}


def test_every_vector_file_has_a_generator():
    made = set()
    for name in GENERATORS:
        made |= set(_load(name).FILES)
    assert made == {path.name for path in VECTORS.glob("*.json")}


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"tax_vectors_{name[:-3]}", VECTORS / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", GENERATORS)
def test_a_file_is_what_its_generator_writes(name):
    for file, data in _load(name).FILES.items():
        assert json.loads((VECTORS / file).read_text()) == json.loads(json.dumps(data)), file


@pytest.mark.parametrize("name", GENERATORS)
def test_a_generator_imports_nothing_from_the_code(name):
    tree = ast.parse((VECTORS / name).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert {alias.name.split(".")[0] for alias in node.names} <= ALLOWED
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0 and node.module.split(".")[0] in ALLOWED


@pytest.mark.parametrize("name", GENERATORS)
def test_every_case_says_its_arithmetic(name):
    for data in _load(name).FILES.values():
        groups = data.values() if isinstance(data, dict) else [data]
        for case in (case for group in groups for case in group):
            assert case["id"] and len(case["note"]) > 10, case
