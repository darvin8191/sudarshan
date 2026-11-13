"""Splits the SUDARSHAN test files into shards for CI (sudarshan-ci.yml runs them side by side).

``SUDARSHAN_TEST_SHARD=i/n`` keeps only the test files whose path hash falls in shard ``i`` of
``n``; a file is never split, so module fixtures stay whole. Without the variable every file is
collected, as on a builder's machine.
"""

from __future__ import annotations

import os
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def shard_of(relative_path: str, count: int) -> int:
    """The shard (1 to ``count``) a test file belongs to, from its path relative to the repo."""
    return zlib.crc32(relative_path.encode("utf-8")) % count + 1


def _wanted() -> tuple[int, int] | None:
    raw = os.environ.get("SUDARSHAN_TEST_SHARD", "").strip()
    if not raw:
        return None
    index, _, count = raw.partition("/")
    shard, total = int(index), int(count)
    if not 1 <= shard <= total:
        raise ValueError(f"SUDARSHAN_TEST_SHARD={raw!r}: the shard must be between 1 and {total}")
    return shard, total


def pytest_ignore_collect(collection_path: Path, config) -> bool | None:
    wanted = _wanted()
    if (
        wanted is None
        or collection_path.suffix != ".py"
        or not collection_path.name.startswith("test_")
    ):
        return None
    shard, total = wanted
    relative = collection_path.resolve().relative_to(ROOT).as_posix()
    return True if shard_of(relative, total) != shard else None
