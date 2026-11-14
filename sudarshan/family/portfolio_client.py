"""The family service's reads of the account, through sudarshan-portfolio (M6.7; design 2.4;
cross-design Y5, Y6).

    POST 127.0.0.1:5583/v1/<op>   one Fernet token sealed for <op> under the portfolio's family
                                  purpose (``seal-portfolio-family.key`` in /etc/sudarshan-family,
                                  written by the deploy's provision step), principal
                                  ``viewer:<member id>``, ``args`` with the member's mask

The family service never calls Upstox and holds no Upstox token. Every figure of the account is
the portfolio's, read on the Analytics Token inside the portfolio's own budget, behind its single
flight and five seconds of memory, so the owner's open screen and the family's screens cost one
Upstox read. A member reads no fresher than that memory, and a principal other than the owner
spends only above the reserve the portfolio keeps for the owner (Y5).

Only the family purpose's read ops are ever sent (``READ_OPS``); the portfolio refuses any other
op under this key in any case. The scope rides in ``args``: the member's mask (``privacy.wire``),
which the portfolio unites with the floor it stores for the member (Y6), and the tabs, which can
only narrow what the owner shared.

A refusal reaches the member as a read state and a sentence (``Refusal``): not shared, the
portfolio switched off, or the account not readable now. The system's own words go to the
journal, once in ten minutes for each kind, never with what a member asked.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx
from cryptography.fernet import Fernet

from sudarshan.autologin.handoff import seal

from . import privacy

log = logging.getLogger("sudarshan.family")

PRINCIPAL_PREFIX = "viewer:"
#: The ops the portfolio opens under the family purpose: reads only.
READ_OPS = (
    "state",
    "summary",
    "tab_get",
    "performance",
    "upstox_book",
    "upstox_history",
    "shares_for_viewer",
)
#: The portfolio's sentence for a part or tab the owner has not shared with the member
#: (``sudarshan/portfolio/sharing.NOT_SHARED``; a test holds the two together).
NOT_SHARED = "Not shared with you."
CONNECT_TIMEOUT_S = 1.0
#: The portfolio's own read of Upstox takes up to 10 s, and a single flight may wait behind one.
READ_TIMEOUT_S = 12.0
#: A refusal of one kind is a warning at most this often.
WARN_EVERY_S = 600.0

WORDS = {
    "not_shared": NOT_SHARED,
    "portfolio_off": "The portfolio service is switched off on the server.",
    "unreachable": "The account cannot be read right now; prices still move.",
}


class Refusal(Exception):
    """A read that gave no figures: ``state`` is a read state of the contract (``not_shared``,
    ``portfolio_off``, ``unreachable``) and ``str()`` the member's sentence."""

    def __init__(self, state: str):
        super().__init__(WORDS[state])
        self.state = state


def principal_of(member_id: int) -> str:
    if isinstance(member_id, bool) or not isinstance(member_id, int) or member_id < 1:
        raise ValueError("a member id is a positive whole number")
    return f"{PRINCIPAL_PREFIX}{member_id}"


class PortfolioClient:
    """One sealed read at a time per call; the HTTP client is the service's loopback client."""

    def __init__(
        self,
        url: str,
        fernet: Fernet,
        http: httpx.AsyncClient,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.url, self.fernet, self.http, self.clock = url.rstrip("/"), fernet, http, clock
        self.reachable: bool | None = None  # for /health: None until the first answer
        self._warned: dict[str, float] = {}

    async def call(
        self,
        op: str,
        member_id: int,
        args: Mapping[str, Any] | None = None,
        *,
        mask: frozenset[str],
        tabs: list[int] | None = None,
    ) -> Any:
        """The ``data`` of the portfolio's answer to ``op`` for the member; ``Refusal`` when it
        gave none. ``mask`` is the member's (``privacy.mask_for``), sent with every read."""
        if op not in READ_OPS:
            raise ValueError(f"{op} is not a read the family may ask of the portfolio")
        sent = {**(args or {}), "mask": privacy.wire(mask)}
        if tabs is not None:
            sent["tabs"] = list(tabs)
        body = seal(self.fernet, op, principal=principal_of(member_id), args=sent)
        try:
            answer = await self.http.post(
                f"{self.url}/v1/{op}",
                content=body,
                headers={"Content-Type": "application/octet-stream"},
                timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
            )
        except httpx.HTTPError as exc:
            self.reachable = False
            self._warn("down", "the portfolio service is not answering the family's reads (%s)",
                       type(exc).__name__)  # fmt: skip
            raise Refusal("unreachable") from None
        self.reachable = True
        try:
            found = answer.json()
        except ValueError:
            found = None
        envelope = found if isinstance(found, dict) else {}
        if answer.status_code == 200 and envelope.get("status") == "success":
            data = envelope.get("data")
            if isinstance(data, dict) and data.get("enabled") is False:
                raise Refusal("portfolio_off")
            return data
        if answer.status_code == 403 and envelope.get("message") == NOT_SHARED:
            raise Refusal("not_shared")
        why = str(envelope.get("code") or "no answer")[:40]
        self._warn("refused", "the portfolio service refused a family read of %s (%d, %s)",
                   op, answer.status_code, why)  # fmt: skip
        raise Refusal("unreachable")

    def _warn(self, kind: str, text: str, *args: Any) -> None:
        """At most one warning of a kind (``down`` or ``refused``) in ten minutes."""
        now = self.clock()
        if now - self._warned.get(kind, -1e9) >= WARN_EVERY_S:
            self._warned[kind] = now
            log.warning(text, *args)
