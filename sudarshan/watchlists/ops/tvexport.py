"""The TradingView export op (M8.12, W20): ``tv_export``.

Answers the text TradingView imports for one ref: an own list (``list:7``), one section of it
(``list:7/section:3``), a built-in list (``preset:<id>``), a flag list (``flag:<n>``) or a ranked
list (``ranked:...``), read as ``view`` reads them. The mapping is ``tvexport.py``'s (W18's in
reverse). A read: it writes nothing and bumps nothing, and is of the contract's ``read`` class.

The master is asked only when a row needs it: a derivative contract (which of its details the
export needs), a follow row on an at-the-money or a strike (resolved as ``resolve`` does, from the
feed's last price, to the contract it shows today), or a dated future (to learn whether it is
the current-month or next-month contract, through the same resolver as a month-future row).
"""

from __future__ import annotations

import re
import time

from flask import session

from sudarshan.watchlists import refs
from sudarshan.watchlists.ops import OpError
from sudarshan.watchlists.tvexport import Block, Entry, build
from sudarshan.watchlists.words import rows as rows_words
from sudarshan.watchlists.words import tvexport as words
from sudarshan.watchlists.words import view as view_words

_CASH = ("NSE", "BSE", "NSE_INDEX", "BSE_INDEX")
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_FILE_MAX = 64


def _user() -> str:
    return session.get("user") or ""


def file_name(name: str) -> str:
    """The list's name as a file name: its own letters, without what a file name cannot hold."""
    clean = " ".join(_UNSAFE.sub(" ", name).split()).strip(". ")[:_FILE_MAX].strip()
    return (clean or words.NO_FILE_NAME) + ".txt"


def _read(ref: str) -> tuple[dict, str, int | None]:
    """``(the View record, the name for the file, a section id)`` of the ref."""
    from sudarshan.watchlists.refs import list as listref

    parts = refs.split(ref)
    if parts is None:
        raise OpError("invalid", field="ref")
    if parts[0] == listref.KIND:
        lid, sid = listref.split_ident(parts[1])
        live = listref.read_live(_user(), lid)
        if live is None:
            raise OpError("gone", view_words.LIST_GONE)
        got = listref.merged(live)
        if sid is None:
            return got, got["name"], None
        found = [s for s in got["sections"] if s["id"] == sid]
        if not found:
            raise OpError("gone", view_words.LIST_GONE)
        return got, f"{got['name']} - {found[0]['name']}", sid
    found_reader = refs.reader_of(ref)
    if found_reader is None:
        raise OpError("invalid", field="ref")
    reader, ident = found_reader
    got = reader.view(ident, _user())
    return got, got["name"], None


def _now_ms() -> int:
    return int(time.time() * 1000)


def _resolve(rules: dict[int, dict], *, price: bool) -> dict[int, dict]:
    """``{id: Resolved}`` of follow rules, now; the feed's last price is read only when ``price``."""
    from sudarshan.watchlists import feedsnap, master, resolver, sessions

    m = master.shared()
    ix = m.index() if m.loaded() else None
    if ix is None:
        raise OpError("failed", rows_words.NOT_LOADED)
    got = resolver.resolve_rows(
        rules, {i: {"id": i} for i in rules}, now_ms=_now_ms(), connected=False,
        ix=ix, row_of=m.row, isin=m.isin, closes_of=lambda fo, dates: sessions.closes(dates, fo),
        is_open=sessions.market_open, feed=feedsnap.read if price else (lambda keys: {}),
    )  # fmt: skip
    return {r["id"]: r for r in got}


def _entry(row, contract: dict, *, today: bool = False) -> Entry:
    """The entry of a master row (None: only its contract record is known)."""
    return Entry(
        "row", contract["exchange"], contract["symbol"], None,
        getattr(row, "instrumenttype", None), contract.get("expiry"),
        getattr(row, "strike", None) or None,
        getattr(row, "name", None) or None, None, today,
    )  # fmt: skip


def _blocks(got: dict, only: int | None) -> list[Block]:
    from sudarshan.watchlists import master

    sections = got.get("sections") or []
    order = (None, *(s["id"] for s in sections)) if only is None else (only,)
    names = {s["id"]: s["name"] for s in sections}
    follow = [f for f in got.get("follow") or [] if f["section_id"] in order]
    rows = [r for r in got["rows"] if r["section_id"] in order]
    fixed_rule = all(
        f["kind"] == "month_future" and f["expiry_rule"] in ("month", "next_month") for f in follow
    )
    derivs = any(r["contract"]["exchange"] not in _CASH for r in rows)
    m = master.shared() if (derivs or not fixed_rule) else None
    if m is not None and not m.loaded():
        raise OpError("failed", rows_words.NOT_LOADED)

    # month futures the file may need today: the follow rows of other rules and every dated future
    need: dict[int, dict] = {}
    for f in follow:
        if f["kind"] == "month_future" and f["expiry_rule"] not in ("month", "next_month"):
            need[f["id"]] = f
    atm = {f["id"]: f for f in follow if f["kind"] != "month_future"}
    fut_rows = {}
    for n, r in enumerate(rows):
        c = r["contract"]
        row = (
            m.row(c["exchange"], c["symbol"])
            if m is not None and c["exchange"] not in _CASH
            else None
        )
        if row is not None and row.instrumenttype == "FUT" and c["exchange"] in ("NFO", "BFO"):
            fut_rows[n] = row
    today: dict[tuple[str, str, str], str | None] = {}
    asks = {}
    for row in fut_rows.values():
        base = "NSE" if row.exchange == "NFO" else "BSE"
        for rule in ("month", "next_month"):
            if (row.exchange, row.name, rule) in asks:
                continue
            asks[(row.exchange, row.name, rule)] = {
                "id": 1_000_000 + len(asks), "kind": "month_future", "underlying": row.name,
                "underlying_exchange": base, "expiry_rule": rule, "strike_rule": None,
                "offset": None, "strike": None, "opt_right": None,
            }  # fmt: skip
    if asks:
        got_f = _resolve({a["id"]: a for a in asks.values()}, price=False)
        for key, a in asks.items():
            fut = got_f[a["id"]].get("future")
            today[key] = fut["symbol"] if fut else None
    resolved = _resolve({**need, **atm}, price=True) if (need or atm) else {}
    # the follow rows to contracts need the master's row for each contract
    out = []
    for where in order:
        entries: list[Entry] = []
        cuts: list[tuple[str, str]] = []
        for f in sorted(
            (f for f in follow if f["section_id"] == where), key=lambda f: f["position"]
        ):
            if f["kind"] == "month_future" and f["expiry_rule"] in ("month", "next_month"):
                entries.append(
                    Entry("follow", f["underlying_exchange"], f["underlying"], f["expiry_rule"])
                )
                continue
            r = resolved.get(f["id"], {})
            made = [c for c in (r.get("future"), r.get("ce"), r.get("pe")) if c]
            if not made:
                cuts.append(
                    (f["underlying"], "no_contract" if r.get("state") == "missing" else "waiting")
                )
            for c in made:
                row = m.row(c["exchange"], c["symbol"])
                e = _entry(row, c, today=f["kind"] != "month_future")
                entries.append(_with_cont(e, row, today))
        for r in rows:
            if r["section_id"] != where:
                continue
            c = r["contract"]
            if r["expired"]:
                cuts.append((c["key"], "expired"))
                continue
            row = (
                m.row(c["exchange"], c["symbol"])
                if m is not None and c["exchange"] not in _CASH
                else None
            )
            entries.append(_with_cont(_entry(row, c), row, today))
        out.append(
            Block(None if where is None else names.get(where, ""), tuple(entries), tuple(cuts))
        )
    if not out[0].entries and not out[0].cuts and len(out) > 1:
        out = out[1:]  # nothing sits in no section: the file opens with its first section
    return out


def _with_cont(e: Entry, row, today: dict) -> Entry:
    """A dated future that is today's current-month or next-month contract says which."""
    if row is None or e.instrument != "FUT" or e.exchange not in ("NFO", "BFO"):
        return e
    for rule in ("month", "next_month"):
        if today.get((row.exchange, row.name, rule)) == e.symbol:
            return e._replace(cont=rule)
    return e


def tv_export(args: dict) -> dict:
    got, name, only = _read(args["ref"])
    out = build(_blocks(got, only))
    return {
        "text": out.text,
        "filename": file_name(name),
        "symbols": out.symbols,
        "sections": out.sections,
        "left_out": [{"token": c.token, "reason": c.reason} for c in out.cuts],
        "words": words.all_words(name, out),
    }


HANDLERS = {"tv_export": tv_export}
