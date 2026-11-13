"""Network helpers for the family service's tests: a running service on an ephemeral port (never
5585) and a plain HTTP client that can send whatever a test needs (forwarded headers, odd methods,
bodies past a cap)."""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from family_testkit import secrets

from sudarshan.autologin.handoff import seal
from sudarshan.family.accounts import Hasher
from sudarshan.family.config import Keys, Settings
from sudarshan.family.service import FamilyService


def keys(enabled: bool = True) -> Keys:
    return Keys(Fernet(Fernet.generate_key()), Fernet(Fernet.generate_key()),
                secrets() if enabled else None)  # fmt: skip


def settings(root: Path, enabled: bool = True, **more: Any) -> Settings:
    return Settings(
        enabled=enabled, http_port=0, etc_dir=root / "etc", state_dir=root / "state",
        export_dir=root / "export", deployed_tag_file=root / "deployed_tag", **more,
    )  # fmt: skip


def fast_hasher() -> Hasher:
    from argon2 import PasswordHasher, Type

    return Hasher(PasswordHasher(time_cost=1, memory_cost=8, parallelism=1, hash_len=16,
                                 salt_len=16, type=Type.ID))  # fmt: skip


@contextlib.asynccontextmanager
async def running(service: FamilyService, wait_bound: bool = True):
    """The service running in a task; stopped (and every task ended) on the way out."""
    task = asyncio.create_task(service.run())
    for _ in range(500):
        if task.done() or (service.listener.port if wait_bound else service.state != "starting"):
            break
        await asyncio.sleep(0.01)
    if task.done():
        task.result()
    try:
        yield service
    finally:
        service.stop("test")
        await asyncio.wait_for(task, 15)


def sealed(fernet: Fernet, op: str, *, principal: str = "owner", now: float | None = None,
           **fields: Any) -> bytes:  # fmt: skip
    return seal(fernet, op, now=now, principal=principal, **fields)


async def ask(
    port: int,
    method: str,
    path: str,
    *,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
    length: bool = True,
) -> tuple[int, dict[str, list[str]], Any]:
    """One request; the status, the headers (every value of each, names in lower case) and the
    JSON answer (None when there is none)."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        lines = [f"{method} {path} HTTP/1.1", "Host: 127.0.0.1"]
        lines += [f"{name}: {value}" for name, value in (headers or {}).items()]
        if length and (body or method in ("POST", "PUT", "PATCH")):
            lines.append(f"Content-Length: {len(body)}")
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body)
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(), 15)
    finally:
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
    head, _, rest = raw.partition(b"\r\n\r\n")
    status_line, *header_lines = head.decode("latin-1").split("\r\n")
    found: dict[str, list[str]] = {}
    for line in header_lines:
        name, _, value = line.partition(":")
        found.setdefault(name.strip().lower(), []).append(value.strip())
    try:
        payload = json.loads(rest) if rest else None
    except ValueError:
        payload = rest
    return int(status_line.split()[1]), found, payload
