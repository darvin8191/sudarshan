"""The tax core's inputs from what the portfolio holds (phase P7, M7.6; design p7 2.1, 4.1 to 4.3).

It translates and never decides, as ``services/strategy_module/risk_adapter.py`` does: Upstox's
trade history and trade-wise P&L (``report_cache``), the custom tabs' ledgers and the owner's tax
inputs become ``sudarshan.tax.model`` objects and one ``PackInputs`` per taxpayer and year. Every
rule (heads, lots, gains, business income, set-off) is the core's; a rule change is a change there.

* **Who and where.** A taxpayer is a member; an account counts by its tax mode
  (``migrations.tax_mode_of``): the Upstox account by its reports, every other by its ledger,
  ``none`` not at all. A ledger entry on an account that counts by its reports is left out, so a
  trade is never counted twice.
* **Upstox's history** is read from complete years only (state ``ok``), the run of them that ends
  at the pack's year; a year not complete is said in ``missing``. A purchase from before that run
  comes from Upstox's P&L pairs (``upstox_pair``) unless the owner entered his own for that share.
* **The ledgers**: a buy or rights issue is a purchase, a sale a sale, a transfer in from outside a
  purchase from before the records at its own date and cost, a bonus a lot at nil cost on its day
  and a split one action per share and day. A transfer between accounts or to outside is not
  followed yet and is said in ``missing``. Charges count without STT only where the entry's
  breakdown names the STT; otherwise they are left out (the larger gain, never the smaller).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from sudarshan.tax.model import Action, Carry, OpeningLot, Settings, Trade
from sudarshan.tax.pack import PackInputs
from sudarshan.tax.units import day_text, fy_first, fy_label, fy_last, fy_of, value_paise

from .migrations import tax_mode_of

REPORT_SEGMENTS = ("EQ", "FO", "COM", "CD", "MF")
#: Upstox keeps the trade history of this many financial years, the running one included.
REPORT_YEARS = 3
PNL_SEGMENTS = ("EQ", "FO", "COM", "CD")
DERIVATIVES = ("FO", "COM", "CD")
SEGMENT_WORDS = {"EQ": "shares", "FO": "F&O", "COM": "commodity", "CD": "currency", "MF": "funds"}
#: The portfolio's asset classes that the core computes; any other is listed with its amount.
EQUITY_CLASSES = ("equity", "mf_equity")


def _sorted_ids(rows):
    """Upstox's trades of a day in its own order: numeric trade ids as numbers."""
    return sorted(rows, key=lambda r: (r["trade_date"], len(r["trade_id"]), r["trade_id"]))


def contract_of(row: Mapping[str, Any]) -> tuple | None:
    """A derivative's contract as the core keys it: (exchange, scrip, expiry, strike_u, CE|PE|FUT);
    None when the row cannot name one (no expiry)."""
    expiry = row.get("expiry")
    scrip = row.get("scrip_name") or row.get("symbol")
    if not expiry or not scrip:
        return None
    return (
        row["exchange"],
        scrip,
        expiry,
        row.get("strike_price_u") or 0,
        row.get("option_type") or "FUT",
    )


def tax_class(asset: Mapping[str, Any] | None) -> str | None:
    """The core's class for a portfolio asset: equity, or one the core never computes."""
    if asset is None:
        return None
    if asset["kind"] == "liability":
        return "liability"
    return "equity" if asset.get("asset_class") in EQUITY_CLASSES else "manual"


def instrument_of(asset: Mapping[str, Any], aliases: list[str]) -> str:
    """A ledger asset's key for its lots: the ISIN (its own, else an ISIN alias), else a name."""
    return asset.get("isin") or (aliases[0] if aliases else None) or str(asset["name"])


def upstox_trades(records, taxpayer: int, account: int, classes, tenders=frozenset()):
    """The history rows of complete years as trades, a sale whose trade id the owner marked as a
    buyback tender (``tenders``) saying so; the rows the core cannot key, as their count."""
    out, unkeyed = [], 0
    for seq, row in enumerate(_sorted_ids(r for record in records for r in record["rows"])):
        if row["segment"] in DERIVATIVES:
            instrument = contract_of(row)
        else:
            instrument = row.get("isin")
        if instrument is None:
            unkeyed += 1
            continue
        tender = row["transaction_type"] == "SELL" and row["trade_id"] in tenders
        out.append(
            Trade(
                taxpayer,
                account,
                row["trade_date"],
                seq,
                row["transaction_type"],
                row["segment"],
                instrument,
                row["qty_u"],
                row["price_u"],
                row["amount_paise"],
                kind_hint="buyback" if tender else None,
                asset_class=classes.get(instrument) if isinstance(instrument, str) else None,
            )
        )
    return out, unkeyed


def _net_charges(entry: Mapping[str, Any]) -> int | None:
    """An entry's charges without STT, when its breakdown says the STT; 0 for none at all."""
    charges = entry.get("charges_paise") or 0
    detail = entry.get("charges_detail") or {}
    if not charges:
        return 0
    stt = detail.get("stt_paise")
    return charges - stt if isinstance(stt, int) else None


def ledger_inputs(entries, accounts, assets, aliases, taxpayer: int, seq0: int):
    """Trades, purchases from before the records, actions and words from the custom tabs."""
    trades, opening, actions, missing, arrived = [], [], {}, [], {}
    unbroken = 0
    order = sorted(entries, key=lambda e: (e["trade_date"], e["tab_id"], e["seq"], e["id"]))
    for n, entry in enumerate(order, start=seq0):
        asset = assets.get(entry.get("asset_id"))
        if entry["status"] != "posted" or asset is None:
            continue
        key = instrument_of(asset, aliases.get(asset["id"], []))
        kind, day, account = entry["kind"], entry["trade_date"], entry.get("account_id")
        if kind == "SPLIT":
            actions[(key, day, entry["ratio_num"], entry["ratio_den"])] = Action(
                "SPLIT", key, day, entry["ratio_num"], entry["ratio_den"]
            )
            continue
        target = entry.get("to_account_id") if kind == "TRANSFER" else account
        if accounts.get(target) is None and accounts.get(account) is None:
            continue  # neither side counts for this taxpayer by its ledger
        segment = "MF" if asset["kind"] == "mf" else "EQ"
        if kind in ("BUY", "RIGHTS", "SELL") and accounts.get(account) is not None:
            charges = _net_charges(entry)
            unbroken += charges is None
            trades.append(
                Trade(
                    taxpayer,
                    account,
                    day,
                    n,
                    "SELL" if kind == "SELL" else "BUY",
                    segment,
                    key,
                    entry["qty_u"],
                    entry["price_u"] or 0,
                    _amount(entry),
                    "ledger",
                    charges,
                    asset_class=tax_class(asset),
                )
            )
        elif kind == "BONUS" and accounts.get(account) is not None:
            opening.append(OpeningLot(taxpayer, account, key, day, entry["qty_u"], 0, "bonus"))
        elif kind == "TRANSFER" and account is None and accounts.get(target) is not None:
            lot = OpeningLot(
                taxpayer,
                target,
                key,
                entry.get("lot_date") or day,
                entry["qty_u"],
                entry.get("amount_paise") or 0,
                "ledger",
            )
            arrived[len(opening)] = day
            opening.append(lot)
        elif kind == "TRANSFER":
            missing.append(
                f"A transfer of {asset['name']} on {day_text(day)} is not followed by the tax "
                "working paper yet: its units are counted in the account they came from."
            )
    if unbroken:
        missing.append(
            f"{_count(unbroken, 'entry has', 'entries have')} charges without a breakdown, so "
            "their charges are left out of the gains (STT is no expense here). Add the breakdown "
            "to count them."
        )
    splits = list(actions.values())
    return trades, _in_first_units(opening, arrived, splits), splits, missing


def _in_first_units(opening, arrived: Mapping[int, int], actions) -> list[OpeningLot]:
    """A lot brought in from outside is entered in the units of the day it arrived (``arrived``,
    by its place in ``opening``), and the core reads an opening lot in the units of the day it
    was bought: a split between the two days is undone on it, so the core does not apply it
    twice. A split after its arrival stays, and one that does not divide it leaves it as it is."""
    out = []
    for n, lot in enumerate(opening):
        qty, day = lot.qty_u, arrived.get(n)
        for action in actions:
            between = day is not None and lot.acquired_day < action.ex_day <= day
            if (
                between
                and action.instrument == lot.instrument
                and qty * action.den % action.num == 0
            ):
                qty = qty * action.den // action.num
        out.append(replace(lot, qty_u=qty))
    return out


def _amount(entry: Mapping[str, Any]) -> int:
    """The gross amount as entered, else quantity x price (the ledger's own reading)."""
    if entry.get("amount_paise") is not None:
        return entry["amount_paise"]
    return value_paise(entry["qty_u"], entry["price_u"])


def pair_lots(pairs, before: int, taxpayer: int, account: int, owned: set[str]):
    """Purchases from before ``before`` that Upstox's P&L pairs name for shares sold since, for
    each share the owner entered none of his own for."""
    return [
        OpeningLot(
            taxpayer,
            account,
            row["isin"],
            row["buy_date"],
            row["qty_u"],
            row["buy_amount_paise"],
            "upstox_pair",
        )
        for row in pairs
        if row.get("isin")
        and row["isin"] not in owned
        and row.get("buy_date")
        and row["buy_date"] < before
    ]


def settlements_of(pairs, trades) -> tuple[dict, list]:
    """Expiry prices from Upstox's pairs dated a contract's expiry: a price for F&O; for
    commodity and currency (where quantity times price is not the money) a closing trade with
    the pair's own amount."""
    prices, closing, seen = {}, [], set()
    for trade in trades:
        contract = trade.instrument
        if trade.segment not in DERIVATIVES or contract in seen:
            continue
        seen.add(contract)
        for row in pairs:
            if row.get("scrip_name") != contract[1] or contract[2] not in (
                row.get("sell_date"),
                row.get("buy_date"),
            ):
                continue
            long = row.get("sell_date") == contract[2]
            if trade.segment == "FO":
                prices[contract] = row["sell_average_u" if long else "buy_average_u"]
            else:
                closing.append(
                    Trade(
                        trade.taxpayer,
                        trade.account,
                        contract[2],
                        10**9,
                        "SELL" if long else "BUY",
                        trade.segment,
                        contract,
                        row["qty_u"],
                        0,
                        row["sell_amount_paise" if long else "buy_amount_paise"],
                        kind_hint="expiry",
                    )
                )
            break
    return prices, closing


def _complete_run(records: Mapping[tuple, Mapping], fy: int) -> list[int]:
    """The years of the run of complete EQ histories that ends at ``fy`` (newest last)."""
    run, year = [], fy
    while (records.get(("trades", year, "EQ")) or {}).get("state") == "ok":
        run.insert(0, year)
        year -= 1
    return run


def inputs(tx, taxpayer: int, fy: int, as_of_day: int, as_of_ms: int) -> PackInputs:
    """Everything the core needs for one taxpayer's year, read in one transaction ``tx``."""
    accounts = {a["id"]: a for a in tx.tax_accounts(taxpayer)}
    upstox = next((a for a in accounts.values() if a["kind"] == "upstox"), None)
    ledger = {i: a for i, a in accounts.items() if tax_mode_of(a) == "ledger"}
    records = {
        (r["kind"], r["fy"], r["segment"]): r
        for kind in ("trades", "pnl")
        for r in tx.report_list(kind)
    }
    assets = {a["id"]: a for a in tx.rows("asset")}
    aliases: dict[int, list[str]] = {}
    for alias in tx.rows("asset_alias", "id", kind="isin"):
        aliases.setdefault(alias["asset_id"], []).append(alias["value"])
    classes = {
        key: tax_class(a)
        for a in assets.values()
        for key in (a.get("isin"), *aliases.get(a["id"], []))
        if key
    }
    trades, opening, missing, sources = [], [], [], []
    own = tx.tax_inputs(taxpayer)
    run = _complete_run(records, fy) if upstox and upstox["tax_mode"] == "reports" else []
    pairs = []
    if upstox and upstox["tax_mode"] == "reports":
        unread = []
        for segment in REPORT_SEGMENTS:
            record = records.get(("trades", fy, segment))
            sources.append(_source("trades", fy, segment, record))
            if (record or {}).get("state") != "ok":
                unread.append(SEGMENT_WORDS[segment])
        if unread:
            missing.append(
                f"Upstox's {_listed(unread)} trades of {fy_label(fy)} are not read in full yet; "
                "the figures leave them out until they are."
            )
        used = [
            records[("trades", y, s)]
            for y in run
            for s in REPORT_SEGMENTS
            if (records.get(("trades", y, s)) or {}).get("state") == "ok"
        ]
        tenders = {m["trade_id"] for m in own["tax_trade_mark"] if m.get("trade_id")}
        found, unkeyed = upstox_trades(
            [r.get("payload") or {"rows": []} for r in used],
            taxpayer,
            upstox["id"],
            classes,
            frozenset(tenders),
        )
        trades += found
        if fy - 1 not in run and fy - 1 > fy_of(as_of_day) - REPORT_YEARS:
            missing.append(
                f"Upstox's trades of {fy_label(fy - 1)} are not read in full, so a position "
                "carried into this year, or a share bought then, may be missing. Read that year "
                "on the Upstox tab's Trades view."
            )
        if unkeyed:
            missing.append(
                f"{_count(unkeyed, 'of Upstox trades names', 'of Upstox trades name')} no share "
                "or contract, so the figures leave them out."
            )
        pairs = [
            row
            for y in run
            for s in PNL_SEGMENTS
            for row in ((records.get(("pnl", y, s)) or {}).get("payload") or {}).get("rows") or []
        ]
    counted = {i for i, a in accounts.items() if tax_mode_of(a) != "none"}
    opening += [
        OpeningLot(
            taxpayer,
            r["account_id"],
            r["isin"],
            r["acquired_day"],
            r["qty_u"],
            r["cost_paise"],
            r["source"],
        )
        for r in own["tax_opening_lot"]
        if r["account_id"] in counted
    ]
    if upstox and run:
        owned = {r["isin"] for r in own["tax_opening_lot"] if r["account_id"] == upstox["id"]}
        opening += pair_lots(pairs, fy_first(run[0]), taxpayer, upstox["id"], owned)
    entries = [e for e in tx.rows("activity") if e["trade_date"] <= fy_last(fy)]
    more, bonus, actions, words = ledger_inputs(
        entries, ledger, assets, aliases, taxpayer, len(trades)
    )
    trades, opening, missing = trades + more, opening + bonus, missing + words
    if ledger:
        sources.append(
            {"kind": "ledger", "fy": fy, "segment": None, "state": "ok", "read_at": None}
        )
    trades += _marks([m for m in own["tax_trade_mark"] if m["account_id"] in counted], taxpayer)
    prices, closing = settlements_of(pairs, trades)
    charges = {
        s: rec["payload"]["charges"]
        for s in PNL_SEGMENTS
        if (rec := records.get(("pnl", fy, s))) and (rec.get("payload") or {}).get("charges")
    }
    traded = {t.segment for t in trades if fy_of(t.day) == fy and t.source != "ledger"}
    return PackInputs(
        fy=fy,
        taxpayer=taxpayer,
        as_of_day=as_of_day,
        as_of_ms=as_of_ms,
        trades=tuple(trades + closing),
        opening=tuple(opening),
        actions=tuple(actions),
        fmv={r["isin"]: r["fmv_u"] for r in tx.rows("tax_fmv")},
        carries=tuple(
            Carry(taxpayer, r["loss_fy"], r["bucket"], r["paise"], r["source"])
            for r in own["tax_carry"]
        ),
        settings=settings_of(tx.setting_get(f"tax:{taxpayer}")),
        all_delivery=frozenset(
            (taxpayer, r["account_id"], r["isin"], r["day"]) for r in own["tax_day_override"]
        ),
        settlements=prices,
        charges=charges,
        charges_state="ok" if traded & set(PNL_SEGMENTS) <= set(charges) else "partial",
        sources=tuple(sources),
        missing=tuple(missing),
        upstox_pnl=_upstox_pnl(records, fy, trades),
    )


def _source(kind: str, fy: int, segment: str, record) -> dict[str, Any]:
    record = record or {}
    return {
        "kind": kind,
        "fy": fy,
        "segment": segment,
        "state": record.get("state", "unavailable"),
        "read_at": record.get("read_at"),
    }


def _marks(marks, taxpayer: int) -> list[Trade]:
    """A buyback tender the owner entered that the history does not list, as a sale."""
    return [
        Trade(
            taxpayer,
            m["account_id"],
            m["day"],
            10**8 + m["id"],
            "SELL",
            "EQ",
            m["isin"],
            m["qty_u"],
            m["price_u"],
            value_paise(m["qty_u"], m["price_u"]),
            "owner",
            kind_hint="buyback",
        )
        for m in marks
        if not m.get("trade_id")
    ]


def _listed(names: list[str]) -> str:
    """Names as a sentence lists them: "shares, F&O and funds"."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _count(n: int, one: str, many: str) -> str:
    """A count with its words agreeing: "1 entry has", "3 entries have"."""
    return f"{n} {one if n == 1 else many}"


def _upstox_pnl(records, fy: int, trades) -> dict[str, int] | None:
    """Upstox's own profit per derivative scrip for the year, for the core's cross-check; None
    unless every derivative segment traded in the year has its P&L report read in full."""
    traded = {t.segment for t in trades if t.segment in DERIVATIVES and fy_of(t.day) == fy}
    out: dict[str, int] = {}
    for segment in traded:
        record = records.get(("pnl", fy, segment)) or {}
        if record.get("state") != "ok":
            return None
        for row in (record.get("payload") or {}).get("rows") or []:
            out[row["scrip_name"]] = out.get(row["scrip_name"], 0) + row["pnl_paise"]
    return out


def settings_of(stored: Mapping[str, Any] | None) -> Settings:
    """A taxpayer's readings of the open rules (``owner_setting`` ``tax:<member id>``)."""
    value = (stored or {}).get("value") or {}
    return Settings(
        turnover_method=value.get("turnover_method"),
        exempt_order=value.get("exempt_order"),
        lt_boundary=value.get("lt_boundary"),
        allocate_expenses=value.get("allocate_expenses", True) is not False,
    )
