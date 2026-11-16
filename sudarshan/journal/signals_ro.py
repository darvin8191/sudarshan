"""TRINETRA's readings for the journal, read-only (J3, M15.1; P8 3.7.7, signals README section 11).

The journal asks sudarshan-signals on 127.0.0.1:5588 under the journal purpose
(``SIGNALS_JOURNAL_INFO``, principal ``sudarshan-journal``, at most 4 KiB) for exactly two reads,
which is all that purpose opens:

    state        the readings and the freshness sentence: the morning brief's TRINETRA lines
    state_at(ts) the readings in effect at a moment, only when a fresh document stood then: a
                 trade's context at its entry (J1, later)

A seal is ``{"op", "nonce", "principal": "sudarshan-journal", "args": {...}}``, one Fernet token,
60 s, once. Before each post the listener on the port is looked up (``alerts/loopback.py``):
sudarshan-signals runs as this service's own account, and nothing is posted to a port another
account holds. A read that cannot be had raises ``SignalsUnavailable`` with what to say; it is
logged once in ten minutes per cause, never with TRINETRA's text. Nothing is cached.

The brief (``brief_trinetra``) says nothing of TRINETRA while the view is switched off
(``SUDARSHAN_SIGNALS`` in OpenAlgo's .env, the release ships it off). Switched on, it carries two
lines when TRINETRA's readings are fresh, "TRINETRA NIFTY: regime ..., gap bias ..." and the same
for BANKNIFTY, else the one sentence the view itself shows (it has not published since, it was
stopped, it is not connected yet, or why the reader could not be asked).
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx
from cryptography.fernet import Fernet

from sudarshan.alerts import loopback
from sudarshan.autologin.handoff import seal
from sudarshan.signals import contract as c

log = logging.getLogger("sudarshan.journal.signals")

PRINCIPAL = c.PRINCIPALS["journal"]
OPS = c.PURPOSE_OPS["journal"]
CONNECT_TIMEOUT_S = 1.0
READ_TIMEOUT_S = c.TIMEOUTS_S["read"]
LOG_EVERY_S = 600.0
#: The modules the brief tells, in its order.
BRIEF_MODULES = ("regime", "gap_bias")
#: Why a read did not happen, in the words the brief uses (the view's own where it has them).
WORDS = {
    "down": c.WORDS["not_answering"],
    "held": "Another program holds the TRINETRA reader's port, so nothing was asked of it. Stop "
    "that program, then restart sudarshan-signals.",
    "refused": "SUDARSHAN's TRINETRA reader did not accept the journal's request; the two may "
    "hold different keys. Restart both services.",
    "failed": c.WORDS["failed"],
}


class SignalsUnavailable(Exception):
    """A read that did not happen; ``kind`` is a key of ``WORDS`` and ``str()`` its words."""

    def __init__(self, kind: str):
        super().__init__(WORDS[kind])
        self.kind = kind


class SignalsReader:
    """Sealed reads from sudarshan-signals. One HTTP client, closed with ``aclose``."""

    def __init__(
        self,
        url: str,
        fernet: Fernet | None,
        *,
        client: httpx.AsyncClient | None = None,
        tables=loopback.TCP_TABLES,
        uid: Callable[[], int] = os.getuid,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.url, self.fernet = url.rstrip("/"), fernet
        self._client, self._own = client, client is None
        self.tables, self.uid, self.clock = tables, uid, clock
        self._said: dict[str, float] = {}

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S), trust_env=False
            )
        return self._client

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None and self._own:
            await client.aclose()

    def _fail(self, kind: str) -> SignalsUnavailable:
        now = self.clock()
        if now - self._said.get(kind, -1e9) >= LOG_EVERY_S:
            self._said[kind] = now
            log.warning("A read of TRINETRA's readings did not happen: %s", WORDS[kind])
        return SignalsUnavailable(kind)

    async def call(self, op: str, **args: Any) -> dict[str, Any]:
        """The data of one read (the ``Off`` record while the view is off), or
        SignalsUnavailable. Only the journal purpose's own ops are asked."""
        if op not in OPS:
            raise ValueError(f"the journal purpose does not open {op}")
        if self.fernet is None:
            raise self._fail("refused")
        held = loopback.holder(self.url, self.tables, expected_uid=self.uid())
        if held == loopback.NOBODY:
            raise self._fail("down")
        if held == loopback.OTHER:
            raise self._fail("held")
        body = seal(self.fernet, op, principal=PRINCIPAL, args=args)
        try:
            answer = await self.client.post(
                f"{self.url}{c.OPS_PREFIX}{op}",
                content=body,
                headers={"Content-Type": "application/octet-stream", "Accept": "application/json"},
                follow_redirects=False,
            )
            envelope = answer.json()
        except httpx.HTTPError:
            raise self._fail("down") from None
        except ValueError:
            raise self._fail("failed") from None
        if not isinstance(envelope, dict) or envelope.get("status") != "success":
            code = envelope.get("code") if isinstance(envelope, dict) else None
            raise self._fail(
                "refused" if code in ("forbidden", "principal", "sealed") else "failed"
            )
        data = envelope.get("data")
        if not isinstance(data, dict):
            raise self._fail("failed")
        return data

    async def state(self) -> dict[str, Any]:
        return await self.call("state")

    async def state_at(self, ts_ms: int) -> dict[str, Any]:
        return await self.call("state_at", ts=int(ts_ms))


def reader_for(service: Any) -> SignalsReader:
    """The service's one reader, built on first use and closed with the service."""
    return service.part(
        "signals",
        lambda svc: SignalsReader(svc.settings.signals_url, getattr(svc, "signals_key", None)),
    )


def _reading(found: Mapping[str, Any]) -> dict[str, Any]:
    confidence = found.get("confidence")
    return {
        "label": str(found.get("label") or ""),
        "bias": found.get("bias") if found.get("bias") in c.BIASES else "neutral",
        "confidence": f"{confidence:.2f}" if isinstance(confidence, int | float) else None,
        "quality": found.get("quality") if found.get("quality") in c.QUALITIES else "ok",
        "lapsed": bool(found.get("lapsed")),
    }


def brief_input(answer: Mapping[str, Any]) -> dict[str, Any]:
    """What the brief says of one ``state`` answer: ``{"sentence": ...}`` when the view is off or
    TRINETRA's readings are not fresh (the view's own words), else ``{"readings": {instrument:
    {module: reading}}}`` with the regime and the gap bias of NIFTY and BANKNIFTY. Pure."""
    if answer.get("enabled") is False:
        return {"sentence": str(answer.get("message") or c.WORDS["off"])}
    freshness = answer.get("freshness") if isinstance(answer.get("freshness"), Mapping) else {}
    if freshness.get("status") != "fresh":
        return {"sentence": str(freshness.get("words") or c.WORDS["never"])}
    readings: dict[str, dict[str, Any]] = {instrument: {} for instrument in c.INSTRUMENTS}
    for found in answer.get("readings") or []:
        if not isinstance(found, Mapping):
            continue
        module, instrument = found.get("module"), found.get("instrument")
        if module in BRIEF_MODULES and instrument in readings:
            readings[instrument][module] = _reading(found)
    return {"readings": readings}


async def brief_trinetra(service: Any) -> dict[str, Any] | None:
    """The brief's TRINETRA input (``brief_input``), or None while the view is switched off."""
    if not getattr(service.settings, "signals", False):
        return None
    try:
        answer = await reader_for(service).state()
    except SignalsUnavailable as exc:
        return {"sentence": str(exc)}
    return brief_input(answer)
