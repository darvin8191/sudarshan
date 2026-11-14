"""The TRINETRA view's contract (T0, M13.3): the letterbox, document v1, the ops, shapes and words.

The one page every part of the TRINETRA view reads: the ``sudarshan-signals`` service, OpenAlgo's
bridge (``blueprints/sudarshan_signals.py``, B1) and its push (B2), the alerts' signals desk
(``alerts/signals_desk.py``, AL2), the journal (J) and the phone. Its twin on the phone is
``frontend/src/sudarshan/signals/types.ts``, generated from this file by ``gentypes.py`` (run
``python -m sudarshan.signals.gentypes``); a test fails when the two differ. ``README.md`` says the
same in prose. Nothing here does I/O.

Frozen from T0: a later change to a shape, an op or a word key is additive only (a new optional
field, a new op, a new key) and is named in the report that makes it.

Units: instants are epoch milliseconds (``*_ms``; ``at`` and ``next_by`` in a document too), a
trading day is YYYYMMDD in IST, a price is a plain number (index points), a share of something is
a decimal string with fixed places (``hit_pct`` "61.54"). Every sentence a person reads is in
``WORDS`` (templates with ``{name}`` fields, filled by ``words.py``); TRINETRA's own text is data,
never markup, and never sent anywhere with a parse mode.

Fields are written ``name:type`` or ``name?:type`` (optional), separated by commas. A type is a
primitive (``string``, ``int``, ``number``, ``bool``), an enum or a record of this file, with
``[]`` for a list and ``|null`` for a value that may be empty.
"""

from __future__ import annotations

from typing import NamedTuple

# ---- the service ------------------------------------------------------------------------------
SERVICE = "sudarshan-signals"
VERSION = 1
PORT = 5588
SWITCH_ENV = "SUDARSHAN_SIGNALS"
#: The set-up's file (root:sudarshan 640): the number of the one account that may publish.
SETUP_FILE = "/etc/sudarshan/signals.env"
PUBLISHER_ENV = "SUDARSHAN_SIGNALS_PUBLISHER_UID"
#: The letterbox: owner sudarshan, mode 1733 (anyone may add a file, only its writer or sudarshan
#: may remove it, nobody but sudarshan may list it). Made by ``ops/signals/bootstrap.sh``.
LETTERBOX = "/var/spool/sudarshan-signals/in"
LETTERBOX_MODE = 0o1733
STATE_DIR = "/var/lib/sudarshan-signals"
DB_FILENAME = "signals.db"
#: The service's notices for the notice desk (``ops/events.py``, src ``signals``).
EVENTS_FILE = "events.jsonl"
HEALTH_PATH = "/health"
OPS_PREFIX = "/v1/"
#: The page (three registrations) and OpenAlgo's bridge for it.
PAGE = "/trinetra"
API_PREFIX = "/sudarshan/signals/api"
#: The service's push into OpenAlgo (B2): sealed, CSRF-exempt, 404 when nginx forwarded it.
INTERNAL_PREFIX = "/sudarshan/signals/internal"
PUSH_PATH = INTERNAL_PREFIX + "/push"
PUSH_OP = "push"
#: Every open app of the owner joins this room (``JOIN_EVENT``, answered ``{"ok": bool}``); a
#: push's items are emitted to it only, never broadcast (SIO1).
ROOM = "sudarshan:signals:owner"
JOIN_EVENT = "sudarshan_signals_join"
EVENT_UPDATE = "sudarshan_signals_update"
PUSH_EVENTS = (EVENT_UPDATE,)
#: The relay to the alerts' signals desk (AL2): one op on the alerts' one listener.
ALERTS_PORT = 5582
RELAY_OP = "signals_relay"
RELAY_PATH = OPS_PREFIX + RELAY_OP

#: The seal purposes (``sudarshan/vault/keys.py`` holds the same strings): who seals, for whom.
PURPOSES = {
    "api": "sudarshan/signals/api/v1",  # OpenAlgo's bridge -> this service
    "journal": "sudarshan/signals/journal/v1",  # sudarshan-journal -> this service, reads only
    "push": "sudarshan/signals/push/v1",  # this service -> OpenAlgo's push route
    "relay": "sudarshan/alerts/signals/v1",  # this service -> the alerts' signals desk
}
#: The seal's ``principal`` must be exactly this for its purpose.
PRINCIPALS = {
    "api": "owner",
    "journal": "sudarshan-journal",
    "push": SERVICE,
    "relay": SERVICE,
}
#: The largest sealed request of each purpose (cross-design X9), checked before it is opened.
SEAL_LIMITS = {"api": 16 * 1024, "journal": 4 * 1024, "push": 32 * 1024, "relay": 16 * 1024}
#: The purposes this service's own listener opens (the other two are sealed here, opened there).
SERVED_PURPOSES = ("api", "journal")

# ---- document version 1 (design 3.4) ----------------------------------------------------------
DOC_VERSION = 1
#: A file's final name; dot names are a writer's temporary files and are never read.
FILE_NAME = r"\d{13}-\d{1,9}\.json"
MODULE_PATTERN = r"[a-z][a-z0-9_]{0,39}"
STATE_PATTERN = r"[A-Z0-9_]{1,40}"
ALERT_ID_PATTERN = r"[A-Za-z0-9._-]{1,64}"
#: Lengths are Unicode code points after control characters are taken out (``README.md``).
DOC_LIMITS = {
    "bytes": 16 * 1024,
    "run": 40,
    "seq_max": 999_999_999,
    "signals": 16,
    "module": 40,
    "state": 40,
    "label": 40,
    "inputs": 8,
    "input_name": 60,
    "input_value": 40,
    "why": 600,
    "alerts": 4,
    "alert_id": 64,
    "title": 80,
    "text": 700,
    "price_max": 10_000_000,
}
#: ``next_by`` at most this long after ``at``.
NEXT_BY_MAX_MS = 6 * 3600 * 1000
#: ``at`` more than this ahead of the server's clock, or more than the next behind it, is
#: "clock differs" (measured against the time the file was written, on the server's clock).
CLOCK_AHEAD_MS = 120_000
CLOCK_BEHIND_MS = 24 * 3600 * 1000
#: The letterbox scan (design 3.5).
INBOX = {"scan_ms": 1000, "per_scan": 64, "waiting": 500}
#: Fresh while the server's clock is before the last good document's next_by plus this.
FRESH_GRACE_MS = 60_000
#: Past this long since the reading was published, no colour of bias is shown.
COLOUR_FOR_MS = 30 * 60_000
#: A stale reading in market hours for this long is told once on Telegram (T9).
STALE_NOTICE_MS = 10 * 60_000
#: "Has not published today": nothing since 08:30 by 09:20 on a trading day (IST).
QUIET = {"since": "08:30", "check": "09:20"}
TIMELINE = {"page": 50, "max": 200}
#: Performance (M13.8): sessions after a change that are scored, and the fewest cases judged.
PERF = {"sessions": (1, 3, 5), "min_cases": 30, "at": "16:20", "snapshot_at": "15:35"}
#: The relay (AL2): items a call carries, TRINETRA's text cut to this, held no longer than this.
RELAY = {"max_items": 8, "text": 300, "hold_ms": 30 * 60_000}
#: Recent alerts in ``state``: at most this many, from the last day.
STATE_ALERTS = {"max": 10, "within_ms": 24 * 3600 * 1000}
IST_OFFSET_S = 19_800

# ---- kinds ------------------------------------------------------------------------------------
#: The modules this release names; any other well-formed module is kept and shown under "Other".
KNOWN_MODULES = ("regime", "gap_bias", "swing_eod", "swing_intraday")
MODULE_NAMES = {
    "regime": "DRISHTI",
    "gap_bias": "PRABHAT",
    "swing_eod": "SANDHI end-of-day",
    "swing_intraday": "SANDHI intraday",
}
OTHER_MODULE_NAME = "Other"
INSTRUMENTS = ("NIFTY", "BANKNIFTY")
BIASES = ("bull", "bear", "neutral")
HORIZONS = ("open", "intraday", "swing")
QUALITIES = ("ok", "degraded", "no_data")
LEVEL_KEYS = ("trigger", "invalid", "target")
ALERT_LEVELS = ("info", "watch", "act")
#: Alerts of these levels go to Telegram; ``info`` stays in the app.
RELAYED_LEVELS = ("watch", "act")
#: ``hi`` is TRINETRA's Hinglish (owner decision 16 makes it the default).
LANGS = ("en", "hi", "gu")
LANG_NAMES = {"en": "English", "hi": "Hinglish", "gu": "Gujarati"}
#: When the owner's language is missing from a reading, the first of these TRINETRA sent.
LANG_FALLBACK = ("en", "hi", "gu")
DEFAULT_LANG = "hi"
FRESH_STATUSES = ("fresh", "stale", "stopped", "never")
#: ``/health``'s ``signals``: on, switched off, or on with no set-up file.
SWITCH_STATES = ("on", "off", "not_set_up")
LETTERBOX_STATES = ("ok", "missing", "wrong", "unknown")
#: first: no live reading before for the pair; state: another state; bias: the same state, another
#: bias; no_data: the quality went to no_data; lapsed: valid_until passed. A move of score or
#: confidence alone is not a change.
CHANGE_KINDS = ("first", "state", "bias", "no_data", "lapsed")
#: The kinds handed to Telegram (``lapsed`` stays in the app).
RELAYED_KINDS = ("first", "state", "bias", "no_data")
NOTE_KEYS = ("refused", "newer_format", "gaps", "clock")
UPDATE_CAUSES = ("document", "lapse", "settings")
CLASSES = ("read", "write")
#: The codes the page acts on (the bridge's envelope), with the HTTP status the bridge gives each.
ERROR_STATUS = {
    "unauthorized": 401,
    "forbidden": 403,
    "rate_limited": 429,
    "bad_request": 400,
    "not_found": 404,
    "version_conflict": 409,
    "not_ready": 501,
    "unavailable": 503,
    "not_confirmed": 504,
}
ERROR_CODES = tuple(ERROR_STATUS)
#: What a stored document was found to be (signals.db ``doc.verdict``).
VERDICTS = ("accepted", "repeat", "older", "newer_format", "refused")
#: Where a relayed item stands (signals.db ``change.relay`` and ``alert.relay``).
RELAY_STATES = ("pending", "handed", "expired", "skipped")

#: What the bridge lets through a minute, and waits, per class of op.
LIMITS_PER_MIN = {"read": 120, "write": 30}
TIMEOUTS_S = {"read": 3.0, "write": 6.0}

# ---- words ------------------------------------------------------------------------------------
#: Section 3.6's words and the rest of the view's, as templates. ``{time}`` is HH:MM:SS for
#: ``fresh`` and HH:MM elsewhere, IST, with "14 Nov " before it when it is not today's;
#: ``{age}`` and ``{difference}`` come from ``AGE_WORDS``.
WORDS = {
    # the freshness header, everywhere it appears
    "fresh": "TRINETRA · updated {time}",
    "stale": "TRINETRA has not published since {time}. This is its last reading, {age} old.",
    "stopped": "TRINETRA was stopped by its operators at {time}.",
    "never": "TRINETRA is not connected yet. Publishing has not been switched on in TRINETRA.",
    # notes under the header (/trinetra)
    "refused": "{count} files from TRINETRA could not be read today. Its last good reading is "
    "shown.",
    "refused_one": "1 file from TRINETRA could not be read today. Its last good reading is shown.",
    "refused_none": "{count} files from TRINETRA could not be read today. None of its readings "
    "can be shown yet.",
    "refused_none_one": "1 file from TRINETRA could not be read today. None of its readings can "
    "be shown yet.",
    "newer_format": "TRINETRA publishes a newer format than this release reads. Update SUDARSHAN.",
    "gaps": "{count} documents from TRINETRA did not arrive today.",
    "gaps_one": "1 document from TRINETRA did not arrive today.",
    "clock": "TRINETRA's clock and the server's differ by {difference}; its times may be off.",
    # on a card
    "degraded": "TRINETRA marks this reading as resting on incomplete data.",
    "no_data": "TRINETRA has no data for this reading.",
    "lapsed": "Lapsed {time}",
    "why_fallback": "TRINETRA did not write this in {wanted}; shown in {shown}.",
    "why_none": "TRINETRA sent no explanation for this reading.",
    # the service itself (an ``enabled: false`` answer, or the bridge when it gets no answer)
    "off": "The TRINETRA view is switched off.",
    "not_answering": "SUDARSHAN's TRINETRA reader is not answering.",
    "not_set_up": "TRINETRA is not connected yet. The letterbox has not been set up on the "
    "server; run its set-up line from the README's TRINETRA section.",
    "letterbox_wrong": "TRINETRA is not connected: the letterbox on the server is not set up as "
    "it should be. Run its set-up line from the README's TRINETRA section again.",
    "store_newer": "The TRINETRA view's records were written by a newer release. Install that "
    "release again; nothing was changed.",
    "store_failed": "The TRINETRA view's records could not be opened. Check that the server has "
    "free disk space; nothing was changed.",
    # Telegram notices (T9, the notices lane)
    "not_today": "TRINETRA has not published today.",
    "stale_notice": "TRINETRA stopped publishing at {time}. SUDARSHAN cannot restart it; see "
    "TRINETRA's own runbook.",
    "back_notice": "TRINETRA is publishing again since {time}.",
    # performance (M13.8)
    "too_few": "too few cases to judge",
    "no_scores": "No scores yet: a change is scored once 5 trading sessions have passed after it.",
    # answers to the owner's requests
    "version_conflict": "TRINETRA's settings were changed on another screen. Nothing was saved; "
    "open them again.",
    "not_found": "This change is not in TRINETRA's timeline. Open the timeline again.",
    "bad_module": "Choose one of TRINETRA's readings from the list.",
    "bad_instrument": "Choose NIFTY or BANKNIFTY.",
    "bad_lang": "Choose English, Hinglish or Gujarati.",
    "bad_request": "That request could not be read. Open the page again, then repeat it.",
    "failed": "The TRINETRA view could not do that just now. Try again in a minute.",
    "busy": "SUDARSHAN's TRINETRA reader is busy. Wait a minute, then try again.",
    # the bridge's own (B1)
    "unauthorized": "Your session has ended. Log in again.",
    "forbidden": "Only the account owner can use the TRINETRA view.",
    "rate_limited": "Too many requests. Wait a minute, then try again.",
    "not_ready": "This part of the TRINETRA view is not ready yet.",
    "not_confirmed": "Not confirmed: open the settings again to see whether they were saved.",
    "held": "Another program on this server holds the TRINETRA reader's port, so nothing was sent "
    "to it. Stop that program, then restart sudarshan-signals.",
    "sealed": "SUDARSHAN's TRINETRA reader could not confirm that this request came from "
    "OpenAlgo. If OpenAlgo's .env was changed, restart sudarshan-signals.",
    "too_big": "That is too large for the TRINETRA view. Send less at a time.",
}
#: An age or a difference of clocks: under 120 minutes in minutes, under 48 hours in hours,
#: else in days (whole units, rounded down; at least one minute once it is a minute).
AGE_WORDS = {
    "under_minute": "under a minute",
    "minute": "1 minute",
    "minutes": "{n} minutes",
    "hours": "{n} hours",
    "days": "{n} days",
}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
BIAS_WORDS = {"bull": "bullish", "bear": "bearish", "neutral": "neutral"}
HORIZON_WORDS = {"open": "at the open", "intraday": "intraday", "swing": "swing"}
LEVEL_WORDS = {"info": "Info", "watch": "Watch", "act": "Act"}
CHANGE_WORDS = {
    "first": "First reading",
    "state": "Changed",
    "bias": "Bias changed",
    "no_data": "No data",
    "lapsed": "Lapsed",
}

# ---- shapes -----------------------------------------------------------------------------------
PRIMITIVES = ("string", "int", "number", "bool")
ENUMS = {
    "KnownModule": KNOWN_MODULES,
    "Instrument": INSTRUMENTS,
    "Bias": BIASES,
    "Horizon": HORIZONS,
    "Quality": QUALITIES,
    "LevelKey": LEVEL_KEYS,
    "AlertLevel": ALERT_LEVELS,
    "Lang": LANGS,
    "FreshStatus": FRESH_STATUSES,
    "SwitchState": SWITCH_STATES,
    "LetterboxState": LETTERBOX_STATES,
    "ChangeKind": CHANGE_KINDS,
    "NoteKey": NOTE_KEYS,
    "UpdateCause": UPDATE_CAUSES,
    "Cls": CLASSES,
    "ErrorCode": ERROR_CODES,
}

RECORDS = {
    # A document's prices, each absent as null.
    "Levels": "trigger:number|null, invalid:number|null, target:number|null",
    "ReadingInput": "name:string, value:string",
    # TRINETRA's explanation in each language it sent (null: not sent). The page shows the
    # owner's ``lang``, else the first of LANG_FALLBACK present, with WORDS["why_fallback"].
    "Why": "en:string|null, hi:string|null, gu:string|null",
    # One current reading. ``module`` may be one this release does not name (shown under
    # "Other"); ``sessions``: trading sessions from ``since_ms``'s day to today, both counted;
    # ``published_ms``: when the document that carried it was published, as shown (server clock);
    # ``lapsed``: ``valid_until_ms`` has passed (the card moves below the live ones).
    "Reading": "module:string, instrument:Instrument, state:string, label:string, bias:Bias, "
    "score:number|null, confidence:number|null, since_ms:int|null, as_of_ms:int, "
    "valid_until_ms:int|null, horizon:Horizon, quality:Quality, levels:Levels|null, "
    "inputs:ReadingInput[], why:Why, lapsed:bool, sessions:int|null, published_ms:int, doc_seq:int",
    "AlertRow": "id:string, at_ms:int, module:string, instrument:Instrument|null, "
    "level:AlertLevel, title:string, text:string, lang:Lang|null",
    "Note": "key:NoteKey, count:int|null, words:string",
    # The header line. ``published_ms`` and ``fresh_until_ms`` are on the server's clock;
    # ``colour``: a bias colour may be shown (never past COLOUR_FOR_MS of age, never when never
    # heard); ``changes_at_ms``: when this answer next changes by the clock alone (the age's next
    # minute, going stale, losing the colour), so a page can work it out again then.
    "Freshness": "status:FreshStatus, words:string, published_ms:int|null, "
    "fresh_until_ms:int|null, colour:bool, clock_ok:bool, changes_at_ms:int|null",
    # The ``state`` answer: everything a screen needs, read at mount and after every reconnect.
    "State": "now_ms:int, lang:Lang, freshness:Freshness, notes:Note[], readings:Reading[], "
    "alerts:AlertRow[]",
    # One row of the timeline. ``at_ms``: when it took effect, as shown (the document's time, or
    # the lapse time); ``recorded_ms``: when the service stored it (server clock).
    "Change": "id:int, at_ms:int, recorded_ms:int, module:string, instrument:Instrument, "
    "kind:ChangeKind, from_state:string|null, from_label:string|null, from_bias:Bias|null, "
    "to_state:string, to_label:string, bias:Bias, score:number|null, confidence:number|null, "
    "quality:Quality, horizon:Horizon, since_ms:int|null, as_of_ms:int|null, "
    "valid_until_ms:int|null, levels:Levels|null, inputs:ReadingInput[], why:Why",
    # Newest first; ``next_before``: pass it as ``before`` for the older page (null: no more).
    "TimelinePage": "changes:Change[], next_before:int|null",
    # One row of the score (M13.8): ``sessions`` 1, 3 or 5 after the change (0: the gap at the
    # open, for gap_bias); ``hits``: cases that moved the way the bias said; ``judged`` false
    # under PERF min_cases, with ``words`` WORDS["too_few"].
    "PerfRow": "module:string, instrument:Instrument, bias:Bias, sessions:int, cases:int, "
    "hits:int, hit_pct:string|null, median_move_pct:string|null, judged:bool, "
    "words:string|null",
    "Performance": "module:string|null, as_of_day:int|null, rows:PerfRow[], words:string|null",
    # The owner's settings: the language of TRINETRA's explanations, whether changes and
    # watch/act alerts go to Telegram, and whether they may wait at night (decision 14).
    "Settings": "lang:Lang, relay:bool, night:bool, version:int",
    # What the journal stores at a trade's entry (3.7.4): the readings in effect at ``ts_ms``,
    # only when a fresh document stood then (``fresh`` false: "not known", never a guess).
    "ReadingAt": "module:string, instrument:Instrument, state:string, label:string, bias:Bias, "
    "confidence:number|null, since_ms:int|null, lapsed:bool",
    "StateAt": "ts_ms:int, fresh:bool, readings:ReadingAt[]",
    # Every op's answer while the view is off or not set up, in place of its own.
    "Off": "enabled:bool, message:string",
    # GET /health, open on loopback: states and counts only, never TRINETRA's text.
    "Health": "service:string, release:string|null, version:string, state:string, "
    "signals:SwitchState, letterbox:LetterboxState, heard_ms:int|null, next_by:int|null, "
    "fresh:bool|null, refused_today:int, gaps_today:int",
    # The push (B2): ``state`` is the ``state`` answer, or null when it would not fit the push
    # (the page then reads GET /state).
    "SignalsUpdate": "cause:UpdateCause, state:State|null",
    "PushItem": "event:string, data:SignalsUpdate",
    "PushRequest": "items:PushItem[]",
    "PushAnswer": "screens:int",
    # The relay (AL2): a change or an alert for the owner's Telegram; the desk makes the words.
    # ``id`` is the once-only claim key ("c-<change id>", "a-<TRINETRA's alert id>"); ``why`` and
    # ``text`` are plain text cut to RELAY text characters; ``night``: may wait at night.
    "RelayChange": "id:string, at_ms:int, module:string, module_name:string, "
    "instrument:Instrument, kind:ChangeKind, from_label:string|null, to_label:string, "
    "bias:Bias, confidence:number|null, why:string|null, why_lang:Lang|null, night:bool",
    "RelayAlert": "id:string, at_ms:int, module:string, module_name:string, "
    "instrument:Instrument|null, level:AlertLevel, title:string, text:string, lang:Lang|null, "
    "night:bool",
    "RelayRequest": "changes:RelayChange[], alerts:RelayAlert[]",
    # ``taken``: claimed now; ``known``: claimed before (both mean the desk has it).
    "RelayAnswer": "taken:string[], known:string[]",
}


class Field(NamedTuple):
    """One field of a request or a record."""

    name: str
    type: str
    optional: bool = False


class OpSpec(NamedTuple):
    """One op: the bridge's route for it (empty for a service op), its class, request, answer."""

    name: str
    method: str
    route: str
    cls: str
    req: str
    res: str


#: The owner's ops, each carried by one route of OpenAlgo's bridge under ``API_PREFIX``. A GET
#: reads its fields from the query string, a PUT from the JSON body; ``<id>`` is the field ``id``.
OPS = (
    OpSpec("state", "GET", "/state", "read", "", "State"),
    OpSpec("timeline", "GET", "/timeline", "read",
           "module?:string, instrument?:Instrument, before?:int, after_ms?:int, limit?:int",
           "TimelinePage"),
    OpSpec("change_get", "GET", "/changes/<id>", "read", "id:int", "Change"),
    OpSpec("performance", "GET", "/performance", "read", "module?:string", "Performance"),
    OpSpec("settings_get", "GET", "/settings", "read", "", "Settings"),
    OpSpec("settings_set", "PUT", "/settings", "write",
           "version:int, lang?:Lang, relay?:bool, night?:bool", "Settings"),
)  # fmt: skip
#: Ops no route carries: the journal's (under its own purpose) and the two calls out.
SERVICE_OPS = (
    OpSpec("state_at", "", "", "read", "ts:int", "StateAt"),
    OpSpec(PUSH_OP, "POST", PUSH_PATH, "write", "items:PushItem[]", "PushAnswer"),
    OpSpec(RELAY_OP, "POST", RELAY_PATH, "write", "changes:RelayChange[], alerts:RelayAlert[]",
           "RelayAnswer"),
)  # fmt: skip
OP_NAMES = tuple(op.name for op in (*OPS, *SERVICE_OPS))
#: The ops each served purpose opens.
PURPOSE_OPS = {
    "api": tuple(op.name for op in OPS),
    "journal": ("state", "state_at"),
}


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


def module_name(module: str) -> str:
    """What a screen calls a module: its TRINETRA name, or "Other" for one this release does not
    name."""
    return MODULE_NAMES.get(module, OTHER_MODULE_NAME)
