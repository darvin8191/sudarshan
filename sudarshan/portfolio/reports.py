"""Upstox's trade history and trade-wise P&L reports, kept per financial year (M7.6, M7.7).

Upstox gives three financial years of executed trades (``GET /v2/charges/historical-trades``,
under Orders) and, per segment and year, a trade-wise realised P&L report with its metadata and
the charges on those trades (``/v2/trade/profit-loss/{metadata,data,charges}``, under Trade Profit
And Loss). Both categories are open to the Analytics Token from the whitelisted static IP, as GETs
(upstox.com/developer/api-documentation/analytics-token, read 8 Nov 2026). This is not market data:
every answer carries the time it was read.

* **Cached per year and segment** in ``report_cache`` (V3). A past year, once complete, is kept
  and never read again on its own. The current year is read again at most once a night, after the
  closing record (``reports_step``), or when the owner opens the view and the record is older than
  ``CURRENT_MAX_AGE_MS``. A record left ``partial`` (the budget ran out mid-way) is completed on
  the next open, from the page it stopped at.
* **Inside the reader's budget.** Every call is one ``Budget.take`` on its own API name (``trades``,
  ``pnl_meta``, ``pnl``, ``charges``: 2 a second, 30 a minute, 200 in 30 minutes each), the same
  60 second pause on a 429 and the same pause on a refused token as the live book
  (``UpstoxReader``). A read that finds the budget empty stops where it is, keeps what it has,
  and says so; nothing waits in a queue.
* **Integers.** Rupees become paise, quantities ``qty_u`` and prices ``price_u`` at the parser,
  half away from zero; a float never reaches the store or the phone. Dates become ``YYYYMMDD``.
* **One read at a time per record.** A read in flight is shared by every caller; the op answers
  within ``ANSWER_WITHIN_S`` seconds with what the cache holds and the state ``reading`` while the
  read goes on in the background.
* **For other services later.** The three ops answer the whole record in one shape (P7 journal,
  P6 family), so a consumer with a sealed purpose of its own reads the cache and never Upstox.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any

import httpx

from .budget import PAUSE_S
from .core.days import day_of_instant, financial_year, is_day
from .core.kinds import PRICE_SCALE, QTY_SCALE, REPORT_SEGMENTS, REPORT_YEARS
from .store import Scope
from .upstox import RATE_CODE, TIMEOUT_S, Unreadable, _signature, paise, price_u, qty_u

log = logging.getLogger("sudarshan.portfolio.reports")

OWNER = Scope.owner()
#: The current year's record is read again when the owner opens the view and it is older than this.
CURRENT_MAX_AGE_MS = 6 * 60 * 60 * 1000
#: The op answers the cache with ``reading`` if the read has not finished in this long.
ANSWER_WITHIN_S = 8.0
#: Trades per page asked for; Upstox states no maximum, so a page is kept modest.
TRADES_PAGE_SIZE = 500
#: Pages read in one go for one record; past this the record is ``partial`` until the next read.
MAX_PAGES_PER_READ = 20
PATHS = {
    "trades": "/v2/charges/historical-trades",
    "pnl_meta": "/v2/trade/profit-loss/metadata",
    "pnl": "/v2/trade/profit-loss/data",
    "charges": "/v2/trade/profit-loss/charges",
}
#: The P&L report has no mutual-fund segment: Upstox lists MF trades in the history alone.
PNL_SEGMENTS = tuple(s for s in REPORT_SEGMENTS if s != "MF")
WORDS = {
    "no_token": "SUDARSHAN cannot find its Upstox token on this server. Check the server; "
    "nothing needs changing in the Upstox app.",
    "token_refused": "Upstox did not accept SUDARSHAN's read-only token, so the reports cannot "
    "be read now. The token is shared with TRINETRA: do not create a new one in the Upstox app; "
    "check the server first.",
    "slow": "Upstox asked SUDARSHAN to slow down; the rest of this report follows in a minute.",
    "busy": "SUDARSHAN has read Upstox as often as it allows for now; the rest of this report "
    "follows in a minute.",
    "silent": "Upstox did not answer.",
    "refused": "Upstox did not accept this request.",
    "unreadable": "Upstox sent an answer SUDARSHAN could not read.",
    "reading": "Reading from Upstox. This takes a moment.",
    "more": "More pages follow; the figures so far are shown.",
    "no_pnl_mf": "Upstox has no trade-wise P&L report for mutual funds; their trades are in "
    "History.",
    "year": "Upstox keeps the last three financial years only.",
    "segment": "Choose one of the segments: EQ, FO, COM, CD or MF.",
}


# ---- the years ---------------------------------------------------------------------------------
def fy_code(fy: int) -> str:
    """Upstox's financial-year code: 2026 (FY 2026-27) is ``2627``."""
    return f"{fy % 100:02d}{(fy + 1) % 100:02d}"


def fy_label(fy: int) -> str:
    return f"FY {fy}-{(fy + 1) % 100:02d}"


def fy_span(fy: int) -> tuple[int, int]:
    """The first and last day of the financial year, as ``YYYYMMDD``."""
    return fy * 10000 + 401, (fy + 1) * 10000 + 331


def years_of(today: int) -> list[dict[str, Any]]:
    """The last REPORT_YEARS financial years, newest first, the current one marked."""
    current = financial_year(today)
    return [
        {"fy": fy, "label": fy_label(fy), "current": fy == current}
        for fy in range(current, current - REPORT_YEARS, -1)
    ]


def year_ok(fy: int, today: int) -> bool:
    current = financial_year(today)
    return isinstance(fy, int) and current - REPORT_YEARS < fy <= current


def _iso_day(text: Any) -> int | None:
    """``YYYY-mm-dd`` as ``YYYYMMDD``."""
    if not isinstance(text, str) or len(text) != 10 or text[4] != "-" or text[7] != "-":
        return None
    day = int(text[:4] + text[5:7] + text[8:10]) if text.replace("-", "").isdigit() else None
    return day if day is not None and is_day(day) else None


def _dmy_day(text: Any) -> int | None:
    """``dd-mm-yyyy`` as ``YYYYMMDD``."""
    if not isinstance(text, str) or len(text) != 10 or text[2] != "-" or text[5] != "-":
        return None
    day = int(text[6:10] + text[3:5] + text[:2]) if text.replace("-", "").isdigit() else None
    return day if day is not None and is_day(day) else None


def _iso(day: int) -> str:
    return f"{day // 10000:04d}-{day // 100 % 100:02d}-{day % 100:02d}"


def _need(value: int | None, what: str) -> int:
    if value is None:
        raise Unreadable(what)
    return value


def _text(value: Any) -> str | None:
    return str(value).strip() if isinstance(value, (str, int)) and str(value).strip() else None


# ---- parsers: Upstox's rows as integers --------------------------------------------------------
def parse_trade(item: Mapping[str, Any]) -> dict[str, Any]:
    """One row of ``/v2/charges/historical-trades``, as the phone's ``TradeRow``."""
    side = str(item.get("transaction_type") or "").upper()
    if side not in ("BUY", "SELL"):
        raise Unreadable("transaction_type")
    segment = str(item.get("segment") or "").upper()
    if segment not in REPORT_SEGMENTS:
        raise Unreadable("segment")
    option = str(item.get("option_type") or "").upper() or None
    return {
        "trade_id": _text(item.get("trade_id")) or "",
        "trade_date": _need(_iso_day(item.get("trade_date")), "trade_date"),
        "exchange": str(item.get("exchange") or "").upper(),
        "segment": segment,
        "transaction_type": side,
        "scrip_name": _text(item.get("scrip_name")) or "",
        "symbol": _text(item.get("symbol")),
        "isin": _text(item.get("isin")),
        "instrument_token": _text(item.get("instrument_token")),
        "option_type": option if option in ("CE", "PE") else None,
        "strike_price_u": price_u(item.get("strike_price")) or None,
        "expiry": _iso_day(item.get("expiry")),
        "qty_u": _need(qty_u(item.get("quantity")), "quantity"),
        "price_u": _need(price_u(item.get("price")), "price"),
        "amount_paise": _need(paise(item.get("amount")), "amount"),
    }


def parse_pnl_row(item: Mapping[str, Any]) -> dict[str, Any]:
    """One row of ``/v2/trade/profit-loss/data``, as the phone's ``PnlRow``."""
    bought = _need(paise(item.get("buy_amount")), "buy_amount")
    sold = _need(paise(item.get("sell_amount")), "sell_amount")
    return {
        "scrip_name": _text(item.get("scrip_name")) or "",
        "isin": _text(item.get("isin")),
        "trade_type": str(item.get("trade_type") or "").upper(),
        "qty_u": _need(qty_u(item.get("quantity")), "quantity"),
        "buy_date": _dmy_day(item.get("buy_date")),
        "sell_date": _dmy_day(item.get("sell_date")),
        "buy_average_u": _need(price_u(item.get("buy_average")), "buy_average"),
        "sell_average_u": _need(price_u(item.get("sell_average")), "sell_average"),
        "buy_amount_paise": bought,
        "sell_amount_paise": sold,
        "pnl_paise": sold - bought,
    }


def parse_charges(data: Mapping[str, Any]) -> dict[str, int | None]:
    """``data.charges_breakdown`` of ``/v2/trade/profit-loss/charges``; a charge Upstox left null
    stays None (never 0)."""
    tree = data.get("charges_breakdown")
    if not isinstance(tree, Mapping):
        raise Unreadable("charges_breakdown")
    taxes = tree.get("taxes") if isinstance(tree.get("taxes"), Mapping) else {}
    other = tree.get("charges") if isinstance(tree.get("charges"), Mapping) else {}
    return {
        "total_paise": paise(tree.get("total")),
        "brokerage_paise": paise(tree.get("brokerage")),
        "gst_paise": paise(taxes.get("gst")),
        "stt_paise": paise(taxes.get("stt")),
        "stamp_duty_paise": paise(taxes.get("stamp_duty")),
        "transaction_paise": paise(other.get("transaction")),
        "clearing_paise": paise(other.get("clearing")),
        "ipft_paise": paise(other.get("ipft")),
        "others_paise": paise(other.get("others")),
        "sebi_turnover_paise": paise(other.get("sebi_turnover")),
        "demat_transaction_paise": paise(other.get("demat_transaction")),
    }


def parse_meta(data: Mapping[str, Any]) -> dict[str, int]:
    count, limit = data.get("trades_count"), data.get("page_size_limit")
    if not isinstance(count, int) or not isinstance(limit, int) or count < 0 or limit <= 0:
        raise Unreadable("metadata")
    return {"trades_count": count, "page_size_limit": limit}


def _page_meta(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Upstox writes the page block as ``metadata`` in one place and ``meta_data`` in another."""
    block = payload.get("metadata") or payload.get("meta_data") or {}
    page = block.get("page") if isinstance(block, Mapping) else None
    return dict(page) if isinstance(page, Mapping) else {}


def _whole(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# ---- the two reports as the phone reads them ----------------------------------------------------
def trades_report(record: Mapping[str, Any] | None, fy: int, segment: str) -> dict[str, Any]:
    payload = (record or {}).get("payload") or {}
    rows = payload.get("rows") or []
    return {
        "state": (record or {}).get("state", "unavailable"),
        "read_at": (record or {}).get("read_at"),
        "note": (record or {}).get("note"),
        "fy": fy,
        "segment": segment,
        "rows": rows,
        "total_records": payload.get("total_records"),
        "bought_paise": sum(r["amount_paise"] for r in rows if r["transaction_type"] == "BUY"),
        "sold_paise": sum(r["amount_paise"] for r in rows if r["transaction_type"] == "SELL"),
    }


def pnl_report(record: Mapping[str, Any] | None, fy: int, segment: str) -> dict[str, Any]:
    payload = (record or {}).get("payload") or {}
    rows = payload.get("rows") or []
    charges = payload.get("charges")
    realised = sum(r["pnl_paise"] for r in rows)
    total = charges.get("total_paise") if isinstance(charges, Mapping) else None
    return {
        "state": (record or {}).get("state", "unavailable"),
        "read_at": (record or {}).get("read_at"),
        "note": (record or {}).get("note"),
        "fy": fy,
        "segment": segment,
        "rows": rows,
        "trades_count": payload.get("trades_count"),
        "realised_paise": realised,
        "charges": charges,
        "net_paise": None if total is None else realised - total,
    }


# ---- the reader ---------------------------------------------------------------------------------
class ReportReader:
    """Reads the reports through the live book's token, budget and client, and keeps them."""

    def __init__(self, service: Any, upstox: Any, *, pages: int = MAX_PAGES_PER_READ):
        self.service, self.upstox, self.pages = service, upstox, pages
        self._tasks: dict[tuple[str, int, str], asyncio.Task] = {}

    @property
    def clock(self) -> Callable[[], int]:
        return self.service.clock

    # ---- the cache ------------------------------------------------------------------------
    async def _get(self, kind: str, fy: int, segment: str) -> dict[str, Any] | None:
        store = self.service.store
        return await asyncio.get_running_loop().run_in_executor(
            None, lambda: store.read(OWNER, lambda tx: tx.report_get(kind, fy, segment))
        )

    async def _put(self, kind: str, fy: int, segment: str, state: str, read_at, note, payload):
        store, now = self.service.store, self.clock()

        def run(tx):
            tx.report_put(kind, fy, segment, state, read_at, note, payload, now)

        await asyncio.get_running_loop().run_in_executor(None, lambda: store.write(OWNER, run))

    def due(self, record: Mapping[str, Any] | None, fy: int, now: int) -> bool:
        """Whether a record should be read (again) when the owner opens it: never for a complete
        past year; a current year past its age; anything partial or missing."""
        if record is None or record.get("state") != "ok":
            return True
        if fy != financial_year(day_of_instant(now)):
            return False
        read_at = record.get("read_at")
        return read_at is None or now - read_at > CURRENT_MAX_AGE_MS

    # ---- the ops' entry ------------------------------------------------------------------------
    async def report(self, kind: str, fy: int, segment: str, *, force: bool = False) -> dict:
        """The record as the phone reads it, read first if due, answered within ANSWER_WITHIN_S."""
        now = self.clock()
        record = await self._get(kind, fy, segment)
        if kind == "pnl" and segment not in PNL_SEGMENTS:
            return pnl_report(
                {"state": "ok", "read_at": now, "note": WORDS["no_pnl_mf"], "payload": {}},
                fy,
                segment,
            )
        if force or self.due(record, fy, now):
            task = self._start(kind, fy, segment, record)
            try:
                await asyncio.wait_for(asyncio.shield(task), ANSWER_WITHIN_S)
            except TimeoutError:
                shown = await self._get(kind, fy, segment)
                shape = trades_report if kind == "trades" else pnl_report
                out = shape(shown, fy, segment)
                out["state"], out["note"] = "reading", WORDS["reading"]
                return out
            record = await self._get(kind, fy, segment)
        return (trades_report if kind == "trades" else pnl_report)(record, fy, segment)

    def _start(self, kind: str, fy: int, segment: str, record) -> asyncio.Task:
        key = (kind, fy, segment)
        task = self._tasks.get(key)
        if task is None or task.done():
            read = self._read_trades if kind == "trades" else self._read_pnl
            task = asyncio.get_running_loop().create_task(read(fy, segment, record))
            self._tasks[key] = task
        return task

    async def refresh_current(self, now: int) -> list[str]:
        """The nightly read of the current year (every segment that has a record, plus EQ for the
        history), each inside the budget; returns what was read."""
        fy = financial_year(day_of_instant(now))
        done = []
        store = self.service.store
        kept = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: store.read(
                OWNER,
                lambda tx: (
                    [(r["kind"], r["segment"]) for r in tx.report_list("trades")]
                    + [(r["kind"], r["segment"]) for r in tx.report_list("pnl")]
                ),
            ),
        )
        wanted = {("trades", "EQ"), *kept}
        for kind, segment in sorted(wanted):
            if kind == "pnl" and segment not in PNL_SEGMENTS:
                continue
            record = await self._get(kind, fy, segment)
            if record is not None and record.get("state") == "ok":
                read_at = record.get("read_at") or 0
                if day_of_instant(read_at) == day_of_instant(now):
                    continue  # read today already (the owner opened it after the close)
            await self._start(kind, fy, segment, record)
            done.append(f"{kind} {segment}")
        return done

    # ---- one call ---------------------------------------------------------------------------
    async def _call(self, api: str, params: Mapping[str, str]) -> tuple[str, Any]:
        """``("ok", payload)`` or ``("<words key>", None)``; spends one read of the budget."""
        upstox = self.upstox
        if upstox._refused:
            if _signature(upstox.token_file) == upstox._refused_at:
                return "token_refused", None
            upstox._refused = False
        if upstox.budget.paused_for(api):
            return "slow", None
        token = upstox._token()
        if token is None:
            return "no_token", None
        if not upstox.budget.take(api):
            return "busy", None
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        try:
            answer = await upstox.client.get(
                upstox.base_url + PATHS[api], headers=headers, params=dict(params)
            )
        except httpx.HTTPError as exc:
            log.warning("Upstox %s did not answer (%s)", api, type(exc).__name__)
            return "silent", None
        finally:
            del token, headers
        status = answer.status_code
        if status == 401:
            upstox._refused, upstox._refused_at = True, _signature(upstox.token_file)
            log.warning("Upstox refused the read-only token; report reads pause until it changes.")
            return "token_refused", None
        if status == 429 or upstox._rate_coded(answer):
            upstox.budget.pause(api, PAUSE_S)
            log.warning("Upstox asked for a pause on %s: %d seconds", api, int(PAUSE_S))
            return "slow", None
        if status >= 500:
            log.warning("Upstox %s answered with a server fault (%d)", api, status)
            return "silent", None
        if status != 200:
            log.warning("Upstox %s refused the request (%d)", api, status)
            return "refused", None
        try:
            payload = json.loads(answer.text, parse_float=Decimal)
        except ValueError:
            return "unreadable", None
        if not isinstance(payload, Mapping) or payload.get("status") != "success":
            return "refused", None
        return "ok", payload

    # ---- trades ----------------------------------------------------------------------------
    async def _read_trades(self, fy: int, segment: str, record) -> None:
        now = self.clock()
        payload = dict((record or {}).get("payload") or {})
        fresh = record is None or record.get("state") == "ok" or not payload.get("rows")
        rows: list[dict] = [] if fresh else list(payload.get("rows") or [])
        page = 1 if fresh else int(payload.get("next_page") or 1)
        first, last = fy_span(fy)
        today = day_of_instant(now)
        end = min(last, today)
        total = None
        state, note = "ok", None
        for _ in range(self.pages):
            outcome, answer = await self._call(
                "trades",
                {
                    "segment": segment,
                    "start_date": _iso(first),
                    "end_date": _iso(end),
                    "page_number": str(page),
                    "page_size": str(TRADES_PAGE_SIZE),
                },
            )
            if outcome != "ok":
                state, note = "partial" if rows else "unavailable", WORDS[outcome]
                break
            try:
                items = [parse_trade(i) for i in answer.get("data") or [] if isinstance(i, Mapping)]
            except Unreadable as exc:
                log.error("a trade row could not be read: %s", exc)
                state, note = "partial" if rows else "unavailable", WORDS["unreadable"]
                break
            rows.extend(items)
            meta = _page_meta(answer)
            total = (
                _whole(meta.get("total_records"))
                if meta.get("total_records") is not None
                else total
            )
            pages = _whole(meta.get("total_pages"))
            page += 1
            if len(items) < TRADES_PAGE_SIZE or (pages is not None and page > pages):
                break
        else:
            state, note = "partial", WORDS["more"]
        seen: dict[str, dict] = {}
        for row in rows:  # a page read twice (a retry) never doubles a trade
            seen[row["trade_id"] or f"{row['trade_date']}-{len(seen)}"] = row
        kept = sorted(seen.values(), key=lambda r: (r["trade_date"], r["trade_id"]))
        read_at = self.clock() if state == "ok" else (record or {}).get("read_at")
        if state == "unavailable" and record is not None and record.get("payload"):
            # Keep the last good figures, say why they are not fresh.
            state = record.get("state", "partial") if record.get("state") != "ok" else "ok"
            kept = list(record["payload"].get("rows") or [])
            total = record["payload"].get("total_records")
            page = int(record["payload"].get("next_page") or 1)
        await self._put(
            "trades", fy, segment, state, read_at, note,
            {"rows": kept, "total_records": total, "next_page": page if state == "partial" else None},
        )  # fmt: skip

    # ---- the P&L ----------------------------------------------------------------------------
    async def _read_pnl(self, fy: int, segment: str, record) -> None:
        payload = dict((record or {}).get("payload") or {})
        fresh = record is None or record.get("state") == "ok" or not payload.get("rows")
        rows: list[dict] = [] if fresh else list(payload.get("rows") or [])
        page = 1 if fresh else int(payload.get("next_page") or 1)
        count = None if fresh else payload.get("trades_count")
        size = None if fresh else payload.get("page_size")
        charges = None if fresh else payload.get("charges")
        base = {"segment": segment, "financial_year": fy_code(fy)}
        state, note = "ok", None
        if count is None or size is None:
            outcome, answer = await self._call("pnl_meta", base)
            if outcome == "ok":
                try:
                    meta = parse_meta(answer.get("data") or {})
                    count, size = meta["trades_count"], meta["page_size_limit"]
                except Unreadable:
                    outcome = "unreadable"
            if outcome != "ok":
                return await self._keep(record, fy, segment, outcome)
        pages_total = max(1, math.ceil(count / size)) if count else 0
        for _ in range(self.pages):
            if page > pages_total:
                break
            outcome, answer = await self._call(
                "pnl", {**base, "page_number": str(page), "page_size": str(size)}
            )
            if outcome != "ok":
                state, note = "partial", WORDS[outcome]
                break
            try:
                items = [
                    parse_pnl_row(i) for i in answer.get("data") or [] if isinstance(i, Mapping)
                ]
            except Unreadable as exc:
                log.error("a P&L row could not be read: %s", exc)
                state, note = "partial", WORDS["unreadable"]
                break
            rows.extend(items)
            page += 1
            if not items:
                break
        else:
            if page <= pages_total:
                state, note = "partial", WORDS["more"]
        if state == "ok" and charges is None:
            outcome, answer = await self._call("charges", base)
            if outcome == "ok":
                try:
                    charges = parse_charges(answer.get("data") or {})
                except Unreadable:
                    outcome = "unreadable"
            if outcome != "ok":
                state, note = "partial", WORDS[outcome]
        read_at = self.clock() if state == "ok" else (record or {}).get("read_at")
        await self._put(
            "pnl", fy, segment, state, read_at, note,
            {
                "rows": rows, "trades_count": count, "page_size": size, "charges": charges,
                "next_page": page if state == "partial" and page <= pages_total else None,
            },
        )  # fmt: skip

    async def _keep(self, record, fy: int, segment: str, outcome: str) -> None:
        """Nothing new could be read: keep what there is and say why."""
        payload = (record or {}).get("payload")
        state = "unavailable" if not payload else record.get("state", "partial")
        if state == "ok":
            state = "ok"  # a complete record stays complete; the note says the refresh failed
        await self._put(
            "pnl", fy, segment, state, (record or {}).get("read_at"), WORDS[outcome], payload
        )


# ---- the nightly job --------------------------------------------------------------------------
async def reports_step(service: Any, now: int) -> str | None:
    """Read the current year's reports once a night, after the closing record."""
    if service.store is None:
        return None
    today = day_of_instant(now)
    store = service.store

    def ready(tx):
        return tx.job_get("eod", today) is not None and tx.job_get("reports", today) is None

    due = await asyncio.get_running_loop().run_in_executor(None, lambda: store.read(OWNER, ready))
    if not due:
        return None
    reader = service.ensure_reports()
    try:
        read = await reader.refresh_current(now)
        status, detail = "ok", f"read {', '.join(read) or 'nothing new'}"
    except Exception:
        log.exception("the nightly report read failed")
        status, detail = "failed", "The reports could not be read tonight."
    await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: store.write(OWNER, lambda tx: tx.job_put("reports", today, status, detail, now)),
    )
    return status


def register_jobs() -> None:
    from .service import job
    from .timers import run_forever

    @job("reports")
    async def reports_job(service) -> None:
        await run_forever(service, "reports", reports_step)


__all__ = [
    "ANSWER_WITHIN_S",
    "CURRENT_MAX_AGE_MS",
    "PNL_SEGMENTS",
    "ReportReader",
    "TIMEOUT_S",
    "WORDS",
    "fy_code",
    "fy_label",
    "fy_span",
    "parse_charges",
    "parse_meta",
    "parse_pnl_row",
    "parse_trade",
    "pnl_report",
    "register_jobs",
    "reports_step",
    "trades_report",
    "year_ok",
    "years_of",
]
