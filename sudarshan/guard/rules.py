"""Does an order add risk or take it off? (M5.13, design section 4)

Pure: no I/O, no clock, no logging. Every input arrives as an argument and every decision leaves
as a return value, as in services/risk/ (CLAUDE.md "Risk rules live in services/risk/"). The gate
(gate.py) reads the broker's book and its own claims and asks these.

Notation:

- k: the position, (exchange, symbol, product). At Upstox MIS is product I and NRML and CNC are
  product D, each a position of its own; in the sandbox every product is its own position.
- p: the signed filled net quantity of k in the broker's own position book.
- c: what this process has already sent on the order's side to reduce k and the book does not
  show yet (claims).
- s: +1 for BUY, -1 for SELL; q: the order's quantity.
- r: what may still be exited, |p| - c when s is opposite to p, and 0 otherwise.

An order reduces risk when s is opposite to p and q <= r. Anything else opens a position, adds to
one or crosses through zero, and adds risk. A sell that would close a long and open a short adds
risk as a whole: the refusal names the exit that fits, and an order is never clipped.
"""

from __future__ import annotations

from dataclasses import dataclass

BUY, SELL = 1, -1

#: Where a CNC sell can only sell shares held (Upstox refuses one beyond them). On a derivative
#: exchange CNC is product D, which is NRML at Upstox, so it is judged like any order there.
EQUITY_CASH = frozenset({"NSE", "BSE"})

#: Upstox's product codes (broker/upstox/mapping/transform_data.map_product_type, whose default
#: for anything else is I).
UPSTOX_PRODUCTS = {"MIS": "I", "NRML": "D", "CNC": "D"}

# Why an order reduces risk.
EXIT = "exit"  # it closes some or all of p
CNC_SELL = "cnc_sell"  # it sells shares held; holdings are not in the positions book
LESS_RISK = "less_risk"  # a modify that leaves its order adding no more risk than before
# Why an order adds risk.
OPENS = "opens"  # no position: p is 0
ADDS = "adds"  # the same side as p
CROSSES = "crosses"  # more than r: it would close the position and open the other side
ON_ITS_WAY = "on_its_way"  # r is 0 although p is not: exits already sent cover the position
OCO = "oco"  # a two-leg GTT: Upstox places it with an entry, so it opens a position
UNKNOWN = "unknown"  # its side or quantity cannot be read

PRICE_TYPES = ("MARKET", "LIMIT", "SL", "SL-M")


@dataclass(frozen=True)
class Verdict:
    """What an order does to its position."""

    reducing: bool
    reason: str
    position: int = 0  # p
    claimed: int = 0  # c on the order's side
    room: int = 0  # r: what may still be exited

    @property
    def adds_risk(self) -> bool:
        return not self.reducing


@dataclass(frozen=True)
class OrderTerms:
    """What a modify can change: the quantity and the price terms."""

    quantity: int
    price_type: str
    price: float = 0.0
    trigger: float = 0.0


@dataclass(frozen=True)
class WorkingOrder:
    """An order working at the broker: its side, what has filled, and its terms."""

    side: int
    filled: int
    terms: OrderTerms


def side_of(action: object) -> int | None:
    """+1 for BUY, -1 for SELL, None for anything else."""
    word = str(action or "").strip().upper()
    return BUY if word == "BUY" else SELL if word == "SELL" else None


def quantity_of(value: object) -> int | None:
    """A positive whole quantity, or None when ``value`` is not one."""
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not 0 < number < float("inf") or number != int(number):  # NaN, zero, fractions
        return None
    return int(number)


def price_of(value: object) -> float:
    try:
        return float(str(value).strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def position_product(pipe: str, product: object) -> str:
    """The product a position is kept under: Upstox's code for Live, the product for the sandbox."""
    word = str(product or "").strip().upper()
    if pipe == "live":
        return UPSTOX_PRODUCTS.get(word, "I")
    return word


def is_cnc_sell(exchange: object, product: object, side: int | None) -> bool:
    """A CNC sell on a cash exchange: it can only sell shares held, so it never adds risk."""
    return (
        side == SELL
        and str(product or "").strip().upper() == "CNC"
        and str(exchange or "").strip().upper() in EQUITY_CASH
    )


def _whole(value: object) -> int:
    number = float(value)
    if number != int(number):
        raise ValueError(f"a position quantity that is not whole: {value!r}")
    return int(number)


def upstox_position(rows: object, instrument_token: str, product_code: str) -> int:
    """p for one position, from Upstox's raw short-term positions (``data`` of the book).

    ``quantity`` is the net filled quantity, the part carried in overnight included. Rows that
    are not about this instrument and product are not read.

    Raises:
        ValueError: a row about this position carries a quantity that cannot be read, so the
            position is unknown.
    """
    total = 0
    for row in rows if isinstance(rows, list) else ():
        if not isinstance(row, dict):
            continue
        if str(row.get("instrument_token") or "") != instrument_token:
            continue
        if str(row.get("product") or "").strip().upper() != product_code:
            continue
        total += _whole(row.get("quantity") or 0)
    return total


def classify(
    side: int | None,
    quantity: int | None,
    position: int,
    claimed: int = 0,
    *,
    cnc_sell: bool = False,
) -> Verdict:
    """Whether a new order reduces or adds risk.

    Args:
        side: +1 BUY, -1 SELL (None: unreadable).
        quantity: The order's quantity (None: unreadable).
        position: p.
        claimed: c, the claims on this order's side.
        cnc_sell: The order is a CNC sell on a cash exchange.
    """
    if cnc_sell:
        return Verdict(True, CNC_SELL, position, claimed, 0)
    if side is None or quantity is None:
        return Verdict(False, UNKNOWN, position, claimed, 0)
    if position == 0:
        return Verdict(False, OPENS, 0, claimed, 0)
    if (side > 0) == (position > 0):
        return Verdict(False, ADDS, position, claimed, 0)
    room = max(0, abs(position) - max(0, claimed))
    if quantity <= room:
        return Verdict(True, EXIT, position, claimed, room)
    return Verdict(False, ON_ITS_WAY if room == 0 else CROSSES, position, claimed, room)


def smart_order(
    target: int, position: int, side: int | None = None, quantity: int | None = None
) -> tuple[int, int] | None:
    """The order a smart order sends, as the broker works it out (place_smartorder_api).

    Returns ``(side, quantity)``, or None when nothing is sent. To a position size of 0 from a
    flat book with a quantity, it sends the request's own order, a fresh trade.

    A smart order reduces risk exactly when its target lies between 0 and p inclusive: the gate
    judges the order this returns, at the broker, so no other rule is needed for smart orders.
    """
    if target == 0 and position == 0 and quantity:
        return (side, quantity) if side is not None else None
    if target == position:
        return None
    return (BUY, target - position) if target > position else (SELL, position - target)


def no_more_risk(order: WorkingOrder, new: OrderTerms) -> bool:
    """Whether a modify leaves a working order adding no more risk than it did.

    No more quantity, the same price type, a limit no more marketable (a buy not raised, a sell
    not lowered) and a stop no nearer the market (a buy stop not lowered, a sell stop not raised).
    """
    old = order.terms
    if new.quantity > old.quantity or new.price_type != old.price_type:
        return False
    buy = order.side == BUY
    if new.price_type in ("LIMIT", "SL"):
        if (new.price > old.price) if buy else (new.price < old.price):
            return False
    if new.price_type in ("SL", "SL-M"):
        if (new.trigger < old.trigger) if buy else (new.trigger > old.trigger):
            return False
    return True


def classify_modify(
    order: WorkingOrder,
    new: OrderTerms,
    position: int,
    claimed: int = 0,
    *,
    cnc_sell: bool = False,
) -> Verdict:
    """Whether a modify of a working order reduces or adds risk.

    The order as modified, its remaining quantity (new quantity less what has filled), is judged
    against p and c, where c leaves out the order's own claim. A modify that only takes risk off
    an order that adds it (less quantity, a less marketable limit, a stop moved away) passes too.
    """
    remaining = new.quantity - order.filled
    if remaining <= 0:
        return Verdict(True, LESS_RISK, position, claimed, 0)  # nothing left to fill
    verdict = classify(order.side, remaining, position, claimed, cnc_sell=cnc_sell)
    if verdict.reducing or not no_more_risk(order, new):
        return verdict
    return Verdict(True, LESS_RISK, position, claimed, verdict.room)


def classify_gtt(
    trigger_type: object,
    side: int | None,
    quantity: int | None,
    position: int,
    claimed: int = 0,
    *,
    cnc_sell: bool = False,
) -> Verdict:
    """Whether a GTT reduces or adds risk.

    A single GTT is judged as a new order: a stop or a target on a position held, for no more
    than it holds. A two-leg GTT (OCO) always adds risk: Upstox places it as MULTIPLE with an
    entry, which opens a position when it is placed (broker/upstox/api/gtt_api.py). Once a GTT
    triggers at Upstox, the order it places is not seen here.
    """
    if str(trigger_type or "").strip().upper() == "OCO":
        return Verdict(False, OCO, position, claimed, 0)
    return classify(side, quantity, position, claimed, cnc_sell=cnc_sell)


def exit_expectation(position: int, claimed_before: int, quantity: int) -> int:
    """|p| the book shows once this exit and those claimed before it on its side have filled."""
    return max(0, abs(position) - max(0, claimed_before) - quantity)


def exit_shown(position_now: int, position_then: int, expect: int) -> bool:
    """Whether the book now shows an exit claimed when it said ``position_then``.

    It does once the position is flat or on the other side, or no larger than expected.
    """
    if position_now == 0 or (position_now > 0) != (position_then > 0):
        return True
    return abs(position_now) <= expect
