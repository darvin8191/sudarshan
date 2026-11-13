"""The SUDARSHAN trade journal (M14) and reports (M15).

``core/`` is the pure part: pairing fills into round trips, the metrics, charges and the words of
the reports. It performs no I/O of any kind (no database, network, clock or logging); every input
is an argument and every decision a return value. The service, the store and the pages are
other lanes' work and build on ``core/kinds.py``, the one contract.
"""
