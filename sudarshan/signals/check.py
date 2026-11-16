"""``python -m sudarshan.signals.check``: the letterbox set-up, proved (O12, design 3.8).

Run as SUDARSHAN's account (``sudo -u sudarshan``), after ``ops/signals/bootstrap.sh``. It proves:

1. the set-up file names a publisher, and it is not this account;
2. the letterbox is a real folder of this account with mode 1733;
3. a file written by another account is refused: it writes one file into the letterbox as this
   account (a stranger to the letterbox), judges it as the service would, and sees it removed.
   If the service is running it may remove the file first, which proves the same.

It reads nothing else in the folder and touches nothing of TRINETRA's. Running it again is safe.
Exit status: 0 PASS, 1 FAIL, 2 nothing to check (the set-up has not been run).
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from sudarshan.signals import contract as c
from sudarshan.signals import inbox

SETUP = "Run the set-up first: sudo sudarshan/ops/signals/bootstrap.sh --publisher <account>"
#: The probe's document name: a document's form (so it is judged, not removed as a bad name),
#: with a sequence number TRINETRA's own counting reaches only after years.
PROBE_SEQ = 999_999_999


def _say(text: str) -> None:
    sys.stdout.write(text + "\n")


def run(folder: Path, env_file: Path, *, now_ms: int | None = None) -> int:
    publisher = inbox.publisher_uid(env_file)
    if publisher is None:
        _say(f"FAIL: {env_file} names no publisher. {SETUP}")
        return 2
    own = os.geteuid()
    if publisher == own:
        _say("FAIL: the publisher is this account itself, so any of SUDARSHAN's programs could "
             f"publish readings. {SETUP} with TRINETRA's account.")  # fmt: skip
        return 1
    box = inbox.Letterbox(folder, publisher)
    state = box.state()
    if state != "ok":
        why = "is missing" if state == "missing" else "is not this account's folder with mode 1733"
        _say(f"FAIL: the letterbox {folder} {why}. {SETUP}")
        return 1
    _say(f"letterbox: {folder} belongs to this account, mode 1733")
    _say(f"publisher: account number {publisher}")
    at = int(time.time() * 1000) if now_ms is None else now_ms
    name = f"{at}-{PROBE_SEQ}.json"
    path = folder / name
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        try:
            os.write(fd, b'{"v":1,"probe":"sudarshan.signals.check"}')
        finally:
            os.close(fd)
    except OSError as exc:
        _say(f"FAIL: no file could be written into the letterbox ({(exc.strerror or '').lower()}).")
        return 1
    verdict = box.probe(name)
    if verdict == "found" or path.exists():
        with contextlib.suppress(OSError):
            path.unlink()
        _say("FAIL: a file written by another account than the publisher was not refused.")
        return 1
    _say("refused: a file written by another account was removed unread")
    _say("PASS.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sudarshan.signals.check")
    parser.add_argument("--folder", type=Path, default=Path(c.LETTERBOX))
    parser.add_argument("--env-file", type=Path, default=Path(c.SETUP_FILE))
    args = parser.parse_args(argv)
    return run(args.folder, args.env_file)


if __name__ == "__main__":
    sys.exit(main())
