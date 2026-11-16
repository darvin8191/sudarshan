"""TRINETRA's changes on the owner's Telegram: the alerts' signals desk (AL2, M13.10; P8 3.7, 5.2).

    sudarshan-signals (signals/relay.py) -- POST 127.0.0.1:5582/v1/signals_relay, sealed -->
      the alerts' one listener (market/api.py): purpose sudarshan/alerts/signals/v1, principal
      sudarshan-signals, at most 16 KiB -> SignalsDesk.relay
        -> each item claimed once in alerts.db before anything is sent, and kept there while it
           waits ("c-<change id>", "a-<TRINETRA's alert id>")
        -> merged over MERGE_S: one message per change of a reading, its newest state
        -> the signals lane of the process's one Pacer (outbox.py: 4 a minute, none while order
           alerts wait, none while a market message has waited 20 s)
        -> OpenAlgoTelegram.send -> the owner's linked chat

sudarshan-signals holds no API key and writes no message: it hands this desk a change, not a
message, and the words are made here from its fields (``compose``):

    सुदर्शन / SUDARSHAN
    TRINETRA · NIFTY regime: FII accumulation → FII distribution (bearish), confidence 0.71.
    "<TRINETRA's own sentence, plain text, cut at 300 characters>"
    10:42:05 IST

  Once. An item is claimed by its id in alerts.db when it arrives, before its message goes out,
        and an id is never claimed twice: a relay sent again (a lost answer, a restart of either
        service) is answered ``known`` and sends nothing. While it waits the claim holds the item
        itself, so a restart of this service still sends it; one a restart finds on its way may
        have reached Telegram and is never sent again (``unsure``).
  Merged. A message waits MERGE_S after the first change of its reading for the rest of the
        burst; the changes of one (module, instrument) are one message, from the reading the owner
        was last told to the newest (one message per change). Each alert is a message of its own.
  Never late. An item that cannot go out within HOLD_S of its own time (``at_ms``, or its arrival
        when that is earlier) is not sent at all (``stale``); the app's timeline has it.
  Dropped, as every lane is, when OpenAlgo says its bot is stopped or the owner's chat is not
        linked: never held back for later.
  Only watch and act. An ``info`` alert or a ``lapsed`` change that arrives anyway is claimed and
        never sent (``skipped``): it stays in the app.
  Not held in market hours. Night hours are a switch (``NIGHT_HOURS``), not built yet (decision
        14, N1); while set, an item the owner lets wait at night (``night``) is held to their end,
        unless it is an alert of level ``act``, and its HOLD_S count from that end.
  Plain text. OpenAlgo's /notify sends with Markdown and does not escape, so TRINETRA's text has
        every Markdown character replaced (as the order alerts do with the broker's), and its
        slashes too, so a word in it can never become a command the owner taps.

SUDARSHAN_ORDER_ALERTS=off stops this service and so this desk with it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sudarshan.autologin.messages import BRAND
from sudarshan.autologin.schedule import IST
from sudarshan.signals import contract as c

from .market.api import ApiError
from .outbox import DROP, RETRY, SENT, SIGNALS, UNCONFIRMED
from .store import SIGNAL_SENDING, SIGNAL_WAITING, Store

log = logging.getLogger("sudarshan.alerts")

# Timings are module constants so the tests can shrink them.
MERGE_S = 5.0
HOLD_S = c.RELAY["hold_ms"] / 1000.0
POLL_S = 0.2  # a message ready to go looks again this soon while its lane gives way
RETRY_S = (2.0, 5.0, 10.0, 30.0, 60.0)
UNCONFIRMED_TRIES = 3
#: Items waiting at most; past this the oldest message's are not sent (a burst far beyond what
#: four a minute could tell within HOLD_S), as the other lanes bound theirs.
MAX_WAITING = 200
#: (start, end) in minutes of the day, IST, or None: decision 14 (22:30 to 07:00), built in N1.
NIGHT_HOURS: tuple[int, int] | None = None
TEXT_MAX = c.RELAY["text"]
LABEL_MAX = c.DOC_LIMITS["label"]
TITLE_MAX = c.DOC_LIMITS["title"]
ARROW = "\u2192"
#: What the owner reads for a module (the screens' names, DRISHTI and the rest, are the page's).
MODULE_WORDS = {
    "regime": "regime",
    "gap_bias": "gap bias",
    "swing_eod": "end-of-day swing",
    "swing_intraday": "intraday swing",
}
#: The relay's answers that are not the owner's to act on: they reach sudarshan-signals' log.
STARTING = "The alert service is starting. The changes are sent again in a moment."
CLOSING = "The alert service is stopping. The changes are sent again once it is back."
OFF = "Order alerts are switched off on the server, and TRINETRA's changes on Telegram with them."
BAD = "That relay could not be read; nothing of it was taken."

_PLAIN = str.maketrans({"*": " ", "_": " ", "`": "'", "[": "(", "]": ")", "/": "\u2215"})
_JOINERS = ("\u200c", "\u200d")  # Indian scripts need them (contract README, section 2)
_CHANGE_ID = re.compile(r"c-[0-9]{1,18}")
_ALERT_ID = re.compile("a-" + c.ALERT_ID_PATTERN)
_MODULE = re.compile(c.MODULE_PATTERN)


def module_words(module: str) -> str:
    """'gap_bias' as 'gap bias'; a module this release does not name, its own name in words."""
    return MODULE_WORDS.get(module) or module.replace("_", " ")


def plain(text: Any, limit: int) -> str:
    """TRINETRA's text as one plain line: printable (the joiners kept), no Markdown character, no
    slash a command could start with, cut at ``limit`` characters (the last three "...")."""
    raw = str(text or "")
    kept = "".join(ch if ch.isprintable() or ch in _JOINERS else " " for ch in raw)
    line = " ".join(kept.translate(_PLAIN).split())
    return line if len(line) <= limit else line[: limit - 3].rstrip() + "..."


def _clock(at_ms: int, now: datetime) -> str:
    moment = datetime.fromtimestamp(at_ms / 1000, IST)
    text = f"{moment:%H:%M:%S} IST"
    if moment.date() != now.astimezone(IST).date():
        text = f"{moment.day} {c.MONTHS[moment.month - 1]} {text}"
    return text


def _confidence(item: Mapping[str, Any]) -> str:
    value = item.get("confidence")
    return f", confidence {value:.2f}" if isinstance(value, int | float) else ""


def change_line(items: list[Mapping[str, Any]]) -> str:
    """One message's line for the changes of one reading, oldest first: from the reading the owner
    was last told (the first's ``from_label``) to the newest."""
    first, last = items[0], items[-1]
    head = f"TRINETRA · {last['instrument']} {module_words(last['module'])}"
    if last["kind"] == "no_data":
        return f"{head}: no data for this reading."
    to = plain(last["to_label"], LABEL_MAX)
    bias = c.BIAS_WORDS[last["bias"]]
    was = plain(first.get("from_label"), LABEL_MAX) if first["kind"] != "first" else ""
    if was and was != to:
        return f"{head}: {was} {ARROW} {to} ({bias}){_confidence(last)}."
    if len(items) == 1 and last["kind"] == "bias":
        return f"{head}: {to}, bias now {bias}{_confidence(last)}."
    return f"{head}: {to} ({bias}){_confidence(last)}."


def alert_line(item: Mapping[str, Any]) -> str:
    """An alert's line: 'TRINETRA · NIFTY regime · Act: <its title>'."""
    where = f"{item['instrument']} " if item.get("instrument") else ""
    level = c.LEVEL_WORDS[item["level"]]
    return f"TRINETRA · {where}{module_words(item['module'])} · {level}: " + plain(
        item["title"], TITLE_MAX
    )


def compose(kind: str, items: list[Mapping[str, Any]], now: datetime) -> str:
    """The Telegram message for one change of a reading (``kind`` change, its items oldest first)
    or one alert: the brand line, the line, TRINETRA's sentence in quotes, the time."""
    last = items[-1]
    line = change_line(items) if kind == "change" else alert_line(last)
    said = plain(last.get("why") if kind == "change" else last.get("text"), TEXT_MAX)
    quote = [f'"{said}"'] if said else []
    return "\n".join([BRAND, line, *quote, _clock(last["at_ms"], now)])


# ---- what a relay may carry (contract RelayChange, RelayAlert) ---------------------------------
def _ms(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value < 10**13


def _text(value: Any, limit: int, *, empty: bool = False, none: bool = False) -> bool:
    if value is None:
        return none
    return isinstance(value, str) and (empty or bool(value)) and len(value) <= limit


def _number(value: Any) -> bool:
    if value is None:
        return True
    ok = isinstance(value, int | float) and not isinstance(value, bool)
    return ok and math.isfinite(value) and 0.0 <= value <= 1.0


_CHANGE_RULES: dict[str, Callable[[Any], bool]] = {
    "id": lambda v: isinstance(v, str) and bool(_CHANGE_ID.fullmatch(v)),
    "at_ms": _ms,
    "module": lambda v: isinstance(v, str) and bool(_MODULE.fullmatch(v)),
    "module_name": lambda v: _text(v, 40),
    "instrument": lambda v: v in c.INSTRUMENTS,
    "kind": lambda v: v in c.CHANGE_KINDS,
    "from_label": lambda v: _text(v, LABEL_MAX, none=True),
    "to_label": lambda v: _text(v, LABEL_MAX),
    "bias": lambda v: v in c.BIASES,
    "confidence": _number,
    "why": lambda v: _text(v, TEXT_MAX, empty=True, none=True),
    "why_lang": lambda v: v is None or v in c.LANGS,
    "night": lambda v: isinstance(v, bool),
}
_ALERT_RULES: dict[str, Callable[[Any], bool]] = {
    "id": lambda v: isinstance(v, str) and bool(_ALERT_ID.fullmatch(v)),
    "at_ms": _ms,
    "module": lambda v: isinstance(v, str) and bool(_MODULE.fullmatch(v)),
    "module_name": lambda v: _text(v, 40),
    "instrument": lambda v: v is None or v in c.INSTRUMENTS,
    "level": lambda v: v in c.ALERT_LEVELS,
    "title": lambda v: _text(v, TITLE_MAX),
    "text": lambda v: _text(v, TEXT_MAX, empty=True),
    "lang": lambda v: v is None or v in c.LANGS,
    "night": lambda v: isinstance(v, bool),
}
KINDS = {"change": ("changes", _CHANGE_RULES), "alert": ("alerts", _ALERT_RULES)}


def _bad(field: str) -> ApiError:
    return ApiError(400, "bad_request", BAD, field)


def parse(args: Any) -> list[tuple[str, dict[str, Any]]]:
    """The items of one relay, ``(kind, fields)`` in the order they came, changes first; one that
    breaks the contract refuses the whole relay (400, naming the field)."""
    if not isinstance(args, Mapping):
        raise _bad("args")
    items: list[tuple[str, dict[str, Any]]] = []
    for kind, (name, rules) in KINDS.items():
        found = args.get(name, [])
        if not isinstance(found, list):
            raise _bad(name)
        for raw in found:
            if not isinstance(raw, Mapping):
                raise _bad(name)
            for key, rule in rules.items():
                if not rule(raw.get(key)):
                    raise _bad(f"{name}.{key}")
            items.append((kind, {key: raw.get(key) for key in rules}))
    if not items or len(items) > c.RELAY["max_items"]:
        raise _bad("changes")
    if len({fields["id"] for _, fields in items}) != len(items):
        raise _bad("id")
    return items


def relayed(kind: str, fields: Mapping[str, Any]) -> bool:
    """Whether an item is told on Telegram: watch and act alerts, and every change but lapsed."""
    if kind == "alert":
        return fields["level"] in c.RELAYED_LEVELS
    return fields["kind"] in c.RELAYED_KINDS


def night_end(at: datetime, hours: tuple[int, int] | None) -> datetime | None:
    """The end of the night hours ``at`` falls in, or None when it is outside them (or there are
    none). ``hours`` is (start, end) in minutes of the day, IST; the night may pass midnight."""
    if hours is None:
        return None
    start, end = hours
    if start == end:
        return None
    local = at.astimezone(IST)
    minute = local.hour * 60 + local.minute
    inside = start <= minute < end if start < end else (minute >= start or minute < end)
    if not inside:
        return None
    day = local.date() + timedelta(days=1 if start > end and minute >= start else 0)
    return datetime(day.year, day.month, day.day, end // 60, end % 60, tzinfo=IST)


# ---- the desk ------------------------------------------------------------------------------------
def _key_of(kind: str, fields: Mapping[str, Any]) -> str:
    """A message's place in the queue: one per reading for changes, one per alert."""
    return f"c:{fields['module']}:{fields['instrument']}" if kind == "change" else fields["id"]


@dataclass
class _Message:
    """One message waiting: a reading's changes (oldest first) or one alert."""

    kind: str
    items: list[dict[str, Any]]
    ids: list[str]
    first: float  # monotonic: when its first item arrived (the merge counts from here)
    ready: float  # epoch seconds: from when it may go (the night's end, else its arrival)


class SignalsDesk:
    """Takes relayed items, claims each once, and sends their messages in the signals lane.

    Args:
        store: The alert record (alerts.db): the once-only rule, and the items while they wait.
        send: Sends one text; returns SENT, RETRY, UNCONFIRMED or DROP (OpenAlgoTelegram.send).
        owner_ready: True when the owner's key is loaded, reading it first if need be.
        clock: The service clock (aware datetimes).
        pacer: The process's one outbox.Pacer: this desk takes the signals lane's slots.
    """

    def __init__(
        self,
        store: Store,
        send: Callable[[str], Awaitable[str]],
        owner_ready: Callable[[], bool],
        clock: Callable[[], datetime],
        pacer,
    ):
        self.store, self.send, self.owner_ready = store, send, owner_ready
        self.clock, self.pacer = clock, pacer
        self._queue: OrderedDict[str, _Message] = OrderedDict()
        self._wake = asyncio.Event()
        self._waiting_slot: float | None = None  # the Pacer's time a message began to wait
        self._sending = self._closing = self._reloaded = False
        self.stats = dict.fromkeys(
            ("taken", "known", "sent", "dropped", "skipped", "stale", "unsure"), 0
        )
        pacer.set_waiting(SIGNALS, lambda: self._waiting_slot is not None,
                          since=lambda: self._waiting_slot)  # fmt: skip

    def health(self) -> dict[str, Any]:
        """Counts only, for /health: nothing TRINETRA wrote."""
        return {"waiting": sum(len(m.ids) for m in self._queue.values()), **self.stats}

    # ---- the relay (the listener's signals_relay op) ------------------------------------------
    async def relay(self, message: Mapping[str, Any]) -> dict[str, list[str]]:
        """Claim the items of one relay and queue their messages; answer the contract's
        ``RelayAnswer``: ``taken`` (claimed now) and ``known`` (claimed before)."""
        if self._closing:
            raise ApiError(503, "busy", CLOSING)
        self._reload_once()  # what the last run left waiting goes first, and is never doubled
        items = parse(message.get("args"))
        now = self.clock()
        rows = []
        for kind, fields in items:
            ready = self._ready(kind, fields, now)
            rows.append((fields["id"], ready.timestamp(), json.dumps([kind, fields])))
        taken = set(self.store.claim_signals(rows, now.timestamp()))
        skipped = []
        for (kind, fields), (_, ready, _) in zip(items, rows, strict=True):
            if fields["id"] not in taken:
                continue
            if relayed(kind, fields):
                self._add(kind, fields, ready, time.monotonic())
            else:
                skipped.append(fields["id"])
        if skipped:
            self.store.set_signals(skipped, "skipped", now.timestamp())
            self.stats["skipped"] += len(skipped)
        answer = {
            "taken": [f["id"] for _, f in items if f["id"] in taken],
            "known": [f["id"] for _, f in items if f["id"] not in taken],
        }
        self.stats["taken"] += len(answer["taken"])
        self.stats["known"] += len(answer["known"])
        if answer["taken"]:
            log.info("TRINETRA: %d item(s) taken for Telegram, %d known before",
                     len(answer["taken"]), len(answer["known"]))  # fmt: skip
        return answer

    def _ready(self, kind: str, fields: Mapping[str, Any], now: datetime) -> datetime:
        """From when its HOLD_S count: its own time, so a change sudarshan-signals had to try
        again for a while is still never told late; the night's end when the night holds it."""
        held = fields["night"] and not (kind == "alert" and fields["level"] == "act")
        night = night_end(now, NIGHT_HOURS) if held else None
        return night or min(now, datetime.fromtimestamp(fields["at_ms"] / 1000, IST))

    def _add(self, kind: str, fields: dict[str, Any], ready: float, seen: float) -> None:
        key = _key_of(kind, fields)
        held = self._queue.get(key)
        if held is not None and kind == "change":
            held.items[1:] = [fields]  # the words need the first and the newest; every id settles
            held.ids.append(fields["id"])
            held.ready = max(held.ready, ready)
        else:
            self._queue[key] = _Message(kind, [fields], [fields["id"]], seen, ready)
        self._bound()
        self._wake.set()

    def _bound(self) -> None:
        """At most MAX_WAITING items wait: past it the oldest messages are not sent."""
        waiting = sum(len(m.ids) for m in self._queue.values())
        while waiting > MAX_WAITING and len(self._queue) > 1:
            _, oldest = self._queue.popitem(last=False)
            waiting -= len(oldest.ids)
            self._end(oldest, "dropped")
            log.warning("TRINETRA: more changes were waiting than can be told; %d older item(s) "
                        "are not sent", len(oldest.ids))  # fmt: skip

    # ---- sending ----------------------------------------------------------------------------
    def _reload_once(self) -> None:
        """``reload`` before the first relay is taken or the first message sent, and only then."""
        if self._reloaded:
            return
        self._reloaded = True
        try:
            self.reload()
        except Exception:
            log.exception("the TRINETRA changes the last run left waiting could not be read")

    def reload(self) -> int:
        """At start: the items a stopped run left waiting go out after all (one past its HOLD_S
        is stale); returns how many were queued again."""
        now = self.clock().timestamp()
        due_back = time.monotonic() - MERGE_S  # they have waited their merge already
        queued = {item_id for message in self._queue.values() for item_id in message.ids}
        count = 0
        for item_id, ready, body in self.store.queued_signals(now):
            if item_id in queued:
                continue
            try:
                kind, fields = json.loads(body)
                (_, fields), *_ = parse({KINDS[kind][0]: [fields]})
            except (ValueError, TypeError, KeyError, ApiError):
                self.store.set_signals([item_id], "dropped", now)
                continue
            self._add(kind, fields, ready, due_back)
            count += 1
        self._expire()
        if count:
            log.info("TRINETRA: %d item(s) left waiting by the last run go to Telegram", count)
        return count

    async def run(self) -> None:
        """Send until cancelled or closed: first what the last run left waiting. A failure costs
        one try, never the service."""
        self._reload_once()
        failures = unconfirmed = 0
        while not self._closing:
            message, wait = self._due()
            if message is None:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), wait)
                except TimeoutError:
                    pass
                continue
            if not self.owner_ready():
                await asyncio.sleep(RETRY_S[min(failures, len(RETRY_S) - 1)])
                failures += 1
                continue
            if not await self._slot(message):
                continue
            outcome = await self._send(message)
            if outcome == SENT:
                failures = unconfirmed = 0
                self._end(message, "sent")
                continue
            if outcome == DROP:
                self._end(message, "dropped")
                log.warning("TRINETRA: a change was not sent: Telegram cannot be reached that way")
                continue
            if outcome == UNCONFIRMED:
                unconfirmed += 1
                if unconfirmed >= UNCONFIRMED_TRIES:
                    unconfirmed = 0
                    self._end(message, "dropped")
                    log.error("Telegram did not confirm a TRINETRA change after %d tries; it is "
                              "not sent again", UNCONFIRMED_TRIES)  # fmt: skip
                    continue
            self._put_back(message)
            await asyncio.sleep(RETRY_S[min(failures, len(RETRY_S) - 1)])
            failures += 1

    async def close(self, timeout: float) -> None:
        """Nothing new from now; a message on its way gets up to ``timeout`` seconds. What still
        waits stays claimed in alerts.db and goes after the next start (``reload``)."""
        self._closing = True
        self._wake.set()
        deadline = time.monotonic() + timeout
        while self._sending and time.monotonic() < deadline:
            await asyncio.sleep(0.05)

    def _due(self) -> tuple[_Message | None, float | None]:
        """The first message whose merge has passed and that the night does not hold, else how
        long until one may be (None: nothing waits)."""
        self._expire()
        now_m, now_e = time.monotonic(), self.clock().timestamp()
        soonest = None
        for message in self._queue.values():
            wait = max(0.0, message.first + MERGE_S - now_m, message.ready - now_e)
            if wait <= 0:
                return message, 0.0
            soonest = wait if soonest is None else min(soonest, wait)
        return None, soonest

    def _expire(self) -> None:
        """An item past HOLD_S from when it could go is never sent late: it is stale."""
        now = self.clock().timestamp()
        for key, message in list(self._queue.items()):
            if now - message.ready > HOLD_S:
                del self._queue[key]
                self.store.set_signals(message.ids, "stale", now)
                self.stats["stale"] += len(message.ids)
                log.warning("TRINETRA: %d item(s) could not be sent within %d minutes and are "
                            "not sent", len(message.ids), HOLD_S // 60)  # fmt: skip

    def _pacer_now(self) -> float:
        return getattr(self.pacer, "clock", time.monotonic)()

    async def _slot(self, message: _Message) -> bool:
        """Until the signals lane has a slot (False: the message went stale meanwhile)."""
        try:
            while True:
                self._expire()
                if self._queue.get(self._key(message)) is not message or self._closing:
                    return False
                if self._waiting_slot is None:
                    self._waiting_slot = self._pacer_now()
                if self.pacer.blocked(SIGNALS):
                    await asyncio.sleep(POLL_S)
                    continue
                wait = self.pacer.allowed_at(SIGNALS) - self._pacer_now()
                if wait <= 0:
                    return True
                await asyncio.sleep(min(wait, 4 * POLL_S))
        finally:
            self._waiting_slot = None

    def _key(self, message: _Message) -> str:
        return _key_of(message.kind, message.items[0])

    async def _send(self, message: _Message) -> str:
        """Take it out of the queue, mark it on its way, take the slot, send. No await before
        the slot is taken: it is still ours."""
        del self._queue[self._key(message)]
        now = self.clock()
        self.store.set_signals(message.ids, SIGNAL_SENDING, now.timestamp())
        self.pacer.took(SIGNALS)
        self._sending = True
        try:
            return await self.send(compose(message.kind, message.items, now))
        except asyncio.CancelledError:  # it may have reached Telegram: never sent again
            self._end(message, "unsure")
            raise
        except Exception:
            log.exception("a TRINETRA change could not be sent")
            return RETRY
        finally:
            self._sending = False

    def _end(self, message: _Message, state: str) -> None:
        self.store.set_signals(message.ids, state, self.clock().timestamp())
        self.stats[state] += len(message.ids)
        if state == "sent":
            log.info("TRINETRA: told the owner about %d item(s)", len(message.ids))

    def _put_back(self, message: _Message) -> None:
        """Back to the front: before any newer change of the same reading that came meanwhile."""
        self.store.set_signals(message.ids, SIGNAL_WAITING, self.clock().timestamp())
        key = self._key(message)
        newer = self._queue.pop(key, None)
        if newer is not None:
            message.items[1:] = newer.items[-1:]
            message.ids += newer.ids
            message.ready = max(message.ready, newer.ready)
        self._queue[key] = message
        self._queue.move_to_end(key, last=False)
