"""What the owner reads about market alerts (M10.5): Telegram lines and the app's words. Pure.

English under the सुदर्शन / SUDARSHAN header, as order alerts are: no icons, no emojis, no codes.
A price has two decimals (the engine counts in paise) with Indian grouping; a traded instrument's
prices are in rupees (₹1,428.25), an index's are plain points (25,903.15).
The owner's note is cleaned of Markdown characters (messages.clean): OpenAlgo sends with
parse_mode Markdown and does not escape.

    सुदर्शन / SUDARSHAN
    Alert: NIFTY crossed above 25,900.00: now 25,903.15 (index)
    Alert: RELIANCE up 2.03% on the day: ₹1,428.25 (previous close ₹1,399.80) (NSE)
    Alert: SBIN broke today's high ₹812.40: now ₹812.55 (NSE) · Note: book half
    10:42:05 IST

A fire's words come in two halves, ``describe`` -> (title, detail): the app's toast shows them,
and ``line`` joins them for Telegram with the exchange, the pause mark and the note.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sudarshan.alerts.market import book, clock, conditions, engine
from sudarshan.alerts.market.grain import edge_up, rupees, to_decimal
from sudarshan.alerts.market.sessions import INDEX_SEGMENTS
from sudarshan.alerts.market.spec import (
    AT_CLOSE,
    CROSS,
    DAY,
    DOWN,
    ONCE,
    OP_WORDS,
    PER_CANDLE,
    UP,
    Alert,
)
from sudarshan.alerts.messages import SAME_MOMENT, clean, instrument, quantity
from sudarshan.autologin.messages import BRAND
from sudarshan.autologin.schedule import IST

NOTE_MAX = 120
#: A pause gets its Telegram lines only when it lasts longer than this inside the session.
PAUSE_TELL_MS = 60_000
TEST_LINE = "Test: market alerts reach this chat (sent from the Alerts page)."
TEST_TITLE = "Test alert"
TEST_DETAIL = "sent from the Alerts page"
WATCHING = "Watching · Real-time"
STARTING = "Starting up"
SWITCHED_OFF = "Switched off on the server"
STOPPED_UNEXPECTEDLY = (
    "Market alerts stopped after an unexpected failure. Order alerts carry on; restart "
    "sudarshan-alerts to start them again."
)
_PAST = {"feed": "the price feed was reconnecting", "service": "market alerts were not running"}
_NOW = {"feed": "the price feed is reconnecting", "service": "market alerts are not running"}


def amount(alert: Alert, paise: int) -> str:
    """'₹1,428.25' for a traded instrument, '25,903.15' (points) for an index."""
    return _number(rupees(paise), "" if alert.segment in INDEX_SEGMENTS else "₹")


def _number(value: Decimal, sign: str = "") -> str:
    whole, _, frac = str(abs(value)).partition(".")
    text = quantity(int(whole)) + (f".{frac}" if frac else "")
    return f"{'-' if value < 0 else ''}{sign}{text}"


def _hm(at: int) -> str:
    return datetime.fromtimestamp(at / 1000, IST).strftime("%H:%M")


def _hms(at: int) -> str:
    return datetime.fromtimestamp(at / 1000, IST).strftime("%H:%M:%S")


def _day(at: int) -> str:
    moment = datetime.fromtimestamp(at / 1000, IST)
    return f"{moment.day} {moment:%b}"


def describe(alert: Alert, fire: engine.Fire, today: date) -> tuple[str, str]:
    """The fire's two halves: (title, detail), e.g. ('NIFTY crossed above 25,900.00',
    'now 25,903.15')."""
    name = instrument(alert.symbol, today)
    way = "above" if fire.direction == UP else "below"
    level, value = amount(alert, fire.level), amount(alert, fire.value)
    closed = alert.frequency == AT_CLOSE
    lead = _close_lead(alert, name) if closed else name
    if fire.auction:  # the closing auction's print, never passed off as an ordinary trade
        return _auction(alert, fire, name, way, level, value)
    if alert.kind == "move":
        rise = "up" if fire.direction == UP else "down"
        on, base = ("on the day", "previous close")
        if alert.params["base"] == "open":
            on, base = ("from today's open", "open")
        if fire.base is None:  # not known: say the alert's own threshold
            return f"{lead} {rise} {alert.params['percent']}% {on}", f"now {value}"
        detail = f"{value} ({base} {amount(alert, fire.base)})"
        share = _share(alert, fire)
        return f"{lead} {rise} {share}% {on}", (_closing(alert) + detail) if closed else detail
    if alert.kind == "day_break":
        side = "high" if fire.direction == UP else "low"
        broke = f"{name} broke today's {side} {amount(alert, fire.base)}"
        if fire.found_later is not None:
            return f"{broke} while market alerts were paused", f"today's {side} is now {value}"
        if fire.price is not None and fire.price != fire.value:
            last = amount(alert, fire.price)
            return broke, f"today's {side} is now {value}, last trade {last}"
        return broke, f"now {value}"
    if alert.kind == "vwap":
        title = f"{lead} {way} VWAP {level}" if closed else f"{name} crossed {way} VWAP {level}"
        return title, _close_detail(alert, fire, value) if closed else f"now {value}"
    quote = alert.cond.source if alert.cond.source in ("bid", "ask") else ""
    source = f"{quote} " if quote else ""
    if fire.opening:
        return (
            f"{name} {source}opened {way} {level}",
            f"first {quote or 'price'} {value} (previous {amount(alert, fire.previous)})",
        )
    verb = f"crossed {way}" if alert.cond.mode == CROSS else f"is at or {way}"
    if closed:
        verb = way if alert.cond.mode == CROSS else f"at or {way}"
        return f"{lead} {verb} {level}", _close_detail(alert, fire, value)
    return f"{name} {source}{verb} {level}", f"now {value}"


def _auction(alert: Alert, fire: engine.Fire, name: str, way: str, level: str, value: str):
    """'SBIN crossed above ₹100.00 in the closing auction', 'closing auction price ₹100.20'.

    At candle close the print is the last candle's close: 'SBIN 15-min candle closed above
    ₹100.00 in the closing auction', and for the day 'SBIN auction close above ₹100.00'."""
    detail, where = f"closing auction price {value}", " in the closing auction"
    closed = alert.frequency == AT_CLOSE
    if closed and alert.candle_min == DAY:
        name, where = f"{name} auction close", ""
    elif closed:
        name = f"{name} {alert.candle_min}-min candle closed"
        if fire.span is not None:
            detail += f" ({_hm(fire.span[0])} to {_hm(fire.span[1])})"
    if alert.kind == "move":
        rise = "up" if fire.direction == UP else "down"
        on, base = ("on the day", "previous close")
        if alert.params["base"] == "open":
            on, base = ("from today's open", "open")
        if fire.base is None:
            return f"{name} {rise} {alert.params['percent']}% {on}{where}", detail
        title = f"{name} {rise} {_share(alert, fire)}% {on}{where}"
        return title, f"{detail} ({base} {amount(alert, fire.base)})"
    if closed:
        verb = way if alert.cond.mode == CROSS else f"at or {way}"
    else:
        verb = f"crossed {way}" if alert.cond.mode == CROSS else f"closed at or {way}"
    return f"{name} {verb} {level}{where}", detail


def _close_lead(alert: Alert, name: str) -> str:
    """The Day candle judges the session's last trade, which is not the exchange's closing
    price (the last half hour's weighted average) and can be on the other side of the level."""
    if alert.candle_min == DAY:
        return f"{name} ended the day's trading"
    return f"{name} {alert.candle_min}-min candle closed"


def _closing(alert: Alert) -> str:
    return "last trade " if alert.candle_min == DAY else "close "


def _close_detail(alert: Alert, fire: engine.Fire, value: str) -> str:
    if fire.span is None:
        return f"{_closing(alert)}{value}"
    start, end = fire.span
    return f"{_closing(alert)}{value} ({_hm(start)} to {_hm(end)})"


def _share(alert: Alert, fire: engine.Fire) -> str:
    """The move from the base in percent, two decimals: 1428.25 on 1399.80 -> '2.03'."""
    base = rupees(fire.base)
    move = abs(rupees(fire.value) - base) / base * 100
    return _number(move.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def line(alert: Alert, fire: engine.Fire, today: date) -> str:
    """One fire on Telegram: 'Alert: NIFTY crossed above 25,900.00: now 25,903.15 (index)'."""
    title, detail = describe(alert, fire, today)
    tag = "index" if alert.segment in INDEX_SEGMENTS else clean(alert.exchange, 12).upper()
    mark = ""
    if fire.found_later is not None and alert.kind != "day_break":
        start, end = fire.found_later
        mark = f" (seen after a pause in market alerts, {_hm(start)} to {_hm(end)})"
    return f"Alert: {title}: {detail} ({tag}){mark}{note(alert)}"


def note(alert: Alert) -> str:
    """' · Note: book half', or '' without a note."""
    text = clean(alert.note, NOTE_MAX)
    return f" · Note: {text}" if text else ""


def message(lines: list[str], first: datetime, last: datetime, dropped: int = 0) -> str:
    """The market lane's Telegram message: the header, its lines, then the IST time or range."""
    first, last = first.astimezone(IST), last.astimezone(IST)
    when = f"{first:%H:%M:%S}"
    if last - first >= SAME_MOMENT:
        when = f"{first:%H:%M:%S} to {last:%H:%M:%S}"
    parts = [BRAND, *lines]
    if dropped:
        parts.append(
            f"{quantity(dropped)} more market alerts could not be listed here. "
            "The Alerts page has them."
        )
    parts.append(f"{when} IST")
    return "\n".join(parts)


def tells_pause(start: int, end: int, active: int) -> bool:
    """A pause in checking gets its two Telegram lines only when it is long and alerts wait."""
    return active > 0 and end - start > PAUSE_TELL_MS


def pause_started(start: int, cause: str = "feed") -> str:
    return f"Market alerts paused since {_hms(start)} IST: {_NOW.get(cause, _NOW['feed'])}."


def pause_ended(start: int, end: int, cause: str = "feed") -> str:
    why = _PAST.get(cause, _PAST["feed"])
    return (
        f"Market alerts paused {_hms(start)} to {_hms(end)} IST: {why}. A level crossed and "
        "crossed back inside that time was not seen."
    )


def status_reason(reason: engine.Reason) -> str:
    """Why an alert stopped or paused itself, as its card says it."""
    if reason.code == "fired_once":
        return f"Fired once at {_hm(reason.at)} on {_day(reason.at)}"
    if reason.code == "ended":
        last = reason.at - 1
        if _hm(reason.at) == "00:00":
            return f"Ended on {_day(last)}"
        return f"Ended at {_hm(reason.at)} on {_day(reason.at)}"
    if reason.code == "expired":
        return f"Contract expired on {_day(reason.at)}"
    if reason.code == "fire_limit":
        return f"Fired {engine.FIRE_LIMIT} times today; paused so Telegram is not flooded"
    raise ValueError(f"not a stop reason: {reason.code!r}")


_NOTES = {
    conditions.WAITING_CLOSE: "Waiting for the previous close",
    conditions.WAITING_OPEN: "Waiting for today's open",
    conditions.WAITING_VWAP: "Waiting for the day's VWAP",
    conditions.UNREACHABLE: "This fall would take the price below 0.01, so it cannot happen today",
    book.BASE_CHANGED: "The previous close changed, so the level was worked out again",
}


def note_words(reason: engine.Reason) -> str:
    """An alert's note for its card: what it waits for, or a candle it could not judge."""
    if reason.code == engine.NOT_CHECKED:
        return f"Not checked: the price feed paused during the {_hm(reason.at)} candle"
    if reason.code == engine.NOT_JUDGED:
        return f"Not checked: the close of the {_hm(reason.at)} candle on {_day(reason.at)}"
    return _NOTES[reason.code]


#: Why the feed is down, by the state its last health message gave, with what to check.
_FEED_DOWN = {
    "token_rejected": "Upstox refused the price feed's token: put the current Analytics Token "
    "in the feed's token file",
    "no_token": "the price feed has no token: put the Analytics Token in the feed's token file",
    "stopped": "the price feed is stopped: start sudarshan-feed again",
    "closed": "the price feed's calendar says the market is closed",
}


def check_words(
    code: str,
    *,
    now: int,
    opens_at: int | None = None,
    since: int | None = None,
    feed: clock.Feed | None = None,
    auction: tuple[int, int] | None = None,
    quotes: bool = False,
) -> str:
    """The page's status line for a segment (clock.status), never a trade's gate result.

    ``opens_at`` is the next session's start (sessions.next_session), ``since`` when the feed
    went down, ``feed`` its last health: a feed that is stopped or refused needs the owner, so
    it is not called "reconnecting". ``auction`` is a CAS key's (from, close): its continuous
    end and when its price is expected (Session.end, Session.auction_close: "price at about
    15:30", X6j); ``quotes`` an alert on its bid or ask."""
    if code == clock.CLOSING_AUCTION and auction is not None:
        if quotes:
            return f"Closing auction · bid and ask are not checked after {_hm(auction[0])}"
        return f"Closing auction · price at about {_hm(auction[1])}"
    then = f" · checks from {_when(opens_at, now)}" if opens_at is not None else ""
    if code == clock.CLOSED_TODAY:
        return f"Market closed today{then}"
    if code == clock.OUTSIDE_HOURS:
        return f"Market closed{then}"
    if code == clock.NOT_OPEN_YET:
        return "The exchange has not opened this segment yet"
    if code == clock.CLOSED_BY_EXCHANGE:
        return "Closed by the exchange"
    if code == clock.FEED_DOWN:
        when = f" since {_hms(since)}" if since is not None else ""
        return f"Not checking{when} · {_feed_down(feed, now)}"
    return WATCHING


def _feed_down(feed: clock.Feed | None, now: int) -> str:
    heard = feed is not None and feed.heard_at is not None
    if not heard or now - feed.heard_at > clock.HEALTH_MAX_AGE_MS:
        return "the price feed is not answering: check that sudarshan-feed is running"
    return _FEED_DOWN.get(feed.state, "the price feed is reconnecting")


def _when(at: int, now: int) -> str:
    """'14:20' later today, 'Mon 09:15' within a week, '9 Nov 09:15' further on."""
    moment, today = datetime.fromtimestamp(at / 1000, IST), datetime.fromtimestamp(now / 1000, IST)
    if moment.date() == today.date():
        return f"{moment:%H:%M}"
    if moment.date() - today.date() < timedelta(days=7):
        return f"{moment:%a %H:%M}"
    return f"{moment.day} {moment:%b %H:%M}"


def checks_at(alert: Alert) -> str | None:
    """'Checks at ₹100.05 (nearest tick)' when a fixed level is off the tick the master gave
    when the alert was set; None on it. A hint only: the engine checks the level itself."""
    cond = alert.cond
    if cond.up is None:
        return None
    level = to_decimal(alert.params["level"])
    up, down = alert.grain.up(level), alert.grain.down(level)
    if up == down:
        return None
    up, down = amount(alert, edge_up(up)), amount(alert, edge_up(down))
    if cond.direction == UP:
        return f"Checks at {up} (nearest tick)"
    if cond.direction == DOWN:
        return f"Checks at {down} (nearest tick)"
    return f"Checks at {up} going up and {down} going down (nearest ticks)"


def condition(alert: Alert) -> str:
    """The alert's check, for its card: 'Crosses above ₹1,428.25', 'Bid crosses below ₹99.50',
    'Up 2% on the day', "Breaks today's high after 09:30", 'Crosses above VWAP'."""
    p = alert.params
    if alert.kind == "price":
        check = OP_WORDS[p["op"]]
        source = p.get("source", "ltp")
        if source != "ltp":
            check = f"{source.capitalize()} {check[0].lower()}{check[1:]}"
        return f"{check} {level(alert)}"
    if alert.kind == "move":
        way = {"up": "Up", "down": "Down", "either": "Up or down"}[p["direction"]]
        on = "from today's open" if p["base"] == "open" else "on the day"
        return f"{way} {p['percent']}% {on}"
    if alert.kind == "day_break":
        return f"Breaks today's {p['side']} after {p['after']}"
    return {"above": "Crosses above VWAP", "below": "Crosses below VWAP"}.get(
        p["direction"], "Crosses VWAP"
    )


def level(alert: Alert) -> str:
    """The level as the owner set it, grouped: '₹25,900.00', or '25,900.00' points for an index
    (a level finer than a paisa keeps its digits; the "Checks at" hint says where it checks)."""
    value = to_decimal(alert.params["level"])
    if value.as_tuple().exponent >= -2:
        value = value.quantize(Decimal("0.01"))
    return _number(value, "" if alert.segment in INDEX_SEGMENTS else "₹")


def meta(alert: Alert, today: date) -> str:
    """How often, the wait between alerts and the end: 'Every time · wait 5 min · ends 20 Dec'."""
    parts = [_how_often(alert)]
    if alert.cooldown_ms:
        parts.append(f"wait {_span(alert.cooldown_ms // 1000)}")
    parts.append(_ending(alert, today))
    return " · ".join(parts)


def _how_often(alert: Alert) -> str:
    candle = alert.candle_min
    if alert.frequency == ONCE:
        return "Once"
    if alert.frequency == PER_CANDLE:
        return "Once a day" if candle == DAY else f"Once per {candle}-min candle"
    if alert.frequency == AT_CLOSE:
        return "At the day's close" if candle == DAY else f"At {candle}-min candle close"
    return "Every time"


def _span(seconds: int) -> str:
    """300 -> '5 min', 90 -> '1 min 30 s', 5400 -> '1 h 30 min'."""
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = (f"{hours} h" if hours else "", f"{minutes} min" if minutes else "",
             f"{secs} s" if secs else "")  # fmt: skip
    return " ".join(part for part in parts if part)


def _ending(alert: Alert, today: date) -> str:
    """'ends 20 Dec' (it works through that day), 'ends 20 Dec 14:30', 'until expiry 25 Nov' or
    'no end date'."""
    ends, expires = alert.ends_at, alert.expires_at
    if ends is not None and (expires is None or ends < expires):
        moment = datetime.fromtimestamp(ends / 1000, IST)
        if (moment.hour, moment.minute, moment.second) == (0, 0, 0):
            return f"ends {_on(moment.date() - timedelta(days=1), today)}"
        return f"ends {_on(moment.date(), today)} {moment:%H:%M}"
    if alert.expiry is not None:
        return f"until expiry {_on(alert.expiry, today)}"
    return "no end date"


def _on(day: date, today: date) -> str:
    """'20 Dec', with the year only when it is not this one."""
    return f"{day.day} {day:%b}" + (f" {day.year}" if day.year != today.year else "")


def quote_waiting(source: str) -> str:
    """A bid or ask alert whose quote has not come yet (review A7.i)."""
    return f"Waiting for the first {source} from the price feed"
