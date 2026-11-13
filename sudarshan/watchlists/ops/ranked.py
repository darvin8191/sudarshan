"""The ``ranked`` op (M8.9, W22): one Upstox ranking as symbols, or the sign-in words. A read.

The read is made by the bridge with OpenAlgo's daily Upstox access token for the owner's session
(``smartlist.py``), so every state is decided here, in this order, each with its words:

* no token today, or a token of another broker: ``signin`` and **no call at all**;
* a fresh answer (60 s) or a refusal of the token (60 s) in the cache: served with no call;
* otherwise one call, in a single flight per category (two requests for one category make one
  call), never retried: the token refused is ``signin`` and the session is never touched; Upstox
  asking us to wait, or the plugin's own limiter, is ``wait``; no usable answer is ``unavailable``
  and the last ranking read with this token stays, labelled with its time; an answer is ``ok``,
  ``stale`` after ten minutes and ``closed`` while the exchange is shut (Upstox's own last ranking
  and time). An answer Upstox calls a success whose rows cannot be read (no list of rows, or no
  row naming an ``instrument_key``: ``ranked.unusable``) is no usable answer: it is never cached
  as a ranking, never replaces the last good one and is logged once by its key names (v0.9.0
  review M2). With no time from Upstox the line says the read's age, never the ranking's.

The cache holds Upstox's answer for the 50-row page, so 20 rows and 50 rows are one read, and it
is dropped whenever the session's token changes (each entry also carries a digest of its token, so
an entry of another token is never served; the digest is kept, the token never). Prices are never
here: the page streams them. No thread, timer or retry loop; request-driven.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, NamedTuple

from flask import session

from sudarshan.watchlists import ranked, sessions, smartlist
from sudarshan.watchlists import view as merge_view
from sudarshan.watchlists.ops import OpError
from sudarshan.watchlists.words import arrange as arrange_words
from sudarshan.watchlists.words import presets as preset_words
from sudarshan.watchlists.words import ranked as words
from utils import real_threading
from utils.keyed_locks import KeyedLocks
from utils.logging import get_logger
from utils.thread_safe_cache import LockedTTLCache

logger = get_logger(__name__)

CACHE_S = 60
SHARE_S = 3  # a failed read is shared with the callers that waited for it, then forgotten
LAST_S = 20 * 60 * 60  # the last good ranking of a token, for an Upstox that does not answer
BROKER = "upstox"
_fresh = LockedTTLCache(maxsize=64, ttl=CACHE_S)  # ok, token and wait, by category
_shared = LockedTTLCache(maxsize=64, ttl=SHARE_S)  # failed, by category
_last = LockedTTLCache(maxsize=64, ttl=LAST_S)  # the last ok, by category
_flight = KeyedLocks(name="watchlists-ranked")
_token_lock = real_threading.Lock()
_token_seen: list[str | None] = [None]  # the digest of the token the caches belong to
#: The unreadable answer shapes already logged (their key names), each once; at most 16 kept.
_shapes_logged: set[str] = set()
SHAPES_KEPT = 16


class Reading(NamedTuple):
    kind: str  # smartlist.Outcome kinds
    answer: Any
    read_at: int
    fp: str


def now_ms() -> int:
    return int(time.time() * 1000)


def owner_token(user: str) -> str | None:
    """OpenAlgo's access token for the owner's session today, read as every service reads it."""
    from database import auth_db

    return auth_db.get_auth_token(user) if user else None


def stored_broker(user: str) -> str | None:
    """The broker OpenAlgo's sign-in of ``user`` is with, for a caller with no web session."""
    from database import auth_db

    record = auth_db.get_auth_token_dbquery(user)
    return getattr(record, "broker", None)


def sign_in(user: str, broker: str | None) -> tuple[str | None, str | None]:
    """``(token, None)`` when ranked lists can be read, else ``(None, the words)``. No call.
    ``broker`` is the session's; without one (the sealed route) the stored sign-in's is read."""
    token = owner_token(user)
    if not token:
        return None, words.SIGNIN_TODAY
    if (broker or stored_broker(user) or "").strip().lower() != BROKER:
        return None, words.OTHER_BROKER
    return token, None


def availability(user: str, broker: str | None) -> dict:
    """The ``Availability`` record ``state`` carries for ranked lists."""
    token, said = sign_in(user, broker)
    return {"ok": token is not None, "message": said}


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _follow(fp: str) -> None:
    """Drop every cached ranking when the session's token is not the one they were read with."""
    with _token_lock:
        changed = _token_seen[0] != fp
        _token_seen[0] = fp
    if changed:
        for cache in (_fresh, _shared, _last):
            cache.invalidate()


def _usable(got: Reading | None, fp: str) -> Reading | None:
    return got if got is not None and got.fp == fp else None


def _unreadable(answer: Any, why: str, category: str) -> None:
    """Log, once for each shape, that Upstox answered a ranking in a shape this version cannot
    read: its key names only (never a value, never the token), for design facts W-1 and W-2."""
    shape = ranked.shape_of(answer)
    text = repr(sorted(shape.items()))
    with _token_lock:  # a set's bookkeeping only
        first = text not in _shapes_logged and len(_shapes_logged) < SHAPES_KEPT
        if first:
            _shapes_logged.add(text)
    if first:
        logger.warning(
            "Watchlists: Upstox answered the %s ranking in a shape this version cannot read (%s); "
            "key names: answer %s, data %s, first row %s",
            category, why, shape["answer"], shape["data"], shape["row"],
        )  # fmt: skip


def reading_of(token: str, source: str, asset: str, category: str) -> Reading:
    """The cached or freshly read answer of one category: one call for any number of callers."""
    fp = digest(token)
    _follow(fp)
    key = (source, asset, category)
    for cache in (_fresh, _shared):
        got = _usable(cache.get(key), fp)
        if got is not None:
            return got
    with _flight.hold(key):  # the callers that waited find what the first one read
        for cache in (_fresh, _shared):
            got = _usable(cache.get(key), fp)
            if got is not None:
                return got
        gens = (_fresh.generation, _shared.generation, _last.generation)
        out = smartlist.fetch(token, source, asset, category, ranked.MAX_SIZE)
        if out.kind == "ok" and (why := ranked.unusable(out.answer)) is not None:
            _unreadable(out.answer, why, category)
            out = smartlist.Outcome("failed")  # not a ranking: never cached as one, never last
        got = Reading(out.kind, out.answer, now_ms(), fp)
        if out.kind == "failed":
            _shared.fill(key, got, gens[1])
        else:
            _fresh.fill(key, got, gens[0])
            if out.kind == "ok" and ranked.extract(out.answer).rows:
                _last.fill(key, got, gens[2])  # an empty ranking never replaces one with rows
        return got


def _record(ref, state, made, read_at, rows, left_out, said, left_words=None) -> dict:
    """The ``Ranked`` record. ``left_out_words`` is the left-out sentence for ``left_out`` (None
    when nothing was left out); the words of the ok and stale states do not repeat it, a closed
    state's do (the phone shows those words whole)."""
    return {"ref": ref, "state": state, "made_at": made, "read_at": read_at, "rows": rows,
            "left_out": left_out, "left_out_words": left_words, "words": said}  # fmt: skip


def lines_of(record: dict) -> list[str]:
    """The lines a view shows over a ranking: its words, then the left-out sentence where the
    words do not carry it (the ok and stale states)."""
    out = [record["words"]]
    if record["left_out_words"] and record["state"] in ("ok", "stale"):
        out.append(record["left_out_words"])
    return out


def _marks() -> dict:
    from sudarshan.watchlists.refs import preset

    try:
        return preset.read_marks()
    except Exception:
        logger.exception("Watchlists: the flags of a ranking's rows could not be read")
        return {}


def _mapped(got: Reading, category: str, size: int, m):
    """``(ranking, missing keys, rows as the contract says)`` for the first ``size`` rows."""
    rows, stamp, metric_key, _ = ranked.extract(got.answer)
    cut = rows[:size]
    body = {"data": {"rows": cut, "time_stamp": stamp, "metric_key": metric_key}}
    ranking = ranked.map_rows(body, category, m.by_token)
    missing = [r["instrument_key"] for r in cut
               if isinstance(r.get("instrument_key"), str) and m.by_token(r["instrument_key"]) is None]  # fmt: skip
    marks = _marks()
    out = []
    for r in ranking.rows:
        flag, noted = marks.get((r.exchange, r.symbol), (None, False))
        row = m.by_token(r.key)
        out.append({
            "item": None,
            "contract": merge_view.contract(r.exchange, r.symbol, row, m.isin(row)),
            "section_id": None,
            "flag": flag,
            "has_note": bool(noted),
            "expired": False,
            "metric": r.figure,
        })  # fmt: skip
    return ranking, missing, out


def _line(ranking: ranked.Ranking, got: Reading, asset: str, now: int) -> tuple[str, str]:
    """``(state, the line)`` of an answer made by Upstox: closed, stale or ok."""
    made = ranking.made_at
    exchange = "MCX" if asset == "COMMODITY" else "NFO"
    if not sessions.market_open(exchange, now):
        return "closed", (words.closed_line(made, now) if made else words.CLOSED_NO_TIME)
    if made is None:  # no age is counted from the read as if it were the ranking's (review M2)
        return "ok", words.no_time_line(got.read_at, now)
    return ("stale" if words.is_stale(made, now) else "ok"), words.age_line(made, now)


def shaped(got: Reading, ref: str, asset: str, category: str, size: int,
           last: Reading | None) -> tuple[dict, list[str]]:  # fmt: skip
    """``(the Ranked record, the keys today's master lacks)`` of a reading; ``last`` is the last
    good one of this token, for the states that keep the old ranking."""
    from sudarshan.watchlists import master  # not at import: the bridge loads no master

    m = master.shared()
    now = now_ms()
    if got.kind == "ok":
        ranking, missing, rows = _mapped(got, category, size, m)
        state, said = _line(ranking, got, asset, now)
        cut = words.left_out(ranking.left_out)
        if state == "closed" and cut:  # shown whole by the phone, so one sentence after another
            said = f"{said}. {cut}"
        return _record(ref, state, ranking.made_at, got.read_at, rows, ranking.left_out, said,
                       cut), missing  # fmt: skip
    old = _mapped(last, category, size, m) if last is not None else None
    made = old[0].made_at if old else None
    rows, missing = (old[2], old[1]) if old else ([], [])
    left = old[0].left_out if old else 0
    read = last.read_at if last is not None else None
    if got.kind == "token":
        said = words.expired_line(made, now) if made else words.EXPIRED
        return _record(ref, "signin", made, read, [], 0, said), []
    cut = words.left_out(left)
    if got.kind == "wait":
        return _record(ref, "wait", made, read, rows, left, words.WAIT, cut), missing
    return _record(ref, "unavailable", made, read, rows, left, words.unavailable(made),
                   cut), missing  # fmt: skip


def answer(source: str, asset: str, category: str, size: int | None, user: str,
           broker: str | None = None) -> tuple[dict, list[str]]:  # fmt: skip
    """The ``Ranked`` record of one ranking for the owner's sign-in, and the instrument keys of
    the rows Upstox named that today's master lacks."""
    ref = ranked.ref_of(source, asset, category)
    size = ranked.clamp_size(size)
    token, said = sign_in(user, broker)
    if token is None:
        return _record(ref, "signin", None, None, [], 0, said), []
    from sudarshan.watchlists import master  # not at import: the bridge loads no master

    if not master.shared().loaded():
        raise OpError("failed", preset_words.NOT_LOADED)
    got = reading_of(token, source, asset, category)
    last = _usable(_last.get((source, asset, category)), got.fp)
    return shaped(got, ref, asset, category, size, last)


def ranked_op(args: dict) -> dict:
    if not ranked.valid(args["src"], args["asset"], args["cat"]):
        raise OpError("invalid", field="cat")
    return answer(args["src"], args["asset"], args["cat"], args.get("size"),
                  session.get("user") or "", session.get("broker"))[0]  # fmt: skip


def is_ranked(spec: dict) -> bool:
    """True when a copy's ``from`` names a ranked list."""
    return str(spec.get("ref", "")).startswith("ranked:")


def copy_source(spec: dict) -> tuple[int, dict, list, bool]:
    """``(BUILTIN, {name, items, note}, picked keys, False)``, as ``copy._source`` answers, for a
    ranked source (M8.9, "Make a copy").

    The phone sends ``keys`` (``EXCHANGE:SYMBOL``) in the order it shows them, as for a built-in
    list, and the server does not read Upstox again for it: the rows are the ones the page holds.
    A key today's master lacks is left out and counted in the answer's words. At most 50 keys (a
    ranking's page), so the 250 cut of a built-in list does not arise.
    """
    from sudarshan.watchlists import master
    from sudarshan.watchlists.ops import presets as presets_op

    parts = ranked.parse_ref(spec["ref"])
    if parts is None:
        raise OpError("invalid", field="from")
    if "section_id" in spec or "keys" not in spec:
        raise OpError("invalid", arrange_words.PICK_NOTHING, "from")
    keys = list(dict.fromkeys(spec["keys"]))
    if not keys or len(keys) > ranked.MAX_SIZE:
        raise OpError("invalid", arrange_words.PICK_NOTHING, "from")
    wanted = []
    for text in keys:
        exchange, colon, symbol = text.partition(":")
        if not colon or not exchange or not symbol:
            raise OpError("invalid", field="from")
        wanted.append((exchange, symbol))
    m = master.shared()
    if not m.loaded():
        raise OpError("failed", preset_words.NOT_LOADED)
    picked = [k for k in wanted if m.known(*k)]
    if not picked:
        raise OpError("invalid", words.copy_nothing(len(wanted)), "from")
    note = words.copy_left_out(len(wanted) - len(picked))
    return (
        presets_op.BUILTIN,
        {"name": words.label(*parts), "items": [], "note": note},
        picked,
        False,
    )


HANDLERS = {"ranked": ranked_op}
