"""Ref kinds of Watchlists v2 (list, preset, ranked, flag), found by discovery (M8.4, W6).

A ref is ``<kind>:<id>``. Each module of this package names its kind (``KIND``) and gives the
``view`` op's dispatcher the reader of that kind; a module added here is found by
``pkgutil.iter_modules`` and needs no edit elsewhere. Built: ``list`` (W6), ``flag`` (W9),
``preset`` (W12) and ``ranked`` (W22; its ident is ``<source>:<asset>:<category>``). A kind that is
not built, or an ident the kind does not offer, is refused as ``invalid`` with field ``ref``. The
ranked reader gives ``view`` its rows from the live ranking; the sealed members route refuses a ranked
ref in words before it reaches ``members`` (W23), since a consumer must never take a changing ranking
for a list.

Every reader module exposes:

``KIND``
    The kind it reads, one of ``contract.ENUMS["RefKind"]``.

``view(ident: str, user: str) -> dict``
    The ``View`` record of ``<KIND>:<ident>`` for OpenAlgo user ``user``. Raises ``OpError("gone")``
    when it does not exist and ``OpError("invalid", field="ref")`` when the id is not one of the
    kind. Reads only; prices are never in it.

``members(ident: str, user: str) -> dict``
    The ``Members`` record of the sealed members route (W23): ``{ref, version, members: Contract[],
    valid_until, left_out: Cut[]}``, the contracts a ref holds today, follow rows resolved, with
    ``valid_until`` the epoch ms at which a rolling row would resolve differently (None when none
    does). A ``Cut`` may carry ``why`` (``atm``, ``strike`` or ``missing``). A reader of a list
    adds ``created_at``, OpenAlgo's own text for the list (its identity, review M4). Never a flag
    or a note. Same refusals as ``view``.

Pure of threads, like the rest of this package; a reader opens its sessions as ``with`` blocks.

The readers are found once: the package's modules are listed when it is imported, and imported
and kept on their first use (``utils/lazy``), so a ``view`` request never lists the directory or
imports again, and importing the package (and so the bridge) loads no reader and no master.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Mapping
from types import MappingProxyType, ModuleType

from sudarshan.watchlists import contract
from utils.lazy import LazyInit

#: The modules of this package, listed once, at import.
_NAMES = tuple(info.name for info in pkgutil.iter_modules(__path__))


def _discover() -> Mapping[str, ModuleType]:
    found: dict[str, ModuleType] = {}
    for name in _NAMES:
        module = importlib.import_module(f"{__name__}.{name}")
        kind = getattr(module, "KIND", None)
        if kind in contract.ENUMS["RefKind"]:
            found[kind] = module
    return MappingProxyType(found)


#: The reader of each kind, built on first use and kept (it only imports this package's modules).
_readers: LazyInit[Mapping[str, ModuleType]] = LazyInit(_discover, "watchlists-refs")


def readers() -> Mapping[str, ModuleType]:
    """The reader of each kind that has one, by kind (read-only, the same map every call)."""
    return _readers.get()


def split(ref: str) -> tuple[str, str] | None:
    """``list:7`` to ``("list", "7")``; None when it is not ``<kind>:<id>`` of a kind of the
    contract."""
    kind, sep, ident = ref.partition(":")
    if not sep or not ident or kind not in contract.ENUMS["RefKind"]:
        return None
    return kind, ident


def reader_of(ref: str) -> tuple[ModuleType, str] | None:
    """The reader and the id for a ref, or None when its kind is unbuilt or the ref is not one."""
    parts = split(ref)
    if parts is None:
        return None
    module = readers().get(parts[0])
    return None if module is None else (module, parts[1])
