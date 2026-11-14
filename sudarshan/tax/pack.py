"""The tax pack (M15.5): one taxpayer's financial year, the one answer both services read.

``build_pack`` runs the whole core over what the portfolio's adapter gathered and returns plain
data (dicts, lists, integers, text) that can be stored and sent as JSON as it is. It works
nothing out itself: lots, gains, the year's schedule, business income and set-off each come from
their own module. Every figure is "to date" while the year is running.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass

from . import business, capgains, losses, lots, rules, words
from .classify import DERIVATIVE_SEGMENTS, NOT_COMPUTED, head_of
from .model import Settings
from .units import fy_first, fy_label, fy_last, fy_of, rupees


@dataclass(frozen=True, slots=True)
class PackInputs:
    """Everything a pack is built from. ``charges`` maps a segment to Upstox's own breakdown
    for the year, of which ``total_paise`` and ``stt_paise`` are read (a segment not read yet is
    absent). ``upstox_pnl`` maps a derivative scrip to Upstox's own profit for the cross-check.
    ``as_of_day`` is "today" as the caller says it; nothing here reads a clock."""

    fy: int
    taxpayer: int
    as_of_day: int
    as_of_ms: int
    trades: tuple = ()
    opening: tuple = ()
    actions: tuple = ()
    fmv: dict = field(default_factory=dict)
    carries: tuple = ()
    settings: Settings = Settings()
    all_delivery: frozenset = frozenset()
    aliases: dict = field(default_factory=dict)
    settlements: dict = field(default_factory=dict)
    charges: dict = field(default_factory=dict)
    charges_state: str = "ready"
    sources: tuple = ()
    missing: tuple = ()
    upstox_pnl: dict | None = None


def plain(value):
    """Model objects as plain data: dataclasses as dicts, tuples as lists."""
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def build_pack(given: PackInputs) -> dict:
    fy, who, notes = given.fy, given.taxpayer, []
    trades = [t for t in given.trades if t.taxpayer == who]
    carries = [c for c in given.carries if c.taxpayer == who]
    in_year = [t for t in trades if fy_of(t.day) == fy]
    book = lots.replay(
        trades,
        [lot for lot in given.opening if lot.taxpayer == who],
        given.actions,
        given.all_delivery,
        given.aliases,
    )
    paired = business.pair(trades, given.settlements, given.as_of_day)
    realised = [r for r in (*paired.realised, *book.intraday) if fy_of(r.close_day) == fy]

    # Share-market charges: intraday's part by traded value, the rest over delivery sales.
    share = given.charges.get("EQ")
    intraday_value = sum(
        r.buy_paise + r.sell_paise for r in book.intraday if fy_of(r.close_day) == fy
    )
    traded = sum(t.amount_paise for t in in_year if t.segment == "EQ" and t.kind_hint != "buyback")
    values = (intraday_value, traded - intraday_value)
    total, stt = (
        business.split_share_charges((share or {}).get(key) or 0, *values)
        for key in ("total_paise", "stt_paise")
    )
    expenses = None
    if share is not None and given.settings.allocate_expenses:
        expenses = capgains.allocate_expenses(book.disposals, fy, total[1] - stt[1])
        notes.append(words.EXPENSES_ALLOCATED)
    elif share is not None:
        notes.append(words.EXPENSES_LEFT_OUT)
    gains = [
        capgains.gain_of(
            d, given.fmv, given.settings, expenses[n] if expenses and not d.kind_hint else None
        )
        for n, d in enumerate(book.disposals)
        if fy_of(d.sale_day) == fy
    ]
    schedule = capgains.year_schedule(gains, fy, carries, given.settings)

    derivative_charges = sum(
        (given.charges.get(s) or {}).get("total_paise") or 0 for s in DERIVATIVE_SEGMENTS
    )
    income = business.income(
        realised, fy, given.settings.turnover_method, derivative_charges, total[0]
    )
    set_off = losses.business_setoff(
        income["non_speculative"]["net_paise"], income["speculative"]["net_paise"], carries, fy
    )
    worthless = sum(1 for r in realised if r.flag == "assumed_zero")
    unknown = [row for row in paired.unknown_settlement if fy_of(row[2][2]) == fy]
    outside = [t for t in in_year if t.side == "SELL" and head_of(t) == NOT_COMPUTED]

    segments = {t.segment for t in in_year} & {"EQ", *DERIVATIVE_SEGMENTS}
    notes += [
        words.CHARGES_UNKNOWN.format(segment=s) for s in sorted(segments - set(given.charges))
    ]
    notes += book.notes
    for count, say in (
        (len(schedule["unknown_cost"]), words.unknown_cost),
        (len(schedule["needs_fmv"]), words.needs_fmv),
        (worthless, words.assumed_zero),
        (len(unknown), words.unknown_settlement),
    ):
        if count:
            notes.append(say(count))
    if len({pool["rate_bp"] for pool in schedule["by_bucket"] if pool["term"] == "long"}) > 1:
        notes.append(words.exempt_order(schedule["exemption"]["order"]))
    if any(g.buyback for g in gains):
        notes.append(words.BUYBACK_NOTE)
    taxable = schedule["taxable"]
    if set_off["available_other_heads_paise"] and taxable["st_paise"] + taxable["lt_paise"]:
        notes.append(words.BUSINESS_LOSS_AND_GAINS)
    not_computed = list(words.NOT_COMPUTED)
    if outside:
        value = rupees(sum(t.amount_paise for t in outside))
        not_computed.insert(0, words.not_computed_sales(len(outside), value))

    schedule["indicative_tax"]["cess_label"] = words.CESS_LABEL
    schedule["set_off_words"] = losses.sentences(
        {**schedule, "steps": schedule["set_off"]}, fy, words.CAPITAL_ORDER
    )
    buyback_dividends = schedule.pop("buyback_dividend_paise")
    return plain({
        "fy": fy,
        "fy_label": fy_label(fy),
        "taxpayer": who,
        "as_of_ms": given.as_of_ms,
        "as_of_day": given.as_of_day,
        "running": fy_first(fy) <= given.as_of_day <= fy_last(fy),
        "note": words.WORKING_PAPER,
        "rules_version": rules.RULES_VERSION,
        "inputs": {"sources": given.sources, "missing": given.missing},
        "capital_gains": {**schedule, "expenses_allocated": expenses is not None},
        "business": {
            **income,
            "turnover_method_label": words.TURNOVER_METHODS[income["turnover_method"]],
            "taxable": set_off["taxable"],
            "available_other_heads_paise": set_off["available_other_heads_paise"],
            "set_off": set_off["steps"],
            "set_off_words": losses.sentences(set_off, fy),
            "carried": set_off["carried"],
            "lapsed": set_off["lapsed"],
            "own_figures": set_off["own_figures"],
            "differs": [] if given.upstox_pnl is None
            else business.differs(paired.realised, given.upstox_pnl, fy),
            "unknown_settlement": unknown,
            "open": paired.open,
            "assumed_zero_count": worthless,
        },
        "dividends": {"by_payer": [], "buyback_paise": buyback_dividends,
                      "total_paise": buyback_dividends, "tds_expected_paise": None,
                      "unconfirmed_count": 0},
        "charges": {"by_segment": given.charges, "state": given.charges_state},
        "not_computed": not_computed,
        "not_computed_sales": [
            {"instrument": t.instrument, "day": t.day, "amount_paise": t.amount_paise}
            for t in outside
        ],
        "notes": notes,
    })  # fmt: skip
