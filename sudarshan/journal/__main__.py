"""Entry point: ``python -m sudarshan.journal`` from the OpenAlgo repo root (JB2)."""

from .jobs import register_all
from .service import main

register_all()  # every job registers from its own module

raise SystemExit(main())
