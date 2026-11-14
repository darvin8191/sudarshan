"""The tax core is pure: the standard library and itself, and no I/O, clock or logging (11.3)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
MODULES = sorted(path for path in PACKAGE.glob("*.py"))
#: Standard-library modules that read the world: files, the clock, the network, the environment.
IMPURE = {"os", "io", "sys", "time", "logging", "socket", "sqlite3", "subprocess", "pathlib",
          "datetime", "random", "urllib", "http", "threading", "asyncio", "shutil", "tempfile"}  # fmt: skip
CALLS = {"open", "print", "input", "exec", "eval", "__import__"}


def _imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield 0, alias.name
        elif isinstance(node, ast.ImportFrom):
            yield node.level, node.module or ""


def test_there_is_code_to_check():
    assert {"rules.py", "model.py", "classify.py", "units.py"} <= {path.name for path in MODULES}


@pytest.mark.parametrize("path", MODULES, ids=lambda path: path.name)
def test_imports_only_the_standard_library_and_itself(path):
    for level, name in _imports(ast.parse(path.read_text())):
        if level:
            assert level == 1 and (PACKAGE / f"{name}.py").exists() or name == "", (level, name)
            continue
        root = name.split(".")[0]
        assert root in sys.stdlib_module_names and root not in IMPURE, name


@pytest.mark.parametrize("path", MODULES, ids=lambda path: path.name)
def test_calls_nothing_that_reads_the_world(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in CALLS, f"{path.name}:{node.lineno} calls {node.func.id}"
        assert not isinstance(node, (ast.Global, ast.AsyncFunctionDef)), path.name


def test_no_float_touches_a_figure():
    for path in MODULES:
        for node in ast.walk(ast.parse(path.read_text())):
            assert not (isinstance(node, ast.Constant) and isinstance(node.value, float)), path
            assert not (isinstance(node, ast.Name) and node.id == "float"), path
            assert not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)), (
                f"{path.name}:{node.lineno} divides with /; use units.round_div"
            )
