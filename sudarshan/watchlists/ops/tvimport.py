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
from sudarshan.watchlists.ops import UNCHANGED, OpError, bumps
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
        from sudarshan.watchlists import engine, master
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


# -- the import ----------------------------------------------------------------------------------
class Done(dict):
    """The ``TvDone`` record, with the refs it changed for ``ops.bumps``."""

    refs: tuple = ()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _futures_today(read: Read, chunks) -> dict:
    """``{(underlying, exchange, rule): (exchange, symbol)}`` of the contract of today for each
    future that cannot be a follow row, found through the follow rows' own resolver (W15), asked
    in slices that yield to the hub."""
    from sudarshan.watchlists import resolver, sessions
    from sudarshan.watchlists.store import rows as rows_store

    wanted = list(dict.fromkeys(
        (i.symbol, i.exchange, i.rule) for c in chunks for s in c for i in s.items
        if i.kind == "fixed_future"))  # fmt: skip
    found, memo = {}, {}

    def closes_of(fo, dates):
        key = (fo, tuple(dates))
        if key not in memo:
            memo[key] = sessions.closes(dates, fo)
        return memo[key]

    for at in range(0, len(wanted), 50):
        part = wanted[at : at + 50]
        rules = {n: rows_store.normalize(None, {
            "kind": "month_future", "underlying": u, "underlying_exchange": x,
            "expiry_rule": r}) for n, (u, x, r) in enumerate(part)}  # fmt: skip
        got = resolver.resolve_rows(
            rules, {n: {"id": n} for n in rules}, now_ms=_now_ms(), connected=False, ix=read.ix,
            row_of=read.master.row, isin=read.master.isin, closes_of=closes_of,
            is_open=lambda exchange, at_ms: True, feed=lambda keys: {},
        )  # fmt: skip
        for n, entry in enumerate(got):
            fut = entry.get("future")
            if fut:
                found[part[n]] = (fut["exchange"], fut["symbol"])
        time.sleep(0)
    return found


def _contents(chunk, future_of: dict):
    """``(keys in file order, {key: section name or None}, [(rule, section name)], names)`` of a
    list's worth of the plan; a contract met twice is kept once."""
    from sudarshan.watchlists.store import rows as rows_store

    keys, where, rules = [], {}, []
    for sec in chunk:
        for it in sec.items:
            if it.kind == "follow":
                rule = rows_store.normalize(None, {
                    "kind": "month_future", "underlying": it.symbol,
                    "underlying_exchange": it.exchange, "expiry_rule": it.rule})  # fmt: skip
                rules.append((rule, sec.name))
                continue
            key = (
                (it.exchange, it.symbol)
                if it.kind == "fixed"
                else future_of.get((it.symbol, it.exchange, it.rule))
            )
            if key is not None and key not in where:
                keys.append(key)
                where[key] = sec.name
    return keys, where, rules, _section_names(chunk)


def _overlay(lid: int, created: str, held: int, keys: list, where: dict, rules, names) -> tuple:
    """The sections, their symbols and the follow rows, in one overlay transaction that claims
    the list's version: ``(version, rows added)``. Raises ``store.Stale`` when another screen moved
    the version. A section of the list with the same name (letter case aside) is used again."""
    from sudarshan.watchlists.store import items as items_store
    from sudarshan.watchlists.store import rows as rows_store
    from sudarshan.watchlists.store import sections as store

    with engine.open_session() as s, s.begin():
        version = store.claim(s, lid, created, held)
        have = store.sections_of(s, lid)
        ids = {r.name.casefold(): r.id for r in have}
        for name in names:
            if name.casefold() not in ids:
                ids[name.casefold()] = store.add(s, lid, name, 0, store.sections_of(s, lid))
        items_store.put(s, lid, {k: ids[where[k].casefold()] if where[k] else None for k in keys},
                        replace=False)  # fmt: skip
        known = [rows_store.as_rule(r) for r in rows_store.rows_of(s, lid)]
        added = 0
        for rule, name in rules:
            if rule not in known and len(known) < rows_store.limit():
                rows_store.add(s, lid, rule, ids[name.casefold()] if name else None)
                known.append(rule)
                added += 1
    return version, added


def _make_list(user: str, name: str, keys: list) -> tuple[int, str]:
    """A new list with all its symbols in one call (one commit); its id and created key."""
    from database import watchlist_db as oa
    from sudarshan.watchlists.refs import list as listref

    try:
        made = oa.create_watchlist(user, name, [{"exchange": e, "symbol": y} for e, y in keys])
    finally:
        oa.db_session.remove()
    if made is None:
        raise OpError("failed", words.part_new([], 0, 0, [name]))
    live = listref.read_live(user, made["id"])
    return made["id"], live["created_at"] if live else ""


def _toast(names: list[str], count: int, sections: int) -> str:
    return words.done(names[0], count, sections) if len(names) == 1 else words.done_many(
        names, count, sections)  # fmt: skip


def _refs(args: dict, data: Done):
    return list(data.refs) or UNCHANGED


def _import_new(read: Read, args: dict, chunks, future_of: dict) -> Done:
    from sudarshan.watchlists.store import sections as store

    user, cap = _user(), contract.LIMITS
    names = _chunk_names(_name(args.get("name")), len(chunks))
    have, taken = _own_lists(user, names)
    if have + len(chunks) > cap["lists"]:
        raise OpError("limit", words.lists_short(len(chunks), cap["lists"] - have))
    for n in names:
        if n in taken:
            raise OpError("invalid", words.name_taken(n), "name")
    for n, c in zip(names, chunks, strict=True):
        if len(_section_names(c)) > cap["sections_per_list"]:
            raise OpError("limit", words.sections_full(n, 0, len(_section_names(c))), "text")
    ids, total, sections, version, changed = [], 0, 0, None, []
    total_keys = sum(_fixed(c) for c in chunks)
    for n, chunk in zip(names, chunks, strict=True):
        keys, where, rules, secs = _contents(chunk, future_of)
        try:
            lid, created = _make_list(user, n, keys)
        except OpError:
            said = words.part_new([names[i] for i in range(len(ids))], total, total_keys,
                                  names[len(ids) :])  # fmt: skip
            raise OpError("failed", said, changed=changed) from None
        ids.append(lid)
        changed.append(f"list:{lid}")
        total += len(keys)
        try:
            if secs or rules or any(where.values()):
                version, more = _overlay(lid, created, 0, keys, where, rules, secs)
                version, total, sections = str(version), total + more, sections + len(secs)
        except (store.Stale, Exception) as exc:
            if not isinstance(exc, store.Stale):
                from utils.logging import get_logger

                get_logger(__name__).exception(
                    "Watchlists: a TradingView import could not set its sections"
                )
            raise OpError(
                "failed", words.overlay_lost(n, len(keys), False), changed=changed
            ) from None
        time.sleep(0)
    done = Done(lists=ids, added=total, version=version if len(ids) == 1 else None,
                words=_toast(names, total, sections))  # fmt: skip
    done.refs = tuple(changed)
    return done


def _import_open(read: Read, args: dict, plan: Plan, future_of: dict) -> Done:
    from sudarshan.watchlists.ops.copy import add_all
    from sudarshan.watchlists.ops.sections import _held as held_of
    from sudarshan.watchlists.store import items as items_store
    from sudarshan.watchlists.store import sections as store

    user, live, cap = _user(), read.live, contract.LIMITS
    if "version" not in args:
        raise OpError("invalid", words.NEED_VERSION, "version")
    held, lid = held_of(args["version"]), live["id"]
    keys, where, rules, secs = _contents(plan.sections, future_of)
    have = _held(read)
    new = [k for k in keys if k not in have]
    room = cap["list_items"] - len(live["items"])
    if len(new) > room:
        raise OpError("limit", words.no_room(live["name"], room, len(new)))
    with engine.open_session() as s:
        if not items_store.current(s, lid, live["created_at"], held):
            raise OpError("conflict", words.CHANGED_ELSEWHERE)
        existing = {r.name.casefold() for r in store.sections_of(s, lid)}
    fresh = [n for n in dict.fromkeys(secs) if n.casefold() not in existing]
    if len(existing) + len(fresh) > cap["sections_per_list"]:
        raise OpError("limit", words.sections_full(live["name"], len(existing), len(fresh)))
    if not new and not rules:
        done = Done(lists=[lid], added=0, version=str(held), words=words.NOTHING_NEW)
        return done
    added = add_all(user, lid, new)
    changed, placed = ([f"list:{lid}"] if added else []), None
    version, more = str(held), 0
    if added or rules:
        try:
            got, more = _overlay(lid, live["created_at"], held, new[:added], where, rules, secs)
            version = str(got)
        except store.Stale:
            placed = words.STALE
        except Exception:
            from utils.logging import get_logger

            get_logger(__name__).exception(
                "Watchlists: a TradingView import could not set its sections"
            )
            placed = words.LOST
        if more:
            changed = [f"list:{lid}"]
    if added < len(new):
        raise OpError("failed", words.part_open(added, len(new), live["name"], placed),
                      changed=changed)  # fmt: skip
    if placed is not None:
        said = words.overlay_lost(live["name"], added, placed == words.STALE)
        raise OpError("conflict" if placed == words.STALE else "failed", said, changed=changed)
    done = Done(lists=[lid], added=added + more, version=version,
                words=words.done(live["name"], added + more, len(secs)))  # fmt: skip
    done.refs = tuple(changed)
    return done


@bumps("items", _refs)
def tv_import(args: dict) -> dict:
    text = args["text"]
    if tvimport.too_big(text):
        raise OpError("invalid", words.TOO_BIG, "text")
    if tvimport.text_hash(text) != args["hash"]:
        raise OpError("invalid", words.CHANGED, "hash")
    list_id = args.get("list")
    if list_id is not None and "name" in args:
        raise OpError("invalid", words.BOTH, "list")
    read = Read(text, list_id)
    if read.plan.matched == 0:
        raise OpError("invalid", words.NO_INDIAN, "text")
    chunks = [tuple(read.plan.sections)] if read.live else tvimport.split(read.plan)
    future_of = _futures_today(read, chunks)
    if read.live:
        return _import_open(read, args, read.plan, future_of)
    return _import_new(read, args, chunks, future_of)


HANDLERS = {"tv_preview": tv_preview, "tv_import": tv_import}
