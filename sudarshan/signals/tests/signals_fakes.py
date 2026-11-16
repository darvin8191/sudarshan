"""Shared fakes for the TRINETRA view's service tests: keys from the test pepper, a sealed request,
a free port, a letterbox folder with an injected owner check, and invented documents."""

from __future__ import annotations

import asyncio
import json
import os
import socket
from pathlib import Path
from typing import Any

import httpx

from sudarshan.autologin.handoff import seal
from sudarshan.signals import inbox
from sudarshan.signals.config import Settings
from sudarshan.vault import SIGNALS_API_INFO, SIGNALS_JOURNAL_INFO, fernet_for

PEPPER = os.environ["API_KEY_PEPPER"]
KEYS = {
    "api": fernet_for(PEPPER, SIGNALS_API_INFO),
    "journal": fernet_for(PEPPER, SIGNALS_JOURNAL_INFO),
}
PRINCIPALS = {"api": "owner", "journal": "sudarshan-journal"}
PUBLISHER = os.geteuid() + 7  # the letterbox's publisher: the test's own files are judged as its
AT = 1794800712250  # Mon 16 Nov 2026 09:15:12 IST (invented)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def sealed(op: str, purpose: str = "api", principal: str | None = None, **args: Any) -> bytes:
    return seal(KEYS[purpose], op, principal=principal or PRINCIPALS[purpose], args=args)


async def ask(port: int, op: str, purpose: str = "api", **args: Any) -> httpx.Response:
    async with httpx.AsyncClient(trust_env=False) as client:
        return await client.post(
            f"http://127.0.0.1:{port}/v1/{op}", content=sealed(op, purpose, **args), timeout=10
        )


async def health(port: int) -> dict:
    async with httpx.AsyncClient(trust_env=False) as client:
        return (await client.get(f"http://127.0.0.1:{port}/health", timeout=10)).json()


async def until(check, timeout: float = 10.0) -> None:
    for _ in range(int(timeout / 0.02)):
        if check():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("the condition did not come true in time")


class Box:
    """A letterbox folder whose files are the publisher's unless named a stranger's."""

    def __init__(self, folder: Path):
        self.folder = folder
        folder.mkdir(parents=True)
        os.chmod(folder, 0o1733)
        self.strangers: set[int] = set()

    def owner_of(self, st: os.stat_result) -> int:
        return os.geteuid() + 1 if st.st_ino in self.strangers else PUBLISHER

    def reader(self, uid: int) -> inbox.Letterbox:
        return inbox.Letterbox(self.folder, uid, owner_of=self.owner_of)

    def drop(self, doc: dict | bytes, name: str | None = None, *, stranger=False,
             written_ms: int | None = None) -> Path:  # fmt: skip
        raw = doc if isinstance(doc, bytes) else json.dumps(doc).encode()
        if name is None:
            name = f"{doc['at']}-{doc['seq']}.json"  # type: ignore[index]
        tmp = self.folder / f".{name}.tmp"
        tmp.write_bytes(raw)
        os.chmod(tmp, 0o644)
        if written_ms is not None:
            os.utime(tmp, ns=(written_ms * 1_000_000, written_ms * 1_000_000))
        path = self.folder / name
        tmp.replace(path)
        if stranger:
            self.strangers.add(path.stat().st_ino)
        return path

    def names(self) -> set[str]:
        return {p.name for p in self.folder.iterdir()}


def reading(module="regime", instrument="NIFTY", **over) -> dict:
    out = {"module": module, "instrument": instrument, "state": "DISTRIBUTION",
           "label": "FII distribution", "bias": "bear", "score": -0.42, "confidence": 0.71,
           "since": AT - 86_400_000, "as_of": AT - 1000, "horizon": "swing", "quality": "ok",
           "why": {"en": "Invented explanation.", "hi": "Banaya hua.", "gu": None}}  # fmt: skip
    out.update(over)
    return out


def document(seq=1, at=AT, run="r1", signals=None, alerts=None, **over) -> dict:
    out = {"v": 1, "run": run, "seq": seq, "at": at, "next_by": at + 300_000,
           "signals": [reading()] if signals is None else signals,
           "alerts": [] if alerts is None else alerts}  # fmt: skip
    out.update(over)
    return out


def settings_for(tmp_path: Path, port: int, enabled: bool = True, **more) -> Settings:
    setup = tmp_path / "signals.env"
    if enabled and not more.pop("unset", False):
        setup.write_text(f"SUDARSHAN_SIGNALS_PUBLISHER_UID={PUBLISHER}\n")
    return Settings(
        enabled=enabled,
        http_port=port,
        letterbox=tmp_path / "in",
        setup_file=setup,
        state_dir=tmp_path / "state",
        deployed_tag_file=tmp_path / "deployed_tag",
        holidays_file=tmp_path / "holidays.txt",
        **more,
    )
