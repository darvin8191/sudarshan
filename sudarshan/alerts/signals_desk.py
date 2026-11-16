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
  Never late. An item that cannot go out within HOLD_S of arriving is not sent at all (``stale``);
        the app's timeline has it.
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

import math
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from sudarshan.autologin.messages import BRAND
from sudarshan.autologin.schedule import IST
from sudarshan.signals import contract as c

from .market.api import ApiError

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
#: The relay's answer when it breaks the contract: it reaches sudarshan-signals' log.
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
