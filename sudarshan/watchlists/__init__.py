"""Watchlists v2 (M8.4 and following): the rules, the contract and the words. See README.md.

This package runs inside the OpenAlgo app under a written exception to the SUDARSHAN ring rule
(README.md, "Why this runs inside OpenAlgo"). Its limits are conditions of that exception: no
thread, timer or event loop here, one master generation, the switch off by default. The first two
are held by ``tests/test_exception.py``.
"""
