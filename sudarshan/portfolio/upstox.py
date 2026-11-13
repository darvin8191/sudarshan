"""Reading the owner's Upstox account on the Analytics Token (M7.1 to M7.4, design 3.1 to 3.3).

Six parts, each its own API with its own budget: holdings, positions, MTF positions and funds
(the Upstox tab), and the day's orders and trades (read for the family and the journal; the
portfolio page does not show them). All are GETs with ``Authorization: Bearer``, a 10 second timeout and no redirects.

* **Numbers.** The answer is parsed with ``Decimal`` for every fraction, and each figure becomes
  paise, ``qty_u`` or ``price_u`` at the parser, half away from zero. A float never reaches the
  store or the phone.
* **Single flight, five seconds of memory.** A part read less than ``max_age`` seconds ago (5 by
  default) is answered from memory; two phones cost one read, and a read in flight is shared.
* **Words, not codes.** A 401, a 429, a 423, a timeout and a 5xx each end in a sentence the owner
  can act on, with the time of the figures that are still shown. The last good figures are always
  kept; a part with none says "unavailable", never zeros.
* **Funds window.** No funds read between 00:00 and 05:30 IST by this service's own clock; a 423
  at any other hour is handled the same way.
* **429.** That API rests for 60 seconds.
* **401.** Reads of the token pause until the token file changes: the same token is shared with
  other services and a second guess would only be refused again.
* **The token** is read from its file for every call and is never logged or kept in a message.

Prices, values and P&L between reads come from ticks (design 3.4), never from this module.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import httpx

from .budget import OWNER, PAUSE_S, Budget
from .core.days import IST_OFFSET_MS, MS_PER_DAY
from .core.kinds import PRICE_SCALE, QTY_SCALE, UPSTOX_PARTS

log = logging.getLogger("sudarshan.portfolio.upstox")

TIMEOUT_S = httpx.Timeout(10.0)
MAX_AGE_S = 5
FAILED_REUSE_MS = 2000  # a failed read is not repeated for two seconds
FUNDS_OPENS_MIN = 5 * 60 + 30  # IST minutes after midnight; no funds read before this
TOKEN_MIN = 16
PATHS = {
    "holdings": ("/v2/portfolio/long-term-holdings", {}),
    "positions": ("/v2/portfolio/short-term-positions", {}),
    "mtf": ("/v3/portfolio/mtf-positions", {}),
    "funds": ("/v3/user/get-funds-and-margin", {"Api-Version": "3.0"}),
    "orders": ("/v2/order/retrieve-all", {}),
    "trades": ("/v2/order/trades/get-trades-for-day", {}),
}
assert set(PATHS) == set(UPSTOX_PARTS)
RATE_CODE = "UDAPI10005"
WORDS = {
    "token_missing": "SUDARSHAN cannot find its Upstox token on this server. Check the server; "
    "nothing needs changing in the Upstox app.",
    "token_refused": "Upstox did not accept SUDARSHAN's read-only token, so the Upstox tab cannot "
    "be read now. The token is shared with TRINETRA: do not create a new one in the Upstox app; "
    "check the server first.",
    "slow": "Upstox asked SUDARSHAN to slow down.",
    "silent": "Upstox did not answer.",
    "refused": "Upstox did not accept this request.",
    "unreadable": "Upstox sent an answer SUDARSHAN could not read.",
    "busy": "SUDARSHAN is reading Upstox as often as it allows.",
    "window": "Upstox does not give funds between midnight and 5:30 AM.",
    "window_none": "Funds are available from 5:30 AM.",
    "held_back": "SUDARSHAN is keeping its Upstox reads for the account owner just now.",
}


class Unreadable(Exception):
    """The answer did not have the shape of the part."""


# ---- numbers ---------------------------------------------------------------------------------
def _decimal(value) -> Decimal | None:
    if value is None or value.__class__ is bool:
        return None
    if isinstance(value, Decimal):
        found = value
    elif isinstance(value, int):
        found = Decimal(value)
    elif isinstance(value, str):
        try:
            found = Decimal(value.strip())
        except ArithmeticError:
            return None
    else:
        return None
    return found if found.is_finite() else None


def scaled(value, factor: int) -> int | None:
    """``value`` x ``factor`` as a whole number, half away from zero; None if it is no number."""
    found = _decimal(value)
    if found is None:
        return None
    return int((found * factor).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def paise(value) -> int | None:
    return scaled(value, 100)


def qty_u(value) -> int | None:
    return scaled(value, QTY_SCALE)


def price_u(value) -> int | None:
    return scaled(value, PRICE_SCALE)


def pct(value) -> str | None:
    found = _decimal(value)
    if found is None:
        return None
    return format(found.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP), "f")


def _need(value: int | None, what: str) -> int:
    if value is None:
        raise Unreadable(what)
    return value


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


# ---- the parts ------------------------------------------------------------------------------
def parse_holding(item: Mapping[str, Any]) -> dict[str, Any]:
    key = _text(item.get("instrument_token"))
    if key is None:
        raise Unreadable("instrument_token")
    close = price_u(item.get("close_price"))
    return {
        "instrument_key": key,
        "exchange": None,
        "symbol": None,
        "name": _text(item.get("company_name")) or _text(item.get("trading_symbol")) or key,
        "isin": _text(item.get("isin")),
        "qty_u": _need(qty_u(item.get("quantity")), "quantity"),
        "t1_qty_u": qty_u(item.get("t1_quantity")) or 0,
        "avg_price_u": _need(price_u(item.get("average_price")), "average_price"),
        "last_price_u": _need(price_u(item.get("last_price")), "last_price"),
        "close_price_u": close or None,
        "pnl_paise": paise(item.get("pnl")) or 0,
        "product": _text(item.get("product")) or "D",
        "collateral_qty_u": qty_u(item.get("collateral_quantity")) or 0,
        "collateral_type": _text(item.get("collateral_type")),
        "haircut_pct": pct(item.get("haircut")),
        "cnc_used_qty_u": qty_u(item.get("cnc_used_quantity")) or 0,
    }


def parse_position(item: Mapping[str, Any]) -> dict[str, Any]:
    key = _text(item.get("instrument_token"))
    if key is None:
        raise Unreadable("instrument_token")
    close = price_u(item.get("close_price"))
    multiplier = _decimal(item.get("multiplier"))
    return {
        "instrument_key": key,
        "exchange": None,
        "symbol": None,
        "name": _text(item.get("trading_symbol")) or key,
        "product": _text(item.get("product")) or "",
        "qty_u": _need(qty_u(item.get("quantity")), "quantity"),
        "overnight_qty_u": qty_u(item.get("overnight_quantity")) or 0,
        "day_buy_qty_u": qty_u(item.get("day_buy_quantity")) or 0,
        "day_sell_qty_u": qty_u(item.get("day_sell_quantity")) or 0,
        "avg_price_u": price_u(item.get("average_price")) or 0,
        "buy_price_u": price_u(item.get("buy_price")) or 0,
        "sell_price_u": price_u(item.get("sell_price")) or 0,
        "last_price_u": _need(price_u(item.get("last_price")), "last_price"),
        "close_price_u": close or None,
        "multiplier": 1
        if multiplier is None
        else (
            int(multiplier) if multiplier == multiplier.to_integral_value() else float(multiplier)
        ),
        "pnl_paise": paise(item.get("pnl")) or 0,
        "realised_paise": paise(item.get("realised")) or 0,
        "unrealised_paise": paise(item.get("unrealised")) or 0,
        "value_paise": paise(item.get("value")) or 0,
    }


def instant(value) -> int | None:
    """Upstox's ``YYYY-MM-DD HH:MM:SS`` (IST) as epoch milliseconds; None if it is not one."""
    if not isinstance(value, str):
        return None
    text = value.strip().replace("T", " ")[:19]
    try:
        stamp = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    days = (stamp.date() - date(1970, 1, 1)).days
    seconds = stamp.hour * 3600 + stamp.minute * 60 + stamp.second
    return days * MS_PER_DAY + seconds * 1000 - IST_OFFSET_MS


def _upper(value) -> str | None:
    text = _text(value)
    return text.upper() if text else None


def parse_order(item: Mapping[str, Any]) -> dict[str, Any]:
    """One row of ``GET /v2/order/retrieve-all``. Prices are ``price_u``, quantities ``qty_u``,
    times epoch milliseconds. ``placed_by`` (the account's own id) and ``guid`` are not kept."""
    order_id, key = _text(item.get("order_id")), _text(item.get("instrument_token"))
    if order_id is None:
        raise Unreadable("order_id")
    if key is None:
        raise Unreadable("instrument_token")
    side = _upper(item.get("transaction_type"))
    if side not in ("BUY", "SELL"):
        raise Unreadable("transaction_type")
    return {
        "order_id": order_id,
        "exchange_order_id": _text(item.get("exchange_order_id")),
        "parent_order_id": _text(item.get("parent_order_id")),
        "instrument_key": key,
        "exchange": _upper(item.get("exchange")),
        "symbol": _text(item.get("trading_symbol")),
        "product": _text(item.get("product")) or "",
        "order_type": _upper(item.get("order_type")),
        "validity": _upper(item.get("validity")),
        "variety": _upper(item.get("variety")),
        "transaction_type": side,
        "status": _text(item.get("status")) or "",
        "status_message": _text(item.get("status_message")),
        "is_amo": item.get("is_amo") is True,
        "tag": _text(item.get("tag")),
        "qty_u": _need(qty_u(item.get("quantity")), "quantity"),
        "filled_qty_u": qty_u(item.get("filled_quantity")),
        "pending_qty_u": qty_u(item.get("pending_quantity")),
        "disclosed_qty_u": qty_u(item.get("disclosed_quantity")),
        "price_u": price_u(item.get("price")),
        "trigger_price_u": price_u(item.get("trigger_price")),
        "avg_price_u": price_u(item.get("average_price")),
        "placed_at": instant(item.get("order_timestamp")),
        "exchange_at": instant(item.get("exchange_timestamp")),
    }


def parse_trade(item: Mapping[str, Any]) -> dict[str, Any]:
    """One row of ``GET /v2/order/trades/get-trades-for-day``: one execution. The amount is
    quantity x average price in paise, worked out in ``Decimal``."""
    trade_id, order_id = _text(item.get("trade_id")), _text(item.get("order_id"))
    if trade_id is None:
        raise Unreadable("trade_id")
    if order_id is None:
        raise Unreadable("order_id")
    key = _text(item.get("instrument_token"))
    if key is None:
        raise Unreadable("instrument_token")
    side = _upper(item.get("transaction_type"))
    if side not in ("BUY", "SELL"):
        raise Unreadable("transaction_type")
    quantity, average = _decimal(item.get("quantity")), _decimal(item.get("average_price"))
    if quantity is None or average is None:
        raise Unreadable("quantity")
    return {
        "trade_id": trade_id,
        "order_id": order_id,
        "exchange_order_id": _text(item.get("exchange_order_id")),
        "instrument_key": key,
        "exchange": _upper(item.get("exchange")),
        "symbol": _text(item.get("trading_symbol")),
        "product": _text(item.get("product")) or "",
        "order_type": _upper(item.get("order_type")),
        "transaction_type": side,
        "qty_u": _need(qty_u(quantity), "quantity"),
        "price_u": _need(price_u(average), "average_price"),
        "amount_paise": _need(paise(quantity * average), "amount"),
        "placed_at": instant(item.get("order_timestamp")),
        "exchange_at": instant(item.get("exchange_timestamp")),
    }


GROUP_LABELS = {
    "cash_available_to_trade": "Cash available to trade",
    "pledge_available_to_trade": "Available against pledged holdings",
    "cash_unavailable_to_trade": "Cash not available yet",
    "pledge_unavailable_to_trade": "Pledged holdings not available",
}
LINE_LABELS = {
    "cash.opening_balance": "Opening balance",
    "cash.added_today": "Added today",
    "cash.withdrawn_today": "Withdrawn today",
    "cash.amount_from_stock_sale": "From stock sales",
    "cash.unpaid_charges": "Unpaid charges",
    "margin_used.total": "Margin used",
    "margin_used.mtf": "Margin used for MTF",
    "margin_used.loss.total": "Losses counted as margin",
    "margin_used.loss.realised": "Realised losses",
    "margin_used.loss.unrealised": "Unrealised losses",
    "margin_used.span_exposure": "SPAN and exposure margin",
    "margin_used.cash_margin_var_elm": "VaR and extreme loss margin",
    "margin_used.premium_present": "Option premium",
    "margin_used.delivery_margin.total": "Delivery margin",
    "margin_used.delivery_margin.equity": "Delivery margin, equity",
    "margin_used.delivery_margin.fo_settlement": "Delivery margin, F&O settlement",
    "margin_from_pledge.total": "Margin from pledged holdings",
    "margin_from_pledge.equity": "From pledged shares",
    "margin_from_pledge.mutual_funds": "From pledged mutual funds",
    "unsettled_profit.todays_profit": "Unsettled profit, today",
    "unsettled_profit.previous_days": "Unsettled profit, earlier days",
    "equity": "Pledged shares",
    "mutual_funds": "Pledged mutual funds",
}


def _label(path: str) -> str:
    return LINE_LABELS.get(path) or path.replace("_", " ").replace(".", ": ").capitalize()


def _lines(node: Mapping[str, Any], prefix: str = "") -> list[dict[str, Any]]:
    out = []
    for name, value in node.items():
        path = f"{prefix}{name}"
        if isinstance(value, Mapping):
            out.extend(_lines(value, path + "."))
        elif (amount := paise(value)) is not None:
            out.append({"key": path, "label": _label(path), "paise": amount})
    return out


def parse_funds(data: Mapping[str, Any]) -> dict[str, Any]:
    """The V3 tree as groups of labelled lines: every number kept, none invented."""
    available = data.get("available_to_trade")
    if not isinstance(available, Mapping):
        raise Unreadable("available_to_trade")
    total = _need(paise(available.get("total")), "available_to_trade.total")
    groups = []
    for top in data.values():
        if not isinstance(top, Mapping):
            continue
        for name, child in top.items():
            if not isinstance(child, Mapping):
                continue
            lines = [ln for ln in _lines(child) if ln["key"] != "total"]
            groups.append(
                {
                    "key": name,
                    "label": GROUP_LABELS.get(name) or name.replace("_", " ").capitalize(),
                    "total_paise": paise(child.get("total")),
                    "lines": lines,
                }
            )
    return {"available_paise": total, "groups": groups}


ROW_PARSERS = {
    "holdings": parse_holding,
    "positions": parse_position,
    "mtf": parse_position,
    "orders": parse_order,
    "trades": parse_trade,
}


def parse_part(name: str, payload: Any) -> Any:
    """The part's figures from the decoded answer; Unreadable if it is not shaped as expected."""
    if not isinstance(payload, Mapping) or payload.get("status") != "success":
        raise Unreadable("status")
    data = payload.get("data")
    if name == "funds":
        if not isinstance(data, Mapping):
            raise Unreadable("data")
        return parse_funds(data)
    if data is None:
        return []
    if not isinstance(data, list):
        raise Unreadable("data")
    parser = ROW_PARSERS[name]
    out, skipped = [], 0
    for item in data:
        try:
            out.append(parser(item) if isinstance(item, Mapping) else _skip())
        except Unreadable:
            skipped += 1
    if skipped:
        log.warning("%d rows of the %s answer were not readable and are left out", skipped, name)
    return out


def _skip():
    raise Unreadable("row")


# ---- time ------------------------------------------------------------------------------------
def minute_of_day(now_ms: int) -> int:
    return (now_ms + IST_OFFSET_MS) % MS_PER_DAY // 60_000


def in_funds_window(now_ms: int) -> bool:
    """True from 00:00 up to, not including, 05:30 IST."""
    return minute_of_day(now_ms) < FUNDS_OPENS_MIN


def at_clock(ms: int) -> str:
    seconds = (ms + IST_OFFSET_MS) % MS_PER_DAY // 1000
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def at_12h(ms: int) -> str:
    minutes = minute_of_day(ms)
    hour, minute = divmod(minutes, 60)
    return f"{(hour + 11) % 12 + 1}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


# ---- the reader ------------------------------------------------------------------------------
@dataclass
class PartMemory:
    data: Any = None
    read_at: int | None = None
    tried_at: int = 0
    state: str = "unavailable"
    note: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def view(self) -> dict[str, Any]:
        return {"state": self.state, "read_at": self.read_at, "note": self.note, "data": self.data}


def _signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


class UpstoxReader:
    def __init__(
        self,
        token_file: Path,
        base_url: str,
        *,
        clock: Callable[[], int],
        budget: Budget | None = None,
        client: httpx.AsyncClient | None = None,
        decorate: Callable[[list[dict[str, Any]], int], None] | None = None,
    ):
        self.token_file, self.base_url = Path(token_file), base_url.rstrip("/")
        self.clock, self.budget = clock, budget or Budget()
        self._client, self._own_client = client, client is None
        #: Fills exchange, symbol and the auction fields of rows (set by the service).
        self.decorate = decorate
        self._memory = {name: PartMemory() for name in UPSTOX_PARTS}
        self._refused_at: tuple[int, int] | None = None
        self._refused = False
        self._told: set[str] = set()

    # ---- the client ------------------------------------------------------------------------
    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False)
        return self._client

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None and self._own_client:
            await client.aclose()

    def held_keys(self) -> list[str]:
        """The instrument keys of the holdings last read (for the feed claims)."""
        rows = self._memory["holdings"].data or []
        return [r["instrument_key"] for r in rows]

    # ---- the book --------------------------------------------------------------------------
    async def book(
        self,
        parts: list[str] | tuple[str, ...],
        max_age_s: float | None = None,
        principal: str = OWNER,
    ) -> dict[str, dict[str, Any]]:
        """The parts asked for. ``principal`` is whose read it is: anyone but the owner spends
        from the budget only above the owner's reserve (budget.RESERVE), and a read the reserve
        refuses answers the figures already held and changes nothing in the memory the owner
        reads from."""
        age = MAX_AGE_S if max_age_s is None else max_age_s
        names = [n for n in UPSTOX_PARTS if n in parts]
        views = await asyncio.gather(*(self._part(name, age, principal) for name in names))
        return dict(zip(names, views, strict=True))

    async def _part(self, name: str, age_s: float, principal: str = OWNER) -> dict[str, Any]:
        memory = self._memory[name]
        if self._fresh(memory, self.clock(), age_s):
            return memory.view()
        held = False
        async with memory.lock:  # single flight: the second caller finds the first one's answer
            if not self._fresh(memory, self.clock(), age_s):
                held = await self._read(name, memory, principal) == "held"
        return self._held_back(memory) if held else memory.view()

    @staticmethod
    def _held_back(memory: PartMemory) -> dict[str, Any]:
        """The figures held, said to be old, for a read the owner's reserve refused: the shared
        memory is left as it was, so the owner's next read is not told it failed."""
        if memory.data is not None and memory.read_at is not None:
            note = f"{WORDS['held_back']} Figures from {at_clock(memory.read_at)}. They refresh in a minute."
            return {"state": "stale", "read_at": memory.read_at, "note": note, "data": memory.data}
        return {
            "state": "unavailable",
            "read_at": None,
            "note": f"{WORDS['held_back']} They refresh in a minute.",
            "data": None,
        }

    @staticmethod
    def _fresh(memory: PartMemory, now: int, age_s: float) -> bool:
        if memory.state == "ok" and memory.read_at is not None:
            return now - memory.read_at < age_s * 1000
        return memory.tried_at != 0 and now - memory.tried_at <= FAILED_REUSE_MS

    # ---- one read --------------------------------------------------------------------------
    def _say_once(self, key: str, text: str, *args: Any) -> None:
        if key not in self._told:
            self._told.add(key)
            log.warning(text, *args)

    def _fail(self, memory: PartMemory, now: int, words: str, *, figures: str = "") -> None:
        """Keep the last good figures and say why they are not fresh."""
        memory.tried_at = now
        if memory.data is not None and memory.read_at is not None:
            memory.state = "stale"
            memory.note = f"{words} Figures from {at_clock(memory.read_at)}.{figures}".strip()
        else:
            memory.state, memory.note = "unavailable", f"{words}{figures}".strip()

    def _token(self) -> str | None:
        try:
            token = self.token_file.read_text(encoding="utf-8-sig").strip()
        except (OSError, UnicodeDecodeError):
            return None
        return token if len(token) >= TOKEN_MIN and not any(c.isspace() for c in token) else None

    async def _read(self, name: str, memory: PartMemory, principal: str = OWNER) -> str | None:
        """One read into ``memory``; ``"held"`` when the owner's reserve refused it."""
        now = self.clock()
        if self._refused:
            if _signature(self.token_file) == self._refused_at:
                return self._fail(memory, now, WORDS["token_refused"])
            self._refused = False  # the file changed: try again
            self._told.discard("401")
        if name == "funds" and in_funds_window(now):
            return self._window(memory, now)
        left = self.budget.paused_for(name)
        if left:
            return self._fail(memory, now, WORDS["slow"], figures=" They refresh in a minute.")
        token = self._token()
        if token is None:
            self._say_once("token", "The Upstox token file is missing or unreadable.")
            return self._fail(memory, now, WORDS["token_missing"])
        if not self.budget.take(name, principal):
            if principal != OWNER and self.budget.can_take(name):
                return "held"  # the owner could still read: the reserve is what said no
            return self._fail(memory, now, WORDS["busy"], figures=" They refresh in a moment.")
        path, extra = PATHS[name]
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", **extra}
        try:
            answer = await self.client.get(self.base_url + path, headers=headers)
        except httpx.HTTPError as exc:
            log.warning("Upstox %s did not answer (%s)", name, type(exc).__name__)
            return self._fail(memory, self.clock(), WORDS["silent"])
        finally:
            del token, headers
        now = self.clock()
        status = answer.status_code
        if status == 401:
            self._refused, self._refused_at = True, _signature(self.token_file)
            self._say_once(
                "401", "Upstox refused the read-only token; reads pause until it changes."
            )
            return self._fail(memory, now, WORDS["token_refused"])
        if status == 423 and name == "funds":
            return self._window(memory, now)
        if status == 429 or self._rate_coded(answer):
            self.budget.pause(name, PAUSE_S)
            log.warning("Upstox asked for a pause on %s: %d seconds", name, int(PAUSE_S))
            return self._fail(memory, now, WORDS["slow"], figures=" They refresh in a minute.")
        if status >= 500:
            log.warning("Upstox %s answered with a server fault (%d)", name, status)
            return self._fail(memory, now, WORDS["silent"])
        if status != 200:
            log.warning("Upstox %s refused the request (%d)", name, status)
            return self._fail(memory, now, WORDS["refused"])
        try:
            payload = json.loads(answer.text, parse_float=Decimal)
            data = parse_part(name, payload)
        except (ValueError, Unreadable) as exc:
            log.error("the %s answer could not be read: %s", name, type(exc).__name__)
            return self._fail(memory, now, WORDS["unreadable"])
        if self.decorate is not None and name in ("holdings", "positions", "mtf"):
            try:
                await asyncio.get_running_loop().run_in_executor(None, self.decorate, data, now)
            except Exception:  # names and auction times are a convenience; the figures stand
                log.exception("the %s rows could not be matched to the master contract", name)
        memory.data, memory.read_at, memory.tried_at = data, now, now
        memory.state, memory.note = "ok", None

    @staticmethod
    def _rate_coded(answer: httpx.Response) -> bool:
        try:
            errors = answer.json().get("errors") or []
        except (ValueError, AttributeError):
            return False
        return any(isinstance(e, dict) and e.get("errorCode") == RATE_CODE for e in errors)

    def _window(self, memory: PartMemory, now: int) -> None:
        memory.tried_at = now
        if memory.data is not None and memory.read_at is not None:
            memory.state = "stale"
            memory.note = f"{WORDS['window']} Showing the figures read at {at_12h(memory.read_at)}."
        else:
            memory.state, memory.note = "unavailable", WORDS["window_none"]


def matcher(resolver, prices) -> Callable[[list[dict[str, Any]], int], None]:
    """Fill each row's OpenAlgo symbol and exchange from the master contract and its closing
    auction fields from the price book. Blocking (a database read): run it on a thread."""

    def decorate(rows: list[dict[str, Any]], now: int) -> None:
        found = resolver.by_keys(r["instrument_key"] for r in rows) if resolver else {}
        for row in rows:
            listing = found.get(row["instrument_key"])
            row["exchange"] = listing.exchange if listing else None
            row["symbol"] = listing.symbol if listing else None
            auction = (
                prices.auction_for(row["symbol"], row["exchange"], now)
                if prices is not None and hasattr(prices, "auction_for")
                else (False, None, None, None)
            )
            row["cas"], row["auction_from"], row["auction_until"], row["auction_close"] = auction

    return decorate
