"""Market alerts (M10): conditions on market prices, judged tick by tick.

The engine in this package is pure, like services/risk/: no I/O of any kind, so no database, no
feed, no clock and no logging. Every input arrives as an argument and every decision leaves as a
return value. The runtime around it (the feed link, the gate's health, the timers, market.db and
delivery) is the service's, and it translates; it never decides what fires.

    grain     tick sizes: feed prices in whole ticks, levels snapped once with Decimal
    sessions  each segment's trading session, special days included
    spec      alert definitions and their checks, in the owner's words
    engine    one alert's decision on one observation

The golden vectors in sudarshan/alerts/tests/market_vectors.json are the contract; add a case
whenever a rule changes.
"""
