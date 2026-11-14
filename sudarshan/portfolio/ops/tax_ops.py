"""The tax working paper (phase P7, lane P: M7.27; design p7 8, 9.1).

``tax_get`` is one taxpayer's financial year: the adapter (``tax_adapter``) gathers the inputs and
the tax core (``sudarshan/tax``) works out every figure; nothing here computes tax. A loss carried
from an earlier year comes from that year's own paper, worked out from the oldest year Upstox
keeps up to the year asked, and a loss the owner entered for a year wins over the computed one.
The view starts no read of Upstox: it works on what the reader keeps and says what is not read.

``tax_inputs``, ``tax_input_set`` and ``tax_input_delete`` keep what the reports cannot say: the
31 Jan 2018 prices, purchases from before the records, losses brought forward, days of delivery,
buyback tenders and the readings of the open rules. A dry run of a save says what it would change
in the year's figures and writes nothing. Every op waits for ``SUDARSHAN_TAX`` (``ops.switched``).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from sudarshan.tax.model import Carry
from sudarshan.tax.pack import build_pack
from sudarshan.tax.units import fy_label, rupees

from .. import reports, tax_adapter
from ..core.days import day_of_instant, financial_year, is_day
from ..core.kinds import (
    CARRY_BUCKETS,
    EXEMPT_ORDERS,
    LT_BOUNDARIES,
    REPORT_YEARS,
    TAX_INPUT_KINDS,
    TURNOVER_METHODS,
    WORDS,
)
from ..errors import ApiError
from ..store import Invalid, NotFound, Refused, VersionConflict
from . import op
from .common import OWNER, answer, maybe_whole, need, whole

ASK = "The request was incomplete. Reload the page."
CHOOSE = "Choose one of the readings offered."
NO_MEMBER = "That family member is not in your portfolio. Reload the page."
NOT_THEIRS = "That account is not one of this member's. Choose another."
ISIN = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")
TRADE_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")
MAX = 10**15
#: The columns a stored input row keeps to itself.
PRIVATE = frozenset({"principal", "request_id", "created_at", "updated_at"})

#: Each kind of input: its table (None: an owner setting, ``tax:<member id>``) and its fields.
INPUTS: dict[str, tuple[str | None, tuple[str, ...]]] = {
    "opening_lot": ("tax_opening_lot", ("member_id", "account_id", "isin", "acquired_day", "qty_u",
                                        "cost_paise", "note")),
    "fmv": ("tax_fmv", ("isin", "fmv_u")),
    "carry": ("tax_carry", ("member_id", "loss_fy", "bucket", "paise", "note")),
    "day_override": ("tax_day_override", ("member_id", "account_id", "isin", "day")),
    "trade_mark": ("tax_trade_mark", ("member_id", "account_id", "isin", "day", "qty_u", "price_u",
                                      "trade_id")),
    "settings": (None, ("member_id", "turnover_method", "exempt_order", "lt_boundary",
                        "allocate_expenses")),
}  # fmt: skip
#: What an entry of the owner's always says; rows of another source are not his to write.
FIXED = {"opening_lot": {"source": "owner"}, "fmv": {"source": "owner"},
         "carry": {"source": "owner"}, "day_override": {"treat": "delivery"},
         "trade_mark": {"mark": "buyback"}}  # fmt: skip
#: One row per key in these tables, and the sentence when a new one would make two.
UNIQUE = {
    "tax_fmv": (("isin",), "A 31 Jan 2018 price for this share is already entered. Edit that one."),
    "tax_carry": (("member_id", "loss_fy", "bucket", "source"),
                  "A loss of this year and kind is already entered. Edit that one."),
    "tax_day_override": (("member_id", "account_id", "isin", "day"),
                         "This day is already marked as delivery for this share."),
}  # fmt: skip


def _int(low: int, high: int = MAX) -> Callable[[Any, int], bool]:
    return lambda v, today: v.__class__ is int and low <= v <= high


def _one_of(names: tuple[str, ...]) -> Callable[[Any, int], bool]:
    return lambda v, today: v is None or v in names


#: How each field is checked (its value and today), and the sentence when it is not right.
CHECKS: dict[str, tuple[Callable[[Any, int], bool], str]] = {
    "member_id": (_int(1), ASK),
    "account_id": (_int(1), ASK),
    "isin": (lambda v, t: isinstance(v, str) and ISIN.fullmatch(v) is not None,
             "An ISIN is 12 letters and digits, such as INE002A01018."),
    "acquired_day": (lambda v, t: is_day(v) and v <= t,
                     "Enter the day it was bought: a real date, not after today."),
    "day": (lambda v, t: is_day(v) and v <= t, "Enter a real date, not after today."),
    "qty_u": (_int(1), "Enter the quantity as a number above zero."),
    "cost_paise": (_int(0), "Enter the cost as an amount of zero or more."),
    "fmv_u": (_int(1), "Enter the price of 31 Jan 2018 as a number above zero."),
    "price_u": (_int(1), "Enter the price as a number above zero."),
    "paise": (_int(1), "Enter the loss as an amount above zero."),
    "loss_fy": (lambda v, t: v.__class__ is int and 2000 <= v < financial_year(t),
                "Choose the financial year of the loss: one before this year."),
    "bucket": (lambda v, t: v in CARRY_BUCKETS,
               "Choose the kind of loss: short-term, long-term, business or speculative."),
    "note": (lambda v, t: v is None or (isinstance(v, str) and len(v) <= 200),
             "A note can have up to 200 characters."),
    "trade_id": (lambda v, t: v is None or (isinstance(v, str) and bool(TRADE_ID.fullmatch(v))),
                 "Enter Upstox's trade id as the trade history shows it, or leave it empty."),
    "turnover_method": (_one_of(TURNOVER_METHODS), CHOOSE),
    "exempt_order": (_one_of(EXEMPT_ORDERS), CHOOSE),
    "lt_boundary": (_one_of(LT_BOUNDARIES), CHOOSE),
    "allocate_expenses": (lambda v, t: isinstance(v, bool), CHOOSE),
}  # fmt: skip
#: The figures a dry run compares, each named as a sentence names it (True: an amount).
HEADLINES: tuple[tuple[str, Callable[[dict], int], bool], ...] = (
    ("short-term gain", lambda p: p["capital_gains"]["st_gain_paise"]
     - p["capital_gains"]["st_loss_paise"], True),
    ("long-term gain", lambda p: p["capital_gains"]["lt_gain_paise"]
     - p["capital_gains"]["lt_loss_paise"], True),
    ("taxable short-term gain", lambda p: p["capital_gains"]["taxable"]["st_paise"], True),
    ("taxable long-term gain", lambda p: p["capital_gains"]["taxable"]["lt_paise"], True),
    ("tax on gains at the special rates",
     lambda p: p["capital_gains"]["indicative_tax"]["tax_paise"], True),
    ("F&O income after charges", lambda p: p["business"]["non_speculative"]["net_paise"], True),
    ("intraday income after charges", lambda p: p["business"]["speculative"]["net_paise"], True),
    ("losses carried forward",
     lambda p: sum(r["paise"] for r in p["capital_gains"]["carried"] + p["business"]["carried"]),
     True),
    ("the number of sales with no purchase record",
     lambda p: len(p["capital_gains"]["unknown_cost"]), False),
    ("the number of sales that need the 31 Jan 2018 price",
     lambda p: len(p["capital_gains"]["needs_fmv"]), False),
)  # fmt: skip


class _Undone(Exception):
    """A dry run's way out of its transaction, carrying the inputs before and after the change."""

    def __init__(self, before: list, after: list):
        super().__init__("dry run")
        self.before, self.after = before, after


def _bad(field: str) -> ApiError:
    return ApiError(400, "bad_request", ASK, field)


def _today(ctx) -> int:
    return day_of_instant(ctx.clock())


def _year(message, today: int) -> int:
    fy = message.get("fy")
    fy = financial_year(today) if fy is None else fy
    if fy.__class__ is not int or not reports.year_ok(fy, today):
        raise ApiError(400, "bad_request", WORDS["tax_years"], "fy")
    return fy


def _taxpayer(tx, member: int | None) -> int:
    """The member asked for, or the owner (the first member, made with the portfolio)."""
    ids = [m["id"] for m in tx.rows("member")]
    if member is None and ids:
        return ids[0]
    if member not in ids:
        raise NotFound(NO_MEMBER)
    return member


def _gather(tx, member: int | None, fy: int, today: int, now: int) -> list:
    """The core's inputs of each year from the oldest Upstox keeps up to ``fy``."""
    taxpayer, first = _taxpayer(tx, member), financial_year(today) - REPORT_YEARS + 1
    return [tax_adapter.inputs(tx, taxpayer, year, today, now) for year in range(first, fy + 1)]


def _chain(years: list, reading: bool = False) -> dict:
    """The last year's pack, each year's carried losses brought into the next. The owner's own
    rows join in the year after their loss, where they win over the computed figure."""
    owned, gaps = years[0].carries, [g.fy for g in years[:-1] if g.missing]
    carries, pack = owned, {}
    for n, given in enumerate(years):
        missing = given.missing
        if n == len(years) - 1:
            if gaps:
                listed, one = " and ".join(fy_label(y) for y in gaps), len(gaps) == 1
                missing += (f"Losses brought forward from {listed} come from what is read of "
                            f"{'that year' if one else 'those years'} so far, and part of "
                            f"{'it' if one else 'them'} is not read in full. If a return you "
                            "filed shows other figures, enter them under Inputs.",)  # fmt: skip
            if reading:
                missing = (WORDS["tax_reading"], *missing)
        pack = build_pack(replace(given, carries=carries, missing=missing))
        rows = pack["capital_gains"]["carried"] + pack["business"]["carried"]
        carries = tuple(
            Carry(given.taxpayer, r["loss_fy"], r["bucket"], r["paise"], r["source"]) for r in rows
        ) + tuple(c for c in owned if c.loss_fy == given.fy)
    return pack


def _paper(store, member: int | None, fy: int, today: int, now: int, reading: bool) -> dict:
    return _chain(store.read(OWNER, lambda tx: _gather(tx, member, fy, today, now)), reading)


def _reading(ctx, fy: int) -> bool:
    """Whether the reader is reading one of the year's reports just now."""
    tasks = getattr(getattr(ctx, "reports", None), "_tasks", None) or {}
    return any(key[1] == fy and not task.done() for key, task in tasks.items())


def _shown(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in PRIVATE}


def _settings(tx, taxpayer: int) -> dict:
    stored = tx.setting_get(f"tax:{taxpayer}")
    value = (stored or {}).get("value") or {}
    return {
        "member_id": taxpayer,
        **{k: value.get(k) for k in ("turnover_method", "exempt_order", "lt_boundary")},
        "allocate_expenses": tax_adapter.settings_of(stored).allocate_expenses,
        "version": (stored or {}).get("version", 0),
    }


def _inputs(tx, member: int | None) -> dict:
    taxpayer = _taxpayer(tx, member)
    own = tx.tax_inputs(taxpayer)
    return {
        "member_id": taxpayer,
        "accounts": [
            {k: a[k] for k in ("id", "name", "kind", "tax_mode")} for a in tx.tax_accounts(taxpayer)
        ],
        "opening_lots": [_shown(r) for r in own["tax_opening_lot"]],
        "fmvs": [_shown(r) for r in tx.rows("tax_fmv")],
        "carries": [_shown(r) for r in own["tax_carry"]],
        "day_overrides": [_shown(r) for r in own["tax_day_override"]],
        "trade_marks": [_shown(r) for r in own["tax_trade_mark"]],
        "settings": _settings(tx, taxpayer),
    }


def _read_row(kind: str, row: dict, today: int) -> dict:
    """The row's fields as the kind takes them, each checked, with what the owner's entry says."""
    values = {}
    for name in INPUTS[kind][1]:
        value = row.get(name)
        if isinstance(value, str):
            value = (value.strip().upper() if name == "isin" else value.strip()) or None
        check, sentence = CHECKS[name]
        if not check(value, today):
            raise ApiError(400, "bad_request", sentence, name)
        values[name] = value
    return {**values, **FIXED.get(kind, {})}


def _save(tx, kind: str, values: dict, row_id, version, request_id, now: int) -> dict:
    """Write one input: a new row, or an existing one from the version it was read at."""
    table = INPUTS[kind][0]
    if "member_id" in values:
        _taxpayer(tx, values["member_id"])
    if table is None:
        member, value = values["member_id"], {k: v for k, v in values.items() if k != "member_id"}
        tx.setting_put(f"tax:{member}", value, version or 0, now)
        return _settings(tx, member)
    if "account_id" in values:
        account = tx.get("account", values["account_id"])
        if account is None or account["member_id"] != values["member_id"]:
            raise Invalid(NOT_THEIRS)
    earlier = tx.by_request(table, request_id) if request_id and row_id is None else None
    if earlier is not None:  # the same save sent again: the row the first one made
        return _shown(earlier)
    keys, sentence = UNIQUE.get(table, ((), ""))
    if keys and any(r["id"] != row_id for r in tx.rows(table, **{k: values[k] for k in keys})):
        raise Refused(sentence)
    if row_id is not None:
        return _shown(tx.change(table, row_id, version, values, now, "version_conflict_item"))
    return _shown(tx.insert(table, values, now, request_id))


def _effect(before: dict, after: dict) -> list[str]:
    """What a change does to the year's figures, in sentences."""
    said = []
    for label, figure, money in HEADLINES:
        was, now = figure(before), figure(after)
        if was != now:
            was, now = (rupees(was), rupees(now)) if money else (was, now)
            said.append(f"In {after['fy_label']} this changes {label} from {was} to {now}.")
    return said or [f"This changes no figure of {after['fy_label']}."]


def _dry(store, kind, values, row_id, version, fy: int, today: int, now: int) -> dict:
    member = values.get("member_id")

    def run(tx):
        before = _gather(tx, member, fy, today, now)
        _save(tx, kind, values, row_id, version, None, now)
        raise _Undone(before, _gather(tx, member, fy, today, now))

    try:
        store.write(OWNER, run)
    except _Undone as undone:
        return {"kind": kind, "row": None,
                "effect": _effect(_chain(undone.before), _chain(undone.after))}  # fmt: skip
    raise AssertionError("a dry run writes nothing")  # pragma: no cover


def _remove(tx, kind: str, row_id: int, version: int) -> dict:
    table = INPUTS[kind][0]
    row = tx.get(table, row_id)
    if row is None:
        raise NotFound()
    if row["version"] != version:
        raise VersionConflict("version_conflict_item")
    tx.remove(table, row_id)
    return {"id": row_id}


@op("tax_get")
async def tax_get(ctx, message):
    store, today = need(ctx), _today(ctx)
    fy, member, now = _year(message, today), maybe_whole(message, "member"), ctx.clock()
    reading = _reading(ctx, fy)
    return await answer(lambda: _paper(store, member, fy, today, now, reading))


@op("tax_pack")
async def tax_pack(ctx, message):
    """The journal's FY pack asks for the same paper over its own purpose (owner scope)."""
    return await tax_get(ctx, message)


@op("tax_inputs")
async def tax_inputs(ctx, message):
    store, member = need(ctx), maybe_whole(message, "member")
    return await answer(lambda: store.read(OWNER, lambda tx: _inputs(tx, member)))


@op("tax_input_set")
async def tax_input_set(ctx, message):
    store, today, now = need(ctx), _today(ctx), ctx.clock()
    kind, row = message.get("kind"), message.get("row")
    if kind not in TAX_INPUT_KINDS:
        raise _bad("kind")
    if not isinstance(row, dict):
        raise _bad("row")
    values = _read_row(kind, row, today)
    row_id, version = maybe_whole(message, "id"), maybe_whole(message, "version")
    if (row_id is not None or kind == "settings") and version is None:
        raise _bad("version")
    request_id = message.get("request_id")
    if request_id is not None and not (isinstance(request_id, str) and 0 < len(request_id) <= 64):
        raise _bad("request_id")
    if message.get("dry_run") is True:
        fy = _year(message, today)
        return await answer(lambda: _dry(store, kind, values, row_id, version, fy, today, now))

    def save(tx):
        return {"kind": kind, "row": _save(tx, kind, values, row_id, version, request_id, now),
                "effect": []}  # fmt: skip

    return await answer(lambda: store.write(OWNER, save))


@op("tax_input_delete")
async def tax_input_delete(ctx, message):
    store, kind = need(ctx), message.get("kind")
    if kind not in TAX_INPUT_KINDS or INPUTS[kind][0] is None:
        raise _bad("kind")
    row_id, version = whole(message, "id"), whole(message, "version")
    return await answer(lambda: store.write(OWNER, lambda tx: _remove(tx, kind, row_id, version)))
