"""The TradingView import ops (M8.12, W19): ``tv_preview`` and ``tv_import``.

``tv_preview`` reads the text with W18's mapping against the master and says what an import would
make; it writes nothing. The 64 KiB limit is said before anything else is read. It answers the
counts, what was left out and why, the conversions, the room in the open list (``list``), the
split over 250 and OpenAlgo's 50 lists, and a hash of the text.

``tv_import`` takes the same text and the preview's hash and refuses when they differ. It parses
again, so what is written is what the preview said, and writes into new lists (named from the file
name, ``name``) or into the open list (``list`` and the version ``view`` answered). Everything
that can be refused is refused first, with its numbers. This module starts no thread, timer or
loop: a request, a parse of at most 64 KiB, and OpenAlgo's own functions, yielding to the hub
between its commits.
"""

from __future__ import annotations

import time

from flask import session

from sudarshan.watchlists import contract, engine, tvimport
from sudarshan.watchlists.ops import OpError
from sudarshan.watchlists.tvimport import Cut, Plan, Sec
from sudarshan.watchlists.words import arrange as arrange_words
from sudarshan.watchlists.words import rows as rows_words
from sudarshan.watchlists.words import tvimport as words
from sudarshan.watchlists.words import view as view_words

NAME_MAX = 64  # the width of OpenAlgo's list name
MAX_ID = 2**31 - 1


def _user() -> str:
    return session.get("user") or ""


def _fo(exchange: str) -> str:
    return "NFO" if exchange.startswith("NSE") else "BFO"


def _name(raw) -> str:
    name = (raw or "").strip() or words.NAME_DEFAULT
    if len(name) > NAME_MAX:
        raise OpError("invalid", words.NAME_LONG, "name")
    return name


def _chunk_names(name: str, n: int) -> list[str]:
    """``Swing`` for one list; ``Swing (1)``, ``Swing (2)`` for several, cut to fit 64 letters."""
    if n == 1:
        return [name]
    out = []
    for i in range(1, n + 1):
        tail = f" ({i})"
        out.append(name[: NAME_MAX - len(tail)].rstrip() + tail)
    return out


def _futureless(plan: Plan, ix) -> Plan:
    """A row that follows a future, or is a future, for an underlying with no futures on its
    derivatives exchange is left out in words, not stored as a row that can never resolve."""
    cuts, secs, follow, rolling = list(plan.cuts), [], plan.follow, plan.not_rolling
    for sec in plan.sections:
        keep = []
        for it in sec.items:
            if it.kind in ("follow", "fixed_future") and not any(
                r.exchange == _fo(it.exchange) for _, r in ix.futures.get(it.symbol, ())
            ):
                bang = "1!" if it.rule == "month" else "2!"
                prefix = "NSE" if it.exchange.startswith("NSE") else "BSE"
                cuts.append(Cut(f"{prefix}:{it.symbol}{bang}", "no_futures"))
                follow, rolling = follow - (it.kind == "follow"), rolling - (it.kind != "follow")
                continue
            keep.append(it)
        secs.append(Sec(sec.name, tuple(keep)))
    return plan._replace(sections=tuple(secs), cuts=tuple(cuts), follow=follow, not_rolling=rolling)


class Read:
    """What one reading of the text found: the plan, the open list when there is one, and the
    master's index (kept for the length of the request, never copied)."""

    def __init__(self, text: str, list_id: int | None):
        from sudarshan.watchlists import master
        from sudarshan.watchlists.refs import list as listref
        from sudarshan.watchlists.store import rows as rows_store

        if tvimport.too_big(text):
            raise OpError("invalid", words.TOO_BIG, "text")
        m = master.shared()
        ix = m.index() if m.loaded() else None
        if ix is None:
            raise OpError("failed", rows_words.NOT_LOADED)
        self.live, self.follow_used = None, 0
        if list_id is not None:
            live = listref.read_live(_user(), list_id) if 0 < list_id <= MAX_ID else None
            if live is None:
                raise OpError("gone", view_words.LIST_GONE)
            self.live = live
            with engine.open_session() as s:
                self.follow_used = rows_store.count(s, list_id)
        room = max(0, contract.LIMITS["rows_per_list"] - self.follow_used)
        self.master, self.ix = m, ix
        self.plan = _futureless(tvimport.parse(text, m.known, room), ix)


def _own_lists(user: str, names: list[str]) -> tuple[int, set[str]]:
    """How many lists the user has and which of ``names`` are taken, in two small queries through
    OpenAlgo's models (never every list with its items)."""
    from sqlalchemy import func

    from database import watchlist_db as oa

    lists = oa.Watchlist
    try:
        have = oa.db_session.query(func.count(lists.id)).filter(lists.user_id == user).scalar()
        rows = oa.db_session.query(lists.name).filter(
            lists.user_id == user, lists.name.in_(names)).all()  # fmt: skip
    finally:
        oa.db_session.remove()
    return int(have or 0), {r[0] for r in rows}


def _count(chunk) -> int:
    return sum(len(s.items) for s in chunk)


def _fixed(chunk) -> int:
    return sum(1 for s in chunk for i in s.items if i.kind != "follow")


def _held(read: Read) -> set:
    return {(e, y) for _, e, y, _ in read.live["items"]}


def _section_names(chunk) -> list[str]:
    return [s.name for s in chunk if s.name is not None]


def tv_preview(args: dict) -> dict:
    text = args["text"]
    read = Read(text, args.get("list"))
    plan = read.plan
    out = {"hash": tvimport.text_hash(text), "sections": plan.found_sections,
           "symbols": plan.found_symbols, "matched": plan.matched, "follow": plan.follow,
           "left_out": [{"token": c.token, "reason": c.reason} for c in plan.cuts],
           "lists": [], "words": words.preview(plan)}  # fmt: skip
    if plan.matched == 0:
        return out
    said, cap = out["words"], contract.LIMITS
    if read.live is not None:
        name, room = read.live["name"], cap["list_items"] - len(read.live["items"])
        said.append(words.room_line(name, room))
        fresh = _fixed(tuple(plan.sections)) - len(
            _held(read)
            & {(i.exchange, i.symbol) for s in plan.sections for i in s.items if i.kind == "fixed"}
        )
        if fresh > room:
            said.append(words.no_room(name, room, fresh))
        out["lists"] = [{"name": name, "count": plan.matched}]
        return out
    chunks = tvimport.split(plan)
    name = _name(args.get("name"))
    names = _chunk_names(name, len(chunks))
    have, taken = _own_lists(_user(), names)
    line = words.split_line(name, [_fixed(c) for c in chunks])
    if line:
        said.append(line)
    if have + len(chunks) > cap["lists"]:
        said.append(words.lists_short(len(chunks), cap["lists"] - have))
    said += [words.name_taken(n) for n in names if n in taken]
    for n, c in zip(names, chunks, strict=True):
        if len(_section_names(c)) > cap["sections_per_list"]:
            said.append(words.sections_full(n, 0, len(_section_names(c))))
    out["lists"] = [{"name": n, "count": _count(c)} for n, c in zip(names, chunks, strict=True)]
    return out


HANDLERS = {"tv_preview": tv_preview}
