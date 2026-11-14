"""Business income from trading (M7.28): derivative round trips, expiry, turnover, flags.

Derivatives are paired first in, first out per taxpayer, account and contract, long and short
alike: a sell with nothing held opens a short. What is still open after the contract's expiry is
closed on the expiry day at the settlement price the caller supplies; with none, an option is
taken as expired worthless (and flagged) and a future is listed as unknown and left out of every
total. A trade belongs to the year it is closed in. Intraday shares (from ``lots``) are the
speculative bucket. Flags are sentences that say "ask your CA", never a verdict.
"""

from __future__ import annotations

from typing import NamedTuple

from . import rules, words
from .classify import DERIVATIVE, head_of, kind_of_contract
from .model import Realised
from .units import allocate, fy_first, fy_of, share_of, value_paise

SPECULATIVE = "speculative"


class Paired(NamedTuple):
    """Closed pieces; futures past expiry with no settlement price; positions still open. The
    last two are rows of (taxpayer, account, contract, side, open day, qty_u, value paise)."""

    realised: tuple[Realised, ...]
    unknown_settlement: tuple[tuple, ...]
    open: tuple[tuple, ...]


def pair(trades, settlements=None, as_of_day: int = 0) -> Paired:
    """Pair derivative trades. ``settlements`` maps a contract to its final settlement price
    (price_u); a contract counts as expired once ``as_of_day`` is past its expiry day."""
    settlements = settlements or {}
    groups: dict[tuple, list] = {}
    for trade in sorted(trades, key=lambda t: (t.day, t.seq)):
        if head_of(trade) == DERIVATIVE:
            groups.setdefault((trade.taxpayer, trade.account, trade.instrument), []).append(trade)
    out = Paired([], [], [])
    for (taxpayer, account, contract), group in sorted(groups.items()):
        kind, held = kind_of_contract(contract), []  # held: [side, day, qty_u, paise] oldest first

        def close(
            first, day, part, value, kind=kind, scope=(taxpayer, account, contract), flag=None
        ):
            """Close ``part`` of the oldest open piece against ``value`` on ``day``."""
            cost = share_of(first[3], part, first[2])
            first[2], first[3] = first[2] - part, first[3] - cost
            buy, sell = (cost, value) if first[0] == "BUY" else (value, cost)
            out.realised.append(
                Realised(*scope, kind, first[1], day, part, buy, sell, sell - buy, flag)
            )

        for trade in group:
            left, amount = trade.qty_u, trade.amount_paise
            while left and held and held[0][0] != trade.side:
                part = min(left, held[0][2])
                value = share_of(amount, part, left)
                close(held[0], trade.day, part, value)
                left, amount = left - part, amount - value
                if not held[0][2]:
                    held.pop(0)
            if left:
                held.append([trade.side, trade.day, left, amount])
        expiry, price = contract[2], settlements.get(contract)
        for piece in held:
            row = (taxpayer, account, contract, piece[0], piece[1], piece[2], piece[3])
            if as_of_day <= expiry:
                out.open.append(row)
            elif price is None and kind == "futures":
                out.unknown_settlement.append(row)
            else:  # an option with no settlement price is taken as expired worthless
                flag = "assumed_zero" if price is None else None
                close(piece, expiry, piece[2], value_paise(piece[2], price or 0), flag=flag)
    return Paired(*(tuple(part) for part in out))


def turnover_of(realised: Realised, method: str) -> int:
    """One closed piece's turnover: the size of its profit or loss; under the older method an
    option also adds the premium it was sold for."""
    extra = method == "abs_pnl_plus_sale_premium" and realised.kind == "options"
    return abs(realised.pnl_paise) + (realised.sell_paise if extra else 0)


def split_share_charges(total_paise: int, intraday_value: int, delivery_value: int) -> list[int]:
    """A year's share-market charges split between intraday and delivery by traded value."""
    return allocate(total_paise, [intraday_value, delivery_value])


def income(
    realised, fy: int, method=None, charges_paise: int = 0, speculative_charges_paise: int = 0
):
    """The year's business income in two buckets from the pieces closed in ``fy``: gross, the
    charges given (all parts, STT included), net, turnover by the method, and the flags."""
    method = rules.choice(rules.TURNOVER_METHOD, fy_first(fy), method)
    buckets = {
        "non_speculative": {"gross_paise": 0, "charges_paise": charges_paise},
        SPECULATIVE: {"gross_paise": 0, "charges_paise": speculative_charges_paise},
    }
    for bucket in buckets.values():
        bucket.update(turnover_paise=0, by_kind={})
    for r in realised:
        if fy_of(r.close_day) != fy:
            continue
        bucket = buckets[SPECULATIVE if r.kind == SPECULATIVE else "non_speculative"]
        kind = bucket["by_kind"].setdefault(
            r.kind, {"gross_paise": 0, "turnover_paise": 0, "count": 0}
        )
        for row in (bucket, kind):
            row["gross_paise"] += r.pnl_paise
            row["turnover_paise"] += turnover_of(r, method)
        kind["count"] += 1
    for bucket in buckets.values():
        bucket["net_paise"] = bucket["gross_paise"] - bucket["charges_paise"]
    turnover = sum(bucket["turnover_paise"] for bucket in buckets.values())
    net = sum(bucket["net_paise"] for bucket in buckets.values())
    return {**buckets, "turnover_method": method, "turnover_paise": turnover,
            "flags": _flags(fy, turnover, net)}  # fmt: skip


def _flags(fy: int, turnover: int, net: int) -> list[str]:
    if not turnover:
        return []
    limits = rules.at(rules.AUDIT, fy_first(fy))
    base, digital = (_crore(limits[key]) for key in ("base", "digital"))
    flags = []
    if turnover > limits["digital"]:
        flags.append(words.audit_above_digital(digital))
    elif turnover > limits["base"]:
        flags.append(words.audit_above_base(base, digital))
    if net * 10_000 < rules.at(rules.LOW_MARGIN_BP, fy_first(fy)) * turnover:
        flags.append(words.LOW_MARGIN)
    return flags


def _crore(paise: int) -> str:
    return f"Rs {paise // rules.CRORE} crore"


def differs(realised, upstox_pnl: dict[str, int], fy: int) -> list[dict]:
    """Scrips whose own profit for the year is off Upstox's report by more than the rule's
    rupee, with both figures (None where one side has no such scrip). Nothing is substituted."""
    own: dict[str, int] = {}
    for r in realised:
        if fy_of(r.close_day) == fy:
            scrip = r.instrument if isinstance(r.instrument, str) else r.instrument[1]
            own[scrip] = own.get(scrip, 0) + r.pnl_paise
    limit = rules.at(rules.DIFFERS_ABOVE, fy_first(fy))
    return [
        {"scrip": scrip, "own_paise": own.get(scrip), "upstox_paise": upstox_pnl.get(scrip)}
        for scrip in sorted({*own, *upstox_pnl})
        if abs(own.get(scrip, 0) - upstox_pnl.get(scrip, 0)) > limit
    ]
