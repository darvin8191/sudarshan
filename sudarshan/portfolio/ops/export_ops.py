"""A tab's exports per view, CSV and PDF (phase P7, lane X: M7.40).

Registered by the phase's contract (P0) with no body: ``export_pdf`` answers "not ready" until X2
adds it here, and while ``SUDARSHAN_PORTFOLIO_GOALS`` is off after that (``ops.switched``).
``export_csv`` stays in import_ops.py until X1 moves it here with its ``view`` argument.
"""
