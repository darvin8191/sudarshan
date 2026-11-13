"""The contract of Watchlists v2 (M8.4 and the rest of lane W): every op, written once.

``OPS`` names each op of the owner's bridge with its method, path, class, request fields and
the record it answers with; ``RECORDS`` and ``ENUMS`` define the shapes. The bridge builds its
routes from the table, the handlers are found by name, and the phone's ``v2/types.ts`` is
generated from this file by ``gentypes.py`` (a test fails when the two differ), so a new op is a
row here and a handler, nothing else.

Fields are written ``name:type`` or ``name?:type`` (optional), separated by commas. A type is a
primitive (``string``, ``int``, ``number``, ``bool``), an enum or a record of this file, with
``[]`` for a list and ``|null`` for a value that may be empty. A GET or DELETE reads its fields
from the query string (primitives only); every other method reads them from the JSON body. A
``<name>`` in a path is the request field of that name.

Nothing here does I/O. Sentences a trader reads are in ``words/``, never in this file.
"""

from __future__ import annotations

from typing import NamedTuple

API_PREFIX = "/sudarshan/watchlists/api"
INTERNAL_PREFIX = "/sudarshan/watchlists/internal"
SWITCH_ENV = "SUDARSHAN_WATCHLISTS_V2"
DB_ENV = "SUDARSHAN_WATCHLISTS_DB"
#: Every open app of the owner joins it with ``EVENT_JOIN``; the push is ``{v, ref, refs, why}``:
#: ``refs`` is every ref whose members the change touched (none for a favourites write, a flag
#: rename or a note), ``ref`` the one of them or ``lists`` (kept for the phone until it reads
#: ``refs``).
ROOM = "sudarshan:watchlists:owner"
EVENT_PUSH = "sudarshan_watchlists"
EVENT_JOIN = "sudarshan_watchlists_join"
INTERNAL_MAX_BYTES = 16 * 1024

#: What the bridge lets through a minute, per class of op (design 2.4).
LIMITS_PER_MIN = {"read": 120, "write": 60, "search": 300, "ranked": 30, "preview": 6}
#: The numbers the rules and the words share (2.2, 2.6, 3.x); ``state`` sends them to the page.
LIMITS = {
    "lists": 50,
    "list_items": 250,
    "sections_per_list": 20,
    "section_name": 40,
    "rows_per_list": 20,
    "note": 500,
    "flag_name": 24,
    "favourite_slots": 7,
    "follow_cap": 250,
    "window": 120,
    "ranked_page": 50,
    "search_results": 50,
    "recents": 20,
    "tv_file_bytes": 64 * 1024,
}

#: ``invalid`` carries ``field``; ``limit`` is a cap that was reached; ``busy`` is the per-class
#: rate limit; ``signin`` is Upstox's daily sign-in; ``session`` is no session or a lapsed CSRF
#: token; ``record`` is a store this release cannot use.
ERROR_CODES = (
    "invalid",
    "conflict",
    "gone",
    "limit",
    "off",
    "record",
    "signin",
    "busy",
    "failed",
    "session",
    "owner_only",
)
ERROR_STATUS = {
    "invalid": 400,
    "conflict": 409,
    "gone": 404,
    "limit": 422,
    "off": 503,
    "record": 503,
    "signin": 412,
    "busy": 429,
    "failed": 500,
    "session": 401,
    "owner_only": 403,
}

PRIMITIVES = ("string", "int", "number", "bool")
#: Why a push was sent (2.5): the write ops' ``why`` and the two that name more than a list.
ENUMS = {
    "Why": ("sections", "arranged", "items", "flags", "rows", "lists", "favourites", "prefs"),
    "RefKind": ("list", "preset", "ranked", "flag"),
    "Sort": ("list", "change", "price", "name", "exchange"),
    "Basis": ("close", "open"),
    "FollowKind": ("atm_option", "month_future", "strike_pair"),
    "ExpiryRule": ("nearest", "next", "month", "next_month"),
    "StrikeRule": ("atm", "fixed"),
    "OptRight": ("CE", "PE"),
    "UnderlyingExchange": ("NSE_INDEX", "NSE", "BSE_INDEX", "BSE"),
    "Segment": ("all", "stocks", "futures", "options", "indices", "commodities"),
    "Group": ("stocks", "indices", "futures", "options", "commodities"),
    "RankSource": ("futures", "options"),
    "RankAsset": ("INDEX", "STOCK", "COMMODITY"),
    "RankCategory": (
        "TOP_TRADED",
        "MOST_ACTIVE",
        "OI_GAINERS",
        "OI_LOSERS",
        "PRICE_GAINERS",
        "PRICE_LOSERS",
        "PREMIUM",
        "DISCOUNT",
        "IV_GAINERS",
        "IV_LOSERS",
        "UNDER_5000",
        "UNDER_10000",
    ),
    "PriceSource": ("tick", "snapshot", "feed", "last"),
    "ResolveState": ("ok", "waiting", "closed", "missing"),
    "RankState": ("ok", "signin", "closed", "stale", "unavailable", "wait"),
    "Cls": tuple(LIMITS_PER_MIN),
}

#: Shapes the ops answer with and take in their requests.
RECORDS = {
    "Limits": ", ".join(f"{k}:int" for k in LIMITS),
    "Versions": "lists:string, favourites:int, flags:int",
    "Availability": "ok:bool, message:string|null",
    "FlagName": "mark:int, name:string",
    "FavSlot": "slot:int, ref:string",
    "Favourite": "slot:int, ref:string, kind:RefKind, name:string, count:int|null",
    "Entry": "ref:string, kind:RefKind, name:string, count:int|null, room:int|null, "
    "locked:bool, note:string|null",
    "Contract": "exchange:string, symbol:string, key:string, name:string, lot:int|null, "
    "tick:number|null, expiry:string|null, isin:string|null",
    "Pref": "sort:Sort, descending:bool, basis:Basis, held:bool",
    "Section": "id:int, name:string, mark:int, position:int, count:int",
    "Row": "item:int|null, contract:Contract, section_id:int|null, flag:int|null, "
    "has_note:bool, expired:bool, metric:string|null",
    "FollowRow": "id:int, kind:FollowKind, section_id:int|null, position:int, underlying:string, "
    "underlying_exchange:UnderlyingExchange, expiry_rule:ExpiryRule, strike_rule:StrikeRule|null, "
    "offset:int|null, strike:number|null, opt_right:OptRight|null",
    "View": "ref:string, kind:RefKind, name:string, version:string, locked:bool, "
    "as_of:string|null, lines:string[], total:int, missing:string[], sections:Section[], "
    "rows:Row[], follow:FollowRow[], pref:Pref",
    "Held": "ref:string, name:string",
    "SymbolInfo": "key:string, flag:int|null, flag_name:string|null, note:string|null, "
    "note_at:int|null, expired:bool, version:int|null, lists:Held[]",
    "Band": "low:number|null, high:number|null",
    "ResolveItem": "id:int, price:number|null, price_at:int|null, source:PriceSource|null, "
    "held:number|null",
    "Resolved": "id:int, state:ResolveState, ce:Contract|null, pe:Contract|null, "
    "future:Contract|null, strike:number|null, band:Band|null, valid_until:int|null, "
    "price:number|null, price_at:int|null, words:string",
    "Resolve": "rows:Resolved[]",
    "Expiry": "date:string, label:string, month:bool",
    "Strike": "strike:number, atm:bool, ce:Contract|null, pe:Contract|null",
    "Strikes": "underlying:string, exchange:string, expiries:Expiry[], expiry:string|null, "
    "atm:number|null, strikes:Strike[], words:string",
    "Hit": "group:Group, title:string, detail:string, contract:Contract, added:bool",
    "Search": "understood:string, hits:Hit[], underlying:string|null, suggestion:string|null",
    "Ranked": "ref:string, state:RankState, made_at:int|null, read_at:int|null, rows:Row[], "
    "left_out:int, words:string",
    "Cut": "token:string, reason:string",
    "TvSection": "name:string, count:int",
    "TvPreview": "hash:string, sections:int, symbols:int, matched:int, follow:int, "
    "left_out:Cut[], lists:TvSection[], words:string[]",
    "TvExport": "text:string, filename:string, left_out:Cut[], words:string[]",
    # A write's answer: ``lists_version`` is the lists version its bump produced (the ``v`` of the
    # push it sent), so the phone knows the push of its own write and skips it; absent when the
    # write bumped nothing (it changed no list's members, or nothing at all).
    "Done": "version:string|null, id:int|null, words:string|null, lists_version?:string",
    "CopyDone": "list:int, added:int, skipped:int, version:string|null, words:string|null, "
    "lists_version?:string",
    "TvDone": "lists:int[], added:int, version:string|null, words:string, lists_version?:string",
    "ArrangeItem": "exchange:string, symbol:string, section_id:int|null",
    "CopySource": "ref:string, section_id?:int, keys?:string[]",
    "CopyTarget": "list?:int, name?:string, section_id?:int|null",
    "Push": "v:string, ref:string, refs:string[], why:Why, favourites?:int, flags?:int",
    "State": "enabled:bool, versions?:Versions, limits?:Limits, flag_names?:FlagName[], "
    "favourites?:Favourite[], ranked?:Availability, alerts?:Availability",
    "Lists": "version:string, own:Entry[], presets:Entry[], ranked:Entry[], flags:Entry[], "
    "favourites:Favourite[]",
    "Members": "ref:string, version:string, members:Contract[], valid_until:int|null, "
    "left_out:Cut[]",
    "Version": "version:string",
}

_ROW = "list:int, section_id?:int|null, version:string"
_FOLLOW = (
    "kind:FollowKind, underlying:string, underlying_exchange:string, "
    "expiry_rule?:ExpiryRule, strike_rule?:StrikeRule, offset?:int, strike?:number, "
    "opt_right?:OptRight|null"
)


class Field(NamedTuple):
    """One field of a request or a record."""

    name: str
    type: str
    optional: bool = False


class OpSpec(NamedTuple):
    """One op of the owner's bridge: the route, its class, its request and its answer."""

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


OPS = (
    OpSpec("state", "GET", "/state", "read", "", "State"),
    OpSpec("lists", "GET", "/lists", "read", "", "Lists"),
    OpSpec("view", "GET", "/view", "read", "ref:string", "View"),
    OpSpec("symbol", "GET", "/symbol", "read", "k:string", "SymbolInfo"),
    OpSpec("resolve", "POST", "/resolve", "read", "rows:ResolveItem[], connected:bool", "Resolve"),
    OpSpec("strikes", "GET", "/strikes", "read",
           "u:string, x:string, exp?:string, n?:int, p?:number", "Strikes"),
    OpSpec("search", "GET", "/search", "search",
           "q:string, seg?:Segment, limit?:int, p?:number, list?:int", "Search"),
    OpSpec("ranked", "GET", "/ranked", "ranked",
           "src:RankSource, asset:RankAsset, cat:RankCategory, size?:int", "Ranked"),
    OpSpec("tv_preview", "POST", "/tv/preview", "preview", "text:string", "TvPreview"),
    OpSpec("tv_export", "POST", "/tv/export", "read", "list:int", "TvExport"),
    OpSpec("section_add", "POST", "/sections", "write",
           "list:int, name:string, mark?:int, version:string", "Done"),
    OpSpec("section_edit", "PATCH", "/sections/<id>", "write",
           "id:int, name?:string, mark?:int, version:string", "Done"),
    OpSpec("section_remove", "DELETE", "/sections/<id>", "write", "id:int, version:string", "Done"),
    OpSpec("arrange", "PUT", "/lists/<id>/arrange", "write",
           "id:int, version:string, sections:int[], items:ArrangeItem[]", "Done"),
    OpSpec("copy", "POST", "/copy", "write",
           "from:CopySource, to:CopyTarget, move?:bool, version?:string", "CopyDone"),
    OpSpec("favourites", "PUT", "/favourites", "write", "slots:FavSlot[], version:int", "Done"),
    OpSpec("flags", "PUT", "/flags", "write", "names:FlagName[], version:int", "Done"),
    OpSpec("mark", "PUT", "/symbols/<k>", "write",
           "k:string, flag?:int|null, note?:string|null, version?:int|null", "Done"),
    OpSpec("expired_clear", "POST", "/symbols/expired/clear", "write", "version:int", "Done"),
    OpSpec("pref", "PUT", "/prefs/<ref>", "write",
           "ref:string, sort?:Sort, descending?:bool, basis?:Basis, held?:bool", "Done"),
    OpSpec("row_add", "POST", "/rows", "write", f"{_ROW}, {_FOLLOW}", "Done"),
    OpSpec("row_edit", "PATCH", "/rows/<id>", "write",
           "id:int, version:string, section_id?:int|null, " + _FOLLOW.replace("kind:", "kind?:")
           .replace("underlying:", "underlying?:")
           .replace("underlying_exchange:", "underlying_exchange?:"), "Done"),
    OpSpec("row_remove", "DELETE", "/rows/<id>", "write", "id:int, version:string", "Done"),
    OpSpec("tv_import", "POST", "/tv/import", "write",
           "text:string, hash:string, list?:int, name?:string, version?:string", "TvDone"),
)  # fmt: skip
OP_NAMES = tuple(op.name for op in OPS)
#: Every route that changes something: the family layer's test asks that a viewer is refused each.
WRITE_ROUTES = tuple((op.method, API_PREFIX + op.route) for op in OPS if op.cls == "write")

#: The sealed members route (W23): ``POST INTERNAL_PREFIX/read?op=members|version``.
INTERNAL_OPS = (
    OpSpec("members", "POST", "/read", "read", "ref:string", "Members"),
    OpSpec("version", "POST", "/read", "read", "", "Version"),
)


def fields_of(spec: str) -> tuple[Field, ...]:
    """The fields of a request or record spec."""
    return parse_fields(spec)


def record(name: str) -> tuple[Field, ...]:
    """The fields of the record ``name``."""
    return parse_fields(RECORDS[name])


def base_type(typ: str) -> str:
    """``int[]|null`` to ``int``: the name a type is built from."""
    t = typ.strip()
    if t.endswith("|null"):
        t = t[:-5]
    if t.endswith("[]"):
        t = t[:-2]
    return t


def known_type(typ: str) -> bool:
    """True when a type's base is a primitive, an enum or a record of this file."""
    b = base_type(typ)
    return b in PRIMITIVES or b in ENUMS or b in RECORDS
