"""The follow-row ops (M8.10, M8.11, W15): ``row_add``, ``row_edit``, ``row_remove`` and
``resolve``.

A row is stored beside its list's sections under the list's version, as a section is: one
transaction in the overlay's file whose first statement claims the version (``store.sections.claim``),
so a stale version is ``conflict`` and nothing changes. A write that would change nothing rolls the
claim back with it and answers the version it was given, with no bump and no push. Rows are never
OpenAlgo items, so ``openalgo.db`` is never written. ``resolve`` is below the writes.
"""

from __future__ import annotations

import time

from flask import session

from sudarshan.watchlists import engine
from sudarshan.watchlists.ops import UNCHANGED, OpError, bumps
from sudarshan.watchlists.store import rows as store
from sudarshan.watchlists.store import sections as sections_store
from sudarshan.watchlists.words import rows as words
from sudarshan.watchlists.words import sections as section_words
from sudarshan.watchlists.words import view as view_words

MAX_VERSION = 2**63 - 1
MAX_ID = 2**31 - 1


class Done(dict):
    """The ``Done`` record, with the list it changed for ``ops.bumps``; ``same`` when the write
    changed nothing."""

    ref: str = ""
    same: bool = False


class _Same(Exception):
    """Raised inside the transaction when the write would change nothing: the claim rolls back."""


def _held(text: str) -> int:
    if not text.isascii() or not text.isdigit() or int(text) > MAX_VERSION:
        raise OpError("conflict", section_words.CONFLICT)
    return int(text)


def _created(list_id: int) -> str:
    from sudarshan.watchlists.refs import list as listref

    live = listref.read_live(session.get("user") or "", list_id) if 0 < list_id <= MAX_ID else None
    if live is None:
        raise OpError("gone", view_words.LIST_GONE)
    return live["created_at"]


def _done(version: int, ident: int, list_id: int, same: bool = False) -> Done:
    done = Done(version=str(version), id=ident, words=words.same_row() if same else None)
    done.ref, done.same = f"list:{list_id}", same
    return done


def _ref(args: dict, data: Done):
    return UNCHANGED if data.same else [data.ref]


def _check_master(rule: dict) -> None:
    """The underlying must be in today's master and have what the row follows. Raises OpError."""
    from sudarshan.watchlists import master  # imported when used: the bridge loads no master

    m = master.shared()
    if not m.loaded():
        raise OpError("failed", words.NOT_LOADED)
    name, exch = rule["underlying"], rule["underlying_exchange"]
    if not m.known(exch, name):
        raise OpError("invalid", words.not_listed(name), "underlying")
    from sudarshan.watchlists.resolver import fo_exchange

    ix, fo = m.index(), fo_exchange(exch)
    if ix is None:
        raise OpError("failed", words.NOT_LOADED)
    if rule["kind"] == "month_future":
        if not any(r.exchange == fo for _, r in ix.futures.get(name, ())):
            raise OpError("invalid", words.no_futures(name, fo), "underlying")
    elif not any(r.exchange == fo for rows in ix.options.get(name, {}).values() for r in rows):
        raise OpError("invalid", words.no_options(name, fo), "underlying")


def _rule(base: dict | None, patch: dict) -> dict:
    try:
        return store.normalize(base, patch)
    except store.Bad as bad:
        raise OpError("invalid", bad.message, bad.field) from None


def _claim(s, list_id: int, created: str, held: int) -> int:
    try:
        return sections_store.claim(s, list_id, created, held)
    except sections_store.Stale:
        raise OpError("conflict", section_words.CONFLICT) from None


def _section(s, list_id: int, section_id) -> int | None:
    if section_id is not None and not store.section_exists(s, list_id, section_id):
        raise OpError("gone", section_words.SECTION_GONE)
    return section_id


@bumps("rows", _ref)
def row_add(args: dict) -> dict:
    held, list_id = _held(args["version"]), args["list"]
    rule = _rule(None, args)
    _check_master(rule)
    created = _created(list_id)
    try:
        with engine.open_session() as s, s.begin():
            version = _claim(s, list_id, created, held)
            section = _section(s, list_id, args.get("section_id"))
            have = store.rows_of(s, list_id)
            twin = next((r for r in have if store.as_rule(r) == rule
                         and r.section_id == section), None)  # fmt: skip
            if twin is not None:
                ident = twin.id
                raise _Same
            if len(have) >= store.limit():
                raise OpError("limit", words.ROWS_FULL)
            ident = store.add(s, list_id, rule, section)
    except _Same:
        return _done(held, ident, list_id, same=True)
    return _done(version, ident, list_id)


def _row_list(row_id: int):
    if not 0 < row_id <= MAX_ID:
        raise OpError("gone", words.ROW_GONE)
    with engine.open_session() as s:
        row = store.find(s, row_id)
        if row is None:
            raise OpError("gone", words.ROW_GONE)
        return row.list_id


@bumps("rows", _ref)
def row_edit(args: dict) -> dict:
    if not any(k in args for k in (*store.FIELDS, "section_id")):
        raise OpError("invalid", words.NOTHING, "kind")
    held, ident = _held(args["version"]), args["id"]
    list_id = _row_list(ident)
    created = _created(list_id)
    try:
        with engine.open_session() as s, s.begin():
            version = _claim(s, list_id, created, held)
            row = store.find(s, ident)
            if row is None or row.list_id != list_id:
                raise OpError("gone", words.ROW_GONE)
            before = store.as_rule(row)
            rule = _rule(before, args)
            if any(rule[k] != before[k] for k in ("kind", "underlying", "underlying_exchange")):
                _check_master(rule)
            section = (_section(s, list_id, args["section_id"]) if "section_id" in args
                       else row.section_id)  # fmt: skip
            if rule == before and section == row.section_id:
                raise _Same
            if any(
                r.id != ident and store.as_rule(r) == rule and r.section_id == section
                for r in store.rows_of(s, list_id)
            ):
                raise OpError("invalid", words.same_row(), "kind")
            store.change(s, row, rule, section)
    except _Same:
        return _done(held, ident, list_id, same=True)
    return _done(version, ident, list_id)


@bumps("rows", _ref)
def row_remove(args: dict) -> dict:
    held, ident = _held(args["version"]), args["id"]
    list_id = _row_list(ident)
    created = _created(list_id)
    with engine.open_session() as s, s.begin():
        version = _claim(s, list_id, created, held)
        row = store.find(s, ident)
        if row is None or row.list_id != list_id:
            raise OpError("gone", words.ROW_GONE)
        store.remove(s, row)
    return _done(version, ident, list_id)


MAX_RESOLVE = 100


def _now_ms() -> int:
    return int(time.time() * 1000)


def _drafts(raw: list, base: int) -> tuple[dict, dict]:
    """``(rules, items)`` of the draft rows, under the ids ``base``, ``base + 1``, ... (above any
    id the page sent, so none is taken for a stored row), each checked as ``row_add`` checks a row: the rule, then the master. Raises ``OpError`` with
    the field of the draft that is wrong."""
    rules, items = {}, {}
    for n, draft in enumerate(raw, base):
        rule = _rule(None, draft)
        _check_master(rule)
        rules[n] = rule
        items[n] = {"id": n, **{k: draft.get(k) for k in ("price", "price_at", "source", "held")}}
    return rules, items


def resolve(args: dict) -> dict:
    """Follow rows to contracts, now (a read: it changes nothing and bumps nothing). ``rows`` is
    what the page knows of each row it asks about: its price and where it came from, and the
    strike the row last showed (``held``); ``connected`` says whether the page's socket is up.

    ``drafts`` are rows the page has not stored yet (its add form): the fields of ``row_add``
    without ``list`` and ``version``, and the page's price as for a stored row. They are answered
    in the same shape, in the order sent, under ``drafts`` with ``id`` 0, and nothing is written.
    They count with the stored rows in the limit of 100."""
    from sudarshan.watchlists import feedsnap, master, resolver, sessions

    items = {}
    for item in args["rows"]:
        items.setdefault(item["id"], item)
    drafts = args.get("drafts")
    if len(items) > MAX_RESOLVE:
        raise OpError("invalid", words.TOO_MANY, "rows")
    if len(items) + len(drafts or ()) > MAX_RESOLVE:
        raise OpError("invalid", words.TOO_MANY, "drafts")
    with engine.open_session() as s:
        found = store.find_many(s, list(items))
        rules = {i: store.as_rule(r) for i, r in found.items()}
    base = max([MAX_ID, *items]) + 1
    draft_rules, draft_items = _drafts(drafts or [], base)
    rules.update(draft_rules)
    items.update(draft_items)
    m = master.shared()
    if rules and not m.loaded():
        raise OpError("failed", words.NOT_LOADED)
    ix = m.index() if rules else None
    if rules and ix is None:
        raise OpError("failed", words.NOT_LOADED)
    now = _now_ms()
    answered = resolver.resolve_rows(
        rules, items, now_ms=now, connected=args["connected"], ix=ix,
        row_of=m.row, isin=m.isin, closes_of=lambda fo, dates: sessions.closes(dates, fo),
        is_open=sessions.market_open, feed=feedsnap.read,
    )  # fmt: skip
    out = {"rows": [r for r in answered if r["id"] < base]}
    if drafts is not None:
        out["drafts"] = [{**r, "id": 0} for r in answered if r["id"] >= base]
    return out


HANDLERS = {
    "row_add": row_add,
    "row_edit": row_edit,
    "row_remove": row_remove,
    "resolve": resolve,
}
