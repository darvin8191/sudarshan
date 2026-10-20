"""The worker's two loopback calls into OpenAlgo, sealed with a key only the two of them derive.

Why the worker calls OpenAlgo at all instead of writing the token to ``openalgo.db`` itself: the
running gunicorn worker keeps the broker token in process memory (``auth_cache`` holds even a
cached "no session" answer), loads the symbol cache in memory after the master contract download,
and runs the order-update adapter. ``upsert_auth`` and the master contract hook refresh all of
that only in the process that calls them. So the token is applied *inside* OpenAlgo, through the
same calls a manual Upstox login makes, and the worker only fetches it.

Sealing: each request body is one Fernet token (AES-CBC + HMAC-SHA256) under a key derived with
HKDF from ``API_KEY_PEPPER`` (``sudarshan.vault.keys.HANDOFF_INFO``). Only a process that can read
OpenAlgo's ``.env`` can produce one, it expires after ``SEAL_TTL_S``, and its nonce is accepted
once. The access token therefore never crosses even the loopback interface in clear, and never
lands in a request log.
"""

from __future__ import annotations

import contextlib
import json
import secrets
import time
from collections.abc import MutableMapping

import httpx
from cryptography.fernet import Fernet, InvalidToken

SEAL_TTL_S = 60
MAX_SEALED_BYTES = 16_384
PREFLIGHT_PATH = "/sudarshan/autologin/internal/preflight"
REPORT_PATH = "/sudarshan/autologin/internal/report"
OPS = {"preflight": PREFLIGHT_PATH, "report": REPORT_PATH}


class HandoffRejected(Exception):
    """A sealed request that is not genuine, has expired, or was already used."""


class LinkError(Exception):
    """OpenAlgo could not be asked (``unreachable``) or refused the request (``rejected``)."""

    def __init__(self, kind: str, status: int | None = None):
        super().__init__(kind)
        self.kind = kind
        self.status = status


def seal(fernet: Fernet, op: str, *, now: float | None = None, **fields) -> bytes:
    body = {"op": op, "nonce": secrets.token_hex(16), **fields}
    data = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return fernet.encrypt_at_time(data, int(now if now is not None else time.time()))


def open_sealed(
    fernet: Fernet,
    blob: bytes,
    *,
    op: str,
    seen: MutableMapping,
    now: float | None = None,
    ttl: int = SEAL_TTL_S,
) -> dict:
    """Verify and decrypt one sealed request for ``op``; record its nonce in ``seen``.

    Raises:
        HandoffRejected: wrong key, tampered, expired (or from the future), wrong op, or replayed.
    """
    if not blob or len(blob) > MAX_SEALED_BYTES:
        raise HandoffRejected("size")
    try:
        plain = fernet.decrypt_at_time(blob, ttl, int(now if now is not None else time.time()))
        message = json.loads(plain.decode("utf-8"))
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise HandoffRejected("seal") from exc
    if not isinstance(message, dict) or message.get("op") != op:
        raise HandoffRejected("op")
    nonce = message.get("nonce")
    if not isinstance(nonce, str) or len(nonce) != 32:
        raise HandoffRejected("nonce")
    lock = getattr(seen, "lock", None)
    with lock if lock is not None else contextlib.nullcontext():
        if nonce in seen:
            raise HandoffRejected("replay")
        seen[nonce] = True
    return message


class OpenAlgoLink:
    """The worker's side: one sealed POST per call, JSON back."""

    def __init__(
        self, base_url: str, fernet: Fernet, client: httpx.Client, timeout_s: float = 20.0
    ):
        self.base_url = base_url.rstrip("/")
        self._fernet = fernet
        self._client = client
        self._timeout = httpx.Timeout(timeout_s, connect=3.0)

    def call(self, op: str, **fields) -> dict:
        blob = seal(self._fernet, op, **fields)
        try:
            response = self._client.post(
                self.base_url + OPS[op],
                content=blob,
                headers={"Content-Type": "application/octet-stream", "Accept": "application/json"},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise LinkError("unreachable") from exc
        if response.status_code in (401, 403):
            raise LinkError("rejected", response.status_code)
        try:
            payload = response.json()
        except ValueError as exc:
            raise LinkError("error", response.status_code) from exc
        if not isinstance(payload, dict):
            raise LinkError("error", response.status_code)
        if response.status_code >= 400 and "applied" not in payload:
            raise LinkError("error", response.status_code)
        return payload

    def preflight(self) -> dict:
        return self.call("preflight")

    def report(self, **fields) -> dict:
        return self.call("report", **fields)
