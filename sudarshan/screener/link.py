"""The service's sealed HTTP calls to OpenAlgo's internal routes and to sudarshan-alerts (6.3, S16).

One ``httpx.AsyncClient`` for the loopback (no proxy from the environment, no redirect followed,
explicit timeouts), closed with the service. Every request is one Fernet token: the watchlist and
push routes under ``sudarshan/screener/internal/v1``, the digest under
``sudarshan/alerts/screener-digest/v1``. Each purpose is derived from OpenAlgo's pepper, so a
service that cannot read it does not start (config.read_pepper).

What comes back is read for its envelope only; a refusal raises ``LinkError`` with the status
and the server's own words for the journal (never shown to the owner: the outbox retries or
drops the message by its kind).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
from cryptography.fernet import Fernet

from sudarshan.autologin.handoff import seal

from . import digests
from .contract import INTERNAL_MAX_BYTES, INTERNAL_PREFIX

log = logging.getLogger("sudarshan.screener")

TIMEOUT_S = 6.0
CONNECT_S = 1.0
DIGEST_PATH = f"/v1/{digests.OP}"


class LinkError(RuntimeError):
    def __init__(self, status: int | None, words: str):
        super().__init__(words)
        self.status = status


class Link:
    """The bridge's internal routes (listsync.Link) and the alerts API, sealed."""

    def __init__(
        self,
        openalgo_url: str,
        alerts_url: str,
        internal_fernet: Fernet,
        digest_fernet: Fernet,
        *,
        client: httpx.AsyncClient | None = None,
    ):
        self.openalgo_url = openalgo_url.rstrip("/")
        self.alerts_url = alerts_url.rstrip("/")
        self._internal, self._digest = internal_fernet, digest_fernet
        self._client = client or httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(TIMEOUT_S, connect=CONNECT_S),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def status(self) -> dict[str, Any]:
        return {"openalgo": self.openalgo_url, "alerts": self.alerts_url}

    async def _post(self, url: str, blob: bytes) -> Mapping[str, Any]:
        try:
            answer = await self._client.post(
                url, content=blob, headers={"Content-Type": "application/octet-stream"}
            )
        except httpx.HTTPError as exc:
            raise LinkError(
                None, f"{url.rsplit('/', 1)[-1]}: {exc.__class__.__name__.lower()}"
            ) from exc
        try:
            body = answer.json()
        except ValueError:
            body = None
        if (
            answer.status_code != 200
            or not isinstance(body, dict)
            or body.get("status") != "success"
        ):
            words = body.get("message") if isinstance(body, dict) else None
            raise LinkError(answer.status_code, str(words or f"answered {answer.status_code}"))
        return body

    async def _internal_call(self, route: str, op: str, **args) -> Mapping[str, Any]:
        blob = seal(self._internal, op, principal="owner", args=args)
        if len(blob) > INTERNAL_MAX_BYTES:
            raise LinkError(413, f"{op}: the request is over {INTERNAL_MAX_BYTES} bytes")
        query = f"?op={op}" if route == "watchlist" else ""
        return await self._post(f"{self.openalgo_url}{INTERNAL_PREFIX}/{route}{query}", blob)

    # ---- listsync.Link ----
    async def watchlist_read(self, list_id: int) -> Mapping[str, Any] | None:
        body = await self._internal_call("watchlist", "watchlist_read", list_id=list_id)
        return dict(body) if body.get("found") else None

    async def watchlist_apply(
        self,
        *,
        list_id: int | None,
        create_name: str | None,
        add: Sequence[Mapping[str, str]],
        remove: Sequence[int],
        ref: str,
    ) -> Mapping[str, Any]:
        body = await self._internal_call(
            "watchlist", "watchlist_apply", list_id=list_id, create_name=create_name,
            add=list(add), remove=list(remove), ref=ref,
        )  # fmt: skip
        if not body.get("found"):
            raise LinkError(404, "the list is gone")
        return dict(body)

    # ---- the page's pushes ----
    async def push(self, items: Sequence[Mapping[str, Any]]) -> int:
        body = await self._internal_call("push", "push", items=list(items))
        return int(body.get("screens") or 0)

    # ---- the digest to sudarshan-alerts ----
    async def digest(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        blob = digests.sealed(self._digest, payload)
        return await self._post(f"{self.alerts_url}{DIGEST_PATH}", blob)


__all__ = ["DIGEST_PATH", "Link", "LinkError"]
