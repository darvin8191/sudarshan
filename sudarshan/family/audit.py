"""The family audit (M2.12, design 4.2): one line per thing that happened, append-only, kept forever.

Sign-ins and their refusals, locks, invites, member and privacy changes, devices: each is one line
in ``audit``, which refuses UPDATE and DELETE (two triggers, ``migrations.py``), so a line stays as
it was written. The owner reads it on the Family page (``page``: newest first, by member and kind,
200 at a time and "load older").

A session appears only as ``session_hash16``, the first 16 hex of its SHA-256, the same string the
device list shows, so a line can be matched to a device; a network address only as its keyed hash
(``Secrets.place``), shown as its first 8 hex. A line never carries a name or a sign-in name: the
member is named by id, and ``detail`` holds small facts (what changed, why), never a secret.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from .contract import AUDIT_KINDS, LIMITS
from .store import Tx

OWNER = "owner"
SYSTEM = "system"
#: The largest ``detail`` kept, as JSON text; a bigger one is kept as ``{"cut": true}``.
MAX_DETAIL = 2048
MAX_TEXT = 200
PAGE = LIMITS["audit_page"]


def member_actor(member_id: int) -> str:
    return f"member:{int(member_id)}"


def hash16(id_hash: str | None) -> str | None:
    """The 16 hex the owner sees for a session (its SHA-256's first 16)."""
    return id_hash[:16] if id_hash else None


def _plain(value: Any, depth: int = 0) -> Any:
    """A fact as plain JSON: text cut to MAX_TEXT, lists and maps three deep, nothing else."""
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, str):
        return value[:MAX_TEXT]
    if depth < 3 and isinstance(value, list | tuple):
        return [_plain(v, depth + 1) for v in list(value)[:50]]
    if depth < 3 and isinstance(value, dict):
        return {str(k)[:40]: _plain(v, depth + 1) for k, v in list(value.items())[:50]}
    return str(value)[:MAX_TEXT]


def record(
    tx: Tx,
    *,
    at: int,
    actor: str,
    kind: str,
    member_id: int | None = None,
    session: str | None = None,
    detail: dict[str, Any] | None = None,
    ip_hash: str | None = None,
) -> int:
    """Append one line; its id. ``session`` is the session's id hash (kept as its first 16)."""
    if kind not in AUDIT_KINDS:
        raise ValueError(f"not an audit kind: {kind}")
    facts = _plain(detail or {})
    if len(json.dumps(facts, separators=(",", ":"), ensure_ascii=False)) > MAX_DETAIL:
        facts = {"cut": True}
    return tx.insert(
        "audit",
        {
            "at": int(at),
            "actor": actor,
            "member_id": member_id,
            "session_hash16": hash16(session),
            "kind": kind,
            "detail": facts,
            "ip_hash": ip_hash,
        },
    )


def line(row: dict[str, Any]) -> dict[str, Any]:
    """One line as the owner's page reads it (the contract's ``AuditLine``)."""
    return {
        "id": row["id"],
        "at": row["at"],
        "actor": row["actor"],
        "member_id": row["member_id"],
        "kind": row["kind"],
        "device": row["session_hash16"],
        "place": row["ip_hash"][:8] if row["ip_hash"] else None,
        "detail": row["detail"],
    }


def page(
    tx: Tx,
    *,
    member_id: int | None = None,
    kind: str | None = None,
    before: int | None = None,
    limit: int = PAGE,
) -> dict[str, Any]:
    """The newest ``limit`` lines (at most 200) older than line ``before``, of one member and one
    kind when given. ``older`` is what to pass as ``before`` for the next page, None at the end."""
    limit = max(1, min(int(limit), PAGE))
    t = tx.t("audit")
    query = select(t)
    if member_id is not None:
        query = query.where(t.c.member_id == member_id)
    if kind is not None:
        query = query.where(t.c.kind == kind)
    if before is not None:
        query = query.where(t.c.id < before)
    found = [dict(r) for r in tx.conn.execute(query.order_by(t.c.id.desc()).limit(limit + 1))
             .mappings()]  # fmt: skip
    rows = found[:limit]
    for row in rows:
        try:
            row["detail"] = json.loads(row["detail"]) if row["detail"] else {}
        except ValueError:
            row["detail"] = {}
    return {
        "lines": [line(r) for r in rows],
        "older": rows[-1]["id"] if len(found) > limit else None,
    }
