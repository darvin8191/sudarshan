"""``python -m sudarshan.screener.migrate [--status]``: screener.db's version and its steps.

``--status`` opens the file read-only and changes nothing (no file made, no step run, no side
file written): it prints the file's version, this code's version and the steps still pending.
Without ``--status`` it applies them, and refuses while the service holds its port, because the
service migrates the file itself at start and two writers of a schema step are one too many
(cross-design X7).

Exit status: 0 up to date or applied, 1 steps pending (``--status``), 2 the file is newer than
this code or could not be used.
"""

from __future__ import annotations

import argparse
import socket
import sys
from collections.abc import Sequence
from pathlib import Path

from . import migrations, store
from .contract import DEFAULT_HTTP_PORT, LOCAL_HOST


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
    parser = argparse.ArgumentParser(description="screener.db migrations")
    parser.add_argument("--status", action="store_true", help="report only; change nothing")
    parser.add_argument(
        "--path", type=Path, default=None, help="the database (default: the state directory's)"
    )
    parser.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT, help="the service's port")
    args = parser.parse_args(argv)
    path = args.path or store.default_path()
    try:
        report = status(path)
    except store.StoreError as exc:
        _say(f"{path}: {exc} ({exc.cause})")
        return 2
    _say(f"file: {path}")
    _say(f"file version: {report['file'] if report['file'] is not None else 'no file'}")
    _say(f"code version: {report['code']}")
    if report["newer"]:
        _say(migrations.SchemaTooNew.WORDS)
        return 2
    if not report["pending"]:
        _say("up to date")
        return 0
    _say("pending steps: " + ", ".join(str(n) for n in report["pending"]))
    if args.status:
        return 1
    if service_is_up(args.port):
        _say(
            f"sudarshan-screener is running on port {args.port}; it migrates the file itself at "
            "start. Stop it first, or let it run."
        )
        return 2
    try:
        opened = store.ScreenerStore(path)
    except (store.StoreError, migrations.SchemaTooNew) as exc:
        _say(str(exc))
        return 2
    try:
        _say("applied steps: " + ", ".join(str(n) for n in opened.applied))
    finally:
        opened.close()
        store.stop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
