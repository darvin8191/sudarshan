"""What the automation's rules read: one strategy, its state, one alert (M12.1).

Plain frozen values with no I/O, so the pure modules (payload.py, rules.py, symbols.py,
words.py) and their vectors need neither a database nor an app.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

ACTIVE, EXITS_ONLY, STOPPED = "active", "exits_only", "stopped"
STATES = (ACTIVE, EXITS_ONLY, STOPPED)
SANDBOX, LIVE = "sandbox", "live"
MODES = (SANDBOX, LIVE)
SAME, FUTURE, OPTION = "same", "future", "option"
KINDS = (SAME, FUTURE, OPTION)
BOTH, LONG, SHORT, FLAT = "both", "long", "short", "flat"
DIRECTIONS = (BOTH, LONG, SHORT)
POSITIONS = (LONG, SHORT, FLAT)
FIXED, SCALED = "fixed", "scaled"
SIZE_MODES = (FIXED, SCALED)
PRODUCTS = ("MIS", "NRML", "CNC")
FUTURE_RULES = ("month", "next_month")
OPTION_RULES = ("nearest", "next", "month")

#: What the intake decided about one alert. ``claimed`` is a row whose order is being placed: it
#: is replaced when the order's answer is in, and stays only if the server stopped in between.
VERDICTS = (
    "sent",
    "no_action",
    "repeat",
    "out_of_order",
    "late",
    "stopped",
    "not_mapped",
    "not_sized",
    "mode_refused",
    "capped",
    "order_refused",
    "not_confirmed",
    "bad_message",
    "claimed",
)
#: The verdicts after which an order went or may have gone: only these move the state on.
MOVED = frozenset({"sent", "no_action", "not_confirmed"})

MAX_AGE_DEFAULT_S, MAX_AGE_MIN_S, MAX_AGE_MAX_S = 30, 10, 120
MAX_ALERTS_DEFAULT, MAX_ALERTS_MOST = 20, 200
NO_ENTRY_BEFORE_STOP_MIN = 15


@dataclass(frozen=True)
class Strategy:
    """One Pine script on one chart with one alert, as the owner set it up."""

    id: int
    name: str
    state: str = ACTIVE
    stopped_why: str | None = None
    mode: str = SANDBOX
    chart: str | None = None
    kind: str = FUTURE
    exchange: str = "NFO"
    underlying: str = ""
    expiry_rule: str | None = "month"
    strike_offset: str | None = None
    product: str = "MIS"
    direction: str = BOTH
    size_mode: str = FIXED
    lots: int | None = 1
    quantity: int | None = None
    multiplier: float | None = None
    max_lots: int | None = None
    max_age_s: int = MAX_AGE_DEFAULT_S
    max_alerts_day: int = MAX_ALERTS_DEFAULT
    roll_on_expiry_day: bool = False
    no_entry_before_stop_min: int = NO_ENTRY_BEFORE_STOP_MIN
    contract_key: str = ""
    token_tail: str = ""
    created_ms: int = 0
    updated_ms: int = 0
    version: int = 1


@dataclass(frozen=True)
class State:
    """What the intake last did for a strategy. A note, never a size: the smart order reads the
    account."""

    strategy_id: int
    last_target: int = 0
    last_fired_s: int | None = None
    held_exchange: str | None = None
    held_symbol: str | None = None
    held_side: str | None = None  # long or short
    held_since_ms: int | None = None
    day: str | None = None  # YYYY-MM-DD in India, the day alerts_today counts
    alerts_today: int = 0
    told_stopped_day: str | None = None
    version: int = 1

    def alerts_on(self, day: str) -> int:
        """Alerts claimed on ``day``: the count restarts with the date."""
        return self.alerts_today if self.day == day else 0


@dataclass(frozen=True)
class Alert:
    """One alert message, read (payload.parse)."""

    position: str  # long, short or flat: where the strategy should be after this alert
    fired_s: int
    size: float | None = None  # absolute, as TradingView counts it
    before: str | None = None
    before_size: float | None = None
    order_id: str | None = None
    action: str | None = None  # buy or sell
    contracts: float | None = None
    exchange: str | None = None  # the chart's, without a delayed-data ending
    ticker: str | None = None
    delayed: bool = False
    interval: str | None = None
    bar_s: int | None = None

    @property
    def target(self) -> int:
        """+1 long, -1 short, 0 flat."""
        return {LONG: 1, SHORT: -1}.get(self.position, 0)

    @property
    def before_target(self) -> int | None:
        return None if self.before is None else {LONG: 1, SHORT: -1}.get(self.before, 0)

    @property
    def claim(self) -> str:
        """What makes two deliveries the same alert: the second it fired, its order id and where
        it points."""
        size = "" if self.size is None else f"{self.size:g}"
        return f"{self.fired_s}|{self.order_id or '-'}|{self.position}:{size}"


def from_row(kind: type, row: object):
    """A frozen value from an ORM row that has the same column names."""
    return kind(**{f.name: getattr(row, f.name) for f in fields(kind) if hasattr(row, f.name)})
