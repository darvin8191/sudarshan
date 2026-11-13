"""The journal's contract (JF0, M14.1 and M15.10): units, kinds, op names and JSON shapes.

The one page every part of the journal reads. Its twin on the phone is
``frontend/src/sudarshan/journal/types.ts``, generated from this file by ``gentypes.py`` (run
``python -m sudarshan.journal.gentypes``); a test fails when the two differ. Nothing here does I/O.

Units, at every service boundary and in every store (cross-design X3, Y31):

    money        integer paise                          ``*_paise``
    quantity     integer units                          ``qty``
    price        integer price_u = rupees x 10^4        ``*_price_u``, ``price_u``, ``*_avg_u``
    ratio        Decimal string, fixed places           ``win_rate`` "60.00", ``beta`` "2.0000"
    instant      epoch milliseconds                     ``*_ms``
    day          integer YYYYMMDD (IST session key)     ``day``, ``open_day``, ``close_day``

Prices carry four decimals because NSE currency derivatives tick at 0.0025 rupees (Y31); every
sum of price x quantity is kept exact in 10^-4 rupee ("u") and becomes paise once, at the end,
half-up to the paisa (``core/money.py``).

Fields are written ``name:type`` or ``name?:type`` (optional), separated by commas. A type is a
primitive (``string``, ``int``, ``number``, ``bool``), an enum or a record of this file, with
``[]`` for a list and ``|null`` for a value that may be empty.
"""

from __future__ import annotations

from typing import NamedTuple

# ---- service ---------------------------------------------------------------------------------
SERVICE = "sudarshan-journal"
PORT = 5586
SWITCH_ENV = "SUDARSHAN_JOURNAL"
API_PREFIX = "/sudarshan/journal/api"
#: The seal purposes of the journal's local API (``sudarshan/vault/keys.py`` holds the keys).
PURPOSES = {
    "api": "sudarshan/journal/api/v1",
    "delivery": "sudarshan/journal/delivery/v1",
    "nudge": "sudarshan/journal/nudge/v1",
    "family": "sudarshan/journal/family/v1",
}
#: The only ops the delivery purpose opens (cross-design Y8).
DELIVERY_OPS = ("report_get", "report_file", "delivery")
#: The only op the family purpose opens (cross-design Y16): counts, never a paise field.
FAMILY_OPS = ("family_day",)
#: The only op the nudge purpose opens (cross-design Y17): "orders changed", nothing else.
NUDGE_OPS = ("nudge",)
#: Who may seal under each purpose: the seal's ``principal`` must be exactly this.
PRINCIPALS = {
    "api": "owner",
    "delivery": "sudarshan-alerts",
    "nudge": "sudarshan-alerts",
    "family": "sudarshan-family",
}
#: The largest sealed request each purpose takes (cross-design X9), checked before it is opened.
SEAL_LIMITS = {"api": 64 * 1024, "delivery": 16 * 1024, "nudge": 4 * 1024, "family": 4 * 1024}
#: The largest answer the API gives: a report's file, base64 inside the envelope (design 7.2).
ANSWER_MAX_BYTES = 8 * 1024 * 1024
#: What a nudge says changed (``sudarshan/alerts/nudge.py``): a CNC fill is ``holdings``.
NUDGE_KINDS = ("orders", "holdings")

# ---- units -----------------------------------------------------------------------------------
PAISE_PER_RUPEE = 100
PRICE_SCALE = 10_000
#: ``qty x price_u / VALUE_DIVISOR`` is paise.
VALUE_DIVISOR = PRICE_SCALE // PAISE_PER_RUPEE
#: Decimals of each ratio string.
RATIO_PLACES = {"pct": 2, "win_rate": 2, "profit_factor": 2, "payoff": 2, "r": 4, "beta": 4}
#: IST is UTC+05:30 all year.
IST_OFFSET_S = 19_800

# ---- limits ----------------------------------------------------------------------------------
LIMITS = {
    "strategy_name": 40,
    "tag_name": 40,
    "playbook_name": 40,
    "playbook_rules": 12,
    "notes": 4_000,
    "report_text": 3_500,
    "claims": 50,
    "beta_days": 60,
    "beta_min_days": 20,
}
#: A fill with no placement row after this long came from outside OpenAlgo (design 3.3).
OUTSIDE_ORDER_WAIT_S = 60

# ---- kinds -----------------------------------------------------------------------------------
MODES = ("live", "sandbox")
ACTIONS = ("BUY", "SELL")
SIDES = ("long", "short")
TRIP_STATUSES = ("open", "closed", "orphan")
LEG_ROLES = ("entry", "exit")
#: MIS and I are intraday; NRML, CNC and D are one carry class (Upstox maps D to CNC).
PRODUCT_CLASSES = ("intraday", "carry")
INTRADAY_PRODUCTS = ("MIS", "I")
CARRY_PRODUCTS = ("NRML", "CNC", "D")
FILL_SOURCES = ("stream_read", "close_reconcile", "history", "sandbox_book", "withdrawn")
SURFACES = (
    "api",
    "app",
    "telegram",
    "flow",
    "strategy_module",
    "python",
    "upstox_app",
    # An order rebuilt from Upstox's trade history (the 22:00 catch-up of a day no check marked):
    # where it was placed is not known, and it is never asked about again.
    "history",
    "unknown",
)
EXCURSION_SOURCES = ("recording", "candles", "none")
MOODS = ("planned", "calm", "rushed", "fomo", "revenge", "tired")
TAG_KINDS = ("mistake", "label")
TAG_COLOURS = ("brand", "gold", "info", "neutral", "loss")
REPORT_KINDS = ("brief", "eod", "weekly", "monthly", "fy")
REPORT_STATUSES = ("ready", "sent", "text_only", "not_sent")
CHARGE_SEGMENTS = ("options", "futures", "equity_delivery", "equity_intraday", "not_modelled")
CHARGE_PLANS = ("plus", "standard")
#: What the owner's reports are called on a screen (design 6.4).
REPORT_TITLES = {
    "brief": "Morning brief",
    "eod": "Day report",
    "weekly": "Week in review",
    "monthly": "Monthly statement",
    "fy": "Tax pack",
}
HOLD_BUCKETS = ("under_5m", "5_30m", "30m_2h", "over_2h", "1_5d", "over_5d")
HOLD_BUCKET_LABELS = {
    "under_5m": "Under 5 min",
    "5_30m": "5-30 min",
    "30m_2h": "30 min-2 h",
    "over_2h": "Over 2 h",
    "1_5d": "1-5 days",
    "over_5d": "Over 5 days",
}
#: Why a ratio has no number: shown as words, never as infinity or zero.
RATIO_STATES = ("ok", "no_trades", "no_losing_trades", "no_variation", "short", "no_capital")
DURATION_STATES = ("none", "recovered", "still_in_drawdown")

#: The owner's op names (design 7.2). Service-only ops are listed after the bridge's.
OP_NAMES = (
    "state",
    "trips",
    "trip_get",
    "trip_update",
    "tags_list",
    "tag_create",
    "tag_update",
    "playbooks_list",
    "playbook_create",
    "playbook_update",
    "analytics",
    "reports_list",
    "report_get",
    "report_file",
    "report_build",
    "settings_get",
    "settings_set",
    "reconcile_now",
    "delivery",
    "family_day",
    "nudge",
)
#: Classes of op and what the bridge lets through a minute (design 7.3).
LIMITS_PER_MIN = {"read": 120, "write": 60, "download": 20, "reconcile": 2}

PRIMITIVES = ("string", "int", "number", "bool")
ENUMS = {
    "Mode": MODES,
    "Action": ACTIONS,
    "Side": SIDES,
    "TripStatus": TRIP_STATUSES,
    "LegRole": LEG_ROLES,
    "ProductClass": PRODUCT_CLASSES,
    "FillSource": FILL_SOURCES,
    "Surface": SURFACES,
    "ExcursionSource": EXCURSION_SOURCES,
    "Mood": MOODS,
    "TagKind": TAG_KINDS,
    "TagColour": TAG_COLOURS,
    "ReportKind": REPORT_KINDS,
    "ReportStatus": REPORT_STATUSES,
    "ChargeSegment": CHARGE_SEGMENTS,
    "ChargePlan": CHARGE_PLANS,
    "HoldBucket": HOLD_BUCKETS,
    "RatioState": RATIO_STATES,
    "DurationState": DURATION_STATES,
    "Cls": tuple(LIMITS_PER_MIN),
    "NudgeKind": NUDGE_KINDS,
}

RECORDS = {
    "Fill": "trade_id:string, order_id:string, mode:Mode, day:int, exchange:string, symbol:string, "
    "instrument_key:string, product:string, action:Action, qty:int, price_u:int, "
    "exch_ts_ms:int, order_ts_ms:int|null, source:FillSource, surface:Surface|null",
    "Leg": "trade_id:string, role:LegRole, qty:int, price_u:int, exch_ts_ms:int, day:int",
    "Trip": "id:int, mode:Mode, exchange:string, symbol:string, instrument_key:string, "
    "product:string, product_class:ProductClass, side:Side, qty_max:int, entry_qty:int, "
    "exit_qty:int, entry_avg_u:int, exit_avg_u:int|null, opened_ms:int, closed_ms:int|null, "
    "open_day:int, close_day:int|null, status:TripStatus, gross_paise:int|null, "
    "charges_est_paise:int|null, charges_actual_paise:int|null, net_paise:int|null, "
    "hold_s:int|null, strategy:string, strategy_override:string|null, setup_id:int|null, "
    "own_expiry_day:bool, index_expiry_day:bool, risk_paise:int|null, mae_paise:int|null, "
    "mfe_paise:int|null, mae_ms:int|null, mfe_ms:int|null, excursion_source:ExcursionSource, "
    "excursion_note:string|null, version:int",
    "TripNote": "round_trip_id:int, notes:string, mistakes:int[], checklist:string, "
    "mood:Mood|null, version:int",
    "Tag": "id:int, kind:TagKind, name:string, colour:TagColour, archived:bool, version:int",
    "Playbook": "id:int, name:string, name_alt:string|null, description:string, rules:string[], "
    "default_risk_paise:int|null, archived:bool, version:int",
    "DayClose": "day:int, mode:Mode, realised_paise:int, unrealised_paise:int, "
    "charges_est_paise:int, trades_closed:int, wins:int, losses:int, flats:int, "
    "kill_switch:string|null, daily_lock:string|null, read_at:int, reconciled_at:int|null, "
    "reconcile_note:string|null",
    "Metrics": "trips:int, wins:int, losses:int, flats:int, net_paise:int, gross_paise:int, "
    "charges_paise:int, win_rate:string|null, win_rate_state:RatioState, "
    "profit_factor:string|null, profit_factor_state:RatioState, expectancy_paise:int|null, "
    "avg_win_paise:int|null, avg_loss_paise:int|null, largest_win_paise:int|null, "
    "largest_loss_paise:int|null, payoff:string|null, payoff_state:RatioState, "
    "r_known:int, r_total:string|null, r_avg:string|null, charges_impact_pct:string|null",
    "HoldBucketRow": "bucket:HoldBucket, trips:int, wins:int, net_paise:int",
    "PeriodRow": "key:string, trips:int, wins:int, net_paise:int",
    "Calendar": "days:PeriodRow[], weeks:PeriodRow[], months:PeriodRow[]",
    "BlockRow": "name:string, metrics:Metrics",
    "EquityPoint": "day:int, net_paise:int, equity_paise:int, peak_paise:int, drawdown_paise:int",
    "Drawdown": "max_drawdown_paise:int, max_drawdown_pct:string|null, peak_day:int|null, "
    "trough_day:int|null, recovered_day:int|null, duration_days:int|null, "
    "duration_state:DurationState",
    "Equity": "points:EquityPoint[], drawdown:Drawdown",
    "HeatCell": "weekday:int, hour:int, trips:int, net_paise:int",
    "ExpirySplit": "index_expiry:Metrics, not_index_expiry:Metrics, own_expiry:Metrics, "
    "not_own_expiry:Metrics",
    "Excursion": "source:ExcursionSource, mae_paise:int|null, mfe_paise:int|null, "
    "mae_ms:int|null, mfe_ms:int|null, capture_pct:string|null, note:string|null",
    "WeekVsIndex": "account_pct:string|null, account_state:RatioState, index_pct:string|null",
    "Beta": "beta:string|null, correlation:string|null, state:RatioState, "
    "correlation_state:RatioState, days:int, need:int, note:string|null",
    "ChargeParts": "brokerage_paise:int, stt_paise:int, exchange_paise:int, sebi_paise:int, "
    "stamp_paise:int, gst_paise:int, total_paise:int",
    "ChargeEstimate": "parts:ChargeParts, modelled:bool, plan:ChargePlan, orders:int",
    "Analytics": "metrics:Metrics, hold:HoldBucketRow[], calendar:Calendar, equity:Equity, "
    "heat:HeatCell[], by_strategy:BlockRow[], by_tag:BlockRow[], by_playbook:BlockRow[], "
    "expiry:ExpirySplit, beta:Beta|null",
    # ``period_key``: 20261109 for a day, 2026W45 a week, 202610 a month, FY2627 a year;
    # ``reason``: why the last delivery did not send it whole, in the owner's words.
    "Report": "id:int, kind:ReportKind, period_key:string, built_ms:int, status:ReportStatus, "
    "text:string, file_name:string|null, file_mime:string|null, file_sha256:string|null, "
    "reconciled_at:int|null, version:int, reason:string|null",
    # The stored file of a report, base64 in the envelope; all four file fields are null until
    # the renderer has made one (the bridge then says the report has no file to download).
    "ReportFile": "file_name:string|null, file_mime:string|null, file_sha256:string|null, "
    "file_b64:string|null",
    "ReportEvent": "id:string, src:string, kind:string, report:int, report_kind:ReportKind, "
    "sha256:string|null",
    # ``alerts_on``: false while nothing delivers reports (the alerts service, or its report
    # desk, switched off: cross-design Y38); ``times``: when each report is built, as the page
    # prints them (the day ones for today, or the next trading day).
    "Settings": "capital_base_paise:int|null, eod_on:bool, brief_on:bool, weekly_on:bool, "
    "monthly_on:bool, fy_on:bool, report_language:string, charges_plan:ChargePlan, version:int, "
    "alerts_on:bool, rate_card:RateCard, times:ReportTimes",
    "RateCard": "version:string, from_day:int",
    "ReportTimes": "brief:string|null, eod:string|null, check:string|null, weekly:string, "
    "monthly:string, fy:string",
    # The family service's per-member line (``core/words.family_day``), which it builds itself
    # from the counts below and the member's own privacy categories (cross-design Y16).
    "FamilyDay": "day:int, line:string|null",
    # The answer of ``family_day``: counts only, no paise field (Y16). Trades are the live trips
    # closed on that day; won and lost are those whose net (after est. charges) is above or
    # below zero, so a flat trade is in ``trades`` and in neither of the other two.
    "FamilyCounts": "day:int, trades:int, won:int, lost:int",
    "State": "enabled:bool, fills_today:int, open_trips:int, last_reconcile_ms:int|null, "
    "limits:Limits",
    "Limits": ", ".join(f"{k}:int" for k in LIMITS),
    "Done": "id:int|null, version:int|null, words:string|null",
    "Version": "version:int",
    # One leg of a trip on the trade sheet: its fill's time, side, price, source and order.
    "TripLeg": "trade_id:string, order_id:string, role:LegRole, action:Action, qty:int, "
    "price_u:int, exch_ts_ms:int, day:int, source:FillSource",
    # What OpenAlgo's records say about one order of a trip (M12.12: latency in ms, no broker's).
    "OrderRef": "order_id:string, strategy:string|null, surface:Surface, pricetype:string|null, "
    "placed_ms:int|null, rtt_ms:int|null, total_latency_ms:int|null",
}
#: ``trip_get``'s answer: the trip, its legs, the owner's note and its orders' references.
# A row of the trades list: the trip and its mistake tags (the ids of its note's ``mistakes``).
RECORDS["TripRow"] = RECORDS["Trip"] + ", mistakes:int[]"
# The trade sheet: the trip, its fills (each as executed: a flip's fill is in both trips, its
# ``legs`` say how much of it is this trip's), the owner's note (its ``checklist`` is JSON text,
# ``[{"rule": ..., "yes": ...}]``, or empty), the excursion, the charges estimate of its orders,
# the first entry order's round trip as OpenAlgo's latency record has it (``latency_ms``), and
# each order's label.
RECORDS["TripDetail"] = RECORDS["Trip"] + (
    ", legs:TripLeg[], fills:Fill[], note:TripNote|null, excursion:Excursion|null, "
    "charges:ChargeEstimate|null, latency_ms:int|null, orders:OrderRef[]"
)


class Field(NamedTuple):
    """One field of a request or a record."""

    name: str
    type: str
    optional: bool = False


class OpSpec(NamedTuple):
    """One op: the route, its class, its request and its answer."""

    name: str
    method: str
    route: str
    cls: str
    req: str
    res: str


def parse_fields(spec: str) -> tuple[Field, ...]:
    """``"a:int, b?:string[]"`` to fields; a malformed field raises ``ValueError``."""
    out = []
    for part in (p.strip() for p in spec.split(",")):
        if not part:
            continue
        name, sep, typ = part.partition(":")
        if not sep or not typ.strip():
            raise ValueError(f"field without a type: {part!r}")
        optional = name.endswith("?")
        out.append(Field(name.rstrip("?").strip(), typ.strip(), optional))
    return tuple(out)


def base_type(typ: str) -> str:
    """The type without ``|null`` and ``[]``."""
    t = typ.strip()
    if t.endswith("|null"):
        t = t[:-5]
    if t.endswith("[]"):
        t = t[:-2]
    return t


def known_type(typ: str) -> bool:
    """True when a type is a primitive, an enum or a record of this file."""
    t = base_type(typ)
    return t in PRIMITIVES or t in ENUMS or t in RECORDS


def record(name: str) -> tuple[Field, ...]:
    """The fields of a record."""
    return parse_fields(RECORDS[name])


# The analytics filter: ``period`` today, week, month (when nothing is sent), fy or all, or the
# days ``from`` and ``to`` in its place; ``expiry`` expiry (opened on an index expiry day or the
# instrument's own) or non_expiry. Calendar keys are 20261109, 2026W45 and 202611; the heat
# map's weekday 0 is Monday.
_FILTER = (
    "mode?:Mode, strategy?:string, tag?:int, playbook?:int, from?:int, to?:int, "
    "expiry?:string, period?:string"
)
OPS = (
    OpSpec("state", "GET", "/state", "read", "", "State"),
    OpSpec(
        "trips",
        "GET",
        "/trips",
        "read",
        "mode?:Mode, status?:TripStatus, before?:int, limit?:int, strategy?:string, "
        "tag?:int, playbook?:int, from?:int, to?:int",
        "TripRow[]",
    ),
    OpSpec("trip_get", "GET", "/trips/<id>", "read", "id:int", "TripDetail"),
    OpSpec(
        "trip_update",
        "PATCH",
        "/trips/<id>",
        "write",
        "id:int, version:int, strategy_override?:string|null, setup_id?:int|null, "
        "risk_paise?:int|null, notes?:string, mistakes?:int[], checklist?:string, "
        "mood?:Mood|null",
        "Done",
    ),
    OpSpec("tags_list", "GET", "/tags", "read", "", "Tag[]"),
    OpSpec("tag_create", "POST", "/tags", "write", "kind:TagKind, name:string, "
           "colour?:TagColour, request_id?:string", "Done"),
    OpSpec("tag_update", "PATCH", "/tags/<id>", "write",
           "id:int, version:int, name?:string, colour?:TagColour, archived?:bool", "Done"),
    OpSpec("playbooks_list", "GET", "/playbooks", "read", "", "Playbook[]"),
    OpSpec("playbook_create", "POST", "/playbooks", "write",
           "name:string, name_alt?:string|null, description?:string, rules?:string[], "
           "default_risk_paise?:int|null, request_id?:string", "Done"),
    OpSpec("playbook_update", "PATCH", "/playbooks/<id>", "write",
           "id:int, version:int, name?:string, name_alt?:string|null, description?:string, "
           "rules?:string[], default_risk_paise?:int|null, archived?:bool", "Done"),
    OpSpec("analytics", "GET", "/analytics", "read", _FILTER, "Analytics"),
    OpSpec("reports_list", "GET", "/reports", "read", "kind?:ReportKind, limit?:int", "Report[]"),
    OpSpec("report_get", "GET", "/reports/<id>", "read", "id:int", "Report"),
    OpSpec("report_file", "GET", "/reports/<id>/file", "download", "id:int", "ReportFile"),
    OpSpec("report_build", "POST", "/reports/build", "write",
           "kind:ReportKind, period?:string", "Done"),
    OpSpec("settings_get", "GET", "/settings", "read", "", "Settings"),
    OpSpec("settings_set", "PUT", "/settings", "write",
           "version:int, capital_base_paise?:int|null, eod_on?:bool, brief_on?:bool, "
           "weekly_on?:bool, monthly_on?:bool, fy_on?:bool, charges_plan?:ChargePlan, "
           "report_language?:string", "Done"),
    OpSpec("reconcile_now", "POST", "/reconcile", "reconcile", "", "Done"),
)  # fmt: skip
#: Ops that no route of the bridge carries: the alerts desk, the family service and the alerts'
#: nudge call them. A nudge's three fields sit in the seal itself, beside ``op`` (alerts/nudge.py).
SERVICE_OPS = (
    OpSpec("delivery", "POST", "", "write",
           "report_id:int, channel:string, status:ReportStatus, reason?:string|null", "Done"),
    OpSpec("family_day", "GET", "", "read", "day:int", "FamilyCounts"),
    OpSpec("nudge", "POST", "", "write", "kind:NudgeKind, sandbox:bool, at:int", "Done"),
)  # fmt: skip
