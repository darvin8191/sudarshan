"""M19: the monthly audit in CI publishes counts only (P8 7.4). The repository is public, so
the workflow must never upload the report, print the audit's own output or open anything."""

from __future__ import annotations

from pathlib import Path

import pytest

from sudarshan.ops import audit_counts

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = (REPO / ".github" / "workflows" / "sudarshan-security.yml").read_text(encoding="utf-8")
MARKER = "db/secret-place.py line 42 holds the key"


def _report(path: Path) -> Path:
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    book.active.title = "Summary"
    book.active.append(["OpenAlgo Security Audit", MARKER])
    findings = book.create_sheet("Findings")
    findings.append(["Check", "Severity", "Status", "Detail", "Remediation", "Evidence"])
    for severity, status in (("HIGH", "FAIL"), ("CRITICAL", "ERROR"), ("LOW", "FAIL"),
                             ("HIGH", "PASS"), ("MEDIUM", "REVIEW"), (MARKER, MARKER)):  # fmt: skip
        findings.append([MARKER, severity, status, MARKER, MARKER, MARKER])
    patterns = book.create_sheet("Code patterns")
    patterns.append(["Severity", "Pattern", "File", "Line", "Source"])
    patterns.append(["HIGH", MARKER, MARKER, 1, MARKER])
    book.create_sheet("a sheet of secrets")
    book.save(path)
    return path


def test_only_fixed_names_and_numbers_are_printed(tmp_path, capsys):
    assert audit_counts.main([str(_report(tmp_path / "r.xlsx"))]) == 0
    printed = capsys.readouterr().out
    assert MARKER not in printed and "secret" not in printed
    lines = printed.splitlines()
    assert "sheet Findings: 6 rows" in lines and "sheet Code patterns: 1 rows" in lines
    assert "sheet Manual review: missing" in lines and "other sheets: 1" in lines
    for line in ("checks FAIL: 2", "checks ERROR: 1", "checks REVIEW: 1", "checks PASS: 1",
                 "checks other: 1", "failing CRITICAL: 1", "failing HIGH: 1", "failing LOW: 1"):  # fmt: skip
        assert line in lines, line


def test_a_file_that_is_not_the_report_says_so_without_quoting(tmp_path, capsys):
    bad = tmp_path / "x.xlsx"
    bad.write_text(MARKER)
    assert audit_counts.main([str(bad)]) == 2
    assert capsys.readouterr().out == "the audit's report could not be read\n"
    assert audit_counts.main([]) == 2


def test_the_sheet_names_are_the_audits_own():
    audit = (REPO / ".claude" / "skills" / "security-audit" / "audit.py").read_text()
    for name in audit_counts.SHEETS:
        assert f'"{name}"' in audit, name


def test_the_workflow_publishes_counts_only():
    assert "workflow_dispatch:" in WORKFLOW and "schedule:" in WORKFLOW
    assert 'cron: "40 21 2 * *"' in WORKFLOW
    assert "permissions:\n  contents: read\n" in WORKFLOW
    for banned in ("upload-artifact", "upload-sarif", "security-events", "issues: write",
                   "pull-requests", "gh issue", "cat ", "tee ", "GITHUB_STEP_SUMMARY"):  # fmt: skip
        assert banned not in WORKFLOW, banned
    assert '> "$RUNNER_TEMP/audit-output.txt" 2>&1' in WORKFLOW
    assert "python -m sudarshan.ops.audit_counts" in WORKFLOW
    assert "rm -rf tmp/security-audits" in WORKFLOW and "if: always()" in WORKFLOW
    assert "steps.audit.outputs.code" in WORKFLOW
