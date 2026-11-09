"""Textbook and Pine Script definitions of the library indicators, written independently of
``openalgo.ta`` in plain Python over lists, one window at a time (no running sums, so no drift).

They exist to check the conventions the screener inherits: where a smoothing starts, which way a
direction points, what a flat market does. A function returns a list the length of its input with
``None`` where there is no value yet.
"""

from __future__ import annotations

import math


def sma(a, n):
    return [None if i < n - 1 else sum(a[i - n + 1 : i + 1]) / n for i in range(len(a))]


def wma(a, n):
    weights = range(1, n + 1)
    total = n * (n + 1) / 2
    return [
        None
        if i < n - 1
        else sum(w * x for w, x in zip(weights, a[i - n + 1 : i + 1], strict=True)) / total
        for i in range(len(a))
    ]


def vwma(a, v, n):
    out = []
    for i in range(len(a)):
        if i < n - 1:
            out.append(None)
            continue
        volume = sum(v[i - n + 1 : i + 1])
        if volume == 0:
            out.append(None)
            continue
        out.append(
            sum(x * w for x, w in zip(a[i - n + 1 : i + 1], v[i - n + 1 : i + 1], strict=True))
            / volume
        )
    return out


def ema(a, n):
    """Pine's ta.ema: the first value seeds it."""
    k = 2 / (n + 1)
    out = [a[0]]
    for x in a[1:]:
        out.append(out[-1] + k * (x - out[-1]))
    return out


def ema_sma_seed(a, n):
    """The textbook EMA: a simple average of the first n values seeds it (the library's Keltner)."""
    k = 2 / (n + 1)
    out = [None] * (n - 1) + [sum(a[:n]) / n]
    for x in a[n:]:
        out.append(out[-1] + k * (x - out[-1]))
    return out


def rma(a, n, start=0):
    """Wilder's smoothing seeded with a simple average of the n values from ``start``."""
    out = [None] * (start + n - 1) + [sum(a[start : start + n]) / n]
    for x in a[start + n :]:
        out.append((out[-1] * (n - 1) + x) / n)
    return out


def rma_na(a, n):
    """Pine's ta.rma over a series that can hold gaps: a gap clears it, and it starts again from
    the simple average of the first ``n`` consecutive values."""
    out, previous = [], None
    for i, x in enumerate(a):
        if x is None:
            previous = None
            out.append(None)
        elif previous is not None:
            previous = (previous * (n - 1) + x) / n
            out.append(previous)
        elif i >= n - 1 and all(w is not None for w in a[i - n + 1 : i + 1]):
            previous = sum(a[i - n + 1 : i + 1]) / n
            out.append(previous)
        else:
            out.append(None)
    return out


def true_range(h, lo, c):
    return [h[0] - lo[0]] + [
        max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(1, len(h))
    ]


def atr(h, lo, c, n):
    return rma(true_range(h, lo, c), n)


def rsi(c, n):
    gains = [max(c[i] - c[i - 1], 0) for i in range(1, len(c))]
    losses = [max(c[i - 1] - c[i], 0) for i in range(1, len(c))]
    avg_gain, avg_loss = rma(gains, n), rma(losses, n)
    out = [None]
    for g, loss in zip(avg_gain, avg_loss, strict=True):
        if g is None:
            out.append(None)
        elif loss == 0:
            out.append(100.0)  # nothing fell: 100, also when nothing moved at all
        else:
            out.append(100 - 100 / (1 + g / loss))
    return out


def macd(c, fast, slow, signal):
    line = [x - y for x, y in zip(ema(c, fast), ema(c, slow), strict=True)]
    sig = ema(line, signal)
    return line, sig, [x - y for x, y in zip(line, sig, strict=True)]


def highest(a, n):
    return [None if i < n - 1 else max(a[i - n + 1 : i + 1]) for i in range(len(a))]


def lowest(a, n):
    return [None if i < n - 1 else min(a[i - n + 1 : i + 1]) for i in range(len(a))]


def stochastic(h, lo, c, k, smooth, d):
    """Slow stochastic: the raw %K averaged over ``smooth``, %D the average of that over ``d``.
    A zero range gives 50, the library's choice (TA-Lib gives 0, Pine gives no value)."""
    hh, ll = highest(h, k), lowest(lo, k)
    raw = []
    for i in range(len(c)):
        if hh[i] is None:
            raw.append(None)
        elif hh[i] == ll[i]:
            raw.append(50.0)
        else:
            raw.append(100 * (c[i] - ll[i]) / (hh[i] - ll[i]))
    first = k - 1
    slow = [None] * first + sma(raw[first:], smooth)
    first += smooth - 1
    return slow, [None] * first + sma(slow[first:], d)


def williams_r(h, lo, c, n):
    hh, ll = highest(h, n), lowest(lo, n)
    return [
        None
        if hh[i] is None
        else (-50.0 if hh[i] == ll[i] else 100 * (c[i] - hh[i]) / (hh[i] - ll[i]))
        for i in range(len(c))
    ]


def donchian(h, lo, n):
    upper, lower = highest(h, n), lowest(lo, n)
    return (
        upper,
        lower,
        [None if u is None else (u + w) / 2 for u, w in zip(upper, lower, strict=True)],
    )


def bbands(c, n, mult):
    """Bands on the population deviation (the divisor is n), as Pine's ta.stdev."""
    middle = sma(c, n)
    upper, lower = [], []
    for i in range(len(c)):
        if middle[i] is None:
            upper.append(None)
            lower.append(None)
            continue
        window = c[i - n + 1 : i + 1]
        deviation = math.sqrt(sum((x - middle[i]) ** 2 for x in window) / n)
        upper.append(middle[i] + mult * deviation)
        lower.append(middle[i] - mult * deviation)
    return upper, middle, lower


def supertrend(h, lo, c, n, factor):
    """Pine's ta.supertrend: direction -1 in an up trend, +1 in a down trend."""
    average = atr(h, lo, c, n)
    value, direction, upper, lower = [], [], [], []
    for i in range(len(c)):
        if average[i] is None:
            for column in (value, direction, upper, lower):
                column.append(None)
            continue
        mid = (h[i] + lo[i]) / 2
        basic_up, basic_low = mid + factor * average[i], mid - factor * average[i]
        if upper[-1] is None:
            up, low = basic_up, basic_low
        else:
            up = basic_up if basic_up < upper[-1] or c[i - 1] > upper[-1] else upper[-1]
            low = basic_low if basic_low > lower[-1] or c[i - 1] < lower[-1] else lower[-1]
        if value[-1] is None:
            way = 1
        elif value[-1] == upper[-1]:
            way = -1 if c[i] > up else 1
        else:
            way = 1 if c[i] < low else -1
        upper.append(up)
        lower.append(low)
        direction.append(way)
        value.append(low if way == -1 else up)
    return value, direction


def adx(h, lo, c, n):
    """Wilder's directional system as Pine's ta.dmi: (+DI, -DI, ADX)."""
    size = len(c)
    plus_move, minus_move = [0.0], [0.0]
    for i in range(1, size):
        up, down = h[i] - h[i - 1], lo[i - 1] - lo[i]
        plus_move.append(up if up > down and up > 0 else 0.0)
        minus_move.append(down if down > up and down > 0 else 0.0)
    smooth_range = rma(true_range(h, lo, c), n)
    plus, minus, dx = [], [], []
    for p, m, r in zip(rma(plus_move, n), rma(minus_move, n), smooth_range, strict=True):
        if r is None or r == 0:
            plus.append(None)
            minus.append(None)
            dx.append(None)
            continue
        plus.append(100 * p / r)
        minus.append(100 * m / r)
        total = plus[-1] + minus[-1]
        dx.append(0.0 if total == 0 else 100 * abs(plus[-1] - minus[-1]) / total)
    return plus, minus, rma_na(dx, n)


def cci(h, lo, c, n):
    tp = [(x + y + z) / 3 for x, y, z in zip(h, lo, c, strict=True)]
    out = []
    for i in range(len(tp)):
        if i < n - 1:
            out.append(None)
            continue
        window = tp[i - n + 1 : i + 1]
        mean = sum(window) / n
        deviation = sum(abs(x - mean) for x in window) / n
        out.append(0.0 if deviation == 0 else (tp[i] - mean) / (0.015 * deviation))
    return out


def mfi(h, lo, c, v, n):
    """Money flow index over the last n changes of the typical price: the money flow (typical price
    x volume) of the bars that rose, against the flow of the bars that rose or fell. A window in
    which the price never moved has no index (None)."""
    tp = [(x + y + z) / 3 for x, y, z in zip(h, lo, c, strict=True)]
    flow = [tp[i] * v[i] for i in range(len(tp))]
    out = [None] * len(tp)
    for i in range(n, len(tp)):
        window = range(i - n + 1, i + 1)
        pos = sum(flow[j] for j in window if tp[j] > tp[j - 1])
        neg = sum(flow[j] for j in window if tp[j] < tp[j - 1])
        out[i] = None if pos + neg == 0 else 100 * pos / (pos + neg)
    return out


def cmf(h, lo, c, v, n):
    """Chaikin money flow: the money flow multiplier (0 on a bar without range) times volume,
    over the volume of the window. A window without volume has none (None)."""
    flow = [0.0 if h[i] == lo[i] else ((c[i] - lo[i]) - (h[i] - c[i])) / (h[i] - lo[i]) * v[i]
            for i in range(len(c))]  # fmt: skip
    out = []
    for i in range(len(c)):
        volume = sum(v[max(0, i - n + 1) : i + 1])
        out.append(None if i < n - 1 or volume == 0 else sum(flow[i - n + 1 : i + 1]) / volume)
    return out


def keltner(h, lo, c, n, atr_n, mult):
    middle = ema_sma_seed(c, n)
    average = atr(h, lo, c, atr_n)
    upper, lower = [], []
    for m, a in zip(middle, average, strict=True):
        if m is None or a is None:
            upper.append(None)
            lower.append(None)
        else:
            upper.append(m + mult * a)
            lower.append(m - mult * a)
    return upper, middle, lower


def rvol(v, n):
    """Volume over the average of the window it ends. No volume in the window: no ratio (None)."""
    average = sma(v, n)
    return [None if a is None or a == 0 else x / a for x, a in zip(v, average, strict=True)]


def obv(c, v):
    out = [0.0]
    for i in range(1, len(c)):
        out.append(out[-1] + (v[i] if c[i] > c[i - 1] else -v[i] if c[i] < c[i - 1] else 0))
    return out


def hma(a, n):
    """Hull: a weighted average, over the square root of n (rounded down), of twice the
    half-length one minus the full-length one."""
    raw = [
        None if x is None or y is None else 2 * x - y
        for x, y in zip(wma(a, n // 2), wma(a, n), strict=True)
    ]
    first = next(i for i, x in enumerate(raw) if x is not None)
    return [None] * first + wma(raw[first:], int(math.sqrt(n)))


def tema(a, n):
    """3 x E1 - 3 x E2 + E3. Each average starts from the first value of its input and drops its
    first n - 1 results, so the next average starts from the first result that is kept."""

    def level(x):
        first = next(i for i, v in enumerate(x) if v is not None)
        out = [None] * first + ema(x[first:], n)
        for i in range(first, first + n - 1):
            out[i] = None
        return out

    one = level(a)
    two = level(one)
    three = level(two)
    return [
        None if z is None else 3 * x - 3 * y + z for x, y, z in zip(one, two, three, strict=True)
    ]
