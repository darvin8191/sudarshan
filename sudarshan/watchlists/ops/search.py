"""The ``search`` and ``strikes`` ops (M8.18, W17): the symbol search sheet and the strike picker.

Both read the process's one master generation (``master.shared()``: the reference maps built once
per snapshot in slices that yield to the hub, behind a single flight, and dropped with the snapshot)
and nothing else: no Upstox call, nothing downloaded, no thread. A request never keeps an index of
its own. The query is understood by ``search.py`` (W16, pure) and the strikes are chosen by
``dynamic.py`` (W14, pure); this module only reads the clock, the calendar and the owner's list,
and turns master rows into the ``Contract`` records the page shows (never a built name: a
contract is a row the master holds, so a side it lacks is told in words).

``search`` marks each hit ``added`` when the list asked for (``list``, an OpenAlgo list of the
owner) already holds the symbol. Hits come grouped (stocks, indices, futures, options, then
commodities), each group keeping the grammar's own order, so the page prints one heading a group.
The price (``p``) is the page's own tick of the underlying (lane F): it only orders the options by
distance from it and resolves ATM, it is never read from here.
"""

from __future__ import annotations

import time

from flask import session

from sudarshan.watchlists import dynamic, numfmt, search
from sudarshan.watchlists import view as merge_view
from sudarshan.watchlists.ops import OpError
from sudarshan.watchlists.words import presets as preset_words
from sudarshan.watchlists.words import search as words
from sudarshan.watchlists.words import strikes as strike_words
from sudarshan.watchlists.words import view as view_words
from utils.thread_safe_cache import LockedTTLCache

GROUPS = ("stocks", "indices", "futures", "options", "commodities")
MAX_QUERY = 100
MAX_SIDE = 25
#: Today's session close from the market calendar, by IST date: at most one read a minute.
_close = LockedTTLCache(maxsize=4, ttl=60)


def now_ms() -> int:
    return int(time.time() * 1000)


def _closes(now: int) -> dict[str, int]:
    """The calendar's close of today's expiry (any other day closes before or after now whatever
    the calendar says); empty when it has no window, and the rule then closes at 15:30 IST."""
    from sudarshan.watchlists.refs import preset as reader

    today = numfmt.ist_date(now).isoformat()
    got = _close.get_or_load(today, lambda: reader.calendar_close(today))
    return {} if got is None else {today: got}


def _index() -> search.Index:
    from sudarshan.watchlists import master  # not at import: the bridge imports no master

    ix = master.shared().index()
    if ix is None:
        raise OpError("failed", preset_words.NOT_LOADED)
    return ix


def _contract(row) -> dict:
    return merge_view.contract(row.exchange, row.symbol, row, search.isin_of(row.token))


def _held(lid: int | None) -> set[tuple[str, str]]:
    """``(exchange, symbol)`` of what the owner's list holds; ``gone`` when it is not his."""
    if lid is None:
        return set()
    from sudarshan.watchlists.refs import list as list_reader

    live = list_reader.read_live(session.get("user") or "", lid)
    if live is None:
        raise OpError("gone", view_words.LIST_GONE)
    return {(exchange, symbol) for _, exchange, symbol, _ in live["items"]}


def _price(args: dict) -> float | None:
    p = args.get("p")
    if p is not None and not p > 0:
        raise OpError("invalid", strike_words.PRICE_BAD, "p")
    return p


def search_op(args: dict) -> dict:
    query = (args.get("q") or "").strip()
    if len(query) > MAX_QUERY:
        raise OpError("invalid", strike_words.QUERY_LONG, "q")
    limit = args.get("limit", search.MAX_HITS)
    if not 1 <= limit <= search.MAX_HITS:
        raise OpError("invalid", strike_words.LIMIT_BAD, "limit")
    segment, price = args.get("seg", "all"), _price(args)
    if not query:
        return {"understood": "", "hits": [], "underlying": None, "suggestion": None}
    held = _held(args.get("list"))
    ix, now = _index(), now_ms()
    closes = _closes(now)
    got = search.search(ix, query, price=price, now_ms=now, segment=segment, limit=limit,
                        closes=closes)  # fmt: skip
    rank = {g: n for n, g in enumerate(GROUPS)}
    hits = sorted(got.hits, key=lambda h: rank[h[0]])  # stable: each group keeps its order
    answer = {
        "understood": words.understood_line(got.understood),
        "hits": [
            {"group": g, "title": words.title(r), "detail": words.detail(r),
             "contract": _contract(r), "added": (r.exchange, r.symbol) in held}
            for g, r in hits
        ],
        "underlying": got.underlying,
        "suggestion": None,
    }  # fmt: skip
    if not hits and not got.understood.needs_price:
        elsewhere = segment != "all" and search.search(
            ix, query, price=price, now_ms=now, closes=closes, limit=1).hits  # fmt: skip
        answer["suggestion"] = (
            strike_words.segment_empty(segment, query)
            if elsewhere
            else words.no_match(query, got.suggestions)
        )
    return answer


def _month_ends(expiries) -> set[str]:
    last: dict[str, str] = {}
    for e in sorted(expiries):
        last[e[:7]] = e
    return set(last.values())


def strikes_op(args: dict) -> dict:
    n = args.get("n", 10)
    if not 0 <= n <= MAX_SIDE:
        raise OpError("invalid", strike_words.STRIKES_BAD, "n")
    price = _price(args)
    ix, now = _index(), now_ms()
    name = ix.display.get(search.key_of(args["u"]), args["u"])
    by = ix.options.get(name)
    if not by:
        raise OpError("gone", strike_words.no_options(name), "u")
    cash = {r.exchange for r in ix.cash.get(search.key_of(name), ())}
    listed = cash or {rows[0].exchange for rows in by.values() if rows}
    if args["x"] not in listed:
        raise OpError("invalid", strike_words.EXCHANGE_BAD, "x")
    closes = _closes(now)
    live = sorted(e for e in by if dynamic.close_ms(e, closes) > now)
    answer = {"underlying": name, "exchange": args["x"], "expiries": [], "expiry": None,
              "atm": None, "strikes": [], "words": strike_words.no_open_expiry(name)}  # fmt: skip
    if not live:
        return answer
    ends = _month_ends(by)
    answer["expiries"] = [
        {"date": e, "label": strike_words.expiry_label(e, i == 0, e in ends), "month": e in ends}
        for i, e in enumerate(live)
    ]
    chosen = args.get("exp") or live[0]
    if chosen not in live:
        raise OpError("invalid", strike_words.EXPIRY_GONE, "exp")
    sides: dict[float, dict[str, object]] = {}
    for r in by[chosen]:
        if r.strike is not None:
            sides.setdefault(r.strike, {}).setdefault(r.instrumenttype, r)
    ladder = sorted(sides)
    lines = []
    if price is not None and ladder:
        centre = dynamic.atm_strike(price, ladder)
        mid = ladder.index(centre)
    else:
        centre, mid = None, len(ladder) // 2
        lines.append(strike_words.NO_PRICE)
    shown = ladder[max(0, mid - n) : mid + n + 1]
    absent = []
    rows = []
    for s in shown:
        pair = sides[s]
        rows.append({"strike": s, "atm": s == centre,
                     "ce": _contract(pair["CE"]) if "CE" in pair else None,
                     "pe": _contract(pair["PE"]) if "PE" in pair else None})  # fmt: skip
        absent += [(s, side) for side in ("CE", "PE") if side not in pair]
    close = dynamic.close_ms(chosen, closes)
    if chosen == numfmt.ist_date(now).isoformat():
        lines.insert(0, strike_words.ends_today(close))
    if absent:
        lines.append(strike_words.missing_sides(absent))
    answer.update(expiry=chosen, atm=centre, strikes=rows, words=" ".join(lines))
    return answer


HANDLERS = {"search": search_op, "strikes": strikes_op}
