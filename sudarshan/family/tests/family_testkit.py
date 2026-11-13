"""Shared helpers for the family service's tests (not a conftest: the G5 lane owns that name here).

Everything is synthetic: members are "Member A" and the like, keys are random per run, and no test
binds 5585 (an alerts test checks that nothing listens there).
"""

from __future__ import annotations

import base64
import os
import sqlite3
from pathlib import Path

from sudarshan.family import store as store_module
from sudarshan.family.store import FamilyStore, Secrets


def secrets() -> Secrets:
    return Secrets(os.urandom(32))


def store_key_text() -> str:
    """A store.key as root writes it: standard base64 of 32 random bytes and a newline."""
    return base64.b64encode(os.urandom(32)).decode("ascii") + "\n"


def open_store(folder: Path, keys: Secrets | None = None) -> FamilyStore:
    return FamilyStore(folder / "family.db", keys or secrets())


def close(opened: FamilyStore | None) -> None:
    """Close the store and end the store thread (no thread outlives a test)."""
    if opened is not None:
        opened.close()
    store_module.stop()


def raw(path: Path) -> sqlite3.Connection:
    """A plain connection to the file, for what the store itself must refuse."""
    return sqlite3.connect(path, isolation_level=None, check_same_thread=False)


def set_version(path: Path, number: int) -> None:
    con = raw(path)
    try:
        con.execute(f"PRAGMA user_version = {int(number)}")
    finally:
        con.close()


def add_member(opened: FamilyStore, now: int = 1_000, **values) -> dict:
    row = {
        "display_name": "Member A",
        "role": "viewer",
        "status": "invited",
        "privacy": {"pnl": True, "qty": True, "funds": False, "orders": True},
        "language": "en",
    } | values
    return opened.write_now(lambda tx: tx.member_add(row, now))
