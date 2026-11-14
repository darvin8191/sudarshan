"""Notices: the facts behind the Telegram messages about the server itself (M18.x).

The deploy tool runs as root and must never hold a Telegram or an OpenAlgo secret, and nothing in
the ring may be able to ask root for anything. So the tool only RECORDS what happened, one JSON
line in a file the ring can read, and ``sudarshan-alerts`` (``alerts/notices.py``) words the
message for the owner and sends it, once per event id. The nightly backup job (``backup.py``)
records its failures the same way, in a file of its own: it runs as the sudarshan user and cannot
write root's.

A line (every value is a string, as ``jq --arg`` writes them):

    v     "1", the format
    id    the event's id: letters, digits and . _ -, at most 120; a message is sent once per id
    src   who recorded it: deploy, backup, family, journal or watch
    kind  what happened (KINDS)
    at    seconds since 1970 when it happened
    run   the events of one run share it; the owner is told one run's facts in one message
    ...   the facts of that kind (tag, was, why, unit, up, backup, ...): short plain words

A reader skips what it cannot use (a line cut off by a crash, a format or a kind it does not know)
and never raises: a notice is a convenience, never the reason a service stops. The file is kept
short by its writer (the newest ``KEEP_LINES`` lines).
"""

from __future__ import annotations

import errno
import json
import math
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .files import read_regular

#: Where each producer records. The deploy tool is root and writes under its own state folder; the
#: backup job runs as the sudarshan user and writes under its unit's state directory.
DEPLOY_SPOOL = Path("/var/lib/sudarshan/events.jsonl")
BACKUP_SPOOL = Path("/var/lib/sudarshan-backup/events.jsonl")
#: The family service's export folder is made by root (mode 2750, group sudarshan), so a spool in
#: it is written 0640 (``record(..., file_mode=0o640)``) and read by the notice desk.
FAMILY_SPOOL = Path("/var/lib/sudarshan-family-export/events.jsonl")
#: The journal records the reports it has made here. It is read by the report desk (a later
#: change), never by the notice desk: a report is not a notice.
JOURNAL_SPOOL = Path("/var/lib/sudarshan-journal/events.jsonl")
#: The watch (``ops/watch``, M19.5) records each change of a check's level here, as the sudarshan
#: user, in its unit's state directory.
WATCH_SPOOL = Path("/var/lib/sudarshan-watch/events.jsonl")
#: What the notice desk reads. A missing file is normal: the family service may not be on.
DEFAULT_SPOOLS = (DEPLOY_SPOOL, BACKUP_SPOOL, FAMILY_SPOOL, WATCH_SPOOL)
#: What a notice can be about. Each has its words in ``sudarshan/alerts/notices.py``, except
#: ``report`` (src journal): it carries the id of a report, its kind and the sha256 of its file and
#: no text, and belongs to the report desk, not to the notice desk.
KINDS = frozenset(
    {
        "installed",
        "rolled_back",
        "unit_refused",
        "waiting",
        "backup_failed",
        "report",
        "lockout",
        "new_device",
        "invite_used",
        "member_removed",
        "push_failing",
        "bot_token_rejected",
        "store_newer",
        "family_check_failed",
        "watch_failing",
        "watch_warning",
        "watch_fine",
        "watch_new",
        "watch_summary",
        "drill_passed",
        "drill_failed",
    }
)
FORMAT = "1"
SOURCES = frozenset({"deploy", "backup", "journal", "family", "watch"})
DEFAULT_FILE_MODE = 0o600
ID = re.compile(r"[A-Za-z0-9._-]{1,120}")
KEEP_LINES = 200
#: Compaction starts above this many lines (the deploy tool uses the same numbers).
COMPACT_ABOVE = 2 * KEEP_LINES
MAX_BYTES = 1 << 20
MAX_VALUE = 200


@dataclass(frozen=True)
class Event:
    id: str
    kind: str
    at: float
    run: str
    src: str
    facts: Mapping[str, str] = field(default_factory=dict)

    def fact(self, name: str, default: str = "") -> str:
        return self.facts.get(name, default)


def parse_line(line: str | bytes) -> Event | None:
    """One event from one line, or None if the line is not a usable event."""
    try:
        data = json.loads(line)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or str(data.get("v")) != FORMAT:
        return None
    ident, kind, src = data.get("id"), data.get("kind"), data.get("src")
    if not isinstance(ident, str) or not ID.fullmatch(ident):
        return None
    if kind not in KINDS or src not in SOURCES:
        return None
    try:
        at = float(data.get("at"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(at) or at <= 0:
        return None
    run = data.get("run")
    run = run if isinstance(run, str) and ID.fullmatch(run) else ident
    facts = {
        str(key): _plain(value)
        for key, value in data.items()
        if key not in {"v", "id", "src", "kind", "at", "run"} and isinstance(value, str | int)
    }
    return Event(ident, kind, at, run, src, facts)


def _plain(value) -> str:
    """A fact as short single-line text."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(value))
    return " ".join(text.split())[:MAX_VALUE]


def read_events(path: Path | str) -> list[Event]:
    """Every usable event in the file, in file order. A missing or unreadable file has none."""
    raw = read_regular(path, MAX_BYTES)
    if raw is None:
        return []
    events = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        event = parse_line(line)
        if event is not None:
            events.append(event)
    return events


def record(
    path: Path | str,
    *,
    kind: str,
    event_id: str,
    at: float,
    run: str,
    src: str,
    file_mode: int = DEFAULT_FILE_MODE,
    **facts: str,
) -> bool:
    """Append one event to the file; True when it is there. Never raises.

    For the writers that run as the sudarshan user (the root deploy tool does the same in bash).
    An id already in the file is not written again. The file is created private (mode 600) and
    compacted to its newest ``KEEP_LINES`` lines once it has grown past ``COMPACT_ABOVE``.

    ``file_mode`` is the mode a new file is created with (0o640 for a spool another account
    reads), set on the file itself once it is made: ``os.open`` takes the umask off its mode, and
    the family unit runs with UMask=0077, which would leave a 0600 spool the notice desk cannot
    read. A file that exists keeps its mode, and compaction keeps it too. A folder is made only
    when it is missing (private, 0o700): one that exists is never changed.
    """
    path = Path(path)
    if kind not in KINDS or src not in SOURCES or not ID.fullmatch(event_id):
        return False
    line = {"v": FORMAT, "id": event_id, "src": src, "kind": kind, "at": f"{at:.0f}", "run": run}
    line.update({name: _plain(value) for name, value in facts.items()})
    try:
        if any(event.id == event_id for event in read_events(path)):
            return True
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = (json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        fd = _open_for_append(path, file_mode)
        try:
            _append(fd, payload)
        finally:
            os.close(fd)
    except OSError:
        return False
    try:
        _compact(path)
    except OSError:
        pass  # the event is written; a file left long is trimmed by the next writer
    return True


def _open_for_append(path: Path, file_mode: int) -> int:
    """The file open for appending, made with exactly ``file_mode`` when it is not there yet. A
    symbolic link is refused (O_NOFOLLOW; O_EXCL never follows one either)."""
    flags = os.O_RDWR | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, file_mode)
    except FileExistsError:
        return os.open(path, flags)
    try:
        os.fchmod(fd, file_mode)  # made by this call: the umask took bits off the mode
    except BaseException:
        os.close(fd)
        raise
    return fd


def _append(fd: int, payload: bytes) -> None:
    """All of ``payload`` at the end of the file, on a line of its own.

    A last line a crash left without its newline is ended first: the record would otherwise be
    glued to it and both lost, while ``record`` said it was there. A write that takes only part of
    the bytes is carried on until they are all in.
    """
    size = os.fstat(fd).st_size
    if size and os.pread(fd, 1, size - 1) != b"\n":
        payload = b"\n" + payload
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError(errno.EIO, "nothing could be written")
        view = view[written:]


def _compact(path: Path) -> None:
    """Keep the newest KEEP_LINES lines once the file has more than COMPACT_ABOVE."""
    raw = read_regular(path, MAX_BYTES)
    if raw is None:
        return
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if len(lines) <= COMPACT_ABOVE:
        return
    keep = "\n".join(lines[-KEEP_LINES:]) + "\n"
    mode = stat.S_IMODE(path.stat().st_mode)
    tmp = path.with_name(f".{path.name}.new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), mode)  # the umask took bits off the creation mode
            handle.write(keep)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)
