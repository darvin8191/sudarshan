"""Maximum adverse and favourable excursion from a price path (JA4, M14.3; design 5.4).

The path is ``[(ms, price_u)]``. From the feed's recording it is one point per tick; from
1-minute candles ``candle_points`` makes two points of each (its low and its high). Only points
from the first entry leg's exchange time to the last exit leg's, both ends included, count: a
price before the trade or after it never reaches MAE or MFE.

* Long: ``MAE = (lowest price - entry) x qty``, never above 0; ``MFE = (highest - entry) x qty``,
  never below 0. A short mirrors it with the high and the low swapped and the signs flipped.
* Each comes with the first instant it happened, or None when it stayed at 0.
* Capture is ``net / MFE`` and exists only where MFE is above 0.
* A path that starts after the entry (the claim took 2 seconds) still computes, with a note.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from sudarshan.journal.core import kinds, money

NOT_RECORDED = "the price path was not recorded"


def candle_points(candles: Iterable[tuple[int, int, int]]) -> list[tuple[int, int]]:
    """Two path points per ``(ms, low_u, high_u)`` candle: its low, then its high."""
    out: list[tuple[int, int]] = []
    for ms, low_u, high_u in candles:
        out.append((ms, low_u))
        out.append((ms, high_u))
    return out


def excursion(
    side: str,
    entry_avg_u: int,
    qty: int,
    path: Sequence[tuple[int, int]],
    start_ms: int,
    end_ms: int,
    net_paise: int,
    source: str = "recording",
) -> dict[str, Any]:
    """MAE, MFE, their instants and the capture of one closed trip (see ``Excursion``)."""
    if side not in kinds.SIDES:
        raise ValueError(f"unknown side: {side!r}")
    if source not in ("recording", "candles"):
        raise ValueError(f"unknown source: {source!r}")
    sign = 1 if side == "long" else -1
    points = sorted(((ms, p) for ms, p in path if start_ms <= ms <= end_ms), key=lambda pt: pt[0])
    if not points:
        return {
            "source": "none",
            "mae_paise": None,
            "mfe_paise": None,
            "mae_ms": None,
            "mfe_ms": None,
            "capture_pct": None,
            "note": NOT_RECORDED,
        }
    worst_u = best_u = 0
    worst_ms = best_ms = None
    for ms, price_u in points:
        move = sign * (price_u - entry_avg_u) * qty
        if move < worst_u:
            worst_u, worst_ms = move, ms
        if move > best_u:
            best_u, best_ms = move, ms
    mae, mfe = money.u_to_paise(worst_u), money.u_to_paise(best_u)
    note = None
    if points[0][0] > start_ms:
        h, m, s = money.ist_clock(points[0][0])
        note = f"from {h:02d}:{m:02d}:{s:02d}"
    return {
        "source": source,
        "mae_paise": mae,
        "mfe_paise": mfe,
        "mae_ms": worst_ms,
        "mfe_ms": best_ms,
        "capture_pct": money.ratio_str(net_paise * 100, mfe, 2) if mfe > 0 else None,
        "note": note,
    }
