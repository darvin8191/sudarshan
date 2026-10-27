"""The SUDARSHAN screener (M9): rules judged on closed candles, results kept as membership lists.

The modules named below are pure, like services/risk/ and the market alerts engine: no database, no
feed, no clock and no logging. Every input is an argument and every decision a return value, so the
service, the replay check and the golden vectors (sudarshan/screener/tests/vectors/) run the same
code. The runtime around them translates; it never decides.

    types       timeframes, three-valued logic, bars, candles, operands, the compiled plan
    catalog     every field, indicator and operator: parameters, units, warm-ups, words
    spec        a screen's JSON: checks in the owner's words, defaults, compile to a plan
    engine      offsets, forming candles, operators, Kleene logic, ranking
    library     indicators over openalgo.ta, with the engine's own fast primitives (rolling)
    price       price action: opening range, pivots, relative strength, extremes; patterns
    options     OI build-up, PCR, max pain, ATM IV, straddle, IV rank
    membership  run outcomes to member transitions and the list's desired set
    alerting    whether a run is worth telling; words: the page's marks and engine line
    bars        ticks to sealed 1-minute bars, the exchange's own candle, pauses, aggregation
    presets     the library of ready screens

The store (store, migrations), the universes (universe, openalgo_read) and the runtime are the
service's. Prices are paise (integers), percentages and ratios Decimal strings at every boundary,
instants epoch milliseconds, a trading day the session key YYYYMMDD (cross-design X3). Candle timing
is the market alerts clock's, imported, never copied (X5).
"""

__version__ = "0.1.0"
