"""Rolling maximum and minimum in linear time, whatever the window.

``openalgo.ta.highest`` and ``lowest`` take about a millisecond for 1,000 bars (a Python loop), a
hundred times the rest of the library, so the screener never calls them: this is the van Herk and
Gil-Werman method. The array is cut into blocks of ``window`` values; the running maximum from each
block's start and from its end are two ``numpy.maximum.accumulate`` passes, and the maximum of any
window is the larger of the end-run at its first value and the start-run at its last. Tens of
microseconds for 1,000 bars, any window, and it agrees with TA-Lib's ``MAX`` and ``MIN``.

Like the rest of the library a window that holds a NaN gives NaN and the series recovers as soon as
the NaN slides out. Pure: no I/O.
"""

from __future__ import annotations

import numpy as np


def rolling_max(values, window: int) -> np.ndarray:
    """``out[i]`` is the largest of ``values[i-window+1 .. i]``; NaN before the first full window."""
    return _rolling(values, window, np.maximum, -np.inf)


def rolling_min(values, window: int) -> np.ndarray:
    """``out[i]`` is the smallest of ``values[i-window+1 .. i]``; NaN before the first full window."""
    return _rolling(values, window, np.minimum, np.inf)


def _rolling(values, window: int, ufunc, neutral: float) -> np.ndarray:
    if window.__class__ is not int or window < 1:
        raise ValueError("a window is a whole number of values, at least 1")
    data = np.asarray(values, dtype=np.float64)
    if data.ndim != 1:
        raise ValueError("rolling extrema work on one series")
    size = data.size
    out = np.full(size, np.nan)
    if size < window:
        return out
    if window == 1:
        return data.copy()
    padding = -size % window
    if padding:
        data = np.concatenate((data, np.full(padding, neutral)))
    blocks = data.reshape(-1, window)
    from_start = ufunc.accumulate(blocks, axis=1).ravel()
    from_end = ufunc.accumulate(blocks[:, ::-1], axis=1)[:, ::-1].ravel()
    out[window - 1 :] = ufunc(from_end[: size - window + 1], from_start[window - 1 : size])
    return out
