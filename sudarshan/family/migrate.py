"""``python -m sudarshan.family.migrate [--status]``: family.db's version and its steps (design 4.3).

``--status`` opens the file read-only and changes nothing (no file made, no step run, no side file
written): it prints the file's version, this code's version and the steps still pending. The deploy
tool logs it before it starts the unit, run as the account that owns the store's folder
(``sudarshan-family``). It needs no key and reads nothing but the store.

Without ``--status`` it applies the pending steps, and refuses while the service holds its port:
the service migrates the file itself at start, and two writers of a schema step are one too many.

Exit status: 0 up to date or applied (``--status`` also with steps pending), 2 the file is newer
than this code or could not be used.
"""

from __future__ import annotations

import argparse
import socket
import sys
from collections.abc import Sequence
from pathlib import Path

from . import migrations, store
from .contract import PORT
from .errors import FamilyError, SchemaTooNew


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
    return {
        "path": str(path),
        "file": found,
        "code": migrations.LATEST,
        "pending": [n for n, _ in migrations.STEPS if n > (found or 0)],
        "newer": found is not None and found > migrations.LATEST,
    }


def service_is_up(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sudarshan.family.migrate")
    parser.add_argument("--status", action="store_true", help="report only; change nothing")
    parser.add_argument("--path", type=Path, default=None, help="family.db (default: state dir)")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args(argv)
    path = args.path or store.default_path()
    try:
        report = status(path)
    except FamilyError as exc:
        _say(str(exc))
        return 2
    _say(f"file:    {report['path']}")
    shown = "none" if report["file"] is None else report["file"]
    _say(f"version: file {shown}, code {report['code']}")
    _say(f"pending: {report['pending'] or 'none'}")
    if report["newer"]:
        _say(SchemaTooNew(report["file"], report["code"]).message)
        return 2
    if args.status:
        return 0
    if service_is_up(args.port):
        _say("The family service is running; stop it first, or let it migrate at start.")
        return 2
    try:
        opened = store.FamilyStore(path)
    except FamilyError as exc:
        _say(str(exc))
        return 2
    opened.close()
    store.stop()
    _say(f"applied: {opened.applied or 'nothing to do'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
