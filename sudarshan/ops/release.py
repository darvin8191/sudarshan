"""The release the deploy tool last installed (M18.1, M6.8).

``sudarshan-deploy`` writes the tag, ``v0.3.3``, to ``/var/lib/sudarshan/deployed_tag`` when an
install has passed its health check, and makes the file readable by the ring (mode 644, the folder
traversable). A service's own ``__version__`` is the version of that service's code, not of the
release (on 27 Oct 2026 the feed's ``0.1.0`` was read as the release), so each service's health
shows both. Best effort: no file (a server that was never deployed by the tool, a test machine) or
one that holds anything but a tag is None, and never an error.
"""

from __future__ import annotations

import re
from pathlib import Path

from .files import read_regular

DEPLOYED_TAG_FILE = Path("/var/lib/sudarshan/deployed_tag")
#: The tags the deploy tool installs (vA.B.C); it ignores any other release name.
TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
_MAX_BYTES = 64


def installed_release(path: Path | str = DEPLOYED_TAG_FILE) -> str | None:
    """The installed release tag, such as ``v0.3.3``, or None when it cannot be told."""
    raw = read_regular(path, _MAX_BYTES)
    if raw is None:
        return None
    text = raw.decode("ascii", errors="replace").strip()
    return text if TAG.fullmatch(text) else None
