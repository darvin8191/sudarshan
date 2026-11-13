"""The sealed members route (M8.14, W23): what the list alerts read of a list, and nothing else.

``POST /sudarshan/watchlists/internal/read?op=members|version`` is for ``sudarshan-alerts`` alone,
on the screener's pattern: no session and no CSRF (the service has no browser to forge; the seal
is its proof), one Fernet token under ``WATCHLISTS_INTERNAL_INFO`` in the body, a nonce book that
refuses a replay, 16 KiB at most, and a 404 for any request nginx forwarded (the service calls
127.0.0.1 directly, so a proxy header means it came from outside). The checks run in that order,
the seal before the switch, so an unsealed caller learns nothing, not even whether v2 is on.

Then: the switch (``off`` for both ops), and the owner (the one account of this instance). ``version``
answers the lists version alone. ``members {ref}`` answers the rows of ``list:<id>``,
``list:<id>/section:<sid>``, ``preset:<id>`` or ``flag:<n>`` as the reader of that kind has them
today (``refs/<kind>.members``), resolved through the master: a ranked ref is refused in words, an
unknown or deleted list is ``gone``, a master that is not loaded is ``failed`` (the caller changes
nothing and asks again).

The answer is ``{status: "ok", data: {version, ref_version, created_at, list_key, rows, left_out,
valid_until}}``:

* ``version`` is the lists version, the same text ``op=version`` answers (a consumer compares the
  two); ``ref_version`` is the ref's own, a short digest of what it holds, so a consumer can tell
  its own list changed when the lists version moved for another's (review L10).
* ``created_at`` and ``list_key`` (the same text) are a ``list:`` ref's identity, OpenAlgo's
  ``created_at`` of the list: OpenAlgo hands a deleted list's id to the next list made (review M4).
  Absent for a built-in list and a flag list.
* ``rows`` are ``{symbol, exchange, instrument_key, tick_size, expiry, lot, valid_until?}``. A
  future carries ``valid_until``, the epoch ms its contract stops (``expiry.stop_ms``, with a
  special session day's close from the calendar): ask again then. The first of ``valid_until`` over
  the rows is also the answer's own.
* ``left_out`` are ``{symbol, why, reason}``: ``atm`` and ``strike`` for a follow row that changes
  contract during the day, ``missing`` for a symbol today's master lacks; ``reason`` is words.

No flag and no note is ever in it. Reads only: nothing is written, so the route is not in
``contract.WRITE_ROUTES``. It runs on the request's thread and starts nothing.
"""

from __future__ import annotations

import hashlib
import os
import time
from datetime import UTC, datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

from database.user_db import find_user_by_username
from sudarshan.autologin.handoff import (
    SEAL_TTL_S,
    HandoffRejected,
    NonceBook,
    NoncesFull,
    open_sealed,
)
from sudarshan.vault import WATCHLISTS_INTERNAL_INFO, fernet_for, pepper_from
from sudarshan.watchlists import contract, engine, master, refs, versions
from sudarshan.watchlists import expiry as stops
from sudarshan.watchlists.ops.state import enabled
from sudarshan.watchlists.words import members as words
from sudarshan.watchlists.words import ranked as ranked_words
from sudarshan.watchlists.words.base import MESSAGES
from utils import real_threading
from utils.lazy import LazyInit
from utils.logging import get_logger
from utils.thread_safe_cache import MISSING, LockedTTLCache

logger = get_logger(__name__)

OPS = tuple(op.name for op in contract.INTERNAL_OPS)
IST = timezone(timedelta(hours=5, minutes=30))
#: Set by nginx on every proxied location: a request carrying any of them did not come from the
#: alerts service, which calls 127.0.0.1:5000 directly (the screener's route reads the same five).
PROXY_HEADERS = (
    "X-Forwarded-For",
    "X-Real-IP",
    "Forwarded",
    "X-Forwarded-Proto",
    "X-Forwarded-Host",
)
internal_bp = Blueprint(
    "sudarshan_watchlists_internal", __name__, url_prefix=contract.INTERNAL_PREFIX
)
_warned = LockedTTLCache(maxsize=8, ttl=600)
_fernet_lazy = LazyInit(
    lambda: fernet_for(pepper_from(os.environ), WATCHLISTS_INTERNAL_INFO),
    "sudarshan-watchlists-internal",
)
#: The alerts service asks once a minute and at each push; the book holds 2 x 60 s of 8 a second and
#: answers 503 past it rather than forget a nonce that could still be replayed.
_nonces = NonceBook(2 * SEAL_TTL_S * 8, lock=real_threading.RLock())


def _warn_once(kind: str, message: str, *args) -> None:
    with _warned.lock:
        first = _warned.get(kind, MISSING) is MISSING
        if first:
            _warned[kind] = True
    if first:
        logger.warning(message, *args)


def _refused(status: int, message: str):
    return jsonify({"status": "error", "message": message}), status


def _error(code: str, message: str | None = None):
    body = {"status": "error", "code": code, "message": message or MESSAGES[code]}
    response = jsonify(body)
    response.status_code = contract.ERROR_STATUS[code]
    return response


def _open(op: str):
    """The sealed request's arguments, or the answer to give instead."""
    if any(header in request.headers for header in PROXY_HEADERS):
        return None, _refused(404, "Not found")
    length = request.content_length
    if length is None:
        return None, _refused(411, "Length required")
    if length > contract.INTERNAL_MAX_BYTES:
        return None, _refused(413, "Too large")
    try:
        message = open_sealed(
            _fernet_lazy.get(), request.get_data(cache=False), op=op, seen=_nonces,
            max_bytes=contract.INTERNAL_MAX_BYTES,
        )  # fmt: skip
    except NoncesFull:
        _warn_once("busy", "Watchlists: too many internal requests in the last two minutes")
        return None, _refused(503, "Busy")
    except HandoffRejected as exc:
        _warn_once(
            "refused",
            "Watchlists: refused an internal request that was not sealed by this server's "
            "sudarshan-alerts (%s)", exc,
        )  # fmt: skip
        return None, _refused(403, "Forbidden")
    return message, None


def ref_version(rows: list[dict], left_out: list[dict]) -> str:
    """A short digest of what a ref holds (its instruments, in order, and what it leaves out): it
    changes when the ref's own members change and not when another list does."""
    text = (
        "|".join(r["instrument_key"] for r in rows)
        + "#"
        + "|".join(f"{c['why']}:{c['symbol']}" for c in left_out)
    )
    return hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()[:12]


def _tick(tick) -> str:
    return "" if tick is None else f"{tick:g}"


def _closes(rows: list, now_ms: int) -> dict[str, int]:
    """The calendar's close of today's expiry when a future of the answer expires today (any other
    day closes before or after now whatever the calendar says: it is not asked)."""
    from sudarshan.watchlists import sessions

    today = datetime.fromtimestamp(now_ms / 1000, UTC).astimezone(IST).date().isoformat()
    if not any(c["expiry"] == today for c in rows):
        return {}
    return sessions.closes([today], "NFO")


def wire(got: dict, now_ms: int) -> dict:
    """A reader's ``Members`` record as the route answers it (see the module's note)."""
    from sudarshan.watchlists import sessions

    m = master.shared()
    if not m.loaded():
        raise _Failed(words.NOT_LOADED)
    closes = _closes(got["members"], now_ms)
    indices = None  # the master's index underlyings (review M1), read once for the first future
    rows, left_out, seen = [], [], set()
    for c in got["members"]:
        row = m.row(c["exchange"], c["symbol"])
        if row is None or not getattr(row, "token", None):
            left_out.append({"symbol": c["symbol"], "why": "missing", "reason": words.NOT_LISTED})
            continue
        if row.token in seen:  # a fixed symbol and a follow row can name one contract
            continue
        seen.add(row.token)
        out = {"symbol": c["symbol"], "exchange": c["exchange"], "instrument_key": row.token,
               "tick_size": _tick(c["tick"]), "expiry": c["expiry"], "lot": c["lot"]}  # fmt: skip
        if row.instrumenttype == "FUT" and c["expiry"]:
            if indices is None:
                indices = sessions.index_underlyings()
            out["valid_until"] = stops.stop_ms(
                c["expiry"], index=stops.is_index(row.underlying or row.name or "", indices),
                exchange=row.exchange, window_end_ms=closes.get(c["expiry"]),
            )  # fmt: skip
        rows.append(out)
    left_out += [{"symbol": c["token"], "why": c.get("why", "missing"), "reason": c["reason"]}
                 for c in got["left_out"]]  # fmt: skip
    until = [r["valid_until"] for r in rows if "valid_until" in r]
    data = {"version": got["version"], "ref_version": ref_version(rows, left_out),
            "rows": rows, "left_out": left_out, "valid_until": min(until) if until else None}  # fmt: skip
    if got.get("created_at") is not None:
        data["created_at"] = data["list_key"] = got["created_at"]
    return data


class _Failed(Exception):
    """The answer cannot be made now: the caller changes nothing and asks again."""

    def __init__(self, text: str):
        super().__init__(text)
        self.text = text


def _members(ref) -> dict:
    """The ``members`` op's answer, or the answer to give instead (an ``Exception`` is not raised
    for a refusal: it is a response)."""
    from sudarshan.watchlists.ops import OpError

    parts = refs.split(ref) if isinstance(ref, str) else None
    if parts is None:
        return _error("invalid", words.BAD_REF)
    if parts[0] == "ranked":
        return _error("invalid", ranked_words.NO_ALERT)
    reader = refs.readers().get(parts[0])
    if reader is None:
        return _error("invalid", words.BAD_REF)
    owner = find_user_by_username()
    if owner is None:
        return _error("failed")
    try:
        got = reader.members(parts[1], owner.username)
        data = wire(got, reader_now())
    except OpError as refused:
        if refused.code == "failed":
            return _error("failed", refused.message)
        return _error(refused.code, refused.message)
    except _Failed as failed:
        return _error("failed", failed.text)
    return jsonify({"status": "ok", "data": data})


def reader_now() -> int:
    return int(time.time() * 1000)


@internal_bp.route("/read", methods=["POST"])
def read():
    op = (request.args.get("op") or "").strip()
    if op not in OPS:
        return _refused(404, "Not found")
    message, refusal = _open(op)
    if refusal is not None:
        return refusal
    if not enabled():
        return _error("off")
    try:
        engine.ready()
        if op == "version":
            response = jsonify({"status": "ok", "data": {"version": versions.read()}})
        else:
            response = _members(message.get("ref"))
    except (engine.RecordTooNew, engine.RecordUnreadable):
        return _error("record")
    except Exception:
        logger.exception("Watchlists: the sealed members route failed")
        return _error("failed")
    response.headers["Cache-Control"] = "no-store"
    return response


@internal_bp.record_once
def _exempt_from_csrf(state):
    """The service has no browser session to forge; its requests are authenticated by the seal."""
    csrf = state.app.extensions.get("csrf") or getattr(state.app, "csrf", None)
    if csrf is not None:
        csrf.exempt(read)


def install(app) -> None:
    """Register the route on ``app``, once (the bridge calls this when its blueprint is added)."""
    if "sudarshan_watchlists_internal" in app.blueprints:
        return
    app.register_blueprint(internal_bp)
