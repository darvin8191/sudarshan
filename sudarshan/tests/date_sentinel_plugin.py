"""pytest plugin for the date sentinel (``.github/workflows/sudarshan-date-sentinel.yml``).

Loaded with ``-p sudarshan.tests.date_sentinel_plugin`` and active only when
``SUDARSHAN_DATE_SENTINEL`` is set, so an ordinary run never sees it. It does two things:

* skips, by name and with the reason printed, the tests that cannot run under libfaketime for a
  reason that has nothing to do with dates (``date_sentinel_skips.txt`` lists each one);
* writes ``SUDARSHAN_DATE_SENTINEL_FAILED`` (a path) with one failing test id per line, so a cell
  of the matrix can upload the list without parsing a log.

A skip is never silent: its reason starts with ``date sentinel:`` and the terminal summary counts
and lists every one.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path

import pytest

ENV = "SUDARSHAN_DATE_SENTINEL"
FAILED_ENV = "SUDARSHAN_DATE_SENTINEL_FAILED"
SKIP_LIST = Path(__file__).with_name("date_sentinel_skips.txt")

_skipped: list[tuple[str, str]] = []
_failed: list[str] = []


def active() -> bool:
    """Whether this run is a sentinel run."""
    return bool(os.environ.get(ENV))


def read_skips(path: Path = SKIP_LIST) -> list[tuple[str, str]]:
    """The (node id pattern, reason) pairs of the skip list; blank lines and # lines are ignored."""
    pairs = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pattern, _, reason = line.partition("|")
        pairs.append((pattern.strip(), reason.strip() or "no reason given"))
    return pairs


def pytest_collection_modifyitems(config, items):
    if not active():
        return
    skips = read_skips()
    for item in items:
        for pattern, reason in skips:
            if fnmatch.fnmatchcase(item.nodeid, pattern):
                text = f"date sentinel: {reason}"
                item.add_marker(pytest.mark.skip(reason=text))
                _skipped.append((item.nodeid, reason))
                break


def pytest_runtest_logreport(report):
    if not active() or not report.failed:
        return
    if report.nodeid not in _failed:
        _failed.append(report.nodeid)


def pytest_collectreport(report):
    """A file that cannot even be collected on the shifted date counts as failing too."""
    if active() and report.failed and report.nodeid not in _failed:
        _failed.append(report.nodeid)


def pytest_report_header(config):
    if active():
        return f"date sentinel: FAKETIME={os.environ.get('FAKETIME', '')!r}, {len(_skipped)} named skips"
    return None


def pytest_terminal_summary(terminalreporter):
    if not active():
        return
    if _skipped:
        terminalreporter.section("date sentinel: tests skipped by name")
        for nodeid, reason in _skipped:
            terminalreporter.write_line(f"{nodeid}  ({reason})")
    if _failed:
        terminalreporter.section("date sentinel: failing tests")
        for nodeid in _failed:
            terminalreporter.write_line(nodeid)


def pytest_sessionfinish(session, exitstatus):
    target = os.environ.get(FAILED_ENV)
    if active() and target:
        Path(target).write_text("".join(f"{nodeid}\n" for nodeid in _failed), encoding="utf-8")
