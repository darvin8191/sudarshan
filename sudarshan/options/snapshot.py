"""One expiry's chain for the phone (M11.1, O2): sign-in, cache, single flight, the record.

Every state is decided here, in this order, each with its words:

* no chain to ask for (MCX, an underlying or an expiry the master does not list): ``none`` and
  **no call at all**; the master not loaded yet: ``unavailable``;
* no token today, or a token of another broker: ``signin`` and no call;
* an answer read in the last 5 s for this chain and this token: served with no call, whatever it
  was, so two phones or two tabs cost one read and a refusal is not asked again at once;
* otherwise one call, in a single flight per chain (the callers that waited share what the first
  one read), never retried: the token refused is ``signin`` and the session is never touched;
  Upstox, or the plugin's limiter, asking us to wait is ``wait``; no usable answer is
  ``unavailable``; an answer is ``ok`` with the chain. An answer Upstox calls a success that
  cannot be read as a chain is no usable answer and is logged once by its key names.

The cache key carries a digest of the token (the digest is kept, the token never), so an entry of
another sign-in is never served. Nothing is kept past the 5 s: when a read fails the phone keeps
the chain it holds and says its time. No thread, timer or retry loop; request-driven.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from sudarshan.options import chainsnap, mapping, words
from utils import real_threading
from utils.keyed_locks import KeyedLocks
from utils.logging import get_logger
from utils.thread_safe_cache import LockedTTLCache

logger = get_logger(__name__)

CACHE_S = 5
BROKER = "upstox"
_fresh = LockedTTLCache(maxsize=64, ttl=CACHE_S)
_flight = KeyedLocks(name="options-chain")
_shape_lock = real_threading.Lock()
#: The unreadable answer shapes already logged (their key names), each once; at most 16 kept.
_shapes_logged: set[str] = set()
SHAPES_KEPT = 16
#: Whether the mapping's note has been logged in this process (one line, counts only).
_mapping_noted: list[bool] = []


def now_ms() -> int:
    return int(time.time() * 1000)


def owner_token(user: str) -> str | None:
    """OpenAlgo's access token for the owner's session today, read as every service reads it."""
    from database import auth_db

    return auth_db.get_auth_token(user) if user else None


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _record(state: str, said: str | None, chain: dict | None = None) -> dict:
    return {"state": state, "words": said, "chain": chain}


def _unreadable(answer: Any, why: str) -> None:
    """Log, once for each shape, that Upstox answered a chain in a shape this version cannot
    read: its key names only (never a value, never the token), for design fact 2."""
    shape = mapping.shape_of(answer)
    text = repr(sorted(shape.items()))
    with _shape_lock:  # a set's bookkeeping only
        first = text not in _shapes_logged and len(_shapes_logged) < SHAPES_KEPT
        if first:
            _shapes_logged.add(text)
    if first:
        logger.warning(
            "Options: Upstox answered a chain in a shape this version cannot read (%s); "
            "key names: answer %s, first row %s, its call %s",
            why, shape["answer"], shape["row"], shape["call"],
        )  # fmt: skip


def _note_mapping(got: mapping.Mapped) -> None:
    """Say once, in counts only, that Upstox's instrument keys were not the master's tokens (legs
    matched by strike and side instead) or that the master lacked some legs: the answer to design
    fact 2, for whoever reads the log after the first live read."""
    if not (got.by_strike or got.left_out):
        return
    with _shape_lock:  # a flag's bookkeeping only
        first = not _mapping_noted
        _mapping_noted[:] = [True]
    if first:
        logger.warning(
            "Options: of a chain's legs, %s were matched by strike and side (their instrument "
            "key was not the master's token) and %s were left out (today's master lacks them)",
            got.by_strike, got.left_out,
        )  # fmt: skip


def _stop_of(underlying: str, exchange: str, expiries: list[str]) -> dict[str, int]:
    """When each expiry's contracts stop trading (epoch ms): the one rule of the watchlists
    package (the calendar's dated close of a special day, else the regime of the day)."""
    from sudarshan.watchlists import dynamic, sessions
    from sudarshan.watchlists import expiry as stops

    closes = sessions.closes(expiries, exchange)
    index = stops.is_index(underlying, sessions.index_underlyings())
    return {e: dynamic.close_ms(e, closes, index=index, exchange=exchange) for e in expiries}


def _stops_at(target: mapping.Target) -> int:
    return _stop_of(target.underlying, target.exchange, [target.expiry])[target.expiry]


def listing(exchange: str, underlying: str) -> dict:
    """``{"state", "words", "listing"}``: the expiries of an underlying that still trade, each
    with when it stops, the underlying's own symbol and the nearest future still trading. Read
    from the master and the calendar; Upstox is never asked, so no sign-in is needed."""
    if exchange == "MCX":
        return {"state": "none", "words": words.NO_MCX, "listing": None}
    from sudarshan.watchlists import master  # not at import: the bridge loads no master

    ix = master.shared().index()
    if ix is None:
        return {"state": "unavailable", "words": words.NOT_LOADED, "listing": None}
    found = mapping.listing_of(ix, exchange, underlying)
    if found is None:
        return {"state": "none", "words": words.no_options(underlying), "listing": None}
    now = now_ms()
    name = found["underlying"]
    stops = _stop_of(name, exchange, found["expiries"])
    live = [{"date": e, "stops_at": stops[e]} for e in found["expiries"] if stops[e] > now]
    if not live:
        return {"state": "none", "words": words.no_options(name), "listing": None}
    dated = [iso for iso, _ in found["futures"]]
    ends = _stop_of(name, exchange, dated)
    future = next(({"symbol": symbol, "exchange": exchange, "expiry": iso}
                   for iso, symbol in found["futures"] if ends[iso] > now), None)  # fmt: skip
    return {"state": "ok", "words": None, "listing": {
        "underlying": name, "exchange": exchange,
        "spot": {"symbol": found["symbol"], "exchange": found["underlying_exchange"]},
        "future": future, "expiries": live, "now": now}}  # fmt: skip


def _read(token: str, target: mapping.Target, by_token) -> dict:
    out = chainsnap.fetch(token, target.instrument_key, target.expiry)
    if out.kind == "token":
        return _record("signin", words.SIGNIN_TODAY)
    if out.kind == "wait":
        return _record("wait", words.WAIT)
    if out.kind == "ok" and (why := mapping.unusable(out.answer)) is not None:
        _unreadable(out.answer, why)
        out = chainsnap.Outcome("failed")
    if out.kind != "ok":
        return _record("unavailable", words.FAILED)
    got = mapping.map_chain(out.answer, target, by_token)
    _note_mapping(got)
    return _record("ok", None, {
        "as_of": now_ms(),
        "exchange": target.exchange,
        "underlying": target.underlying,
        "underlying_exchange": target.underlying_exchange,
        "expiry": target.expiry,
        "expiry_ts": _stops_at(target),
        "spot": got.spot,
        "pcr": got.pcr,
        "strikes": got.strikes,
        "left_out": got.left_out,
        "by_strike": got.by_strike,
    })  # fmt: skip


def answer(exchange: str, underlying: str, expiry: str, user: str, broker: str | None) -> dict:
    """``{"state", "words", "chain"}`` of one expiry's chain for the owner's sign-in. The record
    is shared by every caller of the same 5 s: nobody changes it."""
    if exchange == "MCX":
        return _record("none", words.NO_MCX)
    from sudarshan.watchlists import master  # not at import: the bridge loads no master

    m = master.shared()
    ix = m.index()
    if ix is None:
        return _record("unavailable", words.NOT_LOADED)
    target = mapping.target_of(ix, exchange, underlying, expiry)
    if target is None:
        return _record("none", words.not_listed(underlying, expiry))
    token = owner_token(user)
    if not token:
        return _record("signin", words.SIGNIN_TODAY)
    if (broker or "").strip().lower() != BROKER:
        return _record("signin", words.OTHER_BROKER)
    key = (target.exchange, target.underlying, target.expiry, digest(token))
    got = _fresh.get(key)
    if got is not None:
        return got
    with _flight.hold(key):  # the callers that waited find what the first one read
        got = _fresh.get(key)
        if got is not None:
            return got
        generation = _fresh.generation
        got = _read(token, target, m.by_token)
        _fresh.fill(key, got, generation)
        return got
