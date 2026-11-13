"""The SUDARSHAN trade journal (M14) and reports (M15).

``core/`` is the pure part: pairing fills into round trips, the metrics, charges and the words of
the reports. It performs no I/O of any kind (no database, network, clock or logging); every input
is an argument and every decision a return value. ``core/kinds.py`` is the one contract.

The rest is the ``sudarshan-journal`` service (``python -m sudarshan.journal``): journal.db
(``store.py``, ``migrations.py``), the sealed local API on 127.0.0.1:5586 (``api.py``, ``ops/``)
and its jobs. Absolute imports of this package only.
"""

__version__ = "0.1.0"
