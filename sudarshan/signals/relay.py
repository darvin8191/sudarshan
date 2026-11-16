"""The relay to the owner's Telegram, on the signals side (AL2, M13.10; README section 10).

    a cycle stores new timeline rows and alerts -> Service._after -> the hook "relay" (here),
      which only wakes the job: the letterbox's scan never waits on the alert service
    the job "relay": the pending changes and alerts of signals.db (store.pending_relay), as the
      contract's RelayChange and RelayAlert (words.relay_change, words.relay_alert), at most 8
      to a call and 16 KiB sealed
      -> POST 127.0.0.1:5582/v1/signals_relay, one token sealed for op signals_relay under
         sudarshan/alerts/signals/v1, principal sudarshan-signals
      -> the alerts' signals desk claims each id once in alerts.db and answers taken and known
      -> every id the desk has is marked handed; the rest stay pending and are tried again every
         RETRY_S while younger than 30 minutes, then marked expired

This service never holds the owner's API key and never writes a message: it hands the alert
service a change, not a message, and the desk makes the words (alerts/signals_desk.py).
TRINETRA's sentence goes in the owner's language, plain text, cut to 300 characters
(words.pick_why, words.cut). A lapsed change and an info alert are marked skipped when they are
stored (store.accept), and so is everything pending while the owner's relay setting is off.

The token goes only to a port this service's own user listens on (``sudarshan.alerts.loopback``:
sudarshan-alerts runs as this user). Its key is derived from OpenAlgo's pepper, as the service's
main derives the others (``key_for``). A failure is logged once an hour per cause in plain
words, never with TRINETRA's text, and the service carries on.
"""

from __future__ import annotations

import asyncio
import logging
import time
import weakref
from collections.abc import Iterable, Mapping
from typing import Any

import httpx
from cryptography.fernet import Fernet

from sudarshan.alerts import loopback
from sudarshan.autologin.handoff import seal
from sudarshan.signals import contract as c
from sudarshan.signals import words
from sudarshan.signals.config import ConfigError, merged_env, read_openalgo_env, read_pepper
from sudarshan.signals.service import hand_on, job
from sudarshan.vault import ALERTS_SIGNALS_INFO, fernet_for

log = logging.getLogger("sudarshan.signals")

MAX_ITEMS = c.RELAY["max_items"]
MAX_BYTES = c.SEAL_LIMITS["relay"]
HOLD_MS = c.RELAY["hold_ms"]
#: How often what is still pending is tried again when no cycle wakes the job sooner.
RETRY_S = 30.0
#: Pending rows read at a time; a longer backlog is read in turns of this many.
BATCH = 50
TIMEOUT_S = 5.0
CONNECT_TIMEOUT_S = 1.0
WARN_EVERY_S = 3600.0
#: The codes the alerts' listener answers a relay with (market/api.py, signals_desk.py).
KNOWN_CODES = frozenset({"bad_request", "busy", "off", "forbidden", "principal", "size", "failed"})

_due: weakref.WeakKeyDictionary[Any, asyncio.Event] = weakref.WeakKeyDictionary()
_keys: dict[str, Fernet] = {}
_warned: dict[str, float] = {}


def _warn(key: str, message: str, *args: Any) -> None:
    now = time.monotonic()
    if now - _warned.get(key, -1e9) >= WARN_EVERY_S:
        _warned[key] = now
        log.warning(message, *args)


def key_for(service: Any) -> Fernet | None:
    """The relay's seal key: the service's own when it carries one (``relay_key``), else derived
    once from OpenAlgo's pepper as the service's main derives the others; None when the pepper
    cannot be read (asked again next time)."""
    given = getattr(service, "relay_key", None)
    if given is not None:
        return given
    path = service.settings.env_file
    if str(path) not in _keys:
        try:
            openalgo = read_openalgo_env(path) if path.exists() else {}
            pepper = read_pepper(merged_env(None, openalgo))
        except ConfigError:
            _warn("key", "TRINETRA's changes cannot go to Telegram: OpenAlgo's pepper could not "
                  "be read. Check API_KEY_PEPPER in OpenAlgo's .env, then restart "
                  "sudarshan-signals.")  # fmt: skip
            return None
        _keys[str(path)] = fernet_for(pepper, ALERTS_SIGNALS_INFO)
    return _keys[str(path)]


def items_for(
    changes: Iterable[Mapping[str, Any]],
    alerts: Iterable[Mapping[str, Any]],
    settings: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """The contract's RelayChange and RelayAlert for pending rows, in the owner's language."""
    lang = settings.get("lang") or c.DEFAULT_LANG
    night = bool(settings.get("night", True))
    out = [words.relay_change(change, lang=lang, night=night) for change in changes]
    return out + [words.relay_alert(alert, night=night) for alert in alerts]


def sealed(key: Fernet, items: list[dict[str, Any]]) -> bytes:
    """One call's body: the contract's RelayRequest sealed for the alerts' signals desk."""
    args = {
        "changes": [item for item in items if item["id"].startswith("c-")],
        "alerts": [item for item in items if item["id"].startswith("a-")],
    }
    return seal(key, c.RELAY_OP, principal=c.PRINCIPALS["relay"], args=args)


def pack(key: Fernet, items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """The items in calls of at most MAX_ITEMS and MAX_BYTES sealed, in order. One item alone is
    far under the limit: its texts are cut to 300 characters."""
    calls: list[list[dict[str, Any]]] = []
    for item in items:
        last = calls[-1] if calls else None
        if (
            last is not None
            and len(last) < MAX_ITEMS
            and len(sealed(key, [*last, item])) <= (MAX_BYTES)
        ):
            last.append(item)
        else:
            calls.append([item])
    return calls


async def post(service: Any, key: Fernet, items: list[dict[str, Any]]) -> set[str] | None:
    """One call to the desk: the ids it has (taken now or known before), or None when it did not
    take the call (nothing listens, another program holds the port, a refusal)."""
    url = service.settings.alerts_url.rstrip("/") + c.RELAY_PATH
    refused = loopback.refusal(url)
    if refused:
        _warn("listener", "TRINETRA's changes were not handed to the alert service: %s", refused)
        return None
    try:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
            answer = await client.post(
                url,
                content=sealed(key, items),
                headers={"Content-Type": "application/octet-stream", "Accept": "application/json"},
                timeout=httpx.Timeout(TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
            )
        payload = answer.json()
    except (httpx.HTTPError, ValueError):
        _warn("down", "The alert service did not take TRINETRA's changes; they are tried again "
              "for 30 minutes")  # fmt: skip
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if (
        answer.status_code != 200
        or payload.get("status") != "success"
        or not isinstance(data, dict)
    ):
        code = payload.get("code") if isinstance(payload, dict) else None
        said = payload.get("message") if isinstance(payload, dict) else None
        known = code if code in KNOWN_CODES else "other"  # the warning's key space stays small
        _warn(f"refused-{known}", "The alert service did not take TRINETRA's changes (%s): %s",
              known, said if isinstance(said, str) else "no reason given")  # fmt: skip
        return None
    have = [*(data.get("taken") or []), *(data.get("known") or [])]
    return {ident for ident in have if isinstance(ident, str)} & {item["id"] for item in items}


async def _mark(store: Any, table: str, ids: list[Any], state: str) -> None:
    if ids:
        await store.call(store.mark_relay, table, ids, state)


def _row_ids(handed: set[str]) -> tuple[list[int], list[str]]:
    """The relay ids back to the rows: a change by its timeline id, an alert by TRINETRA's."""
    changes = sorted(int(ident[2:]) for ident in handed if ident.startswith("c-"))
    alerts = sorted(ident[2:] for ident in handed if ident.startswith("a-"))
    return changes, alerts


async def relay_pending(service: Any) -> int:
    """One round: skip everything while the owner's relay is off, expire what has waited 30
    minutes, hand on the rest; returns how many the desk has now. Stops at the first call the
    desk does not take: the next round tries again."""
    store = service.store
    if store is None:
        return 0
    settings = dict(service.prefs or {})
    total = 0
    while True:
        changes, alerts = await store.call(store.pending_relay, BATCH)
        if not changes and not alerts:
            return total
        if not settings.get("relay", True):
            await _mark(store, "change", [ch["id"] for ch in changes], "skipped")
            await _mark(store, "alert", [a["id"] for a in alerts], "skipped")
            log.info("%d TRINETRA item(s) are not sent: the owner's relay is off",
                     len(changes) + len(alerts))  # fmt: skip
            continue
        now = service.clock()
        old_changes = [ch for ch in changes if now - int(ch.get("recorded_ms") or 0) > HOLD_MS]
        old_alerts = [a for a in alerts if now - int(a.get("received_ms") or 0) > HOLD_MS]
        if old_changes or old_alerts:
            await _mark(store, "change", [ch["id"] for ch in old_changes], "expired")
            await _mark(store, "alert", [a["id"] for a in old_alerts], "expired")
            _warn(
                "expired",
                "%d TRINETRA item(s) could not be handed to the alert service "
                "within 30 minutes and are not sent",
                len(old_changes) + len(old_alerts),
            )
        live_changes = [ch for ch in changes if ch not in old_changes]
        live_alerts = [a for a in alerts if a not in old_alerts]
        key = key_for(service) if live_changes or live_alerts else None
        handed = 0
        if key is not None:
            for items in pack(key, items_for(live_changes, live_alerts, settings)):
                have = await post(service, key, items)
                if have is None:
                    return total + handed
                change_ids, alert_ids = _row_ids(have)
                await _mark(store, "change", change_ids, "handed")
                await _mark(store, "alert", alert_ids, "handed")
                handed += len(have)
        total += handed
        progress = handed or old_changes or old_alerts
        if not progress or (len(changes) < BATCH and len(alerts) < BATCH):
            return total


def _event(service: Any) -> asyncio.Event:
    found = _due.get(service)
    if found is None:
        found = _due[service] = asyncio.Event()
    return found


@hand_on("relay")
async def on_batch(service: Any, batch: Any) -> None:
    """A cycle stored changes or alerts: wake the job when any of them is pending."""
    if any(item.get("relay") == "pending" for item in (*batch.changes, *batch.alerts)):
        _event(service).set()


@job("relay")
async def relayer(service: Any) -> None:
    """Hand on what is pending: at start (what a stopped run left), whenever a cycle wakes it,
    and every RETRY_S. A failure costs one round, never the job."""
    due = _event(service)
    due.set()
    while True:
        try:
            await asyncio.wait_for(due.wait(), RETRY_S)
        except TimeoutError:
            pass
        due.clear()
        try:
            handed = await relay_pending(service)
        except Exception:
            log.exception("TRINETRA's changes could not be handed to the alert service this "
                          "time; they are tried again")  # fmt: skip
            continue
        if handed:
            log.info("%d TRINETRA item(s) handed to the alert service for Telegram", handed)
