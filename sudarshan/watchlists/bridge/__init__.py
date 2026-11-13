"""Parts of the bridge that install themselves (M8.4): the blueprint calls ``install_all``.

A module here may define ``install(app)``, called once when the blueprint is registered (the push
of W5 and the hook of W6 do). Found by ``pkgutil.iter_modules``; nothing is imported at app start
beyond this file, and nothing here starts a thread.
"""

from __future__ import annotations

import importlib
import pkgutil


def install_all(app) -> None:
    """Call ``install(app)`` of every module of this package that has one."""
    for info in pkgutil.iter_modules(__path__):
        install = getattr(importlib.import_module(f"{__name__}.{info.name}"), "install", None)
        if install is not None:
            install(app)
