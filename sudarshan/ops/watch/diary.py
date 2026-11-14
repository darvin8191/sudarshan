"""The server diary (M19.5, M19.10; P8 7.1, 7.8): every notice the server ever recorded, kept.

The notice files (``ops/events.py``) keep their newest 200 lines; the diary keeps 8 years. Each
run of the watch appends what it has not seen yet from every notice file to
``/var/lib/sudarshan-watch/log/<year>.jsonl`` (the year of the event, in India): installs and
rollbacks, backups that failed, family sign-ins, reports made, drills, every change the watch
itself told. It is the server's part of the records the owner keeps each year (README, "Records
to keep each year"), and the nightly backup takes the folder.

A line is the event as its producer wrote it: id, src, kind, at, run and its facts. No secret
is in an event, so none is here. An event is appended once per id; the ids seen are kept beside
the year files (``seen.json``), written after the lines, so a run that was stopped between the
two can leave a line twice (same id), never miss one.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from sudarshan.ops import events
from sudarshan.ops.files import read_regular

FOLDER = "log"
KEEP_YEARS = 8
#: How many ids are remembered: every notice file together holds fewer lines than this.
SEEN_MAX = 3000
SEEN_FILE = "seen.json"
_MAX_SEEN_BYTES = 1 << 20
_IST = timezone(timedelta(hours=5, minutes=30))
_YEAR_FILE = re.compile(r"(2[0-9]{3})\.jsonl")
#: Every producer's file, the journal's reports included: a report made is a record too.
SPOOLS = (*events.DEFAULT_SPOOLS, events.JOURNAL_SPOOL)


def _year(at: float) -> int:
    return datetime.fromtimestamp(at, UTC).astimezone(_IST).year


def _line(event: events.Event) -> str:
    document = {
        "id": event.id,
        "src": event.src,
        "kind": event.kind,
        "at": int(event.at),
        "run": event.run,
        **dict(sorted(event.facts.items())),
    }
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n"


def append_new(
    state_dir: Path, seen: Iterable[str], spools: Iterable[Path] = SPOOLS
) -> tuple[int, list[str]]:
    """Append every event not in ``seen`` to its year's file. Returns how many were written and
    the ids to remember (the newest ``SEEN_MAX``). Never raises: an event that could not be
    written is not remembered, so the next run writes it."""
    known = list(seen)
    have = set(known)
    by_year: dict[int, list[events.Event]] = {}
    for spool in spools:
        for event in events.read_events(spool):
            if event.id not in have:
                have.add(event.id)
                by_year.setdefault(_year(event.at), []).append(event)
    written = 0
    folder = state_dir / FOLDER
    for year, found in sorted(by_year.items()):
        found.sort(key=lambda event: (event.at, event.id))
        try:
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(folder / f"{year}.jsonl", flags, 0o600)
            try:
                os.write(fd, "".join(_line(event) for event in found).encode("utf-8"))
            finally:
                os.close(fd)
        except OSError:
            continue
        known += [event.id for event in found]
        written += len(found)
    return written, known[-SEEN_MAX:]


def prune(state_dir: Path, this_year: int) -> list[str]:
    """Remove the year files older than ``KEEP_YEARS`` years; returns the names removed. Only
    files named ``<year>.jsonl`` are ever touched."""
    removed = []
    try:
        names = sorted(os.listdir(state_dir / FOLDER))
    except OSError:
        return removed
    for name in names:
        found = _YEAR_FILE.fullmatch(name)
        if found and int(found[1]) < this_year - KEEP_YEARS:
            try:
                os.unlink(state_dir / FOLDER / name)
                removed.append(name)
            except OSError:
                pass
    return removed


def keep(state_dir: Path, now: float, spools: Iterable[Path] = SPOOLS) -> int:
    """One run's diary work: append what is new, remember it, drop the years past keeping.
    Returns how many events were written. Never raises."""
    seen_file = state_dir / FOLDER / SEEN_FILE
    raw = read_regular(seen_file, _MAX_SEEN_BYTES)
    try:
        seen = json.loads(raw) if raw else []
    except ValueError:
        seen = []
    seen = [item for item in seen if isinstance(item, str)] if isinstance(seen, list) else []
    written, remembered = append_new(state_dir, seen, spools)
    if written:
        temp = seen_file.with_name(f".{SEEN_FILE}.new")
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(temp, flags, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(remembered, handle, separators=(",", ":"))
            os.replace(temp, seen_file)
        except OSError:
            pass
    prune(state_dir, _year(now))
    return written
