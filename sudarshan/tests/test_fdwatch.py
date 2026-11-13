"""The descriptor watch blames only what the window opened and kept (see fdwatch.py)."""

from __future__ import annotations

import os
import threading

import pytest

from sudarshan.tests.fdwatch import FdWatch


def old_count() -> int:
    return len(os.listdir("/proc/self/fd"))


def test_a_descriptor_another_thread_opens_before_the_snapshot_is_not_the_windows(tmp_path):
    """The old check: a count, then a snapshot a moment later. A descriptor opened by a leftover
    thread between the two is in the snapshot and not in the count, so the old comparison failed
    and had nothing new to name."""
    before_count = old_count()
    held = []
    other = threading.Thread(target=lambda: held.append(open(tmp_path / "other.db", "wb")))
    other.start()
    other.join()
    try:
        watch = FdWatch()
        assert old_count() != before_count  # the old check would fail here, naming nothing new
        watch.assert_clean()
    finally:
        held[0].close()


def test_a_descriptor_opened_in_the_window_and_kept_fails_naming_its_target(tmp_path):
    watch = FdWatch(settle_s=0.2)
    kept = open(tmp_path / "kept-open.db", "wb")
    try:
        with pytest.raises(AssertionError, match=r"kept-open\.db"):
            watch.assert_clean()
    finally:
        kept.close()


def test_a_descriptor_opened_in_the_window_and_closed_within_the_settle_passes(tmp_path):
    watch = FdWatch(settle_s=3.0)
    late = open(tmp_path / "late.db", "wb")
    threading.Timer(0.3, late.close).start()
    watch.assert_clean()
    assert late.closed


def test_a_closed_descriptor_whose_number_a_new_file_takes_is_still_a_leak(tmp_path):
    old = open(tmp_path / "old.db", "wb")
    watch = FdWatch(settle_s=0.2)
    old.close()
    taker = open(tmp_path / "taker.db", "wb")  # the lowest free number: old's
    try:
        with pytest.raises(AssertionError, match=r"taker\.db"):
            watch.assert_clean()
    finally:
        taker.close()


def test_a_descriptor_that_closes_inside_the_window_is_not_a_leak(tmp_path):
    old = open(tmp_path / "old.db", "wb")
    watch = FdWatch()
    old.close()
    watch.assert_clean()
