"""The built-in lists made from typed files: NIFTY 50, NIFTY BANK, NIFTY 100, 200, 500 and the ten
sector lists (M8.8, W13).

A constituent list is typed by a person, never downloaded: nothing here fetches anything from NSE
or anywhere else, and no constituent file is added by this feature. The format is one symbol a
line, ``#`` comments, and the date of the index review in a header line ``# as of YYYY-MM-DD``
(a file with no such line reads "date not recorded"). A line that is not a symbol (spaces, lower
case) or that repeats an earlier one is left out and named by its line number; the rest of the
list stands.

Where a file comes from: the server folder first (``/etc/sudarshan/universes/``, or the folder
named by ``SUDARSHAN_UNIVERSES_DIR``), then, for the two lists the repository ships (NIFTY 50 and
NIFTY BANK, the screener's files), ``sudarshan/screener/universes/``. The server copy of a name
wins. A list whose file is in neither place is not offered at all. A folder that cannot be read is
treated as absent and logged once (a folder that is simply not there is the normal state before the
owner makes it, and is not logged).

A file changed on disk is read again at the next call: the stamp (path, modification time, size,
inode) is one ``stat`` per file, and the parsed symbols are kept only while the stamp holds. The
prepared list (references to the master's own rows) is kept by ``refs/preset.py`` with the master's
generation and this stamp, so it goes with the generation as the master-derived lists do. No thread,
timer or file watcher: the request is the check.

This module reads files (``pathlib``) and logs once; it imports nothing else of OpenAlgo and no
Flask. The rule that the other modules of this package are pure does not cover it.
"""

from __future__ import annotations

import errno
import os
import re
import stat
from datetime import date
from functools import partial
from pathlib import Path
from typing import NamedTuple

from sudarshan.watchlists import search
from sudarshan.watchlists.presets import Built, Entry, Prepared, master_lists
from sudarshan.watchlists.words import presets as words
from utils.logging import get_logger

logger = get_logger(__name__)

ORDER = 10  # before the lists made from the master (20): they come first in the catalogue

ENV = "SUDARSHAN_UNIVERSES_DIR"
SERVER_DIR = "/etc/sudarshan/universes"
#: The repository's own typed files: the screener's (it reads the same two).
REPO_DIR = Path(__file__).resolve().parents[2] / "screener" / "universes"
#: A file larger than this is not a list somebody typed (NIFTY 500 is about 5 KB).
MAX_BYTES = 1_000_000

SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9&_-]*\Z")
HEADER = re.compile(r"#\s*as\s+of\s+(.*?)\s*\Z", re.IGNORECASE)
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


class Spec(NamedTuple):
    id: str
    name: str
    file: str
    repo: bool  # also read from the repository when the server folder has no such file


#: Every typed list: the owner types the files named here by hand on the server.
SPECS = (
    Spec("nifty50", "NIFTY 50", "NIFTY50.txt", True),
    Spec("niftybank", "NIFTY BANK", "NIFTYBANK.txt", True),
    Spec("nifty100", "NIFTY 100", "NIFTY100.txt", False),
    Spec("nifty200", "NIFTY 200", "NIFTY200.txt", False),
    Spec("nifty500", "NIFTY 500", "NIFTY500.txt", False),
    Spec("niftyit", "NIFTY IT", "NIFTYIT.txt", False),
    Spec("niftyauto", "NIFTY AUTO", "NIFTYAUTO.txt", False),
    Spec("niftypharma", "NIFTY PHARMA", "NIFTYPHARMA.txt", False),
    Spec("niftymetal", "NIFTY METAL", "NIFTYMETAL.txt", False),
    Spec("niftyfmcg", "NIFTY FMCG", "NIFTYFMCG.txt", False),
    Spec("niftypsubank", "NIFTY PSU BANK", "NIFTYPSUBANK.txt", False),
    Spec("niftypvtbank", "NIFTY PRIVATE BANK", "NIFTYPVTBANK.txt", False),
    Spec("niftyrealty", "NIFTY REALTY", "NIFTYREALTY.txt", False),
    Spec("niftyenergy", "NIFTY ENERGY", "NIFTYENERGY.txt", False),
    Spec("niftyfinservice", "NIFTY FINANCIAL SERVICES", "NIFTYFINSERVICE.txt", False),
)  # fmt: skip


# ----------------------------------------------------------------------------- the format (pure)


class Typed(NamedTuple):
    """What a typed file holds: its symbols in order, its date, and the lines left out."""

    symbols: tuple[str, ...]
    as_of: str | None  # ISO date of the ``# as of`` line; None: date not recorded
    problems: tuple[tuple[int, str, str, int], ...] = ()  # (line, kind, text, other line)


def parse(text: str) -> Typed:
    """Read the typed format. A bad line is left out and named by its (1-based) number; it never
    fails the list. Symbols are kept in the file's order."""
    symbols: list[str] = []
    seen: dict[str, int] = {}
    problems: list[tuple[int, str, str, int]] = []
    as_of: str | None = None
    for number, raw in enumerate(text.lstrip("﻿").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            head = HEADER.fullmatch(line)
            if head is not None and as_of is None:
                value = head.group(1)
                try:
                    if not ISO_DATE.fullmatch(value):
                        raise ValueError(value)
                    date.fromisoformat(value)
                    as_of = value
                except ValueError:
                    problems.append((number, "date", value, 0))
            continue
        symbol = line.split("#", 1)[0].strip()  # a comment may follow a symbol
        if not SYMBOL.fullmatch(symbol):
            problems.append((number, "symbol", symbol, 0))
        elif symbol in seen:
            problems.append((number, "repeat", symbol, seen[symbol]))
        else:
            seen[symbol] = number
            symbols.append(symbol)
    return Typed(tuple(symbols), as_of, tuple(problems))


# ------------------------------------------------------------------------------ the files (I/O)


class Loaded(NamedTuple):
    stamp: tuple  # (path, modification time in ns, size, inode)
    typed: Typed


#: The parsed content of each file while its stamp holds: at most one per list.
_loaded: dict[str, Loaded] = {}
#: The folders already logged as unreadable (logged once, and again after they have been readable).
_logged: set[str] = set()


def server_dir() -> Path:
    return Path(os.environ.get(ENV) or SERVER_DIR)


def _unreadable(folder: Path) -> None:
    key = str(folder)
    if key not in _logged:
        _logged.add(key)
        logger.exception("Watchlists: a folder of typed lists could not be read: %s", folder)


def _fine(folder: Path) -> None:
    _logged.discard(str(folder))


def load(spec: Spec) -> Loaded | None:
    """The file of a list: the server folder's, else (for NIFTY 50 and NIFTY BANK) the
    repository's. None when it is in neither place, or cannot be read. One ``stat`` when the file
    has not changed since it was read."""
    folders = [server_dir(), *([REPO_DIR] if spec.repo else [])]
    for folder in folders:
        path = folder / spec.file
        try:
            st = path.stat()
            if not stat.S_ISREG(st.st_mode):
                continue
            if st.st_size > MAX_BYTES:
                raise OSError(errno.EFBIG, "typed list file too large", str(path))
            stamp = (str(path), st.st_mtime_ns, st.st_size, st.st_ino)
            have = _loaded.get(spec.id)
            if have is not None and have.stamp == stamp:
                _fine(folder)
                return have
            text = path.read_bytes().decode("utf-8", errors="replace")
        except (FileNotFoundError, NotADirectoryError):
            _fine(folder)  # not there: the normal state, not a fault
            continue
        except OSError:
            _unreadable(folder)  # the folder or the file cannot be read: absent, said once
            continue
        _fine(folder)
        got = _loaded[spec.id] = Loaded(stamp, parse(text))
        return got
    _loaded.pop(spec.id, None)
    return None


# ----------------------------------------------------------------------------- the lists (pure)


def prepare(spec: Spec, index: search.Index) -> Prepared:
    """The list against one master generation: the master's own NSE cash row of each symbol in
    the file's order, and the symbols the master lacks. Absent file (it went since it was
    offered): an empty list, which the next call finds not offered."""
    got = load(spec)
    if got is None:
        return Prepared()
    rows, missing = [], []
    for symbol in got.typed.symbols:
        row = master_lists._row(index, symbol, "NSE", ("EQ", ""))
        if row is None:
            missing.append(f"NSE:{symbol}")
        else:
            rows.append(row)
    notes = (words.problems_line(list(got.typed.problems)),) if got.typed.problems else ()
    return Prepared(rows=tuple(rows), missing=tuple(missing), as_of=got.typed.as_of, notes=notes)


def members(prepared: Prepared, now_ms: int, closes: dict) -> Built:
    """A typed list does not depend on the clock."""
    return Built(rows=prepared.rows, missing=prepared.missing, notes=prepared.notes)


def _offered(spec: Spec) -> bool:
    return load(spec) is not None


def _stamp(spec: Spec):
    got = load(spec)
    return None if got is None else got.stamp


ENTRIES = tuple(
    Entry(
        s.id, s.name, "typed", s.file, partial(prepare, s), members, review=True,
        offered=partial(_offered, s), stamp=partial(_stamp, s),
    )
    for s in SPECS
)  # fmt: skip
