"""The built-in lists made from today's master: Indices, F&O stocks, Index futures (M8.8, W12).

Pure builders over the master's rows, as ``search.Index`` holds them (references to the snapshot's
own rows, never copies). No Flask, no OpenAlgo, no I/O, no clock: the time and the calendar's
closes come in as arguments.

* **Indices**: a typed list of 24 OpenAlgo index symbols, in code, checked against today's master.
  A name the master lacks is left out and reported; the list is checked at each index review.
* **F&O stocks**: the screener's rule (``sudarshan/screener/universe.py`` ``fno_stocks``), mirrored
  here because the screener's function works on its own ``Listing`` copies of the master, and
  making those for a master of 100,000 rows would copy what this feature promises never to copy.
  The cash listing (NSE) of every stock that has a future in NFO, at any expiry, index
  underlyings excluded. An underlying with futures and no cash row is reported, not dropped
  silently. A test runs both on one synthetic master and finds them equal.
* **Index futures**: per index underlying, the first future whose expiry close is still ahead, by
  3.7's roll: the calendar's close, else 15:30 IST, and the close of expiry day rolls it. NFO and
  BFO both (the screener's index futures read NFO only, which leaves SENSEX and BANKEX out).
"""

from __future__ import annotations

from sudarshan.watchlists import dynamic, search
from sudarshan.watchlists.presets import Built, Entry, Prepared

ORDER = 20  # after the typed lists (W13), which come first in the catalogue

NSE_INDEX, BSE_INDEX = "NSE_INDEX", "BSE_INDEX"
#: The indices of design 3.5, by OpenAlgo's symbol. The master is the check, not the source.
INDICES = (
    *((n, NSE_INDEX) for n in (
        "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "INDIAVIX",
        "NIFTY100", "NIFTY200", "NIFTY500", "NIFTYMIDCAP100", "NIFTYSMLCAP100",
        "NIFTYIT", "NIFTYAUTO", "NIFTYPHARMA", "NIFTYFMCG", "NIFTYMETAL", "NIFTYREALTY",
        "NIFTYENERGY", "NIFTYMEDIA", "NIFTYPSUBANK", "NIFTYPVTBANK", "NIFTYINFRA",
    )),
    ("SENSEX", BSE_INDEX),
    ("BANKEX", BSE_INDEX),
)  # fmt: skip
#: The index underlyings, in the order Index futures show them. The screener's set
#: (``sudarshan.alerts.market.sessions.INDEX_UNDERLYINGS``), which a test holds equal.
INDEX_UNDERLYINGS = (
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX", "BANKEX", "SENSEX50",
)  # fmt: skip


def _row(ix: search.Index, symbol: str, exchange: str, types: tuple = ()):
    """The master's row of a symbol on an exchange (the EQ one when there are two), or None."""
    found = [r for r in ix.cash.get(search.key_of(symbol), ())
             if r.symbol == symbol and r.exchange == exchange
             and (not types or (r.instrumenttype or "") in types)]  # fmt: skip
    return min(found, key=lambda r: r.instrumenttype != "EQ") if found else None


def prepare_indices(ix: search.Index) -> Prepared:
    rows, missing = [], []
    for name, exchange in INDICES:
        row = _row(ix, name, exchange)
        if row is None:
            missing.append(f"{exchange}:{name}")
        else:
            rows.append(row)
    return Prepared(rows=tuple(rows), missing=tuple(missing))


def prepare_fno_stocks(ix: search.Index) -> Prepared:
    names = sorted(
        u for u, listed in ix.futures.items()
        if u not in INDEX_UNDERLYINGS and any(r.exchange == "NFO" for _, r in listed)
    )  # fmt: skip
    rows, missing = [], []
    for name in names:
        row = _row(ix, name, "NSE", ("EQ", ""))
        if row is None:
            missing.append(f"NSE:{name}")
        else:
            rows.append(row)
    return Prepared(rows=tuple(rows), missing=tuple(missing))


def prepare_index_futures(ix: search.Index) -> Prepared:
    return Prepared(futures=tuple((u, ix.futures[u]) for u in INDEX_UNDERLYINGS if u in ix.futures))


def same(prepared: Prepared, now_ms: int, closes: dict) -> Built:
    """A list the clock does not change."""
    return Built(rows=prepared.rows, missing=prepared.missing)


def current_futures(prepared: Prepared, now_ms: int, closes: dict) -> Built:
    """Per index underlying the first future whose expiry close is after ``now_ms``; ``valid_until``
    is the soonest close among them (when the first row of the list would change)."""
    rows, until = [], None
    for _, listed in prepared.futures:
        for iso, row in listed:
            close = dynamic.close_ms(iso, closes, index=True, exchange=row.exchange)
            if close > now_ms:
                rows.append(row)
                until = close if until is None else min(until, close)
                break
    return Built(rows=tuple(rows), valid_until=until)


ENTRIES = (
    Entry("indices", "Indices", "master", "indices", prepare_indices, same, review=True),
    Entry("fno_stocks", "F&O stocks", "master", "fno_stocks", prepare_fno_stocks, same),
    Entry("index_futures", "Index futures", "master", "index_futures",
          prepare_index_futures, current_futures),
)  # fmt: skip
