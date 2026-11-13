"""The one rule for the instant an expiring contract stops trading (M8.10).

Pure: the day, the kind of underlying and the exchange are arguments; no clock, no I/O. The
regime of the day comes from ``sudarshan.alerts.market.sessions``, the repository's one table of
sessions:

    until 6 Dec 2026   an index contract trades to the F&O close, 15:40, and so does a stock's
    from 7 Dec 2026    an index contract stops at 15:30 on its expiry day; a stock's contract
                       trades to the F&O close, 15:45

A special session day (an evening Muhurat, a holiday row that leaves the exchange open) takes its
close from OpenAlgo's market calendar: the caller reads the window's end and passes it as
``window_end_ms``. Index futures (W12) and follow rows (W15) both ask this, so the roll happens at
the same instant in a list and in a row.

Which underlyings are an index's is OpenAlgo's one rule, the master contract's answer
(``sudarshan.index_contracts``, v0.8.1 review L1), never a tuple of this package's own (v0.9.0
review M1): the caller reads it inside OpenAlgo (``sessions.index_underlyings``) and passes it in.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date

from sudarshan.alerts.market import sessions

_BSE = frozenset({"BFO", "BSE_FO"})


def is_index(underlying: str, indices: Collection[str]) -> bool:
    """True for an index underlying (NIFTY, BANKNIFTY, SENSEX ...): its contracts stop earlier
    than a stock's from 7 Dec 2026. ``indices`` is the master's answer the caller read
    (``sessions.index_underlyings``): the index underlyings the master lists, or the names every
    reader knows while it cannot be read."""
    return underlying in indices


def stop_ms(
    day: date | str, *, index: bool, exchange: str = "NFO", window_end_ms: int | None = None
) -> int:
    """Epoch ms an expiring contract stops trading on its expiry ``day`` (a date or ISO text)."""
    if window_end_ms:
        return int(window_end_ms)
    expiry = day if isinstance(day, date) else date.fromisoformat(day)
    segment = "BSE_FO" if exchange in _BSE else "NSE_FO"
    return sessions.expiry_close(segment, expiry, index=index)  # F&O segments always have hours
