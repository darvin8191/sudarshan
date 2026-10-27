"""The portfolio's pure engines (M7): money, the ledger, valuation, performance, history.

Nothing in this package performs I/O of any kind: no database, no network, no files, no clock,
no logging. Every input arrives as an argument and every decision leaves as a return value, as in
services/risk/ and sudarshan/alerts/market/. That is what lets the same rule run in the service,
in a replay check and in a test, and what lets the phone's TypeScript copy be held to the same
golden vectors (tests/vectors/). A structural test (tests/test_portfolio_contract.py) holds every
module here to it: standard-library imports from a short list, nothing outside this package, no
clock, no file, no print.

The README's "Where things are" table names each module and the change that builds it. ``kinds``
is the contract the others and the phone share.
"""
