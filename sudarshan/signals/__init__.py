"""SUDARSHAN's view of TRINETRA's published readings (M13): the letterbox and ``sudarshan-signals``.

TRINETRA reaches SUDARSHAN only by dropping JSON documents into a folder SUDARSHAN owns
(``/var/spool/sudarshan-signals/in``). Nothing here opens a TRINETRA file, port, database, unit
or log. ``contract.py`` is the one contract (the document, the ops, the shapes and the words);
``frontend/src/sudarshan/signals/types.ts`` is generated from it by ``gentypes.py``.

The pure parts perform no I/O of any kind: reading one document (``contract.py``), what changed
and the words (``diff.py``, ``words.py``). The rest is the ``sudarshan-signals`` service
(``python -m sudarshan.signals``) on 127.0.0.1:5588. Absolute imports of this package only.
"""

__version__ = "0.1.0"
