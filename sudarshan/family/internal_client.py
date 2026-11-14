"""The family service's one call into OpenAlgo: the lists the owner shares (M8.15; design 2.4;
the mid-point review's M3).

    POST 127.0.0.1:5000/sudarshan/family/internal/watchlists
        one Fernet token sealed for ``watchlists`` under ``sudarshan/family/internal/v1``
        (``seal-internal.key`` in /etc/sudarshan-family), principal ``sudarshan-family``, with the
        member it is for (``member_id``, ``role``) and 1 to 50 distinct ``list_ids``

OpenAlgo answers only the lists its own note says the owner shared with that member or their role,
and only while the list's ``created_at`` is the one noted when it was shared (OpenAlgo gives a
deleted list's id to the next list made); every other id comes back in ``gone``. So even this
process, if it were broken into, reads no list the owner did not share. The answer is the
contract's ``InternalWatchlists``; a list or a row not shaped as it says, or an id not asked for,
is left out. A failure is ``Refusal("unreachable")``, said in the journal once in ten minutes;
nothing is retried here (the lists are read again within a minute).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from typing import Any

import httpx
from cryptography.fernet import Fernet

from sudarshan.autologin.handoff import seal

from .contract import INTERNAL_OPS, INTERNAL_PATH, INTERNAL_PRINCIPAL, LIMITS, ROLES
from .portfolio_client import Refusal

log = logging.getLogger("sudarshan.family")

OP = INTERNAL_OPS[0].name
TIMEOUT = httpx.Timeout(6.0, connect=1.0)
WARN_EVERY_S = 600.0


def _row(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    symbol, exchange = raw.get("symbol"), raw.get("exchange")
    if not isinstance(symbol, str) or not isinstance(exchange, str) or not symbol or not exchange:
        return None
    return {key: raw.get(key) for key in ("symbol", "exchange", "instrument_key", "name",
                                          "tick_size", "lot_size")}  # fmt: skip


def _list(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or type(raw.get("id")) is not int:
        return None
    name, made, rows = raw.get("name"), raw.get("created_at"), raw.get("rows")
    if not isinstance(name, str) or not isinstance(rows, list):
        return None
    return {"id": raw["id"], "name": name, "created_at": made if isinstance(made, str) else None,
            "rows": [r for r in map(_row, rows) if r is not None]}  # fmt: skip


class InternalClient:
    def __init__(self, url: str, fernet: Fernet, http: httpx.AsyncClient,
                 clock: Callable[[], float] = time.monotonic):  # fmt: skip
        self.url, self.fernet, self.http, self.clock = url.rstrip("/"), fernet, http, clock
        self.reachable: bool | None = None
        self._warned: dict[str, float] = {}

    async def lists(self, list_ids: Iterable[int], *, member_id: int, role: str) -> dict[str, Any]:
        """The shared lists among ``list_ids`` for the member: ``{"lists": [...], "gone": [...]}``."""
        ids = list(dict.fromkeys(list_ids))
        if not 0 < len(ids) <= LIMITS["internal_lists"] or role not in ROLES:
            raise ValueError("1 to 50 list ids and a role of the contract")
        body = seal(self.fernet, OP, principal=INTERNAL_PRINCIPAL, list_ids=ids,
                    member_id=member_id, role=role)  # fmt: skip
        try:
            answer = await self.http.post(self.url + INTERNAL_PATH, content=body, timeout=TIMEOUT,
                                          headers={"Content-Type": "application/octet-stream"})  # fmt: skip
        except httpx.HTTPError as exc:
            self.reachable = False
            self._warn("down", "OpenAlgo did not answer the family's read of the shared lists (%s)",
                       type(exc).__name__)  # fmt: skip
            raise Refusal("unreachable") from None
        try:
            found = answer.json() if answer.status_code == 200 else None
        except ValueError:
            found = None
        data = found.get("data") if isinstance(found, dict) and found.get("status") == "success" \
            else None  # fmt: skip
        if not isinstance(data, dict):
            self.reachable = answer.status_code < 500
            self._warn("refused", "OpenAlgo refused the family's read of the shared lists (%d)",
                       answer.status_code)  # fmt: skip
            raise Refusal("unreachable")
        self.reachable = True
        lists = [found for found in map(_list, data.get("lists") or []) if found is not None]
        lists = [found for found in lists if found["id"] in ids]
        kept = {found["id"] for found in lists}
        return {"lists": lists, "gone": [i for i in ids if i not in kept]}

    def _warn(self, kind: str, text: str, *args: Any) -> None:
        now = self.clock()
        if now - self._warned.get(kind, -1e9) >= WARN_EVERY_S:
            self._warned[kind] = now
            log.warning(text, *args)
