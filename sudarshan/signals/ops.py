"""The ops of the TRINETRA view's sealed local API (T6): one handler per op of the contract.

``REGISTRY`` maps an op's name to ``async handler(service, args) -> data``; the API answers 501
"not ready" for an op with no handler.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

REGISTRY: dict[str, Callable[[Any, dict[str, Any]], Awaitable[Any]]] = {}
