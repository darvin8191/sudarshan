"""pytest plugin for the date sentinel (``.github/workflows/sudarshan-date-sentinel.yml``).

Loaded with ``-p sudarshan.tests.date_sentinel_plugin`` and active only when
``SUDARSHAN_DATE_SENTINEL`` is set, so an ordinary run never sees it. It does two things:

* skips, by name and with the reason printed, the tests that cannot run under libfaketime for a
  reason that has nothing to do with dates (``date_sentinel_skips.txt`` lists each one);
* corrects ``os.utime`` for the shift (below);
* writes ``SUDARSHAN_DATE_SENTINEL_FAILED`` (a path) with one failing test id per line, so a cell
  of the matrix can upload the list without parsing a log.

Why ``os.utime`` is wrapped: libfaketime moves the times ``stat`` reports forward by the offset but
leaves the kernel's own file times alone. A test that stamps a file "60 seconds ago" with
``os.utime(path, (time.time() - 60,) * 2)`` stores a time that is already shifted, and ``stat`` then
shifts it a second time, so the file looks a day (or 100) newer than the test meant. That is not a
date bomb. The wrapper takes the offset back off an explicit time, so ``stat`` returns what the test
wrote. (A coreutils ``stat`` or ``find`` in a child process is not faked at all and is not covered:
such a test goes in the skip list.)

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

_OFFSET_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_skipped: list[tuple[str, str]] = []
_failed: list[str] = []


def active() -> bool:
    """Whether this run is a sentinel run."""
    return bool(os.environ.get(ENV))


def offset_seconds(faketime: str) -> int:
    """The seconds in a FAKETIME of the form +30d, +90m or +5 (0 for anything else)."""
    text = faketime.strip()
    if not text.startswith("+"):
        return 0
    body, unit = (text[1:-1], text[-1]) if text[-1] in _OFFSET_UNITS else (text[1:], "s")
    try:
        return int(float(body) * _OFFSET_UNITS[unit])
    except ValueError:
        return 0


def pytest_configure(config):
    if not active():
        return
    offset = offset_seconds(os.environ.get("FAKETIME", ""))
    if not offset:
        return
    real_utime = os.utime

    def utime(path, times=None, *, ns=None, dir_fd=None, follow_symlinks=True):
        kwargs = {"dir_fd": dir_fd, "follow_symlinks": follow_symlinks}
        if ns is not None:
            kwargs["ns"] = tuple(t - offset * 1_000_000_000 for t in ns)
        elif times is not None:
            times = tuple(t - offset for t in times)
        return real_utime(path, times, **kwargs)

    os.utime = utime


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
