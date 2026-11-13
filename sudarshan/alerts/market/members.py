"""The list alerts' view of a list: OpenAlgo's sealed members route (M8.14), the seal and the
transport in this one module.

    members(ref) / version() -> POST 127.0.0.1:5000/sudarshan/watchlists/internal/read?op=members|version
      one Fernet token under WATCHLISTS_INTERNAL_INFO (sudarshan/vault/keys.py; the route itself is
      W23), sealed as the screener's internal calls are

The contract this was written against (a stub until the route exists, tests/groups_fakes.py):

    members {ref} -> ok:  {status: "ok", data: {version, ref_version, created_at, rows: [{symbol, exchange,
                          instrument_key, tick_size, expiry, lot, valid_until?}],
                          left_out: [{symbol?, why, reason}]}}
    version       -> ok:  {status: "ok", data: {version}}
    the watchlist feature switched off -> {status: "error", code: "off", ...}   (a freeze)
    the list is not there (deleted)    -> {status: "error", code: "gone", ...}  (a stop)

``why`` is ``atm`` or ``strike`` for the rows that change contract during the day. A route that
cannot be asked (not answering, refused, an answer that cannot be read) raises ``Unavailable``:
the caller changes nothing and asks again at the next check, which is not a freeze and not an
error shown to anyone. One connection pool belongs to the runtime's own client, closed with it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, NamedTuple, Protocol

import httpx
from cryptography.fernet import Fernet

from sudarshan.alerts import loopback
from sudarshan.alerts.market import groups
from sudarshan.autologin.handoff import seal

log = logging.getLogger("sudarshan.alerts.market")

PATH = "/sudarshan/watchlists/internal/read"
TIMEOUT_S = 3.0
CONNECT_TIMEOUT_S = 1.0


class Unavailable(RuntimeError):
    """The route could not be asked or its answer could not be read: change nothing."""


class Answer(NamedTuple):
    """``state`` is groups.OK, OFF or GONE; rows and left_out are empty for a version."""

    state: str
    version: str | None = None
    rows: tuple[groups.Row, ...] = ()
    left_out: tuple[Mapping[str, Any], ...] = ()
    #: The list's own created_at text: a reused list id is told apart by it (review M4).
    list_key: str | None = None
    #: The ref's own digest of what it holds (the route's ``ref_version``): it moves when this
    #: list's members move and not when another list does (v0.9.0 review L1).
    ref_version: str | None = None


class MembersSource(Protocol):
    async def members(self, ref: str) -> Answer: ...

    async def version(self) -> Answer: ...


class OpenAlgoMembers:
    """The sealed route, called over the runtime's own loopback client (no proxy, no redirect)."""

    def __init__(self, base_url: str, client: httpx.AsyncClient, fernet: Fernet):
        self.url = base_url.rstrip("/") + PATH
        self.client, self.fernet = client, fernet

    async def members(self, ref: str) -> Answer:
        return await self._ask("members", ref=ref)

    async def version(self) -> Answer:
        return await self._ask("version")

    async def _ask(self, op: str, **fields) -> Answer:
        refused = loopback.refusal(self.url)  # the sealed request goes only to our own user's port
        if refused:
            raise Unavailable(refused)
        try:
            answer = await self.client.post(
                self.url, params={"op": op}, content=seal(self.fernet, op, **fields),
                headers={"Content-Type": "application/octet-stream", "Accept": "application/json"},
                timeout=httpx.Timeout(TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
            )  # fmt: skip
            payload = answer.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise Unavailable(exc.__class__.__name__) from exc
        return read(payload if isinstance(payload, dict) else {}, answer.status_code)


def read(payload: Mapping[str, Any], status: int = 200) -> Answer:
    """The route's envelope as an Answer; anything else is ``Unavailable``."""
    if payload.get("status") == "error" and payload.get("code") in (groups.OFF, groups.GONE):
        return Answer(payload["code"])
    data = payload.get("data")
    if status != 200 or payload.get("status") != "ok" or not isinstance(data, dict):
        raise Unavailable(f"answered {status}")
    version = data.get("version")
    try:
        rows = tuple(groups.Row.of(row) for row in data.get("rows", ()))
        left_out = tuple(item for item in data.get("left_out", ()) if isinstance(item, dict))
    except (KeyError, TypeError, AttributeError) as exc:
        raise Unavailable("rows could not be read") from exc
    if version is not None and not isinstance(version, str | int):
        raise Unavailable("version could not be read")
    key, digest = data.get("created_at"), data.get("ref_version")
    return Answer(groups.OK, None if version is None else str(version), rows, left_out,
                  None if key is None else str(key),
                  digest if isinstance(digest, str) else None)  # fmt: skip
