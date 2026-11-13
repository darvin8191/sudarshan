"""The reader of ``preset:<id>`` refs (M8.8, W12): the built-in lists made from today's master.

The list is built from ``master.shared()``, the process's one master, and kept with that
generation: a ``Prepared`` (references to the master's own rows, never copies) per list in a
``WeakKeyDictionary`` keyed by the ``Generation``, so it is dropped the moment the generation is
(OpenAlgo publishing the next master, or a lapsed session) and nothing here keeps an old row
alive. Nothing is built before a list is asked for, and nothing runs on a timer: the master is
the clock of these lists (each morning after the day's login) and the request is the rest.

What depends on the clock is not cached. Index futures pick the first future whose expiry close
is still ahead at each call; the close is the market calendar's, asked for only when an expiry
falls today (an expiry on any other day closes before or after now whatever the calendar says),
and 15:30 IST when the calendar has no row (``dynamic.close_ms``).

The owner's flags and notes are read from the store (a flag or a note on a symbol belongs to the
symbol and applies in every list); only whether a note exists is read, never the note. Built-in
lists are locked and have no version: nothing is written against them. No Upstox call and nothing
downloaded: the lists are made of the rows OpenAlgo already holds.
"""

from __future__ import annotations

import time
import weakref
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import func, select

from sudarshan.watchlists import engine, master, presets, schema, versions
from sudarshan.watchlists import view as merge_view
from sudarshan.watchlists.ops import OpError
from sudarshan.watchlists.schema import OWNER
from sudarshan.watchlists.words import presets as words
from utils import real_threading
from utils.logging import get_logger

logger = get_logger(__name__)
KIND = "preset"
IST = timezone(timedelta(hours=5, minutes=30))
#: The prepared lists of each generation. Entries leave with their generation (weak keys).
_prepared: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
#: Guards the dictionary's reads and writes (an identity lookup and one assignment), nothing else.
_slot = real_threading.Lock()


def now_ms() -> int:
    return int(time.time() * 1000)


def entry_of(ident: str) -> presets.Entry:
    """The catalogue entry of ``preset:<ident>``; ``gone`` when it is not offered."""
    found = presets.entry(ident)
    if found is None:
        raise OpError("gone", words.GONE)
    return found


def prepared_for(e: presets.Entry, gen: master.Generation) -> presets.Prepared:
    """The list prepared against this generation: built once, kept with it."""
    with _slot:
        got = _prepared.get(gen, {}).get(e.id)
    if got is not None:
        return got
    built = e.prepare(gen.index)  # references only, no hub wait: done outside the lock
    with _slot:
        return _prepared.setdefault(gen, {}).setdefault(e.id, built)


def calendar_close(iso: str) -> int | None:
    """The F&O session close of an expiry date from OpenAlgo's market calendar (epoch ms), or
    None when it has no window for the day (the rule then closes it at 15:30 IST)."""
    try:
        from database.market_calendar_db import get_effective_session_window

        window = get_effective_session_window(datetime.fromisoformat(iso).date(), "NFO")
    except Exception:
        logger.exception("Watchlists: the market calendar could not be read for an expiry")
        return None
    return int(window["end_ms"]) if window else None


def closes_for(prepared: presets.Prepared, at_ms: int) -> dict[str, int]:
    """The calendar's close of today's expiry when a future of the list expires today. Any other
    day closes before or after now whatever the calendar says, so it is not asked."""
    today = datetime.fromtimestamp(at_ms / 1000, UTC).astimezone(IST).date().isoformat()
    if today not in prepared.expiries():
        return {}
    close = calendar_close(today)
    return {} if close is None else {today: close}


def as_of_of(snapshot) -> str | None:
    """The date of OpenAlgo's contract list: the day its session began (ISO), or None."""
    began = getattr(snapshot, "session_start", None)
    return began.date().isoformat() if isinstance(began, datetime) else None


def built_now(e: presets.Entry, at_ms: int | None = None) -> tuple[presets.Built, str | None]:
    """``(rows now, the date they are as of)``; ``failed`` in words when the master is not loaded."""
    m = master.shared()  # the process's one: a request never builds a master of its own
    gen = m.generation()
    if gen is None:
        raise OpError("failed", words.NOT_LOADED)
    prepared = prepared_for(e, gen)
    at = now_ms() if at_ms is None else at_ms
    built = e.members(prepared, at, closes_for(prepared, at))
    return built, prepared.as_of or as_of_of(m.snapshot())


def read_marks() -> dict:
    """The owner's flags and whether a symbol has a note, ``{(exchange, symbol): (flag, noted)}``.
    Every symbol that has either (the owner's own few), so a list of any length costs one query."""
    sym = schema.SymbolRow
    with engine.open_session() as s:
        return {
            (r[0], r[1]): (r[2], bool(r[3]))
            for r in s.execute(select(sym.exchange, sym.symbol, sym.flag,
                                      func.coalesce(func.length(sym.note), 0) > 0)
                               .where(sym.principal == OWNER)).all()
        }  # fmt: skip


def read_pref(ref: str) -> dict | None:
    with engine.open_session() as s:
        p = s.scalars(select(schema.PrefRow).where(
            schema.PrefRow.principal == OWNER, schema.PrefRow.ref == ref)).first()  # fmt: skip
        return None if p is None else {"sort": p.sort, "descending": p.descending,
                                       "basis": p.basis, "held": p.held}  # fmt: skip


def view(ident: str, user: str) -> dict:
    """The ``View`` record of ``preset:<ident>``."""
    e = entry_of(ident)
    built, as_of = built_now(e)
    return presets.merge(e, built, as_of=as_of, marks=read_marks(), isin=master.Master.isin,
                         pref=read_pref(e.ref))  # fmt: skip


def members(ident: str, user: str) -> dict:
    """The contracts the list holds now (the sealed route, W23): never a flag or a note. Index
    futures carry ``valid_until``, the close at which a row becomes the next month's."""
    e = entry_of(ident)
    built, _ = built_now(e)
    return {
        "ref": e.ref,
        "version": versions.read(),
        "members": [merge_view.contract(r.exchange, r.symbol, r, master.Master.isin(r))
                    for r in built.rows],
        "valid_until": built.valid_until,
        "left_out": [{"token": k, "reason": words.LEFT_OUT} for k in built.missing],
    }  # fmt: skip


def holding(exchange: str, symbol: str) -> list[tuple[str, str]]:
    """``(ref, name)`` of each built-in list that holds the symbol now, in catalogue order. A list
    that cannot be built (no master) is not counted."""
    out = []
    for e in presets.CATALOGUE:
        try:
            built, _ = built_now(e)
        except OpError:
            return []  # no master today: the sheet shows the own lists alone
        except Exception:
            logger.exception("Watchlists: the built-in lists could not be read for a symbol")
            return []
        if any(r.exchange == exchange and r.symbol == symbol for r in built.rows):
            out.append((e.ref, e.name))
    return out
