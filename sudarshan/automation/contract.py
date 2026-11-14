"""The contract between the automation's server (lane A) and its page (lane P), frozen by A1.

Three things live here and nowhere else:

- what a strategy may be (``clean`` reads the owner's form and answers a refusal in a sentence);
- which contract and product a strategy owns (``contract_key``);
- the routes, fields, verdicts and the live event the page is built against. ``gentypes.py``
  writes them as TypeScript, so the two sides cannot drift.

Pure: no I/O, no clock, no logging.
"""

from __future__ import annotations

import re
from typing import Any

from sudarshan.watchlists.tvimport import INDEX_ALIASES

from . import model as m

VERSION = 1
PUBLIC_PREFIX = "/sudarshan/tv"
API_PREFIX = "/sudarshan/automation/api"
PAGE = "/automation"
#: The Socket.IO event sent to the owner's admitted screens when an alert was handled or a
#: strategy changed. It carries ``EVENT_FIELDS`` and never a token or an API key.
EVENT = "sudarshan_automation"
TOKEN_PREFIX = "sdt_"
TOKEN = re.compile(r"sdt_[A-Za-z0-9_-]{43}")
MAX_BODY = 8 * 1024
ALERTS_PAGE = 200
TOKEN_PER_MINUTE, CALLER_PER_MINUTE = 60, 120
READS_PER_MINUTE, WRITES_PER_MINUTE = 120, 30
STRATEGIES_MOST = 50

#: method, path under API_PREFIX, what it takes, what it answers, write or read.
ROUTES = (
    ("GET", "/list", "", "ListAnswer", "read"),
    ("GET", "/one", "id", "OneAnswer", "read"),
    ("POST", "/create", "StrategyForm", "CreateAnswer", "write"),
    ("PUT", "/update", "UpdateRequest", "OneAnswer", "write"),
    ("POST", "/state", "StateRequest", "OneAnswer", "write"),
    ("POST", "/mode", "ModeRequest", "OneAnswer", "write"),
    ("POST", "/token", "TokenRequest", "CreateAnswer", "write"),
    ("GET", "/alerts", "strategy, before", "AlertsAnswer", "read"),
    ("POST", "/check", "CheckRequest", "CheckAnswer", "read"),
    ("GET", "/others", "", "OthersAnswer", "read"),
)

#: name -> TypeScript type. ``?`` after a name marks a field that may be left out.
FORM = {
    "name": "string",
    "chart?": "string | null",
    "kind": "Kind",
    "exchange?": "string",
    "underlying?": "string",
    "expiry_rule?": "string | null",
    "product": "Product",
    "direction?": "Direction",
    "lots?": "number | null",
    "quantity?": "number | null",
    "max_age_s?": "number",
    "max_alerts_day?": "number",
    "roll_on_expiry_day?": "boolean",
    "no_entry_before_stop_min?": "number",
}
STRATEGY = {
    "id": "number",
    "name": "string",
    "state": "StrategyState",
    "stopped_why": "string | null",
    "mode": "Mode",
    "chart": "string | null",
    "kind": "Kind",
    "exchange": "string",
    "underlying": "string",
    "expiry_rule": "string | null",
    "strike_offset": "string | null",
    "product": "Product",
    "direction": "Direction",
    "size_mode": "SizeMode",
    "lots": "number | null",
    "quantity": "number | null",
    "max_age_s": "number",
    "max_alerts_day": "number",
    "roll_on_expiry_day": "boolean",
    "no_entry_before_stop_min": "number",
    "token_tail": "string",
    "created_ms": "number",
    "updated_ms": "number",
    "version": "number",
    # the state row, and what the page shows from it
    "trades": "string",  # "NIFTY future, this month · Intraday · 1 lot"
    "last_target": "number",
    "held_exchange": "string | null",
    "held_symbol": "string | null",
    "held_side": "string | null",
    "held_since_ms": "number | null",
    "alerts_today": "number",
    "last_alert": "AlertLine | null",
    "sandbox_run": "SandboxRun",
}
ALERT = {
    "id": "number",
    "strategy_id": "number",
    "received_ms": "number",
    "fired_s": "number | null",
    "verdict": "Verdict",
    "sentence": "string",
    "exchange": "string | null",
    "symbol": "string | null",
    "action": "string | null",
    "quantity": "number | null",
    "target": "number | null",
    "mode": "Mode | null",
    "order_id": "string | null",
    "tv_delay_ms": "number | null",
    "answer_ms": "number | null",
}
SANDBOX_RUN = {"alerts": "number", "sessions": "number", "sent": "number", "refused": "number"}
EVENT_FIELDS = {
    "kind": "'alert' | 'strategy'",
    "strategy_id": "number",
    "name": "string",
    "verdict?": "Verdict",
    "sentence?": "string",
    "state?": "StrategyState",
    "mode?": "Mode",
    "at_ms": "number",
}
ANSWERS = {
    "ListAnswer": {
        "status": "'success'",
        "stage": "Stage",
        "stage_words": "string",
        "live_allowed": "boolean",
        "live_blocked_why": "string | null",
        "kill_switch_on": "boolean",
        "strategies": "Strategy[]",
    },
    "OneAnswer": {"status": "'success'", "strategy": "Strategy"},
    "CreateAnswer": {
        "status": "'success'",
        "strategy": "Strategy",
        "token": "string",  # shown once, here and nowhere after
        "link_path": "string",
        "messages": "AlertMessages",
    },
    "AlertsAnswer": {"status": "'success'", "alerts": "AlertLine[]", "more": "boolean"},
    "CheckAnswer": {
        "status": "'success'",
        "verdict": "Verdict",
        "sentence": "string",
        "order": "Record<string, string> | null",
    },
    "OthersAnswer": {"status": "'success'", "others": "OtherAutomation[]"},
    "Refusal": {"status": "'error'", "message": "string", "field?": "string"},
}
REQUESTS = {
    "UpdateRequest": {"id": "number", "version": "number", "changes": "Partial<StrategyForm>"},
    "StateRequest": {
        "id": "number",
        "version": "number",
        "state": "StrategyState",
        "phrase?": "string",
    },
    "ModeRequest": {"id": "number", "version": "number", "mode": "Mode", "phrase?": "string"},
    "TokenRequest": {"id": "number", "version": "number"},
    "CheckRequest": {"id": "number", "message": "string"},
}
ALERT_MESSAGES = {"strategy": "string", "indicator": "string", "condition": "string[]"}
OTHER = {"kind": "string", "name": "string", "on": "boolean", "detail": "string", "path": "string"}

#: What may change while a strategy holds a position: its name and its limits.
HELD_EDITABLE = frozenset({"name", "max_age_s", "max_alerts_day"})
EDITABLE = frozenset(key.rstrip("?") for key in FORM)

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}")
_CHART = re.compile(r"([A-Z]{2,12}):([A-Z0-9&_!.-]{1,40})")
_FOLLOW = re.compile(r"(.+?)([12])!")
_OPTION = re.compile(r"([A-Z0-9&_-]+?)(\d{6})([CP])(\d+(?:\.\d+)?)")
_CASH = ("NSE", "BSE")
_FO = {"NSE": "NFO", "BSE": "BFO", "NFO": "NFO", "BFO": "BFO"}
_COMMODITY = ("MCX", "NCDEX", "NCO")


class Refused(ValueError):
    """The owner's form cannot be saved. ``str()`` is the sentence he reads."""

    def __init__(self, sentence: str, field: str | None = None):
        super().__init__(sentence)
        self.field = field


def chart_parts(chart: str | None) -> tuple[str, str] | None:
    """``NSE:NIFTY1!`` as (exchange, ticker), or ``None`` when it is not such a name."""
    found = _CHART.fullmatch((chart or "").strip().upper())
    return (found.group(1), found.group(2)) if found else None


def contract_key(kind: str, exchange: str, underlying: str, chart: str | None) -> str:
    """What a strategy trades, as far as two strategies must not share it: every future of an
    underlying is one key (they meet at a roll), and so is every option of one."""
    if kind == m.FUTURE:
        return f"{exchange}:{underlying}:FUT"
    if kind == m.OPTION:
        return f"{exchange}:{underlying}:OPT"
    parts = chart_parts(chart)
    if parts is None:
        return f"{exchange}:{underlying}"
    ex, ticker = parts
    follow = _FOLLOW.fullmatch(ticker)
    if follow:
        return f"{_FO.get(ex, ex)}:{INDEX_ALIASES.get(follow.group(1), follow.group(1))}:FUT"
    if _OPTION.fullmatch(ticker):
        return f"{_FO.get(ex, ex)}:{ticker}"
    return f"{ex}:{ticker}"


def _whole(value: Any, low: int, high: int, what: str, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise Refused(f"{what} must be a whole number from {low} to {high}.", field)
    return value


def clean(form: dict[str, Any]) -> dict[str, Any]:
    """The owner's form as the columns of a strategy, or ``Refused`` with its sentence.

    A strategy is born Sandbox and Stopped: the mode and the switch have their own routes.
    """
    if not isinstance(form, dict):
        raise Refused("The strategy could not be read. Open the form again.")
    unknown = sorted(set(form) - EDITABLE)
    if unknown:
        raise Refused("The strategy has a setting this release does not know. Reload the page.")
    name = " ".join(str(form.get("name") or "").split())
    if not _NAME.fullmatch(name):
        raise Refused(
            "Give the strategy a name of 1 to 40 letters, digits, spaces, dots, dashes or "
            "underscores, starting with a letter or a digit.",
            "name",
        )
    kind = form.get("kind")
    if kind == m.OPTION:
        raise Refused("Option strategies come in a later release.", "kind")
    if kind not in (m.SAME, m.FUTURE):
        raise Refused("Choose what the strategy trades.", "kind")
    chart = (form.get("chart") or "").strip().upper() or None
    parts = chart_parts(chart)
    if chart and parts is None:
        raise Refused(
            "Write the chart as TradingView shows it, exchange first: NSE:NIFTY1! or NSE:RELIANCE.",
            "chart",
        )
    if parts and parts[0] in _COMMODITY:
        raise Refused("Commodity strategies are not supported yet.", "chart")
    product = form.get("product")
    if product not in m.PRODUCTS:
        raise Refused("Choose Intraday (MIS), Carry (NRML) or Delivery (CNC).", "product")
    direction = form.get("direction") or m.BOTH
    if direction not in m.DIRECTIONS:
        raise Refused("Choose long and short, long only or short only.", "direction")
    expiry_rule, lots, quantity = None, None, None
    if kind == m.FUTURE:
        exchange = str(form.get("exchange") or "NFO").upper()
        underlying = str(form.get("underlying") or "").strip().upper()
        if exchange in _COMMODITY:
            raise Refused("Commodity strategies are not supported yet.", "exchange")
        if exchange not in ("NFO", "BFO"):
            raise Refused("A future trades on NFO or BFO.", "exchange")
        if not re.fullmatch(r"[A-Z0-9&-]{1,40}", underlying):
            raise Refused(
                "Name the underlying whose future it trades, such as NIFTY.", "underlying"
            )
        expiry_rule = form.get("expiry_rule") or "month"
        if expiry_rule not in ("month", "next_month"):
            raise Refused("Choose this month's future or next month's.", "expiry_rule")
        derivative = True
    else:
        if parts is None:
            raise Refused(
                "A strategy that trades what its chart shows needs the chart's name, such as "
                "NSE:RELIANCE.",
                "chart",
            )
        ex, ticker = parts
        follow, option = _FOLLOW.fullmatch(ticker), _OPTION.fullmatch(ticker)
        if ex not in _CASH:
            raise Refused("The chart must be an NSE or BSE chart.", "chart")
        derivative = bool(follow or option)
        if follow:
            exchange, underlying = _FO[ex], INDEX_ALIASES.get(follow.group(1), follow.group(1))
            expiry_rule = "month" if follow.group(2) == "1" else "next_month"
        elif option:
            exchange, underlying = _FO[ex], option.group(1)
            if direction != m.LONG:
                raise Refused(
                    "A strategy on an option's own chart can only buy that option: choose long only.",
                    "direction",
                )
        else:
            if ticker in INDEX_ALIASES:
                raise Refused(
                    "An index cannot be traded itself. Choose its future as what the strategy trades.",
                    "kind",
                )
            exchange, underlying = ex, ticker
    if derivative:
        if product == "CNC":
            raise Refused("Futures and options take Intraday (MIS) or Carry (NRML).", "product")
        lots = _whole(form.get("lots", 1), 1, 100, "Lots", "lots")
    else:
        if product == "NRML":
            raise Refused("Shares take Intraday (MIS) or Delivery (CNC).", "product")
        if product == "CNC" and direction != m.LONG:
            raise Refused(
                "Delivery (CNC) cannot go short. Choose Intraday (MIS), or long only.", "direction"
            )
        quantity = _whole(form.get("quantity"), 1, 100000, "Shares", "quantity")
    carry = product in ("NRML", "CNC")
    return {
        "name": name,
        "chart": chart,
        "kind": kind,
        "exchange": exchange,
        "underlying": underlying,
        "expiry_rule": expiry_rule,
        "strike_offset": None,
        "product": product,
        "direction": direction,
        "size_mode": m.FIXED,
        "lots": lots,
        "quantity": quantity,
        "max_age_s": _whole(
            form.get("max_age_s", m.MAX_AGE_DEFAULT_S),
            m.MAX_AGE_MIN_S,
            m.MAX_AGE_MAX_S,
            "The seconds an alert may be late",
            "max_age_s",
        ),
        "max_alerts_day": _whole(
            form.get("max_alerts_day", m.MAX_ALERTS_DEFAULT),
            1,
            m.MAX_ALERTS_MOST,
            "The most alerts in a day",
            "max_alerts_day",
        ),
        "roll_on_expiry_day": bool(form.get("roll_on_expiry_day", carry)) and derivative,
        "no_entry_before_stop_min": _whole(
            form.get("no_entry_before_stop_min", m.NO_ENTRY_BEFORE_STOP_MIN),
            0,
            120,
            "The minutes before a contract stops",
            "no_entry_before_stop_min",
        ),
        "contract_key": contract_key(kind, exchange, underlying, chart),
    }


def form_of(strategy: m.Strategy) -> dict[str, Any]:
    """A saved strategy as the form that made it, for an edit."""
    return {key.rstrip("?"): getattr(strategy, key.rstrip("?")) for key in FORM}
