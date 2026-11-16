"""The push to the owner's open screens (B2, M13.7; README section 9).

    a cycle that changed the readings, a lapse, a settings change -> Service.updated(cause)
      -> the "push" job (one push per burst) -> POST 127.0.0.1:5000/sudarshan/signals/internal/push
      -> OpenAlgo emits sudarshan_signals_update to the room sudarshan:signals:owner only, and
         answers how many screens are in it

The body is one token sealed for op ``push`` under ``SIGNALS_PUSH_INFO``, principal
``sudarshan-signals``, at most 32 KiB, with one item: the contract's ``SignalsUpdate``, the cause
and the ``state`` answer as it is when the push is sent, or ``state`` null when the token would
pass 32 KiB (the page then reads ``GET /state``). Updates that come while a push is on its way are
folded into the next one, which carries the newest state, so a burst is one push.

A push is never retried: an open page reads the state at mount and after every reconnect, so a
lost push costs what the next update or a reconnect mends. The token goes only to a port this
service's own user listens on (``sudarshan.alerts.loopback``: OpenAlgo runs as this user). A
failure is logged once an hour in plain words, and the service carries on.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from cryptography.fernet import Fernet

from sudarshan.alerts import loopback
from sudarshan.autologin.handoff import seal
from sudarshan.signals import contract as c
from sudarshan.signals.service import job

log = logging.getLogger("sudarshan.signals")

MAX_BYTES = c.SEAL_LIMITS["push"]
TIMEOUT_S = 3.0
CONNECT_TIMEOUT_S = 1.0
#: A burst of updates (a backlog read in one cycle, a settings change meanwhile) waits this long
#: and goes as one push.
BATCH_S = 0.05


def cause_of(causes: set[str]) -> str | None:
    """The one cause a folded push names: a document before a lapse before a settings change."""
    return next((cause for cause in c.UPDATE_CAUSES if cause in causes), None)


def sealed(key: Fernet, cause: str, state: dict[str, Any] | None) -> bytes:
    """The push's body: the update with its state, or with ``state`` null when that is too big."""

    def body(with_state: dict[str, Any] | None) -> bytes:
        item = {"event": c.EVENT_UPDATE, "data": {"cause": cause, "state": with_state}}
        return seal(key, c.PUSH_OP, principal=c.PRINCIPALS["push"], items=[item])

    token = body(state)
    return token if len(token) <= MAX_BYTES or state is None else body(None)


async def push_once(service: Any, cause: str) -> int | None:
    """One push of the state now: the screens OpenAlgo showed it on, or None when it was not
    sent or not taken."""
    from sudarshan.signals.ops import state_of

    if service.push_key is None or service.store is None:
        return None
    url = service.settings.openalgo_url.rstrip("/") + c.PUSH_PATH
    refused = loopback.refusal(url)
    if refused:
        service._warn("push-listener", "The TRINETRA view's update was not pushed to the open "
                      "screens: %s", refused)  # fmt: skip
        return None
    token = sealed(service.push_key, cause, await state_of(service))
    try:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
            answer = await client.post(
                url,
                content=token,
                headers={"Content-Type": "application/octet-stream", "Accept": "application/json"},
                timeout=httpx.Timeout(TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
            )
        payload = answer.json()
    except (httpx.HTTPError, ValueError):
        service._warn("push-down", "OpenAlgo did not take the TRINETRA view's update for the open "
                      "screens; a page reads the view again when it reconnects")  # fmt: skip
        return None
    screens = payload.get("screens") if isinstance(payload, dict) else None
    if answer.status_code != 200 or not isinstance(screens, int) or isinstance(screens, bool):
        service._warn("push-refused", "OpenAlgo refused the TRINETRA view's update for the open "
                      "screens (it answered %d)", answer.status_code)  # fmt: skip
        return None
    log.debug("the TRINETRA view's update (%s) was shown on %d open screens", cause, screens)
    return max(0, screens)


@job("push")
async def pusher(service: Any) -> None:
    """Wait for an update, let the burst gather, and push once; never retried."""
    while True:
        await service.push_due.wait()
        await asyncio.sleep(BATCH_S)
        service.push_due.clear()
        causes, service.push_causes = service.push_causes, set()
        cause = cause_of(causes)
        if cause is None:
            continue
        try:
            await push_once(service, cause)
        except Exception:
            log.exception("The TRINETRA view's update could not be pushed to the open screens; "
                          "the next one is pushed as usual")  # fmt: skip
