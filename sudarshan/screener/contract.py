"""The contract between the screener service, OpenAlgo's bridge and the /screener page (M9.1, 6.3).

One table (``OPS``) names every op of the sealed local API, the bridge route that carries it, its
class (read, write, preview) and how its query string is read. The bridge builds its routes from
the table, the API accepts only its names, and the page's ``types.ts`` mirrors it: a new op is a
row here and a line in each of those, nothing else. ``WORDS`` are the sentences said word for
word on every surface; anything with a number or a name in it is built where that is known.

Ports and sizes come from the cross-design rules: the service listens on 127.0.0.1:5584 only
(X2, S10), a sealed request to its API is at most 64 KiB and to OpenAlgo's internal routes at
most 16 KiB (X9).
"""

from __future__ import annotations

from typing import NamedTuple

LOCAL_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 5584
API_PREFIX = "/sudarshan/screener/api"
INTERNAL_PREFIX = "/sudarshan/screener/internal"
#: The most a sealed request may be: the service's API (X9) and OpenAlgo's internal routes.
API_MAX_BYTES = 64 * 1024
INTERNAL_MAX_BYTES = 16 * 1024
#: What the bridge lets through a minute, per class of op (6.3: preview 6 a minute).
LIMITS_PER_MIN = {"read": 120, "write": 60, "preview": 6}
#: How long the bridge waits for the service, per class (6.3).
TIMEOUTS_S = {"read": 3, "write": 6, "preview": 6}
#: The Socket.IO events the page listens for (4.5, UI4, UI7).
EVENT_RUN = "sudarshan_screener_run"
EVENT_LISTS = "sudarshan_screener_lists"
#: The engine line as it changes (design 4.5): ``{engine, screens, removed}`` (types.ts StatePush).
EVENT_STATE = "sudarshan_screener_state"
#: The dashboard's layout changed on another screen (M9.16): ``{version}``; a page whose version is
#: older reads the dashboard again.
EVENT_DASHBOARD = "sudarshan_screener_dashboard"
#: Every event the bridge forwards to the owner's room. A new event is added here and nowhere else:
#: the bridge derives its set from this tuple, and a test holds it to it.
PUSH_EVENTS = (EVENT_RUN, EVENT_LISTS, EVENT_STATE, EVENT_DASHBOARD)
OWNER_ROOM = "sudarshan_owner"

#: ``not_ready`` the op is not built yet; ``bad_request`` the arguments are wrong; ``not_found``
#: the thing is gone; ``version_conflict`` it changed on another screen; ``refused`` a rule says
#: no (the message says why, ``field`` names the line of the screen); ``unavailable`` the service
#: or its record could not be used just now. The bridge adds ``not_confirmed`` (a write was sent
#: and no answer came back), ``unauthorized``, ``forbidden`` and ``rate_limited``.
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
#: The HTTP status the bridge gives each code. The page reads the code, never the status.
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

WORDS = {
    "off": "The screener is switched off on the server.",
    "not_ready": "This part of the screener is not ready yet.",
    "not_answering": (
        "The screener is not answering. Your screens are kept; checking resumes when it is back."
    ),
    "not_confirmed": "Not confirmed: open the screen to see whether the change was saved.",
    "version_conflict": (
        "This screen was changed on another screen. Nothing was saved; open it again and repeat "
        "the change."
    ),
    "forbidden": "Only the account owner can use the screener.",
    "unauthorized": "Your session has ended. Log in again.",
    "rate_limited": "Too many requests. Wait a minute, then try again.",
    "preview_limited": "Previews run six times a minute. Wait a little, then try again.",
    "gone": "This screen is no longer there; it may have been deleted on another screen.",
    "too_big": "That is too large for the screener. Shorten the screen and try again.",
    "unreadable": "That request could not be read. Open the page again, then repeat it.",
}

#: How an op's query string is read: a whole number, text, a flag (1, true, yes).
QUERY_TYPES = ("int", "str", "flag")


class OpSpec(NamedTuple):
    """One op of the sealed local API and the bridge route that carries it.

    ``query`` lists the GET parameters the bridge reads into ``args`` and how (``QUERY_TYPES``);
    a POST or PATCH takes its args from the JSON body, and ``<id>`` in the route is always
    ``args["id"]``, whatever the body says.
    """

    name: str
    method: str
    route: str
    cls: str
    query: tuple[tuple[str, str], ...] = ()


OPS = (
    OpSpec("state", "GET", "/state", "read"),
    OpSpec("create", "POST", "/screens", "write"),
    OpSpec("update", "PATCH", "/screens/<id>", "write"),
    OpSpec("delete", "DELETE", "/screens/<id>", "write", query=(("remove_added", "flag"),)),
    OpSpec("preview", "POST", "/preview", "preview"),
    OpSpec("results", "GET", "/screens/<id>/results", "read"),
    OpSpec(
        "runs", "GET", "/screens/<id>/runs", "read", query=(("before", "int"), ("limit", "int"))
    ),
    OpSpec("runs_since", "GET", "/runs/since", "read", query=(("after", "int"), ("limit", "int"))),
    OpSpec("run", "GET", "/runs/<id>", "read"),
    OpSpec("membership", "GET", "/membership", "read", query=(("list", "int"), ("screen", "int"))),
    OpSpec("membership_update", "PATCH", "/membership/<id>", "write"),
    OpSpec("presets", "GET", "/presets", "read"),
    OpSpec("universes", "GET", "/universes", "read"),
    OpSpec("today", "GET", "/today", "read"),
    # The dashboard (M9.16): a widget is addressed by its screen and its kind, never by an id.
    OpSpec("dashboard", "GET", "/dashboard", "read"),
    OpSpec(
        "widget", "GET", "/dashboard/widget", "read", query=(("screen_id", "int"), ("kind", "str"))
    ),
    OpSpec("widget_add", "POST", "/dashboard/widgets", "write"),
    OpSpec("widget_update", "PATCH", "/dashboard/widget", "write"),
    OpSpec(
        "widget_remove",
        "DELETE",
        "/dashboard/widget",
        "write",
        query=(("screen_id", "int"), ("kind", "str"), ("version", "int")),
    ),
    OpSpec("widget_order", "POST", "/dashboard/order", "write"),
)
OP_NAMES = tuple(op.name for op in OPS)
#: The membership moves the owner may make (PATCH /membership/<id>, ``{"action": ..., "version"}``).
MEMBERSHIP_ACTIONS = ("pin", "unpin", "dismiss", "restore")
#: The screen moves (PATCH /screens/<id>): ``edit`` carries ``spec``; the others only ``version``.
SCREEN_ACTIONS = ("edit", "pause", "resume", "alerts_on", "alerts_off")

#: OpenAlgo's internal routes the service calls (sealed under the internal purpose, 6.3): the
#: op is named inside the seal.
INTERNAL_OPS = ("watchlist_read", "watchlist_apply", "push")
