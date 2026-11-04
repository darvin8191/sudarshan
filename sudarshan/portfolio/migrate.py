"""``python -m sudarshan.portfolio.migrate [--status]``: portfolio.db's version and its steps.

``--status`` opens the file read-only and changes nothing (no file made, no step run, no side
file written): it prints the file's version, this code's version and the steps still pending.
Without ``--status`` it applies them (and the first-start seeds), and refuses while the service
holds its port, because the service migrates the file itself at start and two writers of a schema
step are one too many (cross-design X7).

Exit status: 0 up to date or applied, 1 steps pending (``--status``), 2 the file is newer than
this code or could not be used.
"""

from __future__ import annotations

import argparse
import socket
import sys
from collections.abc import Sequence
from pathlib import Path

from sudarshan.portfolio import migrations, store
from sudarshan.portfolio.core.kinds import DEFAULT_HTTP_PORT, LOCAL_HOST


def _say(text: str) -> None:
    sys.stdout.write(text + "\n")


def file_version(path: Path) -> int | None:
    """The file's user_version without touching it; None when there is no file."""
    if not path.exists():
        return None
    made = store.open_read_only(path)
    try:
        with made.connect() as conn:
            return migrations.version(conn)
    finally:
        made.dispose()


def status(path: Path) -> dict:
    found = file_version(path)
    known = migrations.LATEST
    return {
        "path": str(path),
        "file": found,
        "code": known,
        "pending": [n for n, _ in migrations.STEPS if n > (found or 0)],
        "newer": found is not None and found > known,
    }


def service_is_up(port: int) -> bool:
    try:
        with socket.create_connection((LOCAL_HOST, port), timeout=0.5):
            return True
    except OSError:
        return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sudarshan.portfolio.migrate")
    parser.add_argument("--status", action="store_true", help="report only; change nothing")
    parser.add_argument("--path", type=Path, default=None, help="portfolio.db (default: state dir)")
    parser.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT)
    args = parser.parse_args(argv)
    path = args.path or store.default_path()
    try:
        report = status(path)
    except store.StoreError as exc:
        _say(str(exc))
        return 2
    _say(f"file:    {report['path']}")
    _say(f"version: file {'none' if report['file'] is None else report['file']}, "
          f"code {report['code']}")  # fmt: skip
    _say(f"pending: {report['pending'] or 'none'}")
    if report["newer"]:
        _say(migrations.SchemaTooNew.WORDS)
        return 2
    if args.status:
        return 1 if report["pending"] else 0
    if service_is_up(args.port):
        _say("The portfolio service is running; stop it first, or let it migrate at start.")
        return 2
    try:
        opened = store.PortfolioStore(path)
    except (store.StoreError, migrations.SchemaTooNew) as exc:
        _say(str(exc))
        return 2
    opened.close()
    store.stop()
    _say(f"applied: {opened.applied or 'nothing to do'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
