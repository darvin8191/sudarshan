"""``python -m sudarshan.signals.migrate [--status]``: signals.db's version and its steps (T3).

``--status`` opens the file read-only and changes nothing (no file made, no step run): it prints
the file's version, this code's version and the steps still pending. The deploy runs it before it
starts the service and logs what it says (M18.11). Without ``--status`` it applies the steps, and
refuses while the service holds its port, because the service migrates the file itself at start
and two writers of a schema step are one too many.

Exit status: 0 up to date, applied, or steps pending under ``--status``; 2 the file is newer than
this code or could not be used.
"""

from __future__ import annotations

import argparse
import socket
import sys
from collections.abc import Sequence
from pathlib import Path

from sudarshan.signals import contract, migrations, store

LOCAL_HOST = "127.0.0.1"


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
    parser = argparse.ArgumentParser(prog="python -m sudarshan.signals.migrate")
    parser.add_argument("--status", action="store_true", help="report only; change nothing")
    parser.add_argument("--path", type=Path, default=None, help="signals.db (default: state dir)")
    parser.add_argument("--port", type=int, default=contract.PORT)
    args = parser.parse_args(argv)
    path = args.path or store.default_path()
    try:
        report = status(path)
    except store.StoreError as exc:
        _say(str(exc))
        return 2
    _say(f"file:    {report['path']}")
    _say(
        f"version: file {'none' if report['file'] is None else report['file']}, "
        f"code {report['code']}"
    )
    _say(f"pending: {report['pending'] or 'none'}")
    if report["newer"]:
        _say(contract.WORDS["store_newer"])
        return 2
    if args.status:
        return 0
    if service_is_up(args.port):
        _say("The TRINETRA reader is running; stop it first, or let it migrate at start.")
        return 2
    try:
        opened = store.SignalsStore(path)
    except (store.StoreError, migrations.SchemaTooNew) as exc:
        _say(str(exc))
        return 2
    opened.close()
    _say(f"applied: {opened.applied or 'nothing to do'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
