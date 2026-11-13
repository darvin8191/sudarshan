"""The renderer's dependency pins agree and carry nothing it does not use (JB9).

JB8 pinned ``defusedxml`` on cross-design Y39's word that fpdf2 needs it. fpdf2 2.9.0 does not:
its metadata asks for Pillow and fonttools only, and nothing imports it. An unused pin on the
server's install list is an attack surface and a failed install for nothing, so it is gone, and
this test fails if it comes back or if one of the real pins drifts between the three files.
"""

from __future__ import annotations

import re
import tomllib
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PINNED = {"fpdf2": "2.9.0", "fonttools": "4.63.0"}


def _pyproject() -> list[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["dependencies"]


def _requirements() -> list[str]:
    text = (ROOT / "requirements-nginx.txt").read_text(encoding="utf-8")
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]


def _name(line: str) -> str:
    return re.split(r"[=<>!~ ;\[]", line, maxsplit=1)[0].lower().replace("_", "-")


def test_defusedxml_is_not_pinned_anywhere_the_server_installs_from():
    assert "defusedxml" not in {_name(x) for x in _pyproject()}
    assert "defusedxml" not in {_name(x) for x in _requirements()}
    direct = re.search(
        r'name = "openalgoui".*?dependencies = \[(.*?)\]',
        (ROOT / "uv.lock").read_text(encoding="utf-8"),
        re.S,
    )
    assert direct and '"defusedxml"' not in direct.group(1)


def test_nothing_in_the_repository_imports_defusedxml():
    pattern = re.compile(r"^\s*(?:import|from)\s+defusedxml\b", re.M)
    skipped = {".venv", "node_modules", "dist", "__pycache__", ".git"}
    offenders = [
        str(p.relative_to(ROOT))
        for top in ("sudarshan", "blueprints", "services", "utils", "database")
        for p in (ROOT / top).rglob("*.py")
        if not skipped & set(p.parts)
        and p != Path(__file__)
        and pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_the_installed_fpdf2_does_not_ask_for_it_either():
    needs = {_name(r) for r in metadata.requires("fpdf2") or [] if "extra ==" not in r}
    assert "defusedxml" not in needs
    assert {"pillow", "fonttools"} <= needs


def test_the_real_pins_agree_in_pyproject_requirements_and_the_environment():
    py = {_name(x): x for x in _pyproject()}
    rq = {_name(x): x for x in _requirements()}
    for name, version in PINNED.items():
        assert py[name] == f"{name}=={version}"
        assert rq[name].lower() == f"{name}=={version}".lower()
        assert metadata.version(name) == version
