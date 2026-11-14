"""The tax core (M7.26, M7.27, M7.28, M15.5): pure rules for the tax working paper.

Standard library only and no I/O of any kind: no database, network, clock, logging or environment.
Every input is an argument and every decision a return value, so one engine gives the portfolio,
the journal and a test the same figure. Money is integer paise, a quantity ``qty_u`` (units x
10^6), a price ``price_u`` (rupees x 10^4) and a day the integer YYYYMMDD in IST. Every rate,
threshold and date is a dated row of ``rules.py``. What comes out is a working paper to verify
with a chartered accountant, never a filing figure.
"""
