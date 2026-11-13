"""Which SUDARSHAN test files one CI shard runs.

``sudarshan-ci.yml`` runs the service tests in four jobs side by side, each with
``python -m sudarshan.tests.ci_shards <shard> <count>`` and pytest on the files it prints. A file
is never split, so module fixtures stay whole, and pytest gets the same paths it would collect
from ``sudarshan`` itself, so nothing about imports changes. (A conftest.py at ``sudarshan/``
cannot do this: ``sudarshan/`` has no ``__init__.py``, so pytest would put that folder on the
import path, and ``import portfolio`` would then find ``sudarshan/portfolio`` instead of the
repository's own ``portfolio`` package.)
"""

from __future__ import annotations

import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def shard_of(relative_path: str, count: int) -> int:
    """The shard (1 to ``count``) a test file belongs to, from its path relative to the repo."""
    return zlib.crc32(relative_path.encode("utf-8")) % count + 1


def all_test_files() -> list[str]:
    """Every SUDARSHAN test file, as a path relative to the repository, in a stable order."""
    files = []
    for path in (ROOT / "sudarshan").rglob("test_*.py"):
        relative = path.relative_to(ROOT).as_posix()
        if "/tests/" in relative:
            files.append(relative)
    return sorted(files)


def files_for(shard: int, count: int) -> list[str]:
    """The test files of one shard."""
    return [name for name in all_test_files() if shard_of(name, count) == shard]


def main(argv: list[str]) -> int:
    try:
        shard, count = int(argv[1]), int(argv[2])
    except (IndexError, ValueError):
        sys.stderr.write("usage: python -m sudarshan.tests.ci_shards <shard> <count>\n")
        return 2
    if not 1 <= shard <= count:
        sys.stderr.write(f"the shard must be between 1 and {count}\n")
        return 2
    sys.stdout.write("".join(f"{name}\n" for name in files_for(shard, count)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
