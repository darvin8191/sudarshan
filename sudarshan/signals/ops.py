"""The ops of the TRINETRA view's sealed local API (T6, design 3.5): one handler per op.

``REGISTRY`` maps an op's name to ``async handler(service, args) -> data``; the API answers 501
"not ready" for an op with no handler (``performance`` until T8). Every handler checks its
arguments and answers the contract's shape; a refusal is an ``ApiError`` in the owner's words with
the field it is about. Reads of signals.db run on the store's one thread (``store.call``); what
the cycles keep in memory (the current readings, the last document, today's counts) is read
directly, so ``state`` costs one small query (the recent alerts).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from sudarshan.signals import contract as c
from sudarshan.signals import diff, words
from sudarshan.signals.api import ApiError
from sudarshan.signals.health import _last
from sudarshan.signals.store import StoreError, VersionConflict

Handler = Callable[[Any, dict[str, Any]], Awaitable[Any]]
REGISTRY: dict[str, Handler] = {}
_MODULE = re.compile(c.MODULE_PATTERN)
_ORDER = {name: n for n, name in enumerate(c.KNOWN_MODULES)}


def op(name: str) -> Callable[[Handler], Handler]:
    def register(function: Handler) -> Handler:
        if name in REGISTRY:
            raise ValueError(f"the op {name} is registered twice")
        REGISTRY[name] = function
        return function

    return register


def _bad(field: str, word: str = "bad_request") -> ApiError:
    return ApiError(400, "bad_request", c.WORDS[word], field)


def _int(args: dict[str, Any], name: str, *, low: int, high: int, default: int | None = None):
    value = args.get(name, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise _bad(name)
    return value


async def _call(service: Any, fn: Callable[..., Any], *args: Any) -> Any:
    """A store read or write on its thread; its failure in the owner's words."""
    try:
        return await service.store.call(fn, *args)
    except VersionConflict:
        raise ApiError(409, "version_conflict", c.WORDS["version_conflict"]) from None
    except StoreError as exc:
        raise ApiError(500, "failed", str(exc)) from None


def _reading(entry: diff.Current, now: int, is_trading_day) -> dict[str, Any]:
    lapse = entry.lapse_ms
    record = dict(entry.reading)
    return {
        **record,
        "lapsed": entry.lapsed or (lapse is not None and lapse <= now),
        "sessions": diff.sessions(record.get("since_ms"), now, is_trading_day),
        "published_ms": entry.published_ms,
        "doc_seq": entry.doc_seq,
    }


def readings(service: Any, now: int) -> list[dict[str, Any]]:
    """The current readings as the contract's ``Reading``: the modules this release names first,
    in their order, then any other; NIFTY before BANKNIFTY."""
    entries = sorted(
        service.current.values(),
        key=lambda e: (
            _ORDER.get(e.reading["module"], len(_ORDER)),
            e.reading["module"],
            c.INSTRUMENTS.index(e.reading["instrument"]),
        ),
    )
    return [_reading(e, now, service.calendar.is_trading_day) for e in entries]


async def state_of(service: Any) -> dict[str, Any]:
    """The contract's ``State`` at the service's clock now (also what the push carries)."""
    now = service.clock()
    last = _last(service.last)
    today = service.today or {}
    newer = service.newer_format_ms is not None and (
        service.last is None or service.newer_format_ms >= int(service.last.get("received_ms", 0))
    )
    alerts = await _call(service, service.store.alerts_recent, now - c.STATE_ALERTS["within_ms"])
    return {
        "now_ms": now,
        "lang": service.prefs.get("lang", c.DEFAULT_LANG),
        "freshness": words.freshness(last, now),
        "notes": words.notes(
            last,
            refused_today=int(today.get("refused", 0)),
            gaps_today=int(today.get("gaps", 0)),
            newer_format=newer,
        ),
        "readings": readings(service, now),
        "alerts": alerts,
    }


@op("state")
async def state(service: Any, args: dict[str, Any]) -> dict[str, Any]:
    return await state_of(service)


@op("timeline")
async def timeline(service: Any, args: dict[str, Any]) -> dict[str, Any]:
    module = args.get("module")
    if module is not None and (not isinstance(module, str) or not _MODULE.fullmatch(module)):
        raise _bad("module", "bad_module")
    instrument = args.get("instrument")
    if instrument is not None and instrument not in c.INSTRUMENTS:
        raise _bad("instrument", "bad_instrument")
    before = _int(args, "before", low=1, high=2**62)
    after_ms = _int(args, "after_ms", low=0, high=10**13)
    limit = _int(args, "limit", low=1, high=c.TIMELINE["max"], default=c.TIMELINE["page"])

    def read() -> dict[str, Any]:
        return service.store.timeline(module=module, instrument=instrument, before=before,
                                      after_ms=after_ms, limit=limit)  # fmt: skip

    return await _call(service, read)


@op("change_get")
async def change_get(service: Any, args: dict[str, Any]) -> dict[str, Any]:
    ident = _int(args, "id", low=1, high=2**62)
    if ident is None:
        raise _bad("id")
    found = await _call(service, service.store.change_get, ident)
    if found is None:
        raise ApiError(404, "not_found", c.WORDS["not_found"])
    return found


@op("settings_get")
async def settings_get(service: Any, args: dict[str, Any]) -> dict[str, Any]:
    return dict(service.prefs) or await _call(service, service.store.settings)


@op("settings_set")
async def settings_set(service: Any, args: dict[str, Any]) -> dict[str, Any]:
    version = _int(args, "version", low=1, high=2**31)
    if version is None:
        raise _bad("version")
    changes: dict[str, Any] = {}
    if "lang" in args:
        if args["lang"] not in c.LANGS:
            raise _bad("lang", "bad_lang")
        changes["lang"] = args["lang"]
    for name in ("relay", "night"):
        if name in args:
            if not isinstance(args[name], bool):
                raise _bad(name)
            changes[name] = args[name]
    new = await _call(service, service.store.settings_set, version, changes)
    service.prefs = new
    service.updated("settings")
    return new


@op("performance")
async def performance(service: Any, args: dict[str, Any]) -> dict[str, Any]:
    """T8: the score of every module, or of one, from the timeline and the daily levels."""
    from sudarshan.signals import perf

    module = args.get("module")
    if module is not None and (not isinstance(module, str) or not _MODULE.fullmatch(module)):
        raise _bad("module", "bad_module")

    def work() -> dict[str, Any]:
        store = service.store
        return perf.score(store.scored(module), store.levels(), service.calendar.is_trading_day,
                          module)  # fmt: skip

    return await _call(service, work)


@op("state_at")
async def state_at(service: Any, args: dict[str, Any]) -> dict[str, Any]:
    ts = _int(args, "ts", low=10**12, high=10**13 - 1)
    if ts is None:
        raise _bad("ts")
    return await _call(service, service.store.state_at, ts)
