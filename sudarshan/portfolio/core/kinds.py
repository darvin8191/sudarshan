"""The portfolio's contract (M7.19, M7.20): units, kinds, limits, fixed sentences and op names.

The one page every part of the portfolio reads on the server. Its twin on the phone is
frontend/src/sudarshan/portfolio/types.ts, and tests/test_portfolio_contract.py holds the two
together (every number, list, label, sentence and op below), so a change here that the phone does
not follow fails the build. The JSON shape of each op is in README.md and in types.ts.

Units, at every service boundary and in every store (cross-design X3):

    money        integer paise                           ``*_paise``
    quantity     integer qty_u = units x 10^6            ``qty_u``
    price        integer price_u = rupees x 10^4         ``price_u``
    percentage   Decimal string, in percent, 4 decimals  ``*_pct``, "8.0000" or "-2.5000"
    instant      epoch milliseconds                      ``*_at``
    day          integer YYYYMMDD in IST                 ``day``, ``trade_date``, ``lot_date``

A day is the session key of sudarshan.alerts.market.sessions (20261108) whether or not the
exchange traded that day: a dividend can be credited on a Sunday and a deposit mature on a
holiday, so a date the owner types is a day in the same unit as a session.

The value of a holding line (one account's quantity of one asset) is
``round(qty_u x price_u / 10^8)`` paise, half away from zero; the lines are rounded one by one
and then added, so every sum on a screen is a sum of figures on that screen (core/money.py).
"""

from __future__ import annotations

from typing import NamedTuple

# ---- units ---------------------------------------------------------------------------------
PAISE_PER_RUPEE = 100
QTY_SCALE = 1_000_000
PRICE_SCALE = 10_000
#: ``qty_u x price_u / VALUE_DIVISOR`` is paise: 10^6 x 10^4 / 100.
VALUE_DIVISOR = QTY_SCALE * PRICE_SCALE // PAISE_PER_RUPEE
#: Decimals of a percentage string.
PCT_DECIMALS = 4
#: At most this many units of one holding, so qty_u stays below 2^53 as a browser number.
MAX_UNITS = 1_000_000_000
MAX_QTY_U = MAX_UNITS * QTY_SCALE
#: Largest price_u (rupees 10^9 a unit) and largest sum of paise a figure may reach (rupees
#: 10^13): both below 2^53, so the browser adds them as numbers and only multiplies with BigInt.
MAX_PRICE_U = 10**13
MAX_PAISE = 10**15

# ---- limits the forms and the service share ------------------------------------------------
MAX_TABS = 50
TAB_NAME_MAX = 40
NAME_MAX = 80
NOTE_MAX = 500
TAGS_MAX = 10
TAG_MAX = 30
#: A manual value or a mutual-fund NAV older than this is marked Old (per tab, 7 to 365 days).
MANUAL_OLD_DAYS = 30
MANUAL_OLD_DAYS_MIN = 7
MANUAL_OLD_DAYS_MAX = 365
#: A NAV more than this many business days behind the last trading day is marked Old.
NAV_OLD_BUSINESS_DAYS = 3
#: XIRR is shown when the valuation day is at least this many days after the first flow.
XIRR_MIN_DAYS = 365
#: A closing record older than this many trading days is frozen (the settle leaves it alone).
FROZEN_AFTER_TRADING_DAYS = 3
PREVIEW_KEEP_HOURS = 24
JOB_KEEP_DAYS = 400
#: Page sizes a screen can rely on: entries (the default and the most), fund search results, rows
#: an import preview lists, days of the Upstox tab's history.
ACTIVITIES_PAGE = 100
ACTIVITIES_PAGE_MAX = 500
MF_SEARCH_MAX = 20
IMPORT_PREVIEW_ROWS = 500
UPSTOX_HISTORY_DAYS = 400

# ---- the service and its bridge (cross-design X2, X9, P19) ---------------------------------
LOCAL_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 5583
API_PREFIX = "/sudarshan/portfolio/api"
#: One sealed request; the import's own limit is 1.25 MiB (the CSV text of up to 900,000 bytes).
API_MAX_BYTES = 64 * 1024
IMPORT_MAX_BYTES = 1_310_720
IMPORT_MAX_FILE_BYTES = 900_000
IMPORT_MAX_ROWS = 5_000
#: Requests a minute the bridge allows. The upstox ops count as reads; a dry run has its own.
LIMITS_PER_MIN = {"read": 120, "write": 60, "import": 10, "dry_run": 120}
#: Seconds the bridge waits for the service. Writes are never retried.
TIMEOUTS_S = {"read": 3, "write": 6, "import": 15, "upstox": 12}

# ---- kinds ---------------------------------------------------------------------------------
ACTIVITY_KINDS = (
    "BUY",
    "SELL",
    "DIVIDEND",
    "INTEREST",
    "FEE",
    "TRANSFER",
    "SPLIT",
    "BONUS",
    "RIGHTS",
)
ACTIVITY_KIND_LABELS = {
    "BUY": "Buy",
    "SELL": "Sell",
    "DIVIDEND": "Dividend",
    "INTEREST": "Interest",
    "FEE": "Fee",
    "TRANSFER": "Transfer",
    "SPLIT": "Split",
    "BONUS": "Bonus",
    "RIGHTS": "Rights",
}
ACTIVITY_STATUSES = ("posted", "draft")
ACTIVITY_SOURCES = ("manual", "import")

#: The parts of a charges breakdown; each is a ``<part>_paise`` key and together they add up to
#: the entry's charges.
CHARGE_PARTS = ("brokerage", "stt", "exchange", "sebi", "stamp", "gst", "dp", "other")

ASSET_KINDS = ("listed", "mf", "manual", "liability")
#: A holding row is never a liability: those are balances, kept outside every return figure.
HOLDING_KINDS = ("listed", "mf", "manual")
MCAPS = ("large", "mid", "small")

ACCOUNT_KINDS = ("demat", "bank", "mf_folio", "physical", "upstox", "other")
ACCOUNT_KIND_LABELS = {
    "demat": "Demat account",
    "bank": "Bank",
    "mf_folio": "Mutual fund folio",
    "physical": "Held physically",
    "upstox": "Upstox",
    "other": "Other",
}

TAB_KINDS = (
    "upstox",
    "stocks",
    "mf",
    "mixed",
    "gold",
    "cash",
    "real_estate",
    "esop",
    "crypto",
    "other",
)
TAB_KIND_LABELS = {
    "upstox": "Upstox",
    "stocks": "Stocks and ETFs",
    "mf": "Mutual funds",
    "mixed": "Mixed",
    "gold": "Gold and silver",
    "cash": "FD and cash",
    "real_estate": "Real estate",
    "esop": "ESOP and unlisted",
    "crypto": "Crypto",
    "other": "Other",
}
TAB_ICONS = ("briefcase", "chart", "landmark", "coins", "home", "gem", "wallet", "target")
#: Decoration only (the D tokens brand, gold, info and neutral); never the only sign of anything.
TAB_COLOURS = ("brand", "gold", "info", "neutral")

ASSET_CLASSES = (
    "equity",
    "mf_equity",
    "mf_debt",
    "mf_hybrid",
    "mf_other",
    "gold",
    "cash",
    "bonds",
    "real_estate",
    "esop",
    "crypto",
    "other",
)
ASSET_CLASS_LABELS = {
    "equity": "Equity",
    "mf_equity": "MF equity",
    "mf_debt": "MF debt",
    "mf_hybrid": "MF hybrid",
    "mf_other": "MF other",
    "gold": "Gold and silver",
    "cash": "FD and cash",
    "bonds": "Bonds",
    "real_estate": "Real estate",
    "esop": "ESOP and unlisted",
    "crypto": "Crypto",
    "other": "Other",
}

#: Where a price came from, and what it is: the last trade, the official close or a fund's NAV.
PRICE_SOURCES = ("feed", "quote", "amfi", "manual")
PRICE_BASES = ("last", "close", "nav", "manual")

IMPORT_FORMATS = ("generic", "zerodha")
IMPORT_STATUSES = ("preview", "committed", "discarded", "undone")
#: The ``action`` column of the generic CSV. TRANSFER (between two accounts of the tab) is an
#: addition to the design's list, so that an export read back gives the same ledger (vector I7).
IMPORT_ACTIONS = (
    "BUY",
    "SELL",
    "DIVIDEND",
    "INTEREST",
    "FEE",
    "SPLIT",
    "BONUS",
    "RIGHTS",
    "TRANSFER_IN",
    "TRANSFER_OUT",
    "TRANSFER",
)
IMPORT_ROW_STATUSES = ("new", "dup", "bad")

#: What a Today figure can be. ``ok``: a number; ``unknown``: the previous close is not known yet;
#: ``pending``: a fund's NAV for today is not published yet; ``none``: a value kept by hand.
TODAY_STATES = ("ok", "unknown", "pending", "none")
#: What the service tells the phone to do with a holding (``unknown`` is the phone's to find).
TODAY_MODES = ("ok", "pending", "none")
FRESHNESS_STATES = ("fresh", "old")
#: The return a tab shows: XIRR; the absolute return with a date (XIRR appears after a year); or
#: nothing (no purchase or no value).
XIRR_STATES = ("shown", "later", "none")
BENCHMARKS = ("nifty50", "nifty500")
BENCHMARK_LABELS = {"nifty50": "NIFTY 50", "nifty500": "NIFTY 500"}

JOB_NAMES = ("eod", "settle", "nav_fetch", "nav_settle", "benchmark", "resolve")
JOB_STATUSES = ("ok", "late", "failed", "skipped")

# ---- layout (M7.25) ------------------------------------------------------------------------
WIDGETS = (
    "summary",
    "today",
    "holdings",
    "movers",
    "allocation",
    "history",
    "performance",
    "income",
    "notes",
)
HOLDING_COLUMNS = ("name", "quantity", "avg_cost", "price", "today", "value", "gain", "weight")
SORTS = ("value", "today_pct", "gain_pct", "weight", "name")
GROUPS = ("none", "account", "class", "sector", "tag")
ALLOCATION_BY = ("class", "sector", "mcap", "account", "member", "tag")
HELD_FILTERS = ("over", "under")
LAYOUT_VERSION = 1

# ---- the Upstox tab ------------------------------------------------------------------------
UPSTOX_PARTS = ("holdings", "positions", "mtf", "funds")
#: ``ok`` a read that succeeded; ``stale`` the last figures kept, the note says why; ``unavailable``
#: nothing read yet, the note says why.
PART_STATES = ("ok", "stale", "unavailable")

# ---- answers -------------------------------------------------------------------------------
#: A switched-off portfolio is not an error: every op answers ``enabled: false`` (WORDS["off"]).
#: ``not_ready`` the op is not built yet; ``bad_request`` the arguments are wrong; ``not_found``
#: the thing is gone; ``version_conflict`` it changed on another screen; ``refused`` the ledger or
#: a rule says no (the message says why, ``entry_id`` names the entry in the way); ``unavailable``
#: the record or Upstox could not be used just now. The bridge adds ``not_confirmed`` (a write
#: was sent and no answer came back), ``unauthorized``, ``forbidden`` and ``rate_limited``.
ERROR_CODES = (
    "not_ready",
    "bad_request",
    "not_found",
    "version_conflict",
    "refused",
    "unavailable",
    "not_confirmed",
    "unauthorized",
    "forbidden",
    "rate_limited",
)
#: The HTTP status the bridge gives each code. The phone reads the code, never the status.
ERROR_STATUS = {
    "not_ready": 501,
    "bad_request": 400,
    "not_found": 404,
    "version_conflict": 409,
    "refused": 422,
    "unavailable": 503,
    "not_confirmed": 504,
    "unauthorized": 401,
    "forbidden": 403,
    "rate_limited": 429,
}

#: The sentences the bridge, the service and the phone say word for word. Anything that depends
#: on a number or a name is built where the number is known; these do not.
WORDS = {
    "off": "The portfolio is switched off on the server.",
    "not_ready": "This part of the portfolio is not ready yet.",
    "not_answering": (
        "The portfolio service is not answering. Your entries are safe; try again in a minute."
    ),
    "not_confirmed": "Not confirmed: open the tab to see whether it was saved.",
    "version_conflict": (
        "This tab was changed on another screen. Nothing was saved; open it again and repeat "
        "the change."
    ),
    "version_conflict_item": (
        "This item was changed on another screen. Nothing was saved; open it again and repeat "
        "the change."
    ),
    "store_failed": (
        "Your portfolio data could not be opened. Nothing was changed; check the service on "
        "the server."
    ),
    "store_newer": (
        "Your portfolio data was written by a newer release. Install that release again; "
        "nothing was changed."
    ),
    "future_date": "A date after today can be saved as a draft only.",
    "tab_limit": "A portfolio can have up to 50 tabs. Archive one you no longer use.",
    "import_too_big": (
        "This file is larger than 900 KB. Download it in shorter date ranges and import each part."
    ),
    "whole_shares": "Shares are whole numbers.",
    "forbidden": "Only the account owner can use the portfolio.",
    "unauthorized": "Your session has ended. Log in again.",
    "rate_limited": "Too many requests. Wait a minute, then try again.",
}

#: How an op's query string is read: a whole number, a day (YYYYMMDD), text, a comma-separated
#: list of whole numbers, a comma-separated list of text.
QUERY_TYPES = ("int", "day", "str", "ints", "strs")


class OpSpec(NamedTuple):
    """One op of the sealed local API and the bridge route that carries it.

    ``cls`` picks the bridge's limiter and timeout: read, write, import or upstox. ``query`` names
    the query-string arguments of a GET and how each is read; every other argument comes from the
    JSON body, and the ``<id>`` in the route is the argument ``id``. ``upload``: the bridge takes
    a multipart file and passes its text on. ``download``: the answer is a file.
    """

    name: str
    method: str
    route: str
    cls: str
    query: tuple[tuple[str, str], ...] = ()
    upload: bool = False
    download: bool = False


OPS = (
    OpSpec("state", "GET", "/state", "read"),
    OpSpec("summary", "GET", "/summary", "read"),
    OpSpec("tab_create", "POST", "/tabs", "write"),
    OpSpec(
        "tab_get",
        "GET",
        "/tabs/<id>",
        "read",
        query=(
            ("as_of", "day"),
            ("view", "int"),
            ("accounts", "ints"),
            ("classes", "strs"),
            ("tags", "strs"),
            ("held", "str"),
        ),
    ),
    OpSpec("tab_update", "PATCH", "/tabs/<id>", "write"),
    OpSpec("tab_delete", "DELETE", "/tabs/<id>", "write"),
    OpSpec(
        "activities_list",
        "GET",
        "/tabs/<id>/activities",
        "read",
        query=(("before", "str"), ("limit", "int"), ("asset", "int")),
    ),
    OpSpec("activity_create", "POST", "/tabs/<id>/activities", "write"),
    OpSpec("activity_update", "PATCH", "/activities/<id>", "write"),
    OpSpec("activity_delete", "DELETE", "/activities/<id>", "write"),
    OpSpec("asset_create", "POST", "/assets", "write"),
    OpSpec("asset_update", "PATCH", "/assets/<id>", "write"),
    OpSpec("price_point_set", "POST", "/assets/<id>/prices", "write"),
    OpSpec("mf_search", "GET", "/mf/search", "read", query=(("q", "str"), ("limit", "int"))),
    OpSpec("account_create", "POST", "/accounts", "write"),
    OpSpec("account_update", "PATCH", "/accounts/<id>", "write"),
    OpSpec("member_create", "POST", "/members", "write"),
    OpSpec("member_update", "PATCH", "/members/<id>", "write"),
    OpSpec("import_preview", "POST", "/tabs/<id>/imports", "import", upload=True),
    OpSpec("import_commit", "POST", "/imports/<id>/commit", "write"),
    OpSpec("import_discard", "DELETE", "/imports/<id>", "write"),
    OpSpec("export_csv", "GET", "/tabs/<id>/export.csv", "read", download=True),
    OpSpec("performance", "GET", "/tabs/<id>/performance", "read"),
    OpSpec("layout_get", "GET", "/tabs/<id>/layout", "read"),
    OpSpec("layout_set", "PUT", "/tabs/<id>/layout", "write"),
    OpSpec("view_list", "GET", "/tabs/<id>/views", "read"),
    OpSpec("view_create", "POST", "/tabs/<id>/views", "write"),
    OpSpec("view_update", "PATCH", "/views/<id>", "write"),
    OpSpec("view_delete", "DELETE", "/views/<id>", "write"),
    OpSpec(
        "upstox_book",
        "GET",
        "/upstox",
        "upstox",
        query=(("parts", "strs"), ("max_age", "int")),
    ),
    OpSpec("upstox_history", "GET", "/upstox/history", "upstox", query=(("limit", "int"),)),
)
OP_NAMES = tuple(op.name for op in OPS)
#: A write op whose body may carry ``dry_run``: the bridge counts those against the dry-run
#: limiter instead of the write limiter, and the service changes nothing.
DRY_RUN_OPS = ("activity_create", "activity_update")

# ---- what types.ts repeats -----------------------------------------------------------------
#: The numbers, lists, labels, strings and sentences the phone's types.ts carries a copy of, by
#: their name there (the test compares each one).
SHARED_NUMBERS = {
    "PAISE_PER_RUPEE": PAISE_PER_RUPEE,
    "QTY_SCALE": QTY_SCALE,
    "PRICE_SCALE": PRICE_SCALE,
    "VALUE_DIVISOR": VALUE_DIVISOR,
    "PCT_DECIMALS": PCT_DECIMALS,
    "MAX_UNITS": MAX_UNITS,
    "MAX_QTY_U": MAX_QTY_U,
    "MAX_PRICE_U": MAX_PRICE_U,
    "MAX_PAISE": MAX_PAISE,
    "MAX_TABS": MAX_TABS,
    "TAB_NAME_MAX": TAB_NAME_MAX,
    "NAME_MAX": NAME_MAX,
    "NOTE_MAX": NOTE_MAX,
    "TAGS_MAX": TAGS_MAX,
    "TAG_MAX": TAG_MAX,
    "MANUAL_OLD_DAYS": MANUAL_OLD_DAYS,
    "MANUAL_OLD_DAYS_MIN": MANUAL_OLD_DAYS_MIN,
    "MANUAL_OLD_DAYS_MAX": MANUAL_OLD_DAYS_MAX,
    "ACTIVITIES_PAGE": ACTIVITIES_PAGE,
    "ACTIVITIES_PAGE_MAX": ACTIVITIES_PAGE_MAX,
    "MF_SEARCH_MAX": MF_SEARCH_MAX,
    "IMPORT_PREVIEW_ROWS": IMPORT_PREVIEW_ROWS,
    "UPSTOX_HISTORY_DAYS": UPSTOX_HISTORY_DAYS,
    "NAV_OLD_BUSINESS_DAYS": NAV_OLD_BUSINESS_DAYS,
    "XIRR_MIN_DAYS": XIRR_MIN_DAYS,
    "IMPORT_MAX_FILE_BYTES": IMPORT_MAX_FILE_BYTES,
    "IMPORT_MAX_ROWS": IMPORT_MAX_ROWS,
    "LAYOUT_VERSION": LAYOUT_VERSION,
}
SHARED_LISTS = {
    "ACTIVITY_KINDS": ACTIVITY_KINDS,
    "ACTIVITY_STATUSES": ACTIVITY_STATUSES,
    "ACTIVITY_SOURCES": ACTIVITY_SOURCES,
    "CHARGE_PARTS": CHARGE_PARTS,
    "ASSET_KINDS": ASSET_KINDS,
    "HOLDING_KINDS": HOLDING_KINDS,
    "MCAPS": MCAPS,
    "ACCOUNT_KINDS": ACCOUNT_KINDS,
    "TAB_KINDS": TAB_KINDS,
    "TAB_ICONS": TAB_ICONS,
    "TAB_COLOURS": TAB_COLOURS,
    "ASSET_CLASSES": ASSET_CLASSES,
    "PRICE_SOURCES": PRICE_SOURCES,
    "PRICE_BASES": PRICE_BASES,
    "IMPORT_FORMATS": IMPORT_FORMATS,
    "IMPORT_STATUSES": IMPORT_STATUSES,
    "IMPORT_ACTIONS": IMPORT_ACTIONS,
    "IMPORT_ROW_STATUSES": IMPORT_ROW_STATUSES,
    "TODAY_STATES": TODAY_STATES,
    "TODAY_MODES": TODAY_MODES,
    "FRESHNESS_STATES": FRESHNESS_STATES,
    "XIRR_STATES": XIRR_STATES,
    "BENCHMARKS": BENCHMARKS,
    "JOB_NAMES": JOB_NAMES,
    "JOB_STATUSES": JOB_STATUSES,
    "WIDGETS": WIDGETS,
    "HOLDING_COLUMNS": HOLDING_COLUMNS,
    "SORTS": SORTS,
    "GROUPS": GROUPS,
    "ALLOCATION_BY": ALLOCATION_BY,
    "HELD_FILTERS": HELD_FILTERS,
    "UPSTOX_PARTS": UPSTOX_PARTS,
    "PART_STATES": PART_STATES,
    "ERROR_CODES": ERROR_CODES,
    "QUERY_TYPES": QUERY_TYPES,
}
SHARED_STRINGS = {"API_PREFIX": API_PREFIX}
SHARED_LABELS = {
    "ACTIVITY_KIND_LABELS": ACTIVITY_KIND_LABELS,
    "ACCOUNT_KIND_LABELS": ACCOUNT_KIND_LABELS,
    "TAB_KIND_LABELS": TAB_KIND_LABELS,
    "ASSET_CLASS_LABELS": ASSET_CLASS_LABELS,
    "BENCHMARK_LABELS": BENCHMARK_LABELS,
    "WORDS": WORDS,
}
