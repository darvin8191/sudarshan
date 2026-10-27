"""Reading a small file another program writes, without ever waiting on it or being fooled by it.

The files the ring reads from the deploy tool (the installed release, the notice spool) are
written by root in a folder only root can change, but a reader of such a file still must not hang
on a FIFO put in its place, follow a symlink, or load something huge into memory. Every reader in
``sudarshan.ops`` goes through here.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path


def read_regular(path: Path | str, limit: int) -> bytes | None:
    """The last ``limit`` bytes of a regular file, or None if it is not one or cannot be read.

    Never blocks (a FIFO is refused, not waited on), never follows a symlink at ``path``, and never
    reads more than ``limit`` bytes. A file longer than ``limit`` is read from its tail, with the
    cut-off first line dropped, so a log-like file read this way always starts on a whole line.
    """
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None
        cut = info.st_size > limit
        if cut:
            os.lseek(fd, info.st_size - limit, os.SEEK_SET)
        chunks: list[bytes] = []
        left = limit
        while left > 0:
            chunk = os.read(fd, min(left, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            left -= len(chunk)
        data = b"".join(chunks)
    except OSError:
        return None
    finally:
        os.close(fd)
    if cut:
        data = data.partition(b"\n")[2]
    return data
