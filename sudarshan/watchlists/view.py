"""The merge behind ``view`` (M8.4, W6): OpenAlgo's list and the overlay become the ``View`` record.

Pure: OpenAlgo's list, the overlay's rows and a ``lookup`` of the master come in as plain data and
the record leaves as a dict. No I/O, no clock, no logging. The readers that fetch the inputs are
in ``refs/list.py``.

Inputs. ``live``: ``{id, name, created_at, items: [(item_id, exchange, symbol, position)]}``, where
``created_at`` is OpenAlgo's own ``created_at`` as text (``created_key``). ``overlay``: what the
store holds; ``{version, oa_created_at, sections: [{id, name, mark, position}], items: {(exchange,
symbol): section_id}, follow: [{id, kind, section_id, position, ...}], symbols: {(exchange,
symbol): (flag, has_note)}, pref: dict | None}``, where ``oa_created_at`` is None for a list the
overlay has not met. ``version`` is the list's stored version, "0" until a write makes one. ``lookup(exchange, symbol)`` is the master's row of a
symbol or None.

Order inside a list: the symbols with no section first, then each section by its ``position``;
inside each, the list's follow rows first (by their position), then the fixed symbols in
OpenAlgo's item order. OpenAlgo's positions stay the only order of fixed symbols. A symbol OpenAlgo
has with no section row is in No section at its OpenAlgo position. A section item for a symbol no
longer in the list is ignored, and a section id that is no longer a section reads as No section.

The guard. SQLite can hand a deleted list's id to a new list. When the overlay's ``oa_created_at``
differs from the live list's ``created_at``, the whole overlay of that list (sections, follow rows,
preference, version) is ignored: nothing is written, and the next write that touches the list
prunes it. A symbol's flag and note belong to the symbol, not the list, and always apply.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from sudarshan.watchlists import search

DEFAULT_PREF = {"sort": "list", "descending": False, "basis": "close", "held": False}


def created_key(value: Any) -> str:
    """OpenAlgo's ``created_at`` as the text the overlay's guard stores and compares: a datetime
    as SQLite writes it (``2026-11-12 10:11:12``, with microseconds only when it has them)."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value)


def key_of(exchange: str, symbol: str) -> str:
    """The key of a row: ``NSE:INFY``."""
    return f"{exchange}:{symbol}"


def contract(exchange: str, symbol: str, row: Any, isin: str | None) -> dict:
    """The ``Contract`` record of a symbol. ``row`` is the master's row, None when it has none
    (the name is then the symbol and every detail is null). ``isin`` is read through master.py."""
    iso = search.expiry_iso(getattr(row, "expiry", None))
    return {
        "exchange": exchange,
        "symbol": symbol,
        "key": key_of(exchange, symbol),
        "name": (getattr(row, "name", None) or symbol),
        "lot": getattr(row, "lotsize", None) or None,
        "tick": getattr(row, "tick_size", None) or None,
        "expiry": iso,
        "isin": isin,
    }


def _follow(f: dict, section_id: int | None) -> dict:
    month = f["kind"] == "month_future"
    paise = f.get("strike_paise")
    return {
        "id": f["id"],
        "kind": f["kind"],
        "section_id": section_id,
        "position": f["position"],
        "underlying": f["underlying"],
        "underlying_exchange": f["underlying_exchange"],
        "expiry_rule": f["expiry_rule"],
        "strike_rule": None if month else f.get("strike_rule"),
        "offset": None if month else f.get("offset", 0),
        "strike": None if month or paise is None else paise / 100,
        "opt_right": f.get("opt_right"),
    }


def _pref(raw: dict | None) -> dict:
    if not raw:
        return dict(DEFAULT_PREF)
    return {"sort": raw["sort"], "descending": bool(raw["descending"]),
            "basis": raw["basis"], "held": bool(raw["held"])}  # fmt: skip


def merge(
    live: dict,
    overlay: dict | None,
    lookup: Callable[[str, str], Any],
    *,
    loaded: bool = True,
    isin: Callable[[Any], str | None] = lambda row: None,
) -> dict:
    """The ``View`` record of a list. ``loaded`` says whether the master is there at all: with it
    absent no row is called expired (nothing is known), with it present a symbol it lacks is."""
    overlay = overlay or {}
    # a list the overlay has not met has no guard row: nothing to ignore, and nothing in it
    trusted = overlay.get("oa_created_at") in (None, live["created_at"])
    own = overlay if trusted else {}
    sections = sorted(own.get("sections", ()), key=lambda s: (s["position"], s["id"]))
    known = {s["id"] for s in sections}
    placed = own.get("items", {})
    symbols = overlay.get("symbols", {})

    def where(section_id):
        return section_id if section_id in known else None

    items = sorted(live["items"], key=lambda i: (i[3], i[0]))
    by_section: dict = {None: [], **{s["id"]: [] for s in sections}}
    for item_id, exchange, symbol, _ in items:
        by_section[where(placed.get((exchange, symbol)))].append((item_id, exchange, symbol))
    follows = sorted(own.get("follow", ()), key=lambda f: (f["position"], f["id"]))
    follow_in: dict = {key: [] for key in by_section}
    for f in follows:
        follow_in[where(f.get("section_id"))].append(_follow(f, where(f.get("section_id"))))

    rows, missing = [], []
    for section_id in (None, *(s["id"] for s in sections)):
        for item_id, exchange, symbol in by_section[section_id]:
            row = lookup(exchange, symbol)
            gone = loaded and row is None
            flag, noted = symbols.get((exchange, symbol), (None, False))
            if gone:
                missing.append(key_of(exchange, symbol))
            rows.append({
                "item": item_id,
                "contract": contract(exchange, symbol, row, isin(row)),
                "section_id": section_id,
                "flag": flag,
                "has_note": bool(noted),
                "expired": gone,
                "metric": None,
            })  # fmt: skip
    return {
        "ref": f"list:{live['id']}",
        "kind": "list",
        "name": live["name"],
        "version": str(own.get("version") or 0),
        "locked": False,
        "as_of": None,
        "lines": [],
        "total": len(rows),
        "missing": missing,
        "sections": [
            {
                "id": s["id"],
                "name": s["name"],
                "mark": s["mark"],
                "position": s["position"],
                "count": len(by_section[s["id"]]) + len(follow_in[s["id"]]),
            }
            for s in sections
        ],  # fmt: skip
        "rows": rows,
        "follow": [f for section_id in follow_in for f in follow_in[section_id]],
        "pref": _pref(own.get("pref")),
    }
