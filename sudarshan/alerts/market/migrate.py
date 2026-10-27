"""``python -m sudarshan.alerts.market.migrate --status``: market.db's version, read-only (X7).

Prints the file's version, this code's version and the steps the service would run at its next
start. It never creates, migrates or writes the file: sudarshan-alerts migrates market.db itself
when market alerts start (migrations.py), as the user that owns it. The deploy logs this before it
starts the ring's units.

    market.db: /var/lib/sudarshan-alerts/market.db
    file version: 1
    code version: 1
    pending steps: none

The file is found as the service finds it: ``SUDARSHAN_ALERTS_STATE_DIR``, else systemd's
``STATE_DIRECTORY``, else /var/lib/sudarshan-alerts; ``--db`` names another one.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from database.engine_factory import create_db_engine
from sudarshan.alerts.config import STATE_DIR
from sudarshan.alerts.market import migrations
from sudarshan.alerts.market.store import DB_FILENAME
from sudarshan.alerts.owner import database_urls


def default_path() -> Path:
    state = os.environ.get("SUDARSHAN_ALERTS_STATE_DIR") or os.environ.get("STATE_DIRECTORY")
    return Path((state or STATE_DIR).split(":")[0]) / DB_FILENAME


def status(path: Path) -> tuple[int, list[str]]:
    """(exit code, lines): 0 for any version, 1 when the file cannot be read."""
    known = migrations.LATEST
    every = [number for number, _ in migrations.STEPS]
    lines = [f"market.db: {path}"]
    if not path.exists():
        lines += [
            "file version: none (sudarshan-alerts makes it when market alerts first start)",
            f"code version: {known}",
            f"pending steps: {', '.join(map(str, every))}",
        ]
        return 0, lines
    found = None
    for url in database_urls(path):
        engine = create_db_engine(url)
        try:
            with engine.connect() as conn:
                found = migrations.version(conn)
            break
        except SQLAlchemyError:
            continue
        finally:
            engine.dispose()
    if found is None:
        return 1, [*lines, "file version: the file could not be read (check its owner and mode)"]
    lines += [f"file version: {found}", f"code version: {known}"]
    if found > known:
        lines.append(
            "pending steps: none; the file is from a newer release, so market alerts stay off "
            "until that release is installed again"
        )
    else:
        pending = [number for number in every if number > found]
        lines.append(f"pending steps: {', '.join(map(str, pending)) or 'none'}")
    return 0, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sudarshan.alerts.market.migrate",
        description="Show market.db's version. sudarshan-alerts migrates the file itself when "
        "market alerts start; this only reads.",
    )
    parser.add_argument("--status", action="store_true", help="show the versions (read-only)")
    parser.add_argument("--db", type=Path, help="the file (default: the service's own)")
    args = parser.parse_args(argv)
    if not args.status:
        parser.print_usage(sys.stderr)
        sys.stderr.write(
            "Only --status is offered: sudarshan-alerts migrates market.db itself when market "
            "alerts start.\n"
        )
        return 2
    code, lines = status(args.db or default_path())
    sys.stdout.write("\n".join(lines) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
