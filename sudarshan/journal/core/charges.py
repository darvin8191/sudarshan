"""The charges estimate (JA6, M14.2; design 5.6 as amended by cross-design Y23).

Everything this module produces is an estimate and is shown as "est." until Upstox's own charges
report replaces it (next release).

* Brokerage is charged per executed ORDER, not per fill: an order filled in three parts pays
  one brokerage.
* STT, exchange, SEBI and stamp are charged on each order's turnover for the day (the sum of
  its fills' quantity x price), by segment, each rounded half-up to the paisa PER ORDER before
  anything is added.
* GST is 18% of the order's brokerage, exchange and SEBI charges (the already rounded figures),
  half-up.
* ``RATE_CARD`` holds dated rows: for a day, the row with the greatest ``from_day`` not after it
  applies; a fill dated before a row's first date uses the earliest row. Brokerage rows carry a
  plan column. The owner's plan is Upstox Plus at Rs 30 an executed order, for every order
  whatever its surface (cross-design Y23: how Plus meets the Rs 10 API-order offer is not
  confirmed, and overstating charges never reads better than the truth). The ``standard`` plan
  is kept for the case where the owner changes plan, and carries the surface split.
* BSE, currency, commodity and any segment without a row are NOT MODELLED: the estimate says so
  and charges nothing, rather than guess.

Rates were read from the Upstox charges page on 14 Nov 2026 (summarised by a fetch tool, so every
row is also marked "to verify" against the page itself). Exchange rates moved on 1 Mar 2026 and
STT on 1 Apr 2026.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal
from fractions import Fraction
from typing import Any, NamedTuple

from sudarshan.journal.core import kinds, money
from sudarshan.journal.core.pairing import product_class

DEFAULT_PLAN = "plus"
GST_RATE = Fraction(18, 100)
PARTS = ("brokerage", "stt", "exchange", "sebi", "stamp", "gst")
#: What a rate row without an earlier date is dated by: the first day of the page's table.
CARD_START = 20241001
#: The card as shipped: the day its rates were read (bump it with any row below).
CARD_VERSION = "2026-11-14"


def _rate(text: str) -> Fraction:
    return Fraction(Decimal(text))


class Row(NamedTuple):
    """A statutory rate: ``rate`` is a fraction of the order's turnover."""

    from_day: int
    segment: str
    side: str  # BUY, SELL or BOTH
    kind: str  # stt, exchange, sebi or stamp
    rate: Fraction


class BrokerageRow(NamedTuple):
    """A flat brokerage per executed order, in paise, for a plan and a surface group."""

    from_day: int
    plan: str
    group: str  # api (any surface but the Upstox app), app (the Upstox app) or any
    paise: int


_SEGMENTS_FO = ("options", "futures")
_SEGMENTS_EQ = ("equity_delivery", "equity_intraday")
# to verify: every row below against https://upstox.com/brokerage-charges/ (read 14 Nov 2026).
RATE_CARD: tuple[Row, ...] = (
    # STT on the sell side of F&O: raised on 1 Apr 2026. To verify.
    Row(CARD_START, "options", "SELL", "stt", _rate("0.001")),
    Row(20260401, "options", "SELL", "stt", _rate("0.0015")),
    Row(CARD_START, "futures", "SELL", "stt", _rate("0.0002")),
    Row(20260401, "futures", "SELL", "stt", _rate("0.0005")),
    Row(CARD_START, "equity_delivery", "BOTH", "stt", _rate("0.001")),
    Row(CARD_START, "equity_intraday", "SELL", "stt", _rate("0.00025")),
    # NSE transaction charges, both sides: raised on 1 Mar 2026. To verify.
    Row(CARD_START, "options", "BOTH", "exchange", _rate("0.000350300")),
    Row(20260301, "options", "BOTH", "exchange", _rate("0.000355300")),
    Row(CARD_START, "futures", "BOTH", "exchange", _rate("0.0000173")),
    Row(20260301, "futures", "BOTH", "exchange", _rate("0.0000183")),
    Row(CARD_START, "equity_delivery", "BOTH", "exchange", _rate("0.0000297")),
    Row(20260301, "equity_delivery", "BOTH", "exchange", _rate("0.0000307")),
    Row(CARD_START, "equity_intraday", "BOTH", "exchange", _rate("0.0000297")),
    Row(20260301, "equity_intraday", "BOTH", "exchange", _rate("0.0000307")),
    # SEBI turnover fee, Rs 10 a crore, every segment, both sides. To verify.
    *(Row(CARD_START, seg, "BOTH", "sebi", _rate("0.000001")) for seg in kinds.CHARGE_SEGMENTS[:4]),
    # Stamp duty, buy side only. To verify.
    Row(CARD_START, "options", "BUY", "stamp", _rate("0.00003")),
    Row(CARD_START, "futures", "BUY", "stamp", _rate("0.00002")),
    Row(CARD_START, "equity_delivery", "BUY", "stamp", _rate("0.00015")),
    Row(CARD_START, "equity_intraday", "BUY", "stamp", _rate("0.00003")),
)
# to verify: Plus is listed by Upstox as "up to Rs 30 per order"; the Rs 10 API offer for the
# standard plan until 31 Dec 2026 is the design's figure and is not on the page.
BROKERAGE_CARD: tuple[BrokerageRow, ...] = (
    BrokerageRow(CARD_START, "plus", "any", 3000),
    BrokerageRow(CARD_START, "standard", "app", 2000),
    BrokerageRow(CARD_START, "standard", "api", 1000),
    BrokerageRow(20270101, "standard", "api", 2000),
)


def segment_of(exchange: str, symbol: str, product: str) -> str:
    """The charges segment of a fill, or ``not_modelled``."""
    ex, sym = str(exchange).upper(), str(symbol).upper()
    if ex == "NFO":
        if sym.endswith(("CE", "PE")):
            return "options"
        if sym.endswith("FUT"):
            return "futures"
        return "not_modelled"
    if ex == "NSE":
        return "equity_intraday" if product_class(product) == "intraday" else "equity_delivery"
    return "not_modelled"


def _statutory(segment: str, side: str, kind: str, day: int) -> Fraction:
    rows = [
        r for r in RATE_CARD if r.segment == segment and r.kind == kind and r.side in (side, "BOTH")
    ]
    if not rows:
        return Fraction(0)
    rows.sort(key=lambda r: r.from_day)
    chosen = rows[0]
    for r in rows:
        if r.from_day <= day:
            chosen = r
    return chosen.rate


def brokerage_paise(plan: str, surface: str | None, day: int) -> int:
    """Brokerage of one executed order, in paise, for a plan on a day."""
    if plan not in kinds.CHARGE_PLANS:
        raise ValueError(f"unknown plan: {plan!r}")
    group = "app" if surface == "upstox_app" else "api"
    rows = [r for r in BROKERAGE_CARD if r.plan == plan and r.group in (group, "any")]
    rows.sort(key=lambda r: r.from_day)
    chosen = rows[0]
    for r in rows:
        if r.from_day <= day:
            chosen = r
    return chosen.paise


def _round(turnover_u: int, rate: Fraction) -> int:
    value = Fraction(turnover_u, money.U_PER_PAISE) * rate
    return money.div_half_up(value.numerator, value.denominator)


def estimate_order(
    fills: Iterable[Mapping[str, Any]], plan: str = DEFAULT_PLAN
) -> tuple[dict[str, int], bool]:
    """The estimate of one order's fills (one order, one day): ``(parts, modelled)``.

    Every part is in paise, rounded half-up for this order; ``total`` is their sum.
    """
    rows = list(fills)
    first = rows[0]
    segment = segment_of(first["exchange"], first["symbol"], first["product"])
    parts = dict.fromkeys(PARTS, 0)
    if segment == "not_modelled":
        parts["total"] = 0
        return parts, False
    day = int(first["day"])
    side = str(first["action"]).upper()
    turnover = sum(money.value_u(int(f["qty"]), int(f["price_u"])) for f in rows)
    parts["brokerage"] = brokerage_paise(plan, first.get("surface"), day)
    for kind in ("stt", "exchange", "sebi", "stamp"):
        parts[kind] = _round(turnover, _statutory(segment, side, kind, day))
    taxable = parts["brokerage"] + parts["exchange"] + parts["sebi"]
    parts["gst"] = money.div_half_up(taxable * GST_RATE.numerator, GST_RATE.denominator)
    parts["total"] = sum(parts[k] for k in PARTS)
    return parts, True


def estimate(fills: Iterable[Mapping[str, Any]], plan: str = DEFAULT_PLAN) -> dict[str, Any]:
    """The estimate of a set of fills (a trip's, a day's): orders found by ``order_id`` and day.

    Fills carry ``order_id``, ``day``, ``action``, ``qty``, ``price_u``, ``exchange``,
    ``symbol``, ``product`` and ``surface``. Returns ``{"parts", "modelled", "plan", "orders"}``;
    ``modelled`` is False when any order is in a segment this card does not cover (its charges
    are then left out, not guessed).
    """
    orders: dict[tuple[str, str, int], list[Mapping[str, Any]]] = {}
    for f in fills:
        key = (str(f.get("mode") or "live"), str(f["order_id"]), int(f["day"]))
        orders.setdefault(key, []).append(f)
    total = dict.fromkeys((*PARTS, "total"), 0)
    modelled = True
    for rows in orders.values():
        parts, ok = estimate_order(rows, plan)
        modelled = modelled and ok
        for k in total:
            total[k] += parts[k]
    return {
        "parts": {
            "brokerage_paise": total["brokerage"],
            "stt_paise": total["stt"],
            "exchange_paise": total["exchange"],
            "sebi_paise": total["sebi"],
            "stamp_paise": total["stamp"],
            "gst_paise": total["gst"],
            "total_paise": total["total"],
        },
        "modelled": modelled,
        "plan": plan,
        "orders": len(orders),
    }


def estimate_gap(estimate_paise: int, actual_paise: int) -> dict[str, Any]:
    """Estimate against Upstox's actual for a month: flagged when it is off by more than 5%."""
    diff = estimate_paise - actual_paise
    pct = money.ratio_str(diff * 100, actual_paise, 2) if actual_paise else None
    return {
        "diff_paise": diff,
        "diff_pct": pct,
        "flagged": bool(actual_paise) and abs(diff) * 100 > 5 * actual_paise,
    }
