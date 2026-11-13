"""The reader of ``list:<id>`` refs (M8.4, W6): OpenAlgo's own lists plus the overlay.

OpenAlgo's list and its items are read in process through ``database/watchlist_db.py``, read only
(its models and its scoped session, which the app's teardown also releases). The overlay comes from
the store, in one read-only session: the list's guard row, sections, section membership, follow
rows (rules, not resolved: resolving is W15), the flag of each symbol and whether it has a note
(never the note), and the list's preference. One session is not one transaction: pysqlite begins
none for reads, so the rows after the guard row can come from a later commit than it. That is safe
as written, because the guard's version is read first: a write in between makes the version the
page holds older than the rows it shows, so its next write is a ``conflict`` and it reads again,
never a write decided on a wrong merge. A reader that needs one snapshot must begin a transaction
itself. The merge itself is
``view.merge`` (imported here as ``merge_view``, because this module's own ``view`` is the
interface of ``refs/__init__``), which is pure. Nothing is written: a stale overlay (see the guard in ``sudarshan/watchlists/view.py``)
is left for the next write that touches the list to prune.
"""

from __future__ import annotations

import time

from sqlalchemy import func, select, tuple_

from sudarshan.watchlists import engine, master, schema, versions
from sudarshan.watchlists import members as merge_members
from sudarshan.watchlists import view as merge_view
from sudarshan.watchlists.ops import OpError
from sudarshan.watchlists.schema import OWNER
from sudarshan.watchlists.words import members as members_words
from sudarshan.watchlists.words import view as words

KIND = "list"
MAX_ID = 2**31 - 1


def now_ms() -> int:
    return int(time.time() * 1000)


def list_id(ident: str) -> int:
    """``7`` to 7; anything else is not a list's id."""
    if not ident.isascii() or not ident.isdigit() or not 0 < int(ident) <= MAX_ID:
        raise OpError("invalid", field="ref")
    return int(ident)


def read_live(user: str, lid: int) -> dict | None:
    """OpenAlgo's list ``lid`` of ``user`` with its items, or None when it does not exist."""
    from database import watchlist_db as oa

    try:
        found = oa.db_session.query(oa.Watchlist).filter_by(id=lid, user_id=user).first()
        if found is None:
            return None
        return {
            "id": found.id,
            "name": found.name,
            "created_at": merge_view.created_key(found.created_at),
            "items": [(i.id, i.exchange, i.symbol, i.position) for i in found.items],
        }
    finally:
        oa.db_session.remove()  # a read: nothing to commit, and the connection goes back at once


def holding(user: str, exchange: str, symbol: str) -> list[tuple[int, str]]:
    """``(id, name)`` of each of ``user``'s lists that holds the symbol, in list order. Built-in
    lists are not here: they join the symbol sheet with W12."""
    from database import watchlist_db as oa

    try:
        return [(r[0], r[1]) for r in oa.db_session.query(oa.Watchlist.id, oa.Watchlist.name)
                .join(oa.WatchlistItem, oa.WatchlistItem.watchlist_id == oa.Watchlist.id)
                .filter(oa.Watchlist.user_id == user, oa.WatchlistItem.symbol == symbol,
                        oa.WatchlistItem.exchange == exchange)
                .order_by(oa.Watchlist.id).all()]  # fmt: skip
    finally:
        oa.db_session.remove()


def read_overlay(lid: int, items: list) -> dict:
    """What the store holds for the list and for its symbols, in one read-only session, the guard
    row first (not one transaction; see the module's note on why that is safe)."""
    ref, out = f"list:{lid}", {"oa_created_at": None, "version": None}
    with engine.open_session() as s:
        guard = s.execute(select(schema.ListRow.oa_created_at, schema.ListRow.version).where(
            schema.ListRow.principal == OWNER, schema.ListRow.list_id == lid)).first()  # fmt: skip
        if guard is not None:
            out["oa_created_at"], out["version"] = guard
        pairs = [(exchange, symbol) for _, exchange, symbol, _ in items]
        sym = schema.SymbolRow
        out["symbols"] = {
            (r[0], r[1]): (r[2], bool(r[3]))
            for r in s.execute(select(sym.exchange, sym.symbol, sym.flag,
                                      func.coalesce(func.length(sym.note), 0) > 0)
                               .where(sym.principal == OWNER,
                                      tuple_(sym.exchange, sym.symbol).in_(pairs))).all()
        } if pairs else {}  # fmt: skip
        pref = schema.PrefRow  # a preference is kept for a list that has no guard row yet (W10)
        p = s.scalars(select(pref).where(pref.principal == OWNER, pref.ref == ref)).first()
        out["pref"] = None if p is None else {"sort": p.sort, "descending": p.descending,
                                              "basis": p.basis, "held": p.held}  # fmt: skip
        if guard is None:
            return out
        sec, item, fol = schema.SectionRow, schema.SectionItemRow, schema.FollowRow
        out["sections"] = [
            {"id": r.id, "name": r.name, "mark": r.mark, "position": r.position}
            for r in s.scalars(select(sec).where(sec.principal == OWNER, sec.list_id == lid))
        ]
        out["items"] = {
            (r.exchange, r.symbol): r.section_id
            for r in s.scalars(select(item).where(item.principal == OWNER, item.list_id == lid))
        }
        out["follow"] = [
            {"id": r.id, "kind": r.kind, "section_id": r.section_id, "position": r.position,
             "underlying": r.underlying, "underlying_exchange": r.underlying_exchange,
             "expiry_rule": r.expiry_rule, "strike_rule": r.strike_rule, "offset": r.offset,
             "strike_paise": r.strike_paise, "opt_right": r.opt_right}
            for r in s.scalars(select(fol).where(fol.principal == OWNER, fol.list_id == lid))
        ]  # fmt: skip
    return out


def view(ident: str, user: str) -> dict:
    """The ``View`` record of ``list:<ident>``; ``gone`` when the list does not exist."""
    lid = list_id(ident)
    live = read_live(user, lid)
    if live is None:
        raise OpError("gone", words.LIST_GONE)
    return merged(live)


def merged(live: dict) -> dict:
    """The ``View`` record of a list already read from OpenAlgo."""
    overlay = read_overlay(live["id"], live["items"])
    m = master.shared()  # the process's one: a request never keeps a master of its own
    return merge_view.merge(live, overlay, m.row, loaded=m.loaded(), isin=m.isin)


def split_ident(ident: str) -> tuple[int, int | None]:
    """``7`` to ``(7, None)``, ``7/section:3`` to ``(7, 3)``; anything else is not a list ref."""
    head, sep, tail = ident.partition("/")
    if not sep:
        return list_id(head), None
    name, colon, number = tail.partition(":")
    if name != "section" or not colon:
        raise OpError("invalid", field="ref")
    return list_id(head), list_id(number)  # a section id is a whole number of the same range


def month_futures(follow: list[dict]) -> dict[int, dict]:
    """Each month-future follow row to a contract: ``{row id: Resolved}``, read now. The row's own
    rule and the master decide (the same code as ``resolve``); no price is needed or asked."""
    from sudarshan.watchlists import resolver, sessions

    rules = {f["id"]: f for f in follow if f["kind"] == "month_future"}
    if not rules:
        return {}
    m = master.shared()
    ix = m.index()
    if ix is None:
        raise OpError("failed", members_words.NOT_LOADED)
    got = resolver.resolve_rows(
        rules, {i: {"id": i} for i in rules}, now_ms=now_ms(), connected=False,
        ix=ix, row_of=m.row, isin=m.isin, closes_of=lambda fo, dates: sessions.closes(dates, fo),
        is_open=sessions.market_open, feed=lambda keys: {},
    )  # fmt: skip
    return {r["id"]: r for r in got}


def members(ident: str, user: str) -> dict:
    """The contracts the list (or one section of it) holds today (the sealed route, W23): fixed
    symbols the master lists, and the future each month-future row follows now. An at-the-money or
    a strike row is left out in words, a symbol the master lacks too. Never a flag or a note.
    ``created_at`` is the list's identity: OpenAlgo gives a deleted list's id to the next one."""
    lid, sid = split_ident(ident)
    live = read_live(user, lid)
    if live is None:
        raise OpError("gone", words.LIST_GONE)
    got = merged(live)
    if sid is not None and sid not in {s["id"] for s in got["sections"]}:
        raise OpError("gone", words.LIST_GONE)
    order = (None, *(s["id"] for s in got["sections"])) if sid is None else (sid,)
    follow = [f for f in got["follow"] if f["section_id"] in order]
    resolved = month_futures(follow)
    members, cuts = [], []
    for where in order:
        for f in (f for f in follow if f["section_id"] == where):
            found = merge_members.follow_cut(f)
            if found is not None:
                cuts.append(found)
                continue
            r = resolved[f["id"]]
            future = r.get("future")
            if future is None:
                cuts.append(
                    merge_members.cut(f["underlying"], "missing", members_words.NO_CONTRACT)
                )
            else:
                members.append(future)
        rows = [r for r in got["rows"] if r["section_id"] == where]
        more, gone = merge_members.from_rows(rows)
        members += more
        cuts += gone
    ref = f"list:{lid}" if sid is None else f"list:{lid}/section:{sid}"
    return merge_members.record(ref, versions.read(), members, cuts, created_at=live["created_at"])
