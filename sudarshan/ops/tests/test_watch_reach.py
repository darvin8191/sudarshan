"""What the watch may reach, held by a test (P8 10.5): SUDARSHAN's own services, ports and
folders and OpenAlgo's, a few named system files, and nothing else on the server or outside it.
It starts no process, and it only reads: nothing here restarts or changes a service."""

from __future__ import annotations

import ast
import re
from pathlib import Path

WATCH = Path(__file__).resolve().parents[1] / "watch"
SOURCES = sorted(WATCH.glob("*.py"))
ALLOWED_PATHS = (
    "/var/lib/sudarshan-watch",
    "/var/lib/sudarshan/deploy.lock",  # the deploy tool's own lock: is an install running
    "/opt/sudarshan/apps/openalgo",
    "/etc/sudarshan/",
    "/etc/systemd/system/multi-user.target.wants",  # only sudarshan-<name>.service is looked for
    "/proc/meminfo",
    "/health",
)
ALLOWED_PORTS = {5000, 5580, 5582, 5583, 5584, 5585, 5586, 443}
ALLOWED_IMPORTS_OUTSIDE_STDLIB = ("sudarshan.", "cryptography", "sqlalchemy", "database.")
NEVER_IMPORTED = {"subprocess", "signal", "multiprocessing", "threading", "asyncio", "dbus"}


def _strings(tree: ast.AST) -> list[str]:
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.ClassDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings
    ]  # fmt: skip


def test_there_is_something_to_walk():
    assert {path.name for path in SOURCES} >= {"checks.py", "readers.py", "run.py", "__main__.py"}


def test_it_starts_no_process_and_no_thread_and_signals_nothing():
    for path in SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert name.split(".")[0] not in NEVER_IMPORTED, f"{path.name} imports {name}"
            if isinstance(node, ast.Attribute):
                assert node.attr not in {"system", "kill", "popen", "execv", "fork"}, path.name


def test_every_path_it_names_is_its_own_or_openalgos():
    for path in SOURCES:
        for text in _strings(ast.parse(path.read_text(encoding="utf-8"))):
            for found in re.findall(r"(?<![\w:/.])/[A-Za-z][\w./-]*", text):
                assert found.startswith(ALLOWED_PATHS) or found == "/", f"{path.name}: {found}"


def test_every_port_and_address_is_the_rings_or_openalgos():
    for path in SOURCES:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for text in _strings(tree):
            for host in re.findall(r"https?://([^/:\"' ]+)", text):
                assert host == "127.0.0.1", f"{path.name} names {host}"
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and type(node.value) is int:
                if 1024 <= node.value <= 65535 and node.value % 1000 != 0:
                    known = node.value in ALLOWED_PORTS or node.value in (1024, 8192)
                    assert known, f"{path.name}: {node.value}"
        assert "0.0.0.0" not in source


def test_it_names_no_other_system_on_the_server_and_never_the_exchange():
    for path in SOURCES:
        text = path.read_text(encoding="utf-8").lower()
        for word in ("trinetra", "nseindia", "niftyindices", "nsearchives", "systemctl"):
            assert word not in text, f"{path.name} names {word}"
        for unit in re.findall(r"[a-z0-9-]+\.(?:service|timer)", text):
            assert unit.startswith("sudarshan-"), f"{path.name} names the unit {unit}"
