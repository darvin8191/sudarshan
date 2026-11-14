"""What the tax tests share: reading a vector file and turning its rows into model objects."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path

import pytest

VECTORS = Path(__file__).parent / "vectors"


def cases(name: str, group: str | None = None):
    """The cases of one vector file (one group of it) as pytest parameters named by their id."""
    data = json.loads((VECTORS / name).read_text())
    return [pytest.param(case, id=case["id"]) for case in (data[group] if group else data)]


def plain(value):
    """A result as JSON would hold it: dataclasses as dicts, tuples as lists."""
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def picked(rows, like):
    """Each result row cut down to the keys the vector's expected row names."""
    rows = plain(rows)
    assert len(rows) == len(like), rows
    return [{key: row[key] for key in want} for row, want in zip(rows, like, strict=True)]
