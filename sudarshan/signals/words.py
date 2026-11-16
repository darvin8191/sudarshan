"""The TRINETRA view's sentences (T2, design 3.6): every one is a template of ``contract.WORDS``,
filled here. Pure: the clock is an argument (``now_ms``), and nothing is read or written.

The freshness header and its notes are worked out from the last accepted document alone
(``Last``): fresh while the server's clock is before its ``fresh_until_ms``; past that, stale with
its age; a ``stopping`` document says so until the next one. A page that holds a ``Freshness``
works it out again at ``changes_at_ms`` (the next minute of the age, going stale, losing the
colour of bias, midnight), so no timed re-read is needed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sudarshan.signals import contract as c

W = c.WORDS
MINUTE_MS = 60_000
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
_EPOCH = datetime(1970, 1, 1)


@dataclass(frozen=True)
class Last:
    """The last accepted document, as the header needs it (``contract.Snapshot`` gives each)."""

    published_ms: int
    fresh_until_ms: int
    stopping: bool = False
    clock_ok: bool = True
    clock_diff_ms: int = 0

    @classmethod
    def of(cls, snap: c.Snapshot) -> Last:
        return cls(snap.published_ms, snap.fresh_until_ms, snap.stopping, snap.clock_ok,
                   snap.clock_diff_ms)  # fmt: skip


def _local(ms: int) -> datetime:
    return _EPOCH + timedelta(milliseconds=ms + c.IST_OFFSET_S * 1000)


def clock_text(ms: int, now_ms: int, *, seconds: bool = False) -> str:
    """10:42 (or 10:42:05) in IST, with "14 Nov " in front when it is not today's."""
    moment = _local(ms)
    text = f"{moment:%H:%M:%S}" if seconds else f"{moment:%H:%M}"
    if moment.date() != _local(now_ms).date():
        text = f"{moment.day} {c.MONTHS[moment.month - 1]} {text}"
    return text


def age_words(delta_ms: int) -> str:
    """An age or a difference of clocks: "under a minute", "1 minute", "14 minutes" (under 120
    minutes), "3 hours" (under 48 hours), "2 days"; whole units, rounded down."""
    delta_ms = max(0, int(delta_ms))
    if delta_ms < MINUTE_MS:
        return c.AGE_WORDS["under_minute"]
    if delta_ms < 120 * MINUTE_MS:
        minutes = delta_ms // MINUTE_MS
        return c.AGE_WORDS["minute"] if minutes == 1 else c.AGE_WORDS["minutes"].format(n=minutes)
    if delta_ms < 48 * HOUR_MS:
        return c.AGE_WORDS["hours"].format(n=delta_ms // HOUR_MS)
    return c.AGE_WORDS["days"].format(n=delta_ms // DAY_MS)


def _next_age_ms(delta_ms: int) -> int:
    """The age at which ``age_words`` next says something else."""
    delta_ms = max(0, int(delta_ms))
    if delta_ms < 120 * MINUTE_MS:
        return (delta_ms // MINUTE_MS + 1) * MINUTE_MS
    if delta_ms < 48 * HOUR_MS:
        return (delta_ms // HOUR_MS + 1) * HOUR_MS
    return (delta_ms // DAY_MS + 1) * DAY_MS


def _next_midnight_ms(now_ms: int) -> int:
    day = _local(now_ms).date() + timedelta(days=1)
    return int((datetime(day.year, day.month, day.day) - _EPOCH).total_seconds() * 1000) - (
        c.IST_OFFSET_S * 1000
    )


def freshness(last: Last | None, now_ms: int) -> dict[str, Any]:
    """The contract's ``Freshness`` at ``now_ms``."""
    if last is None:
        return {"status": "never", "words": W["never"], "published_ms": None,
                "fresh_until_ms": None, "colour": False, "clock_ok": True,
                "changes_at_ms": None}  # fmt: skip
    published = last.published_ms
    age = max(0, now_ms - published)
    if last.stopping:
        status, words = "stopped", W["stopped"].format(time=clock_text(published, now_ms))
    elif now_ms < last.fresh_until_ms:
        status = "fresh"
        words = W["fresh"].format(time=clock_text(published, now_ms, seconds=True))
    else:
        status = "stale"
        words = W["stale"].format(time=clock_text(published, now_ms), age=age_words(age))
    colour = status == "fresh" or age < c.COLOUR_FOR_MS
    moments = []
    if status == "fresh":
        moments.append(last.fresh_until_ms)
    if status == "stale":
        moments.append(published + _next_age_ms(age))
    if colour and status != "fresh":
        moments.append(published + c.COLOUR_FOR_MS)
    if _local(published).date() == _local(now_ms).date():
        moments.append(_next_midnight_ms(now_ms))  # the time then takes its date
    return {
        "status": status,
        "words": words,
        "published_ms": published,
        "fresh_until_ms": last.fresh_until_ms,
        "colour": colour,
        "clock_ok": last.clock_ok,
        "changes_at_ms": min(m for m in moments if m > now_ms) if moments else None,
    }


def notes(
    last: Last | None, *, refused_today: int, gaps_today: int, newer_format: bool
) -> list[dict[str, Any]]:
    """The contract's ``Note`` list under the header (on ``/trinetra``)."""
    out: list[dict[str, Any]] = []
    if refused_today > 0:
        key = "refused" if last is not None else "refused_none"
        template = W[key + "_one"] if refused_today == 1 else W[key]
        out.append({"key": "refused", "count": refused_today,
                    "words": template.format(count=refused_today)})  # fmt: skip
    if newer_format:
        out.append({"key": "newer_format", "count": None, "words": W["newer_format"]})
    if gaps_today > 0:
        template = W["gaps_one"] if gaps_today == 1 else W["gaps"]
        out.append({"key": "gaps", "count": gaps_today, "words": template.format(count=gaps_today)})
    if last is not None and not last.clock_ok:
        difference = age_words(abs(last.clock_diff_ms))
        out.append({"key": "clock", "count": None,
                    "words": W["clock"].format(difference=difference)})  # fmt: skip
    return out


def card_words(reading: Mapping[str, Any], lapsed: bool, now_ms: int) -> list[str]:
    """A card's own lines: the quality TRINETRA gave, and "Lapsed 09:15"."""
    out = []
    if reading.get("quality") == "degraded":
        out.append(W["degraded"])
    elif reading.get("quality") == "no_data":
        out.append(W["no_data"])
    until = reading.get("valid_until_ms")
    if lapsed and until is not None:
        out.append(W["lapsed"].format(time=clock_text(until, now_ms)))
    return out


def pick_why(why: Mapping[str, str | None] | None, lang: str) -> tuple[str | None, str | None, str]:
    """TRINETRA's explanation in the owner's language: (text, language shown, the line to show
    with it). A missing language falls back to the first of ``LANG_FALLBACK`` sent, labelled;
    none at all is ``why_none``."""
    why = why or {}
    if why.get(lang):
        return why[lang], lang, ""
    for other in c.LANG_FALLBACK:
        if why.get(other):
            line = W["why_fallback"].format(wanted=c.LANG_NAMES.get(lang, lang),
                                            shown=c.LANG_NAMES[other])  # fmt: skip
            return why[other], other, line
    return None, None, W["why_none"]


def cut(text: str | None, limit: int = c.RELAY["text"]) -> str | None:
    """Plain text cut to ``limit`` characters (the last three "..." when it was longer)."""
    if text is None:
        return None
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def relay_change(change: Mapping[str, Any], *, lang: str, night: bool) -> dict[str, Any]:
    """The contract's ``RelayChange`` for one timeline row (``Change``), for the alerts' desk."""
    text, shown, _line = pick_why(change.get("why"), lang)
    return {
        "id": f"c-{change['id']}",
        "at_ms": change["at_ms"],
        "module": change["module"],
        "module_name": c.module_name(change["module"]),
        "instrument": change["instrument"],
        "kind": change["kind"],
        "from_label": change.get("from_label"),
        "to_label": change["to_label"],
        "bias": change["bias"],
        "confidence": change.get("confidence"),
        "why": cut(text),
        "why_lang": shown,
        "night": bool(night),
    }


def relay_alert(alert: Mapping[str, Any], *, night: bool) -> dict[str, Any]:
    """The contract's ``RelayAlert`` for one alert (``AlertRow``), for the alerts' desk."""
    return {
        "id": f"a-{alert['id']}",
        "at_ms": alert["at_ms"],
        "module": alert["module"],
        "module_name": c.module_name(alert["module"]),
        "instrument": alert.get("instrument"),
        "level": alert["level"],
        "title": alert["title"],
        "text": cut(alert.get("text") or ""),
        "lang": alert.get("lang"),
        "night": bool(night),
    }
