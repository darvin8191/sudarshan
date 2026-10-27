"""Market alerts (M10): conditions on market prices, judged tick by tick.

The engine in this package is pure, like services/risk/: no I/O of any kind, so no database, no
feed, no clock and no logging. Every input arrives as an argument and every decision leaves as a
return value. The runtime around it (the feed link, the gate's health, the timers, market.db and
delivery) is the service's, and it translates; it never decides what fires.

    grain     tick sizes: feed prices in whole ticks, levels snapped once with Decimal
    sessions  each segment's trading session, special days included
    spec      alert definitions and their checks, in the owner's words
    clock     candles on the exchange clock, the gate, pauses in checking
    conditions  levels taken from the feed: % move, day high or low, VWAP
    engine    one alert's decision on one observation
    book      every Active alert by instrument key: one feed frame in, decisions out

The runtime's seams are ``book.Book`` (``put``, ``drop``, ``seed``, ``on_tick``, ``on_clock``,
``changed``, ``modes``), ``clock.Market`` (what it knows about the market, rebuilt when health
arrives, the day turns or a pause starts or ends) and ``spec.check`` / ``spec.Alert.of``.

The golden vectors in sudarshan/alerts/tests/market_vectors.json are the contract; add a case
whenever a rule changes.
"""
