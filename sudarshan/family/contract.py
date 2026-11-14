"""The family service's contract (P6 F0; M2.6, M2.7): its doors, routes, ops, shapes and codes.

The one page every part of the family view reads: the service itself (``sudarshan-family`` on
127.0.0.1:5585), OpenAlgo's owner bridge, the alerts' nudges and the phone. Its twin on the phone
is ``frontend/src/sudarshan/family/types.ts``, generated from this file by ``gentypes.py`` (run
``python -m sudarshan.family.gentypes``); a test fails when the two differ. Nothing here does I/O,
and no sentence a person reads is written here: those live in ``words.py``, by the keys in
``WORD_KEYS``.

One listener, four doors:

    viewer  /family/api/<route>   a family phone, through nginx: the session cookie on every
                                  route but the public ones, and the CSRF header (the CSRF
                                  cookie's value) on every request that is not a GET. Any answer
                                  sets the CSRF cookie when the request had none, so a page reads
                                  ``GET /me`` once before it signs in or joins.
            /family/ws            the phone's WebSocket: a GET with Upgrade, the cookie checked
                                  at the handshake; closed with 4401 when the session ends
    admin   POST /v1/admin/<op>   OpenAlgo's owner bridge: the body is one Fernet token sealed
                                  for <op> under ``sudarshan/family/admin/v1``, principal
                                  ``owner``; the op in the path must be the op in the seal
    nudge   POST /v1/nudge        the alerts: one token sealed for ``nudge`` under
                                  ``sudarshan/family/nudge/v1``, principal ``sudarshan-alerts``
    health  GET /health           counts and states only: no name, handle, endpoint or id

and one call out, the family service's only route into OpenAlgo (lane FB serves it, F7 calls it):

    POST /sudarshan/family/internal/watchlists   one token sealed for ``watchlists`` under
                          ``sudarshan/family/internal/v1``, principal ``sudarshan-family``, with
                          1 to 50 distinct ``list_ids`` (at most 4 KiB); the answer names each list
                          found with its ``created_at`` (OpenAlgo reuses a deleted list's id, so a
                          share is tied to the id and the ``created_at`` together) and the ids gone

The sealed doors and /health refuse a request that carries X-Forwarded-For, X-Forwarded-Proto or
X-Real-IP with 404: nginx sets them, and nothing it forwards may reach those doors.

Every answer is an envelope: ``{"status": "success", "data": ...}`` or ``{"status": "error",
"code": ..., "message": ..., "field": ...}`` with the message in the reader's words. Switched off,
every viewer route answers 503 ``off``, and every admin op ``{"enabled": false, "message": ...}``
(the ``Off`` record) in place of its own answer.

Units: instants are epoch milliseconds (``*_at``), a day is YYYYMMDD. Fields are written
``name:type`` or ``name?:type`` (optional), separated by commas. A type is a primitive
(``string``, ``int``, ``number``, ``bool``, or ``json`` for any JSON value), an enum or a record
of this file, with ``[]`` for a list and ``|null`` for a value that may be empty. A GET or DELETE
reads its fields from the path and query string; every other method from the JSON body. A
``<name>`` in a route is the request field of that name.

The data shapes (``Book``, ``Summary``, ``Portfolio``) carry the portfolio service's rows as
``json`` after the member's mask: the lanes that serve them (F5 to F7) narrow them here.
"""

from __future__ import annotations

from typing import NamedTuple

# ---- the service ------------------------------------------------------------------------------
SERVICE = "sudarshan-family"
#: The Unix account it runs as: not in the group ``sudarshan``, so it reads no owner secret.
ACCOUNT = "sudarshan-family"
PORT = 5585
SWITCH_ENV = "SUDARSHAN_FAMILY"
VIEWER_PREFIX = "/family/api"
WS_PATH = "/family/ws"
ADMIN_PREFIX = "/v1/admin/"
NUDGE_PATH = "/v1/nudge"
HEALTH_PATH = "/health"
#: Headers nginx sets: their presence on a sealed door or /health answers 404.
FORWARDED_HEADERS = ("X-Forwarded-For", "X-Forwarded-Proto", "X-Real-IP")
COOKIE = "__Host-sudarshan_family"
CSRF_COOKIE = "__Host-sudarshan_family_csrf"
CSRF_HEADER = "X-Family-CSRF"
#: The seal purposes of the two sealed doors and of the one call out (``sudarshan/vault/keys.py``
#: holds the same strings).
PURPOSES = {
    "admin": "sudarshan/family/admin/v1",
    "nudge": "sudarshan/family/nudge/v1",
    "internal": "sudarshan/family/internal/v1",
}
ADMIN_PRINCIPAL = "owner"
NUDGE_PRINCIPAL = "sudarshan-alerts"
#: The family service's one route into OpenAlgo (the shared watchlists), and who it says it is.
INTERNAL_PATH = "/sudarshan/family/internal/watchlists"
INTERNAL_PRINCIPAL = SERVICE
#: The WebSocket close code of a session that ended (signed out, expired, removed).
WS_CLOSE_SIGNED_OUT = 4401

# ---- limits -----------------------------------------------------------------------------------
LIMITS = {
    "max_members": 4,
    "max_devices": 3,
    "handle_min": 3,
    "handle_max": 20,
    "password_min": 12,
    "password_max": 256,
    "display_name_max": 40,
    "device_label_max": 40,
    "invite_hours": 2,
    "session_days": 30,
    "idle_days": 14,
    "audit_page": 200,
    "admin_bytes": 64 * 1024,
    "nudge_bytes": 4 * 1024,
    "viewer_bytes": 64 * 1024,
    "internal_bytes": 4 * 1024,
    "internal_lists": 50,
    "ws_symbols": 150,
}
#: Failures in a row (since the last good sign-in) that lock a sign-in, and for how long (s).
LOCKOUT = ((5, 15 * 60), (10, 60 * 60), (15, 24 * 60 * 60))
#: A failure past the last threshold locks again for the last period every this many failures.
LOCKOUT_REPEAT = 5

# ---- kinds ------------------------------------------------------------------------------------
ROLES = ("viewer", "child")
MEMBER_STATUSES = ("invited", "active", "suspended", "removed")
SET_STATUSES = ("active", "suspended")
LANGUAGES = ("en", "gu")
#: The four privacy categories (design 3.1; cross-design Y24), as ``privacy_fields.json`` lists them.
CATEGORIES = ("pnl", "qty", "funds", "orders")
#: What each role is shown when the owner has not chosen (true: shown).
PRIVACY_DEFAULTS = {
    "viewer": {"pnl": True, "qty": True, "funds": False, "orders": True},
    "child": {"pnl": False, "qty": False, "funds": False, "orders": False},
}
BOOK_PARTS = ("positions", "holdings", "orders", "trades", "funds")
READ_STATES = ("ok", "budget", "unreachable", "portfolio_off", "not_shared", "closed")
FEED_STATES = ("realtime", "reconnecting", "closed")
SCREENS = ("dashboard", "positions", "holdings", "orders", "trades", "watchlist", "portfolio")
SHARE_KINDS = ("watchlist", "book")
SHARE_LEVELS = ("view", "restricted")
CHANNELS = ("push", "telegram")
NUDGE_KINDS = ("orders", "holdings")
AUTHS = ("public", "session")
#: F8 (feat/family-t2): the family bot as the owner's page reads it (the admin ``state``).
BOT_STATES = ("polling", "token_missing", "token_refused")
WS_ACTIONS = ("authenticate", "subscribe", "unsubscribe", "screen")
WS_TYPES = ("auth", "market_data", "feed_state", "subscribe", "book", "error")
AUDIT_KINDS = (
    "login_ok",
    "login_failed",
    "lockout",
    "logout",
    "logout_all",
    "session_revoked",
    "session_expired",
    "invite_created",
    "invite_used",
    "invite_revoked",
    "member_created",
    "member_updated",
    "member_suspended",
    "member_resumed",
    "member_removed",
    "privacy_changed",
    "signin_reset",
    "share_set",
    "share_removed",
    "device_renamed",
    "password_changed",
    "totp_reset",
    "push_subscribed",
    "push_removed",
    "telegram_linked",
    "telegram_unlinked",
    "bot_command",
    "view",
    "digest_sent",
    "check_failed",
)

# ---- errors -----------------------------------------------------------------------------------
#: Every error code with its HTTP status. ``invalid`` and ``code_mismatch`` carry ``field``. The
#: last two are the owner bridge's own: the service did not answer or its seal could not be made
#: (``unavailable``), and a write sent with no answer, which may have happened (``not_confirmed``).
ERROR_STATUS = {
    "invalid": 400,
    "code_mismatch": 400,
    "signin_failed": 401,
    "signed_out": 401,
    "csrf": 403,
    "forbidden": 403,
    "not_shared": 403,
    "not_found": 404,
    "conflict": 409,
    "invite_gone": 410,
    "too_large": 413,
    "limit": 422,
    "locked": 429,
    "failed": 500,
    "not_ready": 501,
    "off": 503,
    "store_newer": 503,
    "busy": 503,
    "unavailable": 503,
    "not_confirmed": 504,
}
ERROR_CODES = tuple(ERROR_STATUS)
#: The sentences ``words.py`` holds, by key (each error code's own, and the rest a person reads).
WORD_KEYS = (
    *ERROR_CODES,
    "expired",
    "removed",
    "suspended",
    "handle_rule",
    "handle_taken",
    "password_rule",
    "password_current",
    "name_rule",
    "label_rule",
    "member_limit",
    "member_gone",
    "already_joined",
    "privacy_rule",
    "ws_signed_out",
    "password_changed",
    "signed_out_everywhere",
    "store_failed",
)

PRIMITIVES = ("string", "int", "number", "bool", "json")
ENUMS = {
    "Role": ROLES,
    "MemberStatus": MEMBER_STATUSES,
    "SetStatus": SET_STATUSES,
    "Language": LANGUAGES,
    "Category": CATEGORIES,
    "BookPart": BOOK_PARTS,
    "ReadState": READ_STATES,
    "FeedState": FEED_STATES,
    "Screen": SCREENS,
    "ShareKind": SHARE_KINDS,
    "ShareLevel": SHARE_LEVELS,
    "Channel": CHANNELS,
    "NudgeKind": NUDGE_KINDS,
    "Auth": AUTHS,
    "WsAction": WS_ACTIONS,
    "WsType": WS_TYPES,
    "AuditKind": AUDIT_KINDS,
    "BotState": BOT_STATES,  # F8 (feat/family-t2)
}

_READ = "read_at:int|null, state:ReadState, words:string|null, hidden:Category[]"
RECORDS = {
    "Limits": ", ".join(f"{k}:int" for k in LIMITS),
    "Privacy": ", ".join(f"{c}:bool" for c in CATEGORIES),
    "Rules": "handle_min:int, handle_max:int, password_min:int",
    "Done": "ok:bool, words:string|null",
    "Off": "enabled:bool, message:string",
    # ---- the member's own account (viewer) ----
    "JoinInfo": "display_name:string, otpauth_uri:string, totp_key:string, expires_at:int, "
    "rules:Rules",
    "Member": "id:int, handle:string, display_name:string, role:Role, language:Language",
    "Device": "id:string, label:string, created_at:int, last_seen_at:int, place:string|null, "
    "current:bool, notifications:string|null",
    "Me": "member:Member, privacy:Privacy, hidden:Category[], limited:bool, device:Device, "
    "expires_at:int",
    # ---- the books and what is shared (viewer; F5 to F7) ----
    "Quote": "symbol:string, exchange:string, name:string, ltp:number|null, change:number|null, "
    "change_percent:number|null, close:number|null, at:int|null",
    "Market": "feed:FeedState, since:int|null, indices:Quote[]",
    "PositionCount": "open:int, long:int, short:int",
    "Summary": f"{_READ}, positions:PositionCount|null, pnl:json|null, funds:json|null",
    "Book": f"part:BookPart, {_READ}, rows:json[]",
    "SharedTab": "id:int, name:string, kind:string, level:ShareLevel, expires_at:int|null",
    "Portfolio": f"{_READ}, tabs:SharedTab[], summary:json|null",
    "PortfolioTab": f"tab:SharedTab, {_READ}, view:json|null",
    "SharedList": "id:int, name:string, count:int",
    "ListRow": "symbol:string, exchange:string, name:string",
    "SharedListView": "id:int, name:string, rows:ListRow[], refreshed_at:int|null",
    # ---- notifications, Telegram and settings (viewer; F8, F10) ----
    "Quiet": "from:string, to:string, digest:bool, alerts:bool",
    "Notify": "digest_push:bool, digest_telegram:bool",
    "Settings": "language:Language, quiet:Quiet, notify:Notify, telegram_linked:bool, version:int",
    "PushKey": "key:string",
    "PushKeys": "p256dh:string, auth:string",
    "TestResult": "channel:Channel, sent:bool, at:int|null, words:string",
    "TelegramLink": "url:string, expires_at:int",
    # ---- the WebSocket's messages (F6): what the phone sends, and what the service sends ----
    "WsSymbol": "symbol:string, exchange:string",
    "WsClient": "action:WsAction, family?:bool, symbols?:WsSymbol[], mode?:int, name?:Screen",
    "Tick": "ltp:number, change:number|null, change_percent:number|null, close:number|null, "
    "ltt:int|null, timestamp:int|null",
    "WsMarketData": "type:WsType, symbol:string, exchange:string, mode:int, data:Tick",
    "WsFeedState": "type:WsType, state:FeedState, since:int",
    "WsBook": "type:WsType, part:BookPart, read_at:int",
    "WsStatus": "type:WsType, status:string, message?:string",
    # ---- the one call out: OpenAlgo's internal watchlist route (F7 calls it, lane FB serves it) ----
    "InternalRow": "symbol:string, exchange:string, instrument_key:string|null, "
    "name:string|null, tick_size:number|null, lot_size:int|null",
    "InternalList": "id:int, name:string, created_at:string|null, rows:InternalRow[]",
    "InternalWatchlists": "lists:InternalList[], gone:int[]",
    # ---- the owner's admin ops (sealed) ----
    "MemberCard": "id:int, handle:string|null, display_name:string, role:Role, "
    "status:MemberStatus, privacy:Privacy, language:Language, devices:int, "
    "last_seen_at:int|null, invite_expires_at:int|null, locked_until:int|null, "
    "telegram_linked:bool, created_at:int, version:int",
    "Invite": "member_id:int, code:string, path:string, expires_at:int",
    "AuditLine": "id:int, at:int, actor:string, member_id:int|null, kind:AuditKind, "
    "device:string|null, place:string|null, detail:json",
    "AuditPage": "lines:AuditLine[], older:int|null",
    "Share": "id:int, kind:ShareKind, ref:string, member_id:int|null, role:Role|null, "
    "level:ShareLevel, expires_at:int|null, created_at:int, version:int",
    # F8 (feat/family-t2): ``bot`` and ``check``, sent while the view is on (the owner's page reads
    # them as optional): the family bot's state, and whether the install check passed and when.
    "AdminState": "enabled:bool, message:string|null, store_version:int|null, members:int, "
    "sessions:int, sockets:int, bot?:BotReport|null, check?:InstallCheck|null",
    "BotReport": "state:BotState, last_message_at:int|null",
    "InstallCheck": "ok:bool, at:int|null",
    "NudgeTaken": "taken:bool",
}


class Field(NamedTuple):
    """One field of a request or a record."""

    name: str
    type: str
    optional: bool = False


class ViewerOp(NamedTuple):
    """One route of the viewer API: under ``VIEWER_PREFIX``; ``public`` needs no session."""

    name: str
    method: str
    route: str
    auth: str
    req: str
    res: str


class SealedOp(NamedTuple):
    """One op of a sealed door: the fields the seal carries besides op, nonce and principal."""

    name: str
    req: str
    res: str


VIEWER_OPS = (
    # sign-in and the member's own account (F4)
    ViewerOp("join_info", "GET", "/join/<code>", "public", "code:string", "JoinInfo"),
    ViewerOp("join", "POST", "/join", "public",
             "code:string, handle:string, password:string, totp:string", "Me"),
    ViewerOp("login", "POST", "/login", "public",
             "handle:string, password:string, totp:string", "Me"),
    ViewerOp("me", "GET", "/me", "session", "", "Me"),
    ViewerOp("logout", "POST", "/logout", "session", "", "Done"),
    ViewerOp("logout_all", "POST", "/logout_all", "session", "", "Done"),
    ViewerOp("password", "POST", "/password", "session",
             "current:string, password:string, totp:string", "Done"),
    ViewerOp("devices", "GET", "/devices", "session", "", "Device[]"),
    ViewerOp("device_rename", "PATCH", "/devices/<id>", "session", "id:string, label:string",
             "Done"),
    ViewerOp("device_signout", "DELETE", "/devices/<id>", "session", "id:string", "Done"),
    # the market, the books and what is shared (F5, F6, F7)
    ViewerOp("market", "GET", "/market", "session", "", "Market"),
    ViewerOp("summary", "GET", "/summary", "session", "", "Summary"),
    ViewerOp("book", "GET", "/books/<part>", "session", "part:BookPart", "Book"),
    ViewerOp("portfolio", "GET", "/portfolio", "session", "", "Portfolio"),
    ViewerOp("portfolio_tab", "GET", "/portfolio/tabs/<id>", "session", "id:int",
             "PortfolioTab"),
    ViewerOp("watchlists", "GET", "/watchlists", "session", "", "SharedList[]"),
    ViewerOp("watchlist", "GET", "/watchlists/<id>", "session", "id:int", "SharedListView"),
    # settings, notifications and Telegram (F8, F10)
    ViewerOp("settings", "GET", "/settings", "session", "", "Settings"),
    ViewerOp("settings_set", "PUT", "/settings", "session",
             "version:int, language?:Language, quiet?:Quiet, notify?:Notify", "Done"),
    ViewerOp("push_key", "GET", "/push/key", "session", "", "PushKey"),
    ViewerOp("push_subscribe", "POST", "/push/subscriptions", "session",
             "endpoint:string, keys:PushKeys, replaces?:string|null, request_id?:string", "Done"),
    ViewerOp("push_unsubscribe", "POST", "/push/subscriptions/remove", "session",
             "endpoint:string", "Done"),
    ViewerOp("test_send", "POST", "/notify/test", "session", "", "TestResult[]"),
    ViewerOp("telegram_link", "POST", "/telegram/link", "session", "", "TelegramLink"),
    ViewerOp("telegram_unlink", "POST", "/telegram/unlink", "session", "", "Done"),
)  # fmt: skip

ADMIN_OPS = (
    SealedOp("state", "", "AdminState"),
    SealedOp("member_list", "", "MemberCard[]"),
    SealedOp("member_create", "display_name:string, role:Role, privacy?:Privacy, "
             "language?:Language, request_id?:string", "MemberCard"),
    SealedOp("member_update", "id:int, version:int, display_name?:string, role?:Role, "
             "privacy?:Privacy, language?:Language", "MemberCard"),
    SealedOp("member_status", "id:int, version:int, status:SetStatus", "MemberCard"),
    SealedOp("member_remove", "id:int, version:int", "Done"),
    SealedOp("invite_create", "member_id:int, request_id?:string", "Invite"),
    SealedOp("invite_revoke", "member_id:int", "Done"),
    SealedOp("signin_reset", "member_id:int, version:int, request_id?:string", "Invite"),
    SealedOp("device_list", "member_id:int", "Device[]"),
    SealedOp("device_revoke", "member_id:int, id:string", "Done"),
    SealedOp("audit_list", "member_id?:int|null, kind?:AuditKind|null, before?:int|null, "
             "limit?:int", "AuditPage"),
    SealedOp("share_list", "", "Share[]"),
    SealedOp("share_set", "kind:ShareKind, ref:string, member_id?:int|null, role?:Role|null, "
             "level:ShareLevel, expires_at?:int|null, version?:int|null", "Share"),
    SealedOp("share_remove", "id:int, version:int", "Done"),
)  # fmt: skip
NUDGE_OPS = (SealedOp("nudge", "kind:NudgeKind, sandbox:bool, at:int", "NudgeTaken"),)
#: The one call out: ``list_ids`` are 1 to ``LIMITS["internal_lists"]`` distinct positive ids.
INTERNAL_OPS = (SealedOp("watchlists", "list_ids:int[]", "InternalWatchlists"),)

VIEWER_OP_NAMES = tuple(op.name for op in VIEWER_OPS)
ADMIN_OP_NAMES = tuple(op.name for op in ADMIN_OPS)
#: Every viewer route that changes something: a viewer session may use these only with the CSRF
#: header, and the G5 suite holds each one's method to that.
VIEWER_WRITES = tuple(
    (op.method, VIEWER_PREFIX + op.route) for op in VIEWER_OPS if op.method != "GET"
)


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


def viewer_op(method: str, path: str) -> tuple[ViewerOp, dict[str, str]] | None:
    """The viewer op a request names, with the values of its route's ``<name>`` parts; ``path``
    is below ``VIEWER_PREFIX``. None when no route matches."""
    parts = [p for p in path.split("/") if p]
    for op in VIEWER_OPS:
        if op.method != method:
            continue
        pattern = [p for p in op.route.split("/") if p]
        if len(pattern) != len(parts):
            continue
        values: dict[str, str] = {}
        for want, have in zip(pattern, parts, strict=True):
            if want.startswith("<") and want.endswith(">"):
                values[want[1:-1]] = have
            elif want != have:
                break
        else:
            return op, values
    return None
