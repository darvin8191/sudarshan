"""Counts from the security audit's report, and nothing else (M19: the monthly audit in CI).

    python -m sudarshan.ops.audit_counts REPORT.xlsx

The repository is public, so the monthly workflow (``.github/workflows/sudarshan-security.yml``)
must not publish what the audit found: a finding's text, a file name or a line would tell a
stranger where to look. This prints, for the report ``.claude/skills/security-audit/audit.py``
wrote: the number of rows on each sheet, and for the Findings sheet the number of checks by
status and by severity among the ones that failed. Only names from the fixed lists below and
numbers are ever printed; a cell that holds anything else is counted as "other".
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

STATUSES = ("FAIL", "ERROR", "REVIEW", "WARN", "PASS", "SKIP")
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")
#: The sheets the audit writes, as the workflow may name them.
SHEETS = (
    "Summary",
    "Findings",
    "Code patterns",
    "Frontend patterns",
    "Routes (unprotected)",
    "Bandit (triaged)",
    "Manual review",
)
OPEN = ("FAIL", "ERROR")


def _known(value, names: tuple[str, ...]) -> str:
    text = str(value or "").strip().upper()
    return text if text in names else "other"


def counts(path: Path) -> list[str]:
    """The lines to print. Raises when the file is not the audit's report."""
    from openpyxl import load_workbook

    book = load_workbook(path, read_only=True)
    try:
        lines = []
        for name in SHEETS:
            if name not in book.sheetnames:
                lines.append(f"sheet {name}: missing")
                continue
            rows = sum(1 for _ in book[name].iter_rows(min_row=2, values_only=True))
            lines.append(f"sheet {name}: {rows} rows")
        unknown = len([name for name in book.sheetnames if name not in SHEETS])
        if unknown:
            lines.append(f"other sheets: {unknown}")
        by_status: Counter[str] = Counter()
        open_by_severity: Counter[str] = Counter()
        if "Findings" in book.sheetnames:
            for row in book["Findings"].iter_rows(min_row=2, max_col=3, values_only=True):
                if len(row) < 3:
                    continue
                status = _known(row[2], STATUSES)
                by_status[status] += 1
                if status in OPEN:
                    open_by_severity[_known(row[1], SEVERITIES)] += 1
        for status in (*STATUSES, "other"):
            if by_status[status]:
                lines.append(f"checks {status}: {by_status[status]}")
        for severity in (*SEVERITIES, "other"):
            if open_by_severity[severity]:
                lines.append(f"failing {severity}: {open_by_severity[severity]}")
        return lines
    finally:
        book.close()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        sys.stdout.write("usage: python -m sudarshan.ops.audit_counts REPORT.xlsx\n")
        return 2
    try:
        lines = counts(Path(args[0]))
    except Exception:
        sys.stdout.write("the audit's report could not be read\n")  # never the reason: it may quote
        return 2
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
