"""Descriptor-leak checks that own their descriptors.

A count of /proc/self/fd compared before and after blames the code under test for any descriptor
another test's leftover thread or object opened in between, and when the count and the snapshot
naming the culprits are two reads, the culprit list can come out empty (CI run 43435366153: 29 ==
28 with nothing to name). This takes ONE snapshot, descriptor number to target, after a collect.
Afterwards a descriptor that is new or retargeted and still open after a short settle (collect,
re-read, up to ``settle_s``) is a leak, named by its target. One that existed before, or closes
within the settle, is not the test's.
"""

from __future__ import annotations

import asyncio
import gc
import os
import time

FD_DIR = "/proc/self/fd"


def snapshot() -> dict[str, str]:
    """Open descriptors by number, with their targets (the listing's own is unreadable, skipped)."""
    out = {}
    for name in os.listdir(FD_DIR):
        try:
            out[name] = os.readlink(f"{FD_DIR}/{name}")
        except OSError:
            pass
    return out


class FdWatch:
    """Take the snapshot on creation; ``assert_clean`` (or ``aassert_clean``) judges the window."""

    def __init__(self, settle_s: float = 2.0):
        self.settle_s = settle_s
        gc.collect()  # a socket an earlier test left unreferenced closes now, not inside the window
        self.before = snapshot()

    def new(self) -> dict[str, str]:
        """Descriptors that are new or retargeted since the snapshot, right now."""
        now = snapshot()
        return {fd: t for fd, t in now.items() if self.before.get(fd) != t}

    def _message(self, leaked: dict[str, str]) -> str:
        return "descriptors opened in the window and still open: " + ", ".join(
            f"fd {fd} -> {target}" for fd, target in sorted(leaked.items(), key=lambda i: int(i[0]))
        )

    def assert_clean(self) -> None:
        deadline = time.monotonic() + self.settle_s
        while True:
            gc.collect()
            leaked = self.new()
            if not leaked:
                return
            if time.monotonic() >= deadline:
                raise AssertionError(self._message(leaked))
            time.sleep(0.05)

    async def aassert_clean(self) -> None:
        """The same, for a test whose event loop must keep running for the descriptors to close."""
        deadline = time.monotonic() + self.settle_s
        while True:
            gc.collect()
            leaked = self.new()
            if not leaked:
                return
            if time.monotonic() >= deadline:
                raise AssertionError(self._message(leaked))
            await asyncio.sleep(0.05)
