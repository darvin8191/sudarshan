"""Entry point: ``python -m sudarshan.portfolio`` from the OpenAlgo repo root."""

from .service import main
from .timers import register_all

register_all()  # every job registers from its own module

raise SystemExit(main())
