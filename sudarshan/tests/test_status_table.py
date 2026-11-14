"""sudarshan/STATUS.md stays one well-formed table, one row per feature id, in id order (M0.1).

The file merges as a union (``.gitattributes``), so when two branches touch the same row both
lines survive the merge, and nothing complains about a row that sorts out of place or a ``|``
inside a cell that splits it into six cells (GitHub then draws the rest of the row in the wrong
columns). Each of those fails here, naming the line.
"""

from __future__ import annotations

import re
from pathlib import Path

STATUS = Path(__file__).resolve().parents[1] / "STATUS.md"
#: id | feature | status | release | PR / notes
CELLS = 5
#: A cell separator: a pipe that is not escaped. Write a pipe inside a cell as ``\|``, which
#: GitHub shows as ``|`` (inside a code span too).
_SEPARATOR = re.compile(r"(?<!\\)\|")


def feature_rows(text: str) -> list[tuple[int, str]]:
    """Every feature row of the table, with its line number."""
    return [(n, line) for n, line in enumerate(text.splitlines(), 1) if line.startswith("| M")]


def cells(line: str) -> list[str]:
    """A row's cells, split on its unescaped pipes; the outer pipes start and end the row."""
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|") and not inner.endswith("\\|"):
        inner = inner[:-1]
    return [cell.strip() for cell in _SEPARATOR.split(inner)]


def natural(feature_id: str) -> tuple:
    """The id's sort key: runs of digits compare as numbers, so M1.2 comes before M1.10."""
    return tuple(int(run) if run.isdigit() else run for run in re.findall(r"\d+|\D+", feature_id))


def problems(text: str) -> dict[str, list[str]]:
    """What is wrong with the table: rows not of five cells, ids twice, rows out of order."""
    rows = feature_rows(text)
    split = [(n, cells(line)) for n, line in rows]
    wrong_cells = [
        f"line {n}: {parts[0]} has {len(parts)} cells, not {CELLS} (escape a | inside a cell as \\|)"
        for n, parts in split
        if len(parts) != CELLS
    ]
    seen: dict[str, int] = {}
    doubled = []
    for n, parts in split:
        if parts[0] in seen:
            doubled.append(f"line {n}: {parts[0]} again (first at line {seen[parts[0]]}): keep one")
        else:
            seen[parts[0]] = n
    unsorted = [
        f"line {n}: {before} before {after}"
        for (_, (before, *_)), (n, (after, *_)) in zip(split, split[1:], strict=False)
        if natural(before) > natural(after)
    ]
    return {"cells": wrong_cells, "doubled": doubled, "unsorted": unsorted}


def test_the_status_table_has_its_feature_rows():
    assert len(feature_rows(STATUS.read_text(encoding="utf-8"))) > 100


def test_every_feature_row_has_exactly_five_cells():
    assert problems(STATUS.read_text(encoding="utf-8"))["cells"] == []


def test_no_feature_id_is_listed_twice():
    assert problems(STATUS.read_text(encoding="utf-8"))["doubled"] == []


def test_rows_are_sorted_by_id_in_natural_order():
    assert problems(STATUS.read_text(encoding="utf-8"))["unsorted"] == []


def test_the_checks_find_each_fault_they_are_for():
    """Run on a table with one fault of each kind, so the checks above cannot pass by finding no
    rows (a changed row prefix, say) or by splitting the way the file is already written."""
    table = "\n".join(
        [
            "| id | feature | status | release | PR / notes |",
            "|---|---|---|---|---|",
            "| M1.2 | fine | done | v1 | a `x \\| y` pipe, escaped |",
            "| M1.10 | after M1.2, as numbers sort | done | v1 | - |",
            "| M1.10 | doubled by a merge | done | v1 | - |",
            "| M1.9 | out of place | done | v1 | - |",
            "| M2.1 | a pipe | splits it | done | v1 | - |",
        ]
    )
    found = problems(table)
    assert found["cells"] == ["line 7: M2.1 has 6 cells, not 5 (escape a | inside a cell as \\|)"]
    assert found["doubled"] == ["line 5: M1.10 again (first at line 4): keep one"]
    assert found["unsorted"] == ["line 6: M1.10 before M1.9"]
    assert natural("M1.2") < natural("M1.10") < natural("M2.1") < natural("M10.1")
