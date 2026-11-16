"""The letterbox reader (T4, design 3.3.3 and 3.5): what may be read from the folder, and how.

The folder may hold a hostile or broken file, so nothing here trusts a name or a listing:

* The folder itself must be a real folder (never followed as a link) owned by this service's
  account, with mode 1733; otherwise nothing is read (``state`` "wrong", or "missing").
* Each file is opened by name without following a link and without blocking (a FIFO cannot hold
  the scan), and every check is made on the open file: a regular file, owned by the publisher's
  account, one link, no execute, group-write, other-write, set-id or sticky bit, at most 16 KiB.
  A file changed between the listing and the open is judged as it is when opened.
* A name that is not a document's, a stranger's file (dot names included) and a file failing a
  check are removed; nothing from them is read, parsed, imported or run. The publisher's own dot
  names are files being written and are left alone while they are younger than
  ``DOT_FILE_AGE_S``; an older one was left by a writer that died, and is removed.
* A folder in the letterbox is removed when it is empty (nothing in it is ever looked at).
  Whatever cannot be removed (a folder with something in it, say) is counted and logged once,
  then passed over until it changes or goes (``Scan.stuck`` says how many are there).
* At most 64 files are read a scan, oldest first by (at, seq); above 500 waiting, the oldest
  beyond 500 are removed.

Reading is not judging: ``scan`` hands back the bytes; the service judges them
(``contract.read_document``), stores the verdict, then calls ``remove`` (or ``keep`` for a newer
format, left for a later release). ``remove`` deletes only the very file that was read (same
device and inode), so a file put under the same name meanwhile is not lost.

The owner check is ``owner_of(stat)`` (``st_uid`` by default), so a test can stand in for another
account on a machine where it cannot make a file another account owns.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import re
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sudarshan.signals import contract as c

log = logging.getLogger("sudarshan.signals")

NAME = re.compile(c.FILE_NAME)
LIMIT = c.DOC_LIMITS["bytes"]
#: Execute (for anyone), group and other write, set-id and sticky: none may be set on a document.
FORBIDDEN_BITS = 0o7000 | 0o111 | 0o022
#: The age past which the publisher's dot-named file is a dead writer's, not one being written.
#: A document is at most 16 KiB and its writer makes the temporary file, writes it and renames
#: it within milliseconds, so an hour is thousands of times longer than any write, and longer
#: than a clock step or a paused writer is likely to last. Age is counted from the later of the
#: file's last write and last change, so a file still being written to is never old. A writer
#: that does come back after an hour finds its rename refused and writes again; no half-written
#: document is ever read, because a dot name is never read at all.
DOT_FILE_AGE_S = 3600.0
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOCTTY


@dataclass(frozen=True)
class Found:
    """One publisher's file read whole: its name, bytes, when it was written (its modification
    time, on the server's clock) and which file it was (device, inode, change time and size: an
    inode number is reused once a file is gone)."""

    name: str
    raw: bytes
    written_ms: int
    ident: tuple[int, int, int, int]

    @property
    def size(self) -> int:
        return len(self.raw)


@dataclass
class Scan:
    """What one scan found. ``refused``: the publisher's files that failed a check, by name and
    the rule (removed); ``strangers``: files of any other account removed; ``overflow``: the
    oldest removed beyond the ceiling; ``waiting``: documents still in the folder after this
    scan's (so the next scan should come at once); ``swept``: the publisher's dot-named files
    removed for their age; ``stuck``: entries that could not be removed, counted once when they
    were found and passed over since."""

    state: str = "ok"
    found: list[Found] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)
    strangers: int = 0
    overflow: int = 0
    waiting: int = 0
    swept: int = 0
    stuck: int = 0


def order_key(name: str) -> tuple[int, int]:
    """A document's (at, seq) from its name; names are compared as numbers, not as text."""
    at, _, rest = name.partition("-")
    return int(at), int(rest.split(".", 1)[0])


class Letterbox:
    """The folder, its publisher's account number and the service's own."""

    def __init__(
        self,
        folder: Path | str,
        publisher_uid: int,
        *,
        own_uid: int | None = None,
        mode: int = c.LETTERBOX_MODE,
        per_scan: int = c.INBOX["per_scan"],
        waiting: int = c.INBOX["waiting"],
        owner_of: Callable[[os.stat_result], int] = lambda st: st.st_uid,
        clock: Callable[[], float] = time.time,
    ):
        self.folder = Path(folder)
        self.publisher_uid = int(publisher_uid)
        self.own_uid = os.geteuid() if own_uid is None else int(own_uid)
        self.mode, self.per_scan, self.ceiling = mode, per_scan, waiting
        self.owner_of = owner_of
        self.clock = clock
        #: What could not be removed, by name, as it was then: passed over while it stays so.
        self._stuck: dict[str, tuple[int, int, int, int]] = {}
        #: Newer-format files left for a later release: not read again while they stay.
        self._kept: dict[str, tuple[int, int, int, int]] = {}

    # ---- the folder --------------------------------------------------------------------------
    def _open_folder(self) -> tuple[int | None, str]:
        """An open descriptor of the folder and "ok", or None and why not."""
        try:
            fd = os.open(self.folder, _DIR_FLAGS)
        except FileNotFoundError:
            return None, "missing"
        except OSError:
            return None, "wrong"  # a link, not a folder, or not ours to read
        try:
            st = os.fstat(fd)
            if (
                not stat.S_ISDIR(st.st_mode)
                or st.st_uid != self.own_uid
                or stat.S_IMODE(st.st_mode) != self.mode
            ):
                os.close(fd)
                return None, "wrong"
        except OSError:
            os.close(fd)
            return None, "wrong"
        return fd, "ok"

    def state(self) -> str:
        """``ok``, ``missing`` or ``wrong``, without reading a file."""
        fd, state = self._open_folder()
        if fd is not None:
            os.close(fd)
        return state

    # ---- one scan ----------------------------------------------------------------------------
    def scan(self) -> Scan:
        """List, sort, clear what may not stay, and read up to ``per_scan`` documents."""
        fd, state = self._open_folder()
        out = Scan(state=state)
        if fd is None:
            return out
        try:
            documents = self._list(fd, out)
            documents.sort(key=order_key)
            if len(documents) > self.ceiling:
                extra, documents = documents[: -self.ceiling], documents[-self.ceiling :]
                for name in extra:
                    self._unlink(fd, name)
                out.overflow = len(extra)
            taken = 0
            for name in documents:
                if taken >= self.per_scan:
                    break
                if name in self._kept:
                    continue
                taken += 1
                self._take(fd, name, out)
            out.waiting = sum(1 for n in documents if n not in self._kept) - taken
        finally:
            os.close(fd)
        return out

    def _list(self, fd: int, out: Scan) -> list[str]:
        """The publisher's documents' names; strangers' files (whatever their name, so that a
        flood of document names cannot push the publisher's past the ceiling) and bad names
        removed on the way."""
        documents = []
        with os.scandir(fd) as entries:
            names = [entry.name for entry in entries]
        for name in names:
            st = self._lstat(fd, name)
            if st is None:
                continue  # gone meanwhile
            if self._stuck.get(name) == _ident(st):
                out.stuck += 1  # said once when it could not be removed; unchanged since
                continue
            if self.owner_of(st) != self.publisher_uid:
                self._unlink(fd, name)
                out.strangers += 1
            elif stat.S_ISDIR(st.st_mode):
                self._unlink(fd, name)  # a folder is never a document, whatever its name
                out.refused.append((name, "kind"))
            elif NAME.fullmatch(name):
                documents.append(name)
            elif not name.startswith("."):
                self._unlink(fd, name)
                out.refused.append((name, "name"))
            elif self._dead_writers(st):
                out.swept += self._unlink(fd, name, _ident(st))
            # else the publisher's dot name: a file being written, left alone
        there = set(names)
        self._stuck = {n: ident for n, ident in self._stuck.items() if n in there}
        live = set(documents)
        self._kept = {n: ident for n, ident in self._kept.items() if n in live}
        return documents

    def _take(self, fd: int, name: str, out: Scan) -> None:
        """Open, check and read one document, or remove it with the rule it broke."""
        try:
            file_fd = os.open(name, _FILE_FLAGS, dir_fd=fd)
        except OSError as exc:
            st = self._lstat(fd, name)
            if st is None:
                return  # gone meanwhile: its writer took it back
            reason = "link" if exc.errno == errno.ELOOP or stat.S_ISLNK(st.st_mode) else "open"
            self._refuse(fd, name, st, reason, out)
            return
        try:
            st = os.fstat(file_fd)
            reason = self._fault(st)
            if reason is not None:
                self._refuse(fd, name, st, reason, out, ident=_ident(st))
                return
            raw = _read_capped(file_fd)
            if raw is None:
                self._refuse(fd, name, st, "size", out, ident=_ident(st))
                return
            written = st.st_mtime_ns // 1_000_000
            out.found.append(Found(name, raw, written, _ident(st)))
        finally:
            os.close(file_fd)

    def _dead_writers(self, st: os.stat_result) -> bool:
        """Whether the publisher's dot-named file is past ``DOT_FILE_AGE_S``: untouched for that
        long by this machine's clock (a time in the future is not old)."""
        touched = max(st.st_mtime_ns, st.st_ctime_ns) / 1e9
        return self.clock() - touched > DOT_FILE_AGE_S

    def _fault(self, st: os.stat_result) -> str | None:
        """The rule a file breaks, checked on the open file, or None."""
        if not stat.S_ISREG(st.st_mode):
            return "kind"
        if self.owner_of(st) != self.publisher_uid:
            return "owner"
        if st.st_nlink != 1:
            return "links"
        if st.st_mode & FORBIDDEN_BITS:
            return "mode"
        if st.st_size > LIMIT:
            return "size"
        return None

    def _refuse(self, fd, name, st, reason, out: Scan, ident=None) -> None:
        self._unlink(fd, name, ident)
        if self.owner_of(st) != self.publisher_uid:
            out.strangers += 1
        else:
            out.refused.append((name, reason))

    @staticmethod
    def _lstat(fd: int, name: str) -> os.stat_result | None:
        try:
            return os.stat(name, dir_fd=fd, follow_symlinks=False)
        except OSError:
            return None

    def _unlink(self, fd: int, name: str, ident: tuple[int, ...] | None = None) -> bool:
        """Remove ``name`` (a folder only when it is empty); with ``ident``, only if it is still
        that very file. One that cannot be removed is logged here, once, and remembered as it
        is, so that the scans after pass it over until it changes."""
        st = self._lstat(fd, name)
        if st is None or (ident is not None and _ident(st) != ident):
            return False
        try:
            if stat.S_ISDIR(st.st_mode):
                os.rmdir(name, dir_fd=fd)
            else:
                os.unlink(name, dir_fd=fd)
            self._stuck.pop(name, None)
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            self._stuck[name] = _ident(st)
            kind = "folder" if stat.S_ISDIR(st.st_mode) else "file"
            log.warning("A %s in the TRINETRA letterbox could not be removed (%s); it is left "
                        "there and not looked at again until it changes",
                        kind, (exc.strerror or "refused").lower())  # fmt: skip
            return False

    # ---- after the verdict -------------------------------------------------------------------
    def remove(self, found: Found) -> bool:
        """Remove a file that was read and stored, if it is still that file."""
        fd, _state = self._open_folder()
        if fd is None:
            return False
        try:
            return self._unlink(fd, found.name, found.ident)
        finally:
            os.close(fd)

    def keep(self, found: Found) -> None:
        """Leave a newer-format file for a later release; it is not read again while it stays."""
        self._kept[found.name] = found.ident

    def probe(self, name: str) -> str:
        """Judge one named file as a scan would, touching no other: ``stranger`` (removed),
        ``refused:<rule>`` (removed), ``found`` (left) or ``gone`` (not there, or no folder)."""
        fd, _state = self._open_folder()
        if fd is None:
            return "gone"
        out = Scan()
        try:
            if self._lstat(fd, name) is None:
                return "gone"
            self._take(fd, name, out)
        finally:
            os.close(fd)
        if out.strangers:
            return "stranger"
        if out.refused:
            return f"refused:{out.refused[0][1]}"
        return "found" if out.found else "gone"


def _ident(st: os.stat_result) -> tuple[int, int, int, int]:
    return st.st_dev, st.st_ino, st.st_ctime_ns, st.st_size


def _read_capped(fd: int) -> bytes | None:
    """The whole file, or None when it holds more than ``LIMIT`` bytes."""
    chunks, total = [], 0
    while True:
        chunk = os.read(fd, LIMIT + 1 - total)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > LIMIT:
            return None


def publisher_uid(env_file: Path | str = c.SETUP_FILE) -> int | None:
    """The publisher's account number from the set-up's file, or None when there is none or it
    cannot be used (the view then says "not set up")."""
    with contextlib.suppress(OSError, ValueError):
        for line in Path(env_file).read_text(encoding="utf-8").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key.strip() == c.PUBLISHER_ENV:
                number = int(value.strip().strip('"').strip("'"))
                return number if 0 < number < 2**31 else None
    return None
