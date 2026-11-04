"""Entry point: ``python -m sudarshan.portfolio`` from the OpenAlgo repo root."""

from .resolver import register_job
from .service import main

register_job()  # the morning check of share identities

raise SystemExit(main())
