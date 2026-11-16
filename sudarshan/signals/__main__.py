"""``python -m sudarshan.signals``: the sudarshan-signals service (sudarshan-signals.service)."""

import sys

from sudarshan.signals.service import main

sys.exit(main())
