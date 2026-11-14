"""The 22:00 catch-up (JB5, M14.2; design 3.5 as amended by cross-design Y22.3).

Every day at 22:00, four steps, each through the portfolio's journal purpose (one budget for
every ring service; the journal never reads the Upstox token):

1. On a trading day, today's trades are read once more, whatever the close check marked: a cash
   post-close or evening commodity fill the 16:05 check could not see is recorded. Nothing is
   withdrawn here; the close check did that from the whole list at the close.
2. The trade history fills each of the last 3 trading days that no close check marked (the
   journal was down that day). Upstox's history has no times, no products and no order ids, so
   such a fill is put at noon of its day (the pairing then orders a day's fills by trade number),
   takes the product the day's recorded fills of its instrument carry when they carry one, else
   the carry class, and its order is labelled from the history alone, never asked about again.
   A share is the exception (verification N1): when the day's history trades of it bought and
   sold the same quantity, they are counted as intraday (``same_day_shares``). Nothing of it was
   carried either way, and the exchange nets a day's delivery buy and sale of one share as
   intraday too; left in the carry class, a short sale before its covering buy read as a sale
   of shares held before the journal plus a purchase still open, a LONG that does not exist.
   A day whose quantities differ keeps the carry class (a delivery sale stays one).
   The history names no order either, so the trades of one instrument, product, side and day are
   taken as one order (``history_order_id``): one brokerage, as an order filled in parts pays,
   never one a trade (review M1); the trade sheet says such charges are from Upstox's history.
   A trade the journal already holds is never touched. A day the history has no rows for yet
   stays unmarked and is asked again the next night, while it is one of the last 3.
3. Upstox's own P&L report: the matched pairs sold or bought on the previous 3 trading days, for
   each segment the journal holds fills of then, and that segment's charges for the year so far. They
   are kept for the comparison of estimated with actual charges, which the next release shows.

4. The open delivery trades of shares are held against Upstox's holdings (``book.recount_shares``,
   verification N2): one whose shares the account no longer holds, because its sale was made
   while the journal was off for longer than step 2 reaches, is ended as a trade whose closing
   was not seen, out of every figure, instead of staying an open LONG for ever. It runs only
   after step 1 read today's trades and step 2 ran.

A first step that could not read fails the run (asked again every 15 minutes until midnight);
steps 2 and 3 then run once a day whatever it did, and step 4 waits for a run whose first step
read. Last, the job log lets go of rows older than KEEP_JOB_DAYS (design 4.2, review L3); report files are kept (a PDF is 50 to 500 KiB a month).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from ..capture import capture_for
from ..core import kinds
from ..core.money import to_date, to_day, u_to_paise
from ..core.pairing import CASH_EXCHANGES
from ..openalgo_ro import RecordsUnavailable, records_for
from ..portfolio import PortfolioUnavailable, link_for
from ..schedule import JobFailed, register_job, schedule_for
from ..store import Scope

log = logging.getLogger("sudarshan.journal")

OWNER = Scope.owner()
QTY_SCALE = 1_000_000
CATCHUP_DAYS = 3
SEGMENTS = ("EQ", "FO", "COM", "CD")
SEGMENT_OF = {"NSE": "EQ", "BSE": "EQ", "NFO": "FO", "BFO": "FO", "MCX": "COM", "CDS": "CD",
              "BCD": "CD"}  # fmt: skip
#: OpenAlgo's exchange for one of Upstox's (segment, exchange) pairs.
EXCHANGE_OF = {("EQ", "NSE"): "NSE", ("EQ", "BSE"): "BSE", ("FO", "NSE"): "NFO",
               ("FO", "BSE"): "BFO", ("FO", "NFO"): "NFO", ("FO", "BFO"): "BFO",
               ("COM", "MCX"): "MCX", ("CD", "NSE"): "CDS", ("CD", "BSE"): "BCD",
               ("CD", "CDS"): "CDS", ("CD", "BCD"): "BCD"}  # fmt: skip
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
HISTORY_PAGES = 5
PNL_PAGES = 3
PNL_PAGE_SIZE = 5000
HISTORY_LABEL = "Trade history"
NOON_MIN = 12 * 60
#: How long the job log keeps a day's rows (design 4.2).
KEEP_JOB_DAYS = 400


def financial_year(day: int) -> int:
    """The year an Indian financial year starts in: 20261109 is in FY 2026-27, so 2026."""
    return day // 10000 if day // 100 % 100 >= 4 else day // 10000 - 1


def _expiry_text(day: int) -> str:
    return f"{day % 100:02d}{MONTHS[day // 100 % 100 - 1]}{day // 10000 % 100:02d}"


def _strike_text(strike_u: int) -> str:
    whole, part = divmod(strike_u, kinds.PRICE_SCALE)
    return str(whole) if not part else f"{whole}.{part:04d}".rstrip("0")


def history_symbol(row: Mapping[str, Any]) -> str | None:
    """The OpenAlgo symbol of a history row whose instrument the master no longer lists (an
    expired contract): the underlying, then DDMMMYY, then the strike and CE or PE, or FUT."""
    base = str(row.get("symbol") or "").strip().upper()
    if not base:
        return None
    if row.get("segment") == "EQ":
        return base
    expiry = row.get("expiry")
    if not isinstance(expiry, int):
        return None
    if row.get("option_type") in ("CE", "PE") and isinstance(row.get("strike_price_u"), int):
        return (
            f"{base}{_expiry_text(expiry)}{_strike_text(row['strike_price_u'])}{row['option_type']}"
        )
    return f"{base}{_expiry_text(expiry)}FUT"


def fill_from_history(row: Mapping[str, Any], listing: Any, at_ms: int) -> dict[str, Any] | None:
    """One row of the portfolio's ``historical_trades`` as a journal fill (source history), or
    None when the journal cannot model it."""
    segment = str(row.get("segment") or "")
    trade_id = str(row.get("trade_id") or "")
    action = str(row.get("transaction_type") or "").upper()
    qty_u, price = row.get("qty_u"), row.get("price_u")
    exchange = (
        listing.exchange
        if listing
        else EXCHANGE_OF.get((segment, str(row.get("exchange") or "").upper()))
    )
    symbol = listing.symbol if listing else history_symbol(row)
    if (
        not trade_id
        or segment not in SEGMENTS
        or action not in kinds.ACTIONS
        or not exchange
        or not symbol
        or not isinstance(qty_u, int)
        or qty_u <= 0
        or qty_u % QTY_SCALE
        or not isinstance(price, int)
        or price < 0
        or not isinstance(row.get("trade_date"), int)
    ):
        return None
    qty = qty_u // QTY_SCALE
    amount = row.get("amount_paise")
    return {
        "mode": "live",
        "day": row["trade_date"],
        "trade_id": trade_id,
        "order_id": f"h:{trade_id}",
        "exchange": exchange,
        "symbol": symbol,
        "instrument_key": str(row.get("instrument_token") or ""),
        "product": "CNC" if segment == "EQ" else "NRML",
        "action": action,
        "qty": qty,
        "price_u": price,
        "value_paise": amount
        if isinstance(amount, int) and amount > 0
        else u_to_paise(qty * price),
        "exch_ts_ms": at_ms,
        "order_ts_ms": None,
        "source": "history",
    }


def history_order_id(fill: Mapping[str, Any]) -> str:
    """The order a history trade is taken to belong to: one per instrument, product, side and
    day (Upstox's history names no order)."""
    return f"h:{fill['day']}:{fill['exchange']}:{fill['symbol']}:{fill['product']}:{fill['action']}"


def same_day_shares(fills: list[dict[str, Any]]) -> int:
    """Give the intraday product to the share fills of each day and instrument whose bought and
    sold quantities are equal (see the module's words, step 2); returns how many fills changed.
    ``fills`` are history fills whose product is still the carry class assumed for a share."""
    days: dict[tuple[int, str, str], list[dict[str, Any]]] = {}
    for f in fills:
        if f["exchange"] in CASH_EXCHANGES and f["product"] == "CNC":
            days.setdefault((f["day"], f["exchange"], f["symbol"]), []).append(f)
    changed = 0
    for group in days.values():
        bought = sum(f["qty"] for f in group if f["action"] == "BUY")
        sold = sum(f["qty"] for f in group if f["action"] == "SELL")
        if bought and bought == sold:
            for f in group:
                f["product"] = "MIS"
            changed += len(group)
    return changed


def pair_row(row: Mapping[str, Any], segment: str, fy: int, now: int) -> dict[str, Any]:
    """One of Upstox's matched pairs as an ``upstox_pair`` row, its hash over what it says."""
    fields = ("scrip_name", "isin", "trade_type", "qty_u", "buy_date", "sell_date",
              "buy_average_u", "sell_average_u", "buy_amount_paise", "sell_amount_paise")  # fmt: skip
    raw = json.dumps([segment, fy, *(row.get(f) for f in fields)], separators=(",", ":"))
    return {
        "segment": segment, "financial_year": fy, "buy_date": row.get("buy_date"),
        "sell_date": row.get("sell_date"), "scrip_name": str(row.get("scrip_name") or "")[:120],
        "isin": row.get("isin"), "trade_type": row.get("trade_type"), "qty_u": row["qty_u"],
        "buy_avg_u": row["buy_average_u"], "sell_avg_u": row["sell_average_u"],
        "buy_amount_paise": row["buy_amount_paise"], "sell_amount_paise": row["sell_amount_paise"],
        "realised_paise": row["sell_amount_paise"] - row["buy_amount_paise"],
        "raw_hash": hashlib.sha256(raw.encode()).hexdigest(), "read_at": now,
    }  # fmt: skip


def charges_row(found: Mapping[str, Any], segment: str, fy: int, now: int) -> dict[str, Any]:
    return {
        "segment": segment, "financial_year": fy, "period_from": fy * 10000 + 401,
        "period_to": (fy + 1) * 10000 + 331, "total_paise": found.get("total_paise"),
        "brokerage_paise": found.get("brokerage_paise"), "gst_paise": found.get("gst_paise"),
        "stt_paise": found.get("stt_paise"), "stamp_paise": found.get("stamp_duty_paise"),
        "transaction_paise": found.get("transaction_paise"),
        "clearing_paise": found.get("clearing_paise"), "ipft_paise": found.get("ipft_paise"),
        "others_paise": found.get("others_paise"), "read_at": now,
    }  # fmt: skip


async def _thread(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


def _read(service: Any, fn):
    return _thread(lambda: service.store.read(OWNER, fn))


def _write(service: Any, fn):
    return _thread(lambda: service.store.write(OWNER, fn))


# ---- step 2: the history of days no check marked -------------------------------------------
async def history(service: Any, days: list[int], now: int) -> str:
    """Fill the unmarked ``days`` from Upstox's trade history; returns the log's line."""
    unmarked = await _read(
        service,
        lambda tx: [
            d for d in days if (tx.day_close_get("live", d) or {}).get("reconciled_at") is None
        ],  # fmt: skip
    )
    if not unmarked:
        return "history: every day checked"
    link, rows = link_for(service), []
    start, end = min(unmarked), max(unmarked)
    for segment in SEGMENTS:
        for page in range(1, HISTORY_PAGES + 1):
            answer = await link.historical_trades(segment, start, end, page)
            if answer.get("state") != "ok":
                raise PortfolioUnavailable("failed", str(answer.get("note") or ""))
            found = [r for r in answer.get("rows") or [] if isinstance(r, Mapping)]
            rows += [r for r in found
                     if r.get("trade_date") in unmarked and r.get("segment") == segment]  # fmt: skip
            if page >= int(answer.get("total_pages") or 1) or not found:
                break
    try:
        listings = await _thread(
            lambda: records_for(service).instruments(r.get("instrument_token") for r in rows)
        )
    except RecordsUnavailable:
        listings = {}
    from sudarshan.alerts.market.sessions import epoch_ms

    fills = [
        fill_from_history(r, listings.get(r.get("instrument_token")),
                          epoch_ms(to_date(r["trade_date"]), NOON_MIN))
        for r in rows
    ]  # fmt: skip
    kept = [f for f in fills if f is not None]

    def known(tx):
        held = tx.fills_by_trade("live", [f["trade_id"] for f in kept])
        products: dict[tuple[int, str, str], set[str]] = {}
        for d in {f["day"] for f in kept}:
            for f in tx.fills_of("live", day=d):
                products.setdefault((d, f["exchange"], f["symbol"]), set()).add(f["product"])
        return held, products

    held, products = await _read(service, known)
    fresh = [f for f in kept if f["trade_id"] not in held]
    assumed = []
    for f in fresh:  # the product the day's recorded fills of the instrument carry, if only one
        found = products.get((f["day"], f["exchange"], f["symbol"])) or set()
        if len(found) == 1:
            f["product"] = next(iter(found))
        else:
            assumed.append(f)
    same_day: dict[int, int] = {}
    if same_day_shares(assumed):
        for f in assumed:
            if f["product"] == "MIS":
                same_day[f["day"]] = same_day.get(f["day"], 0) + 1
    for f in fresh:
        f["order_id"] = history_order_id(f)
    labels = {f["order_id"]: {"strategy": HISTORY_LABEL, "surface": "history"} for f in fresh}
    if fresh:
        await capture_for(service).record_history(fresh, labels)
    per_day: dict[int, int] = {}
    for f in kept:
        per_day[f["day"]] = per_day.get(f["day"], 0) + 1

    def mark(tx):
        for day, count in per_day.items():
            note = (
                f"Rebuilt from Upstox's trade history: {count} "
                f"{'trade' if count == 1 else 'trades'}. That history has no times or products, "
                "so these trades are ordered by trade number and counted as carried."
            )
            if same_day.get(day):
                note += (
                    f" {same_day[day]} of them are share trades that bought and sold the same "
                    "quantity that day, counted as bought and sold the same day (intraday)."
                )
            tx.day_close_put("live", day, {"reconciled_at": now, "reconcile_note": note}, now)

    await _write(service, mark)
    left = len(unmarked) - len(per_day)
    return (
        f"history: {len(fresh)} trades added for {len(per_day)} days, {len(fills) - len(kept)} "
        f"left out, {left} days without rows yet"
    )


# ---- step 3: Upstox's P&L report -------------------------------------------------------------
async def pnl_rows(service: Any, days: list[int], now: int) -> str:
    """Upstox's matched pairs of ``days`` and the year's charges, for the segments traded then."""

    def traded(tx):
        out: set[tuple[str, int]] = set()
        for day in days:
            for fill in tx.fills_of("live", day=day):
                segment = SEGMENT_OF.get(fill["exchange"])
                if segment and fill["source"] != "withdrawn":
                    out.add((segment, financial_year(day)))
        return out

    wanted = await _read(service, traded)
    if not wanted:
        return "P&L report: nothing traded"
    link, pairs, charges = link_for(service), [], []
    for segment, fy in sorted(wanted):
        for page in range(1, PNL_PAGES + 1):
            answer = await link.pnl_report(segment, fy, "data", page, PNL_PAGE_SIZE)
            if answer.get("state") != "ok":
                raise PortfolioUnavailable("failed", str(answer.get("note") or ""))
            found = [r for r in answer.get("rows") or [] if isinstance(r, Mapping)]
            pairs += [
                pair_row(r, segment, fy, now)
                for r in found
                if r.get("sell_date") in days or r.get("buy_date") in days
            ]
            if len(found) < int(answer.get("page_size") or PNL_PAGE_SIZE):
                break
        answer = await link.pnl_report(segment, fy, "charges")
        if answer.get("state") == "ok" and isinstance(answer.get("charges"), Mapping):
            charges.append(charges_row(answer["charges"], segment, fy, now))

    def write(tx):
        added = tx.upstox_pairs_put(pairs, now)
        for row in charges:
            tx.upstox_charges_put(row, now)
        return added

    added = await _write(service, write)
    return f"P&L report: {added} new pairs, charges for {len(charges)} segments"


# ---- the job ---------------------------------------------------------------------------------
async def catch_up(service: Any, today: int, now: int, late: bool) -> str:
    plan = schedule_for(service)
    lines, failed = [], None
    if plan.trading(to_date(today)):
        out = await capture_for(service).read_live("close_reconcile", withdraw=False)
        lines.append(f"today: {out.line()}")
        if out.state != "ok":
            failed = f"Today's trades could not be read at 22:00: {out.words}"
    done = service.parts.setdefault("catchup_done", set())
    days = [to_day(d) for d in plan.previous_trading_days(to_date(today), CATCHUP_DAYS)]
    for name, step in (("history", history), ("pnl", pnl_rows)):
        if (today, name) in done:
            continue
        try:
            lines.append(await step(service, days, now))
            done.add((today, name))
        except PortfolioUnavailable as exc:
            lines.append(f"{name}: not read ({exc})")
    if (today, "held") not in done:
        # Only on what today's read and the history made whole: a trade whose sale one of them
        # brings is not one whose sale was missed.
        if failed or (today, "history") not in done:
            lines.append("holdings: not checked tonight")
        else:
            try:
                from ..book import recount_shares

                async with capture_for(service).locks["live"]:
                    lines.append(await recount_shares(service, today, now))
                done.add((today, "held"))
            except PortfolioUnavailable as exc:
                lines.append(f"holdings: not read ({exc})")
    for key in [k for k in done if k[0] < today]:
        done.discard(key)
    cutoff = to_day(to_date(today) - timedelta(days=KEEP_JOB_DAYS))
    let_go = await _write(service, lambda tx: tx.job_forget(cutoff))
    if let_go:
        lines.append(f"job log: {let_go} rows older than {KEEP_JOB_DAYS} days let go")
    if failed:
        raise JobFailed("; ".join([failed, *lines[1:]]))
    return "; ".join(lines)


def register_jobs() -> None:
    register_job("catchup", catch_up)
