"""What the owner reads on the Screener page (M9.18): reasons, marks, chips and the engine line. Pure.

Plain English in a trader's words: no codes, no status numbers, no protocol terms, plain text only
(a chip's icon is a name for the page to draw). Nothing here reads a clock: every time comes in as
an argument (epoch ms) and is shown in IST. The Telegram line of a digest is composed by sudarshan-alerts from the digest's fields,
never here; what stays on the page, as the market alerts rule says, is the "Not checked" notes, the
pause notice and the reason a run was not told.

    Not checked: warming up (37 of 50 candles so far); no VWAP yet
    Closing auction · price at about 15:30
    Real-time · next check 10:45
    Not checking · the price feed is reconnecting (since 10:41:05)
    New · 10:45        Since Mon        Missing 1 of 2        Pinned        Removed by you
    Exited today: INFY 11:30, TCS 12:15

"Real-time" is said only while ticks arrive, never "Live" (LIVE stays the trading-mode chip).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sudarshan.alerts.messages import quantity
from sudarshan.autologin.schedule import IST

from . import alerting as al
from . import membership as mem
from . import types as T

#: How many distinct reasons a "Not checked" line spells out before it counts the rest.
REASONS_SHOWN = 2
#: How many exits the watchlist's "Exited today" line names before it counts the rest.
EXITS_SHOWN = 5

# ---- time ------------------------------------------------------------------------------------------


def _moment(at: int) -> datetime:
    return datetime.fromtimestamp(at / 1000, IST)


def hm(at: int) -> str:
    """'10:45' (IST)."""
    return _moment(at).strftime("%H:%M")


def hms(at: int) -> str:
    """'10:41:05' (IST)."""
    return _moment(at).strftime("%H:%M:%S")


def when(at: int, now: int) -> str:
    """'14:20' today, 'Mon 09:15' within the week, '9 Nov 09:15' further on, with the year if not this."""
    moment, today = _moment(at), _moment(now)
    if moment.date() == today.date():
        return f"{moment:%H:%M}"
    days = (today.date() - moment.date()).days
    if abs(days) < 7:
        return f"{moment:%a %H:%M}"
    year = f" {moment.year}" if moment.year != today.year else ""
    return f"{moment.day} {moment:%b}{year} {moment:%H:%M}"


def day_words(at: int, now: int) -> str:
    """'10:45' today, 'Mon' within the week, '12 Oct' further on (the year if not this one)."""
    moment, today = _moment(at), _moment(now)
    if moment.date() == today.date():
        return f"{moment:%H:%M}"
    if abs((today.date() - moment.date()).days) < 7:
        return f"{moment:%a}"
    year = f" {moment.year}" if moment.year != today.year else ""
    return f"{moment.day} {moment:%b}{year}"


def _count(n: int, one: str, many: str) -> str:
    return f"{quantity(n)} {one if n == 1 else many}"


def _tidy(text: object, limit: int) -> str:
    """A name from the owner's data on one line, printable, cut to ``limit`` characters."""
    text = " ".join("".join(ch if ch.isprintable() else " " for ch in str(text or "")).split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


# ---- why a screen could not judge an instrument ------------------------------------------------------

#: What a missing value is called in "no ... yet".
_NOUNS = {
    "prev_close": "previous close",
    "oi": "open interest",
    "vwap": "VWAP",
    "year_high": "52-week high",
    "year_low": "52-week low",
    "spot": "spot price",
    "iv": "implied volatility",
    "option price": "option price",
    "India VIX": "India VIX value",
    "opening range": "opening range",
    "index": "index prices",
    "earlier option values": "earlier option values",
}
_OI_READINGS = {"buildup": "build-up", "oi_change_pct": "open interest change"}
_UNKNOWN = "could not be checked this time"


def _reason(why: T.Why) -> str:
    """The reason as a phrase that follows 'Not checked: ' (no capital, no full stop)."""
    code, detail = why.code, tuple(why.detail)
    try:
        if code == T.WARMING:
            need, have = detail
            return f"warming up ({quantity(have)} of {_count(need, 'candle', 'candles')} so far)"
        if code == T.NO_CANDLE:
            return "no candle to read yet"
        if code == T.NO_DATA:
            (name,) = detail
            noun = _NOUNS.get(name)
            return f"no {noun} yet" if noun else "a value it needs is not available yet"
        if code == T.ZERO:
            return "a value is zero, so a ratio cannot be worked out"
        if code == T.NOT_A_NUMBER:
            return "the indicator gave no number here"
        if code == T.PAUSED:
            (start,) = detail
            return f"the price feed paused during the {hm(start)} candle"
        if code == T.NO_AUCTION:
            return "no closing auction price yet"
        if code == T.NO_OPTIONS:
            (underlying,) = detail
            shown = _tidy(underlying, 40)
            name = shown if shown and underlying != "self" else "this instrument"
            return f"option strikes of {name} are not followed"
        if code == T.SHORT_STORE:
            have, need = detail
            return f"needs {_count(need, 'stored day', 'stored days')} for a rank, {quantity(have)} so far"
        if code == T.WINDOW_OPEN:
            (minutes,) = detail
            return f"the opening range (first {quantity(minutes)} min) is not complete yet"
        if code == T.OTHER_DAY:
            (reading,) = detail
            label = _OI_READINGS.get(reading, "this value")
            return f"{label} for the day is read from today's candles only"
        if code == T.NO_FEED:
            return "no price from Upstox yet"
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    return _UNKNOWN


def why_words(why: T.Why) -> str:
    """One reason as a sentence: 'Warming up (37 of 50 candles so far)'."""
    text = _reason(why)
    return text[0].upper() + text[1:]


def not_checked_words(whys: Iterable[T.Why], limit: int = REASONS_SHOWN) -> str:
    """'Not checked: warming up (37 of 50 candles so far); no VWAP yet', the same reason once."""
    reasons = list(dict.fromkeys(_reason(why) for why in whys))
    if not reasons:
        return "Not checked"
    text = "Not checked: " + "; ".join(reasons[:limit])
    if len(reasons) > limit:
        text += f"; and {len(reasons) - limit} more"
    return text


def not_checked_title(count: int) -> str:
    """The results page's expandable row: 'Not checked (12)'."""
    return f"Not checked ({quantity(count)})"


def auction_words(price_at: int) -> str:
    """A closing-auction stock's wait: 'Closing auction · price at about 15:30'."""
    return f"Closing auction · price at about {hm(price_at)}"


# ---- marks ----------------------------------------------------------------------------------------------

#: The keys of a run's ``marks`` (a JSON object): what is true of the run as a whole.
M_REPAIRED, M_LATE, M_WOULD_TELL = "repaired", "late", "would_tell"


def run_marks(
    *, repaired: bool = False, late_at: int | None = None, would_tell: bool = False
) -> dict[str, object]:
    """The ``run.marks`` object: a candle read at the run had been repaired after a pause, the run was
    made late (``late_at``, epoch ms), or it would have raised an alert (before alerts are connected)."""
    marks: dict[str, object] = {}
    if repaired:
        marks[M_REPAIRED] = True
    if late_at is not None:
        marks[M_LATE] = late_at
    if would_tell:
        marks[M_WOULD_TELL] = True
    return marks


def mark_words(marks: Mapping[str, object] | None) -> tuple[str, ...]:
    """A run's marks as the History tab shows them, in a fixed order."""
    marks = marks or {}
    out = []
    if marks.get(M_REPAIRED):
        out.append("repaired after a pause")
    late = marks.get(M_LATE)
    if late is not None:
        try:
            out.append(f"checked late at {hm(int(late))} after a restart")
        except (TypeError, ValueError, OverflowError, OSError):
            out.append("checked late after a restart")
    if marks.get(M_WOULD_TELL):
        out.append("would have raised an alert")
    return tuple(out)


def row_marks(*, forming: bool = False, repaired: bool = False) -> tuple[str, ...]:
    """What a result row says about the candles its figures came from."""
    out = []
    if forming:
        out.append("forming")
    if repaired:
        out.append("repaired after a pause")
    return tuple(out)


# ---- alerts on the page --------------------------------------------------------------------------------

PAUSED_NOTE = "Alerts paused after the first alert"
RESUME = "Resume"


def alerts_words(policy: al.Policy, *, paused: bool = False) -> str:
    """A screen card's alert line: 'Alerts on', 'Alerts off' or the pause notice."""
    if not policy.on:
        return "Alerts off"
    return PAUSED_NOTE if paused else "Alerts on"


def told_words(decision: al.Decision, policy: al.Policy) -> str:
    """Why a run did or did not raise an alert, for the History tab (never sent to Telegram)."""
    if decision.why == al.TOLD:
        entered = len(decision.entered) + decision.more_entered
        left = len(decision.exited) + decision.more_exited
        parts = []
        if entered:
            parts.append(f"{quantity(entered)} entered")
        if left:
            parts.append(f"{quantity(left)} left")
        news = ", ".join(parts) if parts else "nothing new"
        return f"Alert raised: {news}; {quantity(decision.matches)} match now"
    if decision.why == al.OFF:
        return "Alerts are off for this screen"
    if decision.why == al.PAUSED:
        return PAUSED_NOTE
    if decision.why == al.FEW:
        return (
            f"No alert: {quantity(decision.matches)} match, {quantity(policy.min_matches)} needed"
        )
    if decision.held:
        return f"No alert: nothing new ({quantity(decision.held)} already told today)"
    return "No alert: nothing new"


# ---- the engine line ------------------------------------------------------------------------------------

(
    NOT_ANSWERING,
    SWITCHED_OFF,
    WAITING_MASTER,
    CLOSED,
    FEED_DOWN,
    AUCTION,
    WARMING_UP,
    NO_PRICES,
    REAL_TIME,
) = (
    "not_answering",
    "switched_off",
    "waiting_master",
    "closed",
    "feed_down",
    "auction",
    "warming_up",
    "no_prices",
    "real_time",
)
#: The states in the order they win when more than one is true.
STATES = (
    NOT_ANSWERING,
    SWITCHED_OFF,
    WAITING_MASTER,
    CLOSED,
    FEED_DOWN,
    AUCTION,
    WARMING_UP,
    NO_PRICES,
    REAL_TIME,
)

#: The price feed's condition: ticks arriving, or why they are not.
FEED_OK = "ok"
_FEED_DOWN = {
    "reconnecting": "the price feed is reconnecting",
    "token_rejected": "Upstox refused the price feed's token: put the current Analytics Token "
    "in the feed's token file",
    "no_token": "the price feed has no token: put the Analytics Token in the feed's token file",
    "stopped": "the price feed is stopped: start sudarshan-feed again",
    "silent": "the price feed is not answering: check that sudarshan-feed is running",
}


@dataclass(frozen=True, slots=True)
class Engine:
    """What the page knows about the screener right now."""

    now: int
    #: The service answered the page's last question.
    answering: bool = True
    #: Not switched off on the server.
    enabled: bool = True
    #: Today's master contract is loaded (False only on a trading day that still lacks it).
    master_ready: bool = True
    #: A segment the screens use is trading.
    trading: bool = True
    #: While a closing auction runs: when its price is expected (epoch ms).
    auction_at: int | None = None
    #: FEED_OK, or why ticks are not arriving (a key of the table above; others read as reconnecting).
    feed: str = FEED_OK
    feed_since: int | None = None
    #: Instruments whose history is still loading, out of how many.
    loading: int = 0
    total: int = 0
    #: Instruments no price has arrived for yet.
    without_prices: int = 0
    #: The screens' next check (epoch ms).
    next_at: int | None = None


def engine_state(engine: Engine) -> str:
    """The one state the line shows, by the order in STATES."""
    if not engine.answering:
        return NOT_ANSWERING
    if not engine.enabled:
        return SWITCHED_OFF
    if not engine.master_ready:
        return WAITING_MASTER
    if not engine.trading:
        return CLOSED
    if engine.feed != FEED_OK:
        return FEED_DOWN
    if engine.auction_at is not None:
        return AUCTION
    if engine.loading > 0:
        return WARMING_UP
    if engine.without_prices > 0:
        return NO_PRICES
    return REAL_TIME


def engine_words(engine: Engine) -> str:
    """The line under the Screener header, e.g. 'Real-time · next check 10:45'."""
    state = engine_state(engine)
    then = ""
    if engine.next_at is not None:
        then = f" · next check {when(engine.next_at, engine.now)}"
    if state == NOT_ANSWERING:
        return "The screener is not answering"
    if state == SWITCHED_OFF:
        return "Switched off on the server"
    if state == WAITING_MASTER:
        return "Waiting for today's master contract"
    if state == CLOSED:
        return f"Market closed{then}"
    if state == FEED_DOWN:
        since = f" (since {hms(engine.feed_since)})" if engine.feed_since is not None else ""
        return f"Not checking · {_FEED_DOWN.get(engine.feed, _FEED_DOWN['reconnecting'])}{since}"
    if state == AUCTION:
        return auction_words(engine.auction_at)
    if state == WARMING_UP:
        count = _count(engine.total, "instrument", "instruments")
        return f"Warming up · history loading for {quantity(engine.loading)} of {count}"
    if state == NO_PRICES:
        return f"No prices from Upstox for {_count(engine.without_prices, 'instrument', 'instruments')} yet"
    return f"Real-time{then}"


# ---- the list: what a row says about its age ---------------------------------------------------------------

CHIP_NEW, CHIP_SINCE, CHIP_MISSING, CHIP_PINNED, CHIP_REMOVED = (
    "new",
    "since",
    "missing",
    "pinned",
    "removed",
)
#: Tones, mapped by the page to the D tokens: brand ink on its tint, quiet grey, amber, outlined.
TONE_BRAND, TONE_QUIET, TONE_AMBER, TONE_OUTLINE = "brand", "quiet", "amber", "outline"


@dataclass(frozen=True, slots=True)
class Chip:
    """The small tag on a list row: always an icon and a word, never colour alone."""

    kind: str
    text: str
    #: A lucide icon name.
    icon: str
    tone: str
    #: What a screen reader says.
    name: str
    #: A second line for a row that needs one.
    note: str = ""


def chip(row: mem.Row, *, now: int, last_run: int, grace: mem.Grace, screen: str) -> Chip | None:
    """The chip for a row of a screen's list, or None (a row the owner added, or one that left).

    ``last_run`` is the id of the screen's latest run: a row that entered at it is new.
    """
    by = _tidy(screen, 40) or "this screen"
    if row.status == mem.DISMISSED:
        return Chip(
            CHIP_REMOVED,
            "Removed by you",
            "ban",
            TONE_QUIET,
            f"You removed it from the list; {by} will not add it again while it keeps matching",
        )
    if row.status == mem.EXITED:
        return None
    if row.pinned:
        note = ""
        if row.status == mem.MISSING:
            note = f"Last matched {when(row.last_seen_at, now)}"
        return Chip(
            CHIP_PINNED,
            "Pinned",
            "pin",
            TONE_OUTLINE,
            f"Pinned: stays in the list whatever {by} finds",
            note,
        )
    if not row.added_by_screener:
        return None
    if row.status == mem.MISSING:
        return _missing(row, grace, by)
    entered = f"Entered {_entered(row.first_seen_at, now)} by {by}"
    if row.first_seen_run == last_run:
        return Chip(CHIP_NEW, f"New · {hm(row.first_seen_at)}", "circle-plus", TONE_BRAND, entered)
    since = day_words(row.first_seen_at, now)
    return Chip(CHIP_SINCE, f"Since {since}", "clock", TONE_QUIET, entered)


def _entered(at: int, now: int) -> str:
    """'at 10:45', 'on Mon at 10:45', 'on 12 Oct at 10:45'."""
    if _moment(at).date() == _moment(now).date():
        return f"at {hm(at)}"
    return f"on {day_words(at, now)} at {hm(at)}"


def _missing(row: mem.Row, grace: mem.Grace, by: str) -> Chip:
    base = f"Not matching {by} at the last run"
    if grace.mode == mem.RUNS:
        text = f"Missing {row.misses} of {grace.n}"
        name = f"{base}; {_count(grace.n, 'run is', 'runs are')} allowed before it leaves"
    elif grace.mode == mem.DAY_END:
        text, name = "Missing · leaves at day end", f"{base}; it leaves at the end of the day"
    else:
        text, name = "Missing · stays in list", f"{base}; it stays until you remove it"
    return Chip(CHIP_MISSING, text, "hourglass", TONE_AMBER, name)


def exited_words(exits: Sequence[tuple[str, int]], limit: int = EXITS_SHOWN) -> str:
    """Under a target list: 'Exited today: INFY 11:30, TCS 12:15' (symbol and exit time, in order)."""
    if not exits:
        return ""
    shown = ", ".join(f"{_tidy(symbol, 24)} {hm(at)}" for symbol, at in exits[:limit])
    text = f"Exited today: {shown}"
    if len(exits) > limit:
        text += f" and {len(exits) - limit} more"
    return text
