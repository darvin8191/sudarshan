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

``tax_csv`` writes one schedule of the paper for the CA, under the working-paper sentence;
``report_csv`` writes Upstox's report rows as the reader kept them. A number is written plain and
any text that a spreadsheet would read as a formula gets an apostrophe (``csvutil.guard``).
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from sudarshan.tax.model import Carry
from sudarshan.tax.pack import build_pack
from sudarshan.tax.units import day_text, fy_label, rupees

from .. import dividends, reports, tax_adapter
from ..core.days import day_of_instant, financial_year, is_day
from ..core.kinds import (
    CARRY_BUCKETS,
    EXEMPT_ORDERS,
    LT_BOUNDARIES,
    REPORT_KINDS,
    REPORT_SEGMENTS,
    REPORT_YEARS,
    TAX_INPUT_KINDS,
    TAX_SCHEDULES,
    TURNOVER_METHODS,
    WORDS,
)
from ..errors import ApiError
from ..imports.csvutil import guard
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


def _with_dividends(store, pack: dict) -> dict:
    """The year's dividends of the taxpayer's accounts that count (``dividends.for_pack``),
    with the buyback's receipt the core worked out beside them."""
    buyback = pack["dividends"]["buyback_paise"]
    pack["dividends"] = store.read(
        OWNER, lambda tx: dividends.for_pack(tx, pack["fy"], pack["taxpayer"], buyback)
    )
    return pack


def _paper(store, member: int | None, fy: int, today: int, now: int, reading: bool) -> dict:
    years = store.read(OWNER, lambda tx: _gather(tx, member, fy, today, now))
    return _with_dividends(store, _chain(years, reading))


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


# ---- the downloads -----------------------------------------------------------------------------
HEADER = "सुदर्शन · SUDARSHAN"
NOT_READ = (
    "Upstox's report for this year and segment is not read yet. Read it on the Upstox tab, then "
    "download it."
)
PLAIN = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")
TERMS = {"short": "Short-term", "long": "Long-term"}
STATUS = {"ok": "Counted", "needs_fmv": "Needs the 31 Jan 2018 price",
          "unknown_cost": "No purchase record", "exempt_buyback": "Buyback, exempt"}  # fmt: skip
BUYBACK = {"exempt": "Exempt", "capital_gain": "Capital gain",
           "dividend_and_loss": "Taxed as a dividend; the cost a capital loss"}  # fmt: skip
BUCKETS = {"stcl": "Short-term capital loss", "ltcl": "Long-term capital loss",
           "business": "Business loss", "speculative": "Speculative loss"}  # fmt: skip
AGAINST = {"short": "Short-term gain", "long": "Long-term gain", "business": "F&O income",
           "speculative": "Intraday income"}  # fmt: skip
BLOCKS = (("non_speculative", "F&O and other derivatives"),
          ("speculative", "Intraday shares (speculative)"))  # fmt: skip
CHARGES = (("total", "Total"), ("brokerage", "Brokerage"), ("gst", "GST"), ("stt", "STT"),
           ("stamp_duty", "Stamp duty"), ("transaction", "Exchange transaction"),
           ("clearing", "Clearing"), ("ipft", "IPFT"), ("others", "Others"),
           ("sebi_turnover", "SEBI turnover fee"),
           ("demat_transaction", "Demat transaction"))  # fmt: skip


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text if PLAIN.fullmatch(text) else guard(text)


def _csv(rows: list[list[Any]]) -> str:
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerows([_cell(value) for value in row] for row in rows)
    return out.getvalue()


def _num(value: int | None, decimals: int, least: int = 0) -> str:
    """A whole number of 10^-decimals as plain text, its sign kept: -105025, 2 is "-1050.25"."""
    if value is None:
        return ""
    whole, part = divmod(abs(value), 10**decimals)
    digits = f"{part:0{decimals}d}".rstrip("0").ljust(least, "0")
    return ("-" if value < 0 else "") + (f"{whole}.{digits}" if digits else str(whole))


def _money(paise: int | None) -> str:
    return _num(paise, 2, 2)


def _iso(day: int | None) -> str:
    return "" if day is None else f"{day // 10000:04d}-{day // 100 % 100:02d}-{day % 100:02d}"


def _gains(pack: dict, names: dict, accounts: dict) -> list[list[Any]]:
    capital = pack["capital_gains"]
    rows = [["Sale day", "ISIN", "Name", "Account", "Bought", "Quantity", "Received", "Sale value",
             "Cost", "Expenses", "Gain", "Term", "Rate %", "Status", "31 Jan 2018 price used",
             "Expenses shared from the year's charges", "Buyback"]]  # fmt: skip
    every = (*capital["schedule"], *capital["needs_fmv"], *capital["unknown_cost"],
             *capital["exempt_buybacks"])  # fmt: skip
    for g in sorted(every, key=lambda g: (g["sale_day"], g["isin"], g["acquired_day"] or 0)):
        rows.append([
            _iso(g["sale_day"]), g["isin"], names.get(g["isin"], ""),
            accounts.get(g["account"], ""), _iso(g["acquired_day"]), _num(g["qty_u"], 6),
            _money(g["receipt_paise"]), _money(g["sale_paise"]), _money(g["cost_paise"]),
            _money(g["expenses_paise"]), _money(g["gain_paise"]), TERMS.get(g["term"], ""),
            _num(g["rate_bp"], 2), STATUS[g["status"]], "Yes" if g["fmv_used"] else "No",
            "Yes" if g["allocated"] else "No", BUYBACK.get(g["buyback"], ""),
        ])  # fmt: skip
    tax = capital["indicative_tax"]
    return rows + [[]] + [[label, _money(paise)] for label, paise in (
        ("Short-term gains", capital["st_gain_paise"]),
        ("Short-term losses", capital["st_loss_paise"]),
        ("Long-term gains", capital["lt_gain_paise"]),
        ("Long-term losses", capital["lt_loss_paise"]),
        ("Exemption used", capital["exemption"]["used_paise"]),
        ("Taxable short-term gain", capital["taxable"]["st_paise"]),
        ("Taxable long-term gain", capital["taxable"]["lt_paise"]),
        ("Tax at the special rates", tax["tax_paise"]),
        (f"Cess ({tax['cess_label']})", tax["cess_paise"]),
    )]  # fmt: skip


def _business(pack: dict, names: dict, accounts: dict) -> list[list[Any]]:
    business = pack["business"]
    rows = [["Bucket", "Line", "Count", "Amount", "Turnover"]]
    for key, label in BLOCKS:
        block = business[key]
        for kind, row in sorted(block["by_kind"].items()):
            rows.append([label, kind.capitalize(), row["count"], _money(row["gross_paise"]),
                         _money(row["turnover_paise"])])  # fmt: skip
        rows += [[label, "Gross", "", _money(block["gross_paise"]),
                  _money(block["turnover_paise"])],
                 [label, "Charges", "", _money(block["charges_paise"]), ""],
                 [label, "Net", "", _money(block["net_paise"]), ""]]  # fmt: skip
    rows += [[], ["Turnover method", business["turnover_method_label"]],
             ["Turnover", _money(business["turnover_paise"])]]  # fmt: skip
    rows += [["Check with your CA", flag] for flag in business["flags"]]
    if business["assumed_zero_count"]:
        rows.append(["Options taken as expired worthless", business["assumed_zero_count"]])
    for position in business["unknown_settlement"]:
        contract = position[2]
        rows.append(["No settlement price, left out", f"{contract[1]} {_iso(contract[2])}",
                     position[3], _num(position[5], 6)])  # fmt: skip
    if business["differs"]:
        rows += [[], ["Scrip", "Worked out here", "Upstox's report"]]
        rows += [[d["scrip"], _money(d["own_paise"]), _money(d["upstox_paise"])]
                 for d in business["differs"]]  # fmt: skip
    return rows


def _losses(pack: dict, names: dict, accounts: dict) -> list[list[Any]]:
    rows = [["Head", "Line", "Kind of loss", "Year of loss", "Against", "Usable to", "Amount",
             "Source"]]  # fmt: skip
    words = []
    for head, block in (("Capital gains", pack["capital_gains"]), ("Business", pack["business"])):
        for step in block["set_off"]:
            against = AGAINST[step["against"]]
            if step.get("rate_bp"):
                against += f" at {_num(step['rate_bp'], 2)}%"
            line = "Set off this year" if step["step"] == "same_year" else "Brought forward"
            rows.append([head, line, BUCKETS[step["bucket"]], fy_label(step["loss_fy"]), against,
                         "", _money(step["paise"]), ""])  # fmt: skip
        for key, line in (("carried", "Carried forward"), ("lapsed", "Lapsed"),
                          ("own_figures", "Worked out here, beside your own figure")):  # fmt: skip
            for r in block[key]:
                source = "You entered it" if r["source"] == "owner" else "Worked out here"
                rows.append([head, line, BUCKETS[r["bucket"]], fy_label(r["loss_fy"]), "",
                             fy_label(r["last_fy"]), _money(r["paise"]), source])  # fmt: skip
        words += block["set_off_words"]
    return rows + ([[], *[[w] for w in words]] if words else [])


def _dividends(pack: dict, names: dict, accounts: dict) -> list[list[Any]]:
    block = pack["dividends"]
    rows = [["Payer ISIN", "Name", "Expected", "Confirmed", "Counted for the year", "TDS likely"]]
    rows += [[p["isin"], p.get("name") or names.get(p["isin"], ""),
              _money(p.get("expected_paise")), _money(p.get("confirmed_paise")),
              _money(p.get("total_paise")), "Yes" if p.get("tds_likely") else "No"]
             for p in block["by_payer"]]  # fmt: skip
    rows += [[], ["Buyback receipts taxed as a dividend", _money(block["buyback_paise"])],
             ["Total", _money(block["total_paise"])]]  # fmt: skip
    if block.get("tds_expected_paise"):
        rows.append(
            ["Tax the companies likely deducted, about", _money(block["tds_expected_paise"])]
        )
    if block.get("unconfirmed_count"):
        rows.append(["Lines not confirmed yet, counted as expected", block["unconfirmed_count"]])
    return rows


SCHEDULES = {"capital_gains": _gains, "business": _business, "losses": _losses,
             "dividends": _dividends}  # fmt: skip


def _labels(tx, taxpayer: int) -> tuple[str, dict, dict]:
    """The taxpayer's name, a name for each ISIN (the holding's own, else Upstox's scrip name)
    and each account's name."""
    names = {
        r["isin"]: r["scrip_name"]
        for record in tx.report_list("trades")
        for r in (record.get("payload") or {}).get("rows") or []
        if r.get("isin") and r.get("scrip_name")
    }
    names.update({a["isin"]: a["name"] for a in tx.rows("asset") if a.get("isin")})
    member = tx.get("member", taxpayer) or {}
    return member.get("name") or "", names, {a["id"]: a["name"] for a in tx.rows("account")}


def _paper_file(store, member, fy: int, schedule: str, today: int, now: int) -> dict:
    def run(tx):
        years = _gather(tx, member, fy, today, now)
        return years, _labels(tx, years[-1].taxpayer)

    years, (who, names, accounts) = store.read(OWNER, run)
    pack = _with_dividends(store, _chain(years))
    title = f"{HEADER} · Tax working paper · {pack['fy_label']} · {who}"
    if pack["running"]:
        title += f" · year to date, as of {day_text(pack['as_of_day'])}"
    rows = [[title], [pack["note"]], [], *SCHEDULES[schedule](pack, names, accounts)]
    label = pack["fy_label"].replace(" ", "")
    name = f"SUDARSHAN_Tax_{label}_member{pack['taxpayer']}_{schedule}.csv"
    return {"filename": name, "text": "\ufeff" + _csv(rows)}


def _report_file(store, kind: str, fy: int, segment: str) -> dict:
    record = store.read(OWNER, lambda tx: tx.report_get(kind, fy, segment))
    if record is None or not record.get("payload"):
        raise NotFound(NOT_READ)
    payload = record["payload"]
    if kind == "trades":
        rows = [["Trade date", "Trade id", "Exchange", "Segment", "Side", "Scrip", "Symbol", "ISIN",
                 "Option type", "Strike", "Expiry", "Quantity", "Price", "Amount"]]  # fmt: skip
        rows += [[_iso(r["trade_date"]), r["trade_id"], r["exchange"], r["segment"],
                  r["transaction_type"], r["scrip_name"], r.get("symbol"), r.get("isin"),
                  r.get("option_type"), _num(r.get("strike_price_u"), 4, 2), _iso(r.get("expiry")),
                  _num(r["qty_u"], 6), _num(r["price_u"], 4, 2), _money(r["amount_paise"])]
                 for r in payload.get("rows") or []]  # fmt: skip
    else:
        rows = [["Scrip", "ISIN", "Type", "Quantity", "Buy date", "Buy average", "Buy amount",
                 "Sell date", "Sell average", "Sell amount", "Profit or loss"]]  # fmt: skip
        rows += [[r["scrip_name"], r.get("isin"), r["trade_type"], _num(r["qty_u"], 6),
                  _iso(r.get("buy_date")), _num(r["buy_average_u"], 4, 2),
                  _money(r["buy_amount_paise"]), _iso(r.get("sell_date")),
                  _num(r["sell_average_u"], 4, 2), _money(r["sell_amount_paise"]),
                  _money(r["pnl_paise"])] for r in payload.get("rows") or []]  # fmt: skip
        charges = payload.get("charges")
        if charges:
            rows += [[], ["Charges for the year", ""]]
            rows += [[label, _money(charges.get(f"{key}_paise")) or "not given"]
                     for key, label in CHARGES]  # fmt: skip
    partial = "" if record.get("state") == "ok" else "_partial"
    label = reports.fy_label(fy).replace(" ", "")
    return {"filename": f"SUDARSHAN_Upstox_{kind}_{label}_{segment}{partial}.csv",
            "text": _csv(rows)}  # fmt: skip


@op("tax_csv")
async def tax_csv(ctx, message):
    store, today = need(ctx), _today(ctx)
    fy, member, now = _year(message, today), maybe_whole(message, "member"), ctx.clock()
    schedule = message.get("schedule")
    if schedule not in TAX_SCHEDULES:
        raise ApiError(400, "bad_request", "Choose a schedule to download.", "schedule")
    return await answer(lambda: _paper_file(store, member, fy, schedule, today, now))


@op("report_csv")
async def report_csv(ctx, message):
    store, today = need(ctx), _today(ctx)
    kind, fy = message.get("kind"), message.get("fy")
    segment = str(message.get("segment") or "").upper()
    if kind not in REPORT_KINDS:
        raise _bad("kind")
    if fy.__class__ is not int or not reports.year_ok(fy, today):
        raise ApiError(400, "bad_request", WORDS["tax_years"], "fy")
    if segment not in REPORT_SEGMENTS:
        raise ApiError(400, "bad_request", reports.WORDS["segment"], "segment")
    if kind == "pnl" and segment not in reports.PNL_SEGMENTS:
        raise ApiError(400, "bad_request", reports.WORDS["no_pnl_mf"], "segment")
    return await answer(lambda: _report_file(store, kind, fy, segment))
