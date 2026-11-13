"""The scope's mask, applied in one place (JB10, design 6.6; cross-design Y16).

Every answer leaves the service through ``apply``. The owner's scope masks nothing. A masked scope
(the family's purpose today, a viewer principal in P6) loses, at any depth of the answer:

* ``money``: every paise field (``*_paise``), the ratios of money (profit factor, payoff, R, the
  percentage fields, beta and correlation) and a report's ``text``, which carries amounts;
* ``price``: every price (``*_u``: entry and exit averages, fill prices, lows and highs);
* ``quantity``: every quantity (``qty``, ``qty_max``, ``entry_qty``, ``exit_qty``, ``qty_u``).

Counts (trades, wins, losses, flats, orders, days), symbols, sides, statuses, times and words stay.
"""

from __future__ import annotations

from typing import Any

from .store import OWNER, Scope

MONEY, PRICE, QUANTITY = "money", "price", "quantity"
#: The mask of a viewer: no money, no price, no quantity (P6's family viewer, the family purpose).
VIEWER_MASK = frozenset({MONEY, PRICE, QUANTITY})
MONEY_RATIOS = frozenset({"profit_factor", "payoff", "r_total", "r_avg", "beta", "correlation"})
QUANTITIES = frozenset({"qty", "qty_max", "entry_qty", "exit_qty", "leg_qty"})


def viewer() -> Scope:
    """The owner's rows, seen without money, prices or quantities."""
    return Scope(OWNER, VIEWER_MASK)


def category(key: str) -> str | None:
    """What a field of an answer holds, when it is one a mask can take out."""
    if key.endswith("_paise") or key.endswith("_pct") or key in MONEY_RATIOS or key == "text":
        return MONEY
    if key in QUANTITIES or key.startswith("qty_") or key.endswith("_qty"):
        return QUANTITY
    if key.endswith("_u"):
        return PRICE
    return None


def apply(value: Any, scope: Scope) -> Any:
    """``value`` as ``scope`` may see it."""
    if not scope.mask:
        return value
    return _masked(value, scope.mask)


def _masked(value: Any, mask: frozenset[str]) -> Any:
    if isinstance(value, dict):
        return {k: _masked(v, mask) for k, v in value.items() if category(k) not in mask}
    if isinstance(value, list | tuple):
        return [_masked(v, mask) for v in value]
    return value
