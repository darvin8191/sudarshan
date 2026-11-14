"""Every line the family bot and the family's notifications say, in English and Gujarati (M16.4,
M1.15, M20.5; design 2.6).

One row per line id: ``(English, Gujarati)``. A member reads the column of their language (the
owner sets it on the member card, the member in Settings or with ``/language``); the app itself
stays in English. A test holds every row to both columns, the same ``{facts}`` in each, and no
icon or emoji in either. The name is "सुदर्शन / SUDARSHAN · Family" in both: it is the bot's name,
not a sentence. Numbers are written the Indian way (1,23,456.50) with a true minus (U+2212), in
the same digits for both languages, as the member's apps show prices.

Nothing here does I/O; nothing here names a member.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sudarshan.autologin.schedule import IST

LANGUAGES = ("en", "gu")
HEADER = "सुदर्शन / SUDARSHAN · Family"
MINUS = "−"
SEP = " · "

LINES: dict[str, tuple[str, str]] = {
    # ---- linking ----
    "link_first": (
        "Open सुदर्शन / SUDARSHAN on your phone, Settings, Link Telegram, and tap the link there.",
        "તમારા ફોનમાં सुदर्शन / SUDARSHAN ખોલો, Settings માં Link Telegram દબાવો, અને ત્યાંની લિંક ખોલો.",
    ),
    "linked": (
        "This chat is now linked to your family account. Send /help to see what it shows.",
        "આ ચેટ હવે તમારા ફેમિલી એકાઉન્ટ સાથે જોડાઈ ગઈ છે. શું જોઈ શકાય તે જાણવા /help મોકલો.",
    ),
    "already_linked": (
        "This chat is already linked to a family account. Send /stop first to link it again.",
        "આ ચેટ પહેલેથી જ એક ફેમિલી એકાઉન્ટ સાથે જોડાયેલી છે. ફરી જોડવા માટે પહેલાં /stop મોકલો.",
    ),
    "unlinked": (
        "This chat is no longer linked to सुदर्शन / SUDARSHAN.",
        "આ ચેટ હવે सुदर्शन / SUDARSHAN સાથે જોડાયેલી નથી.",
    ),
    # ---- the one answer to anything outside the table ----
    "refusal": (
        "This bot only shows information. It cannot place, change or cancel an order.",
        "આ બોટ માત્ર માહિતી બતાવે છે. તે ઓર્ડર મૂકી, બદલી કે રદ કરી શકતો નથી.",
    ),
    "help": (
        "/status: the market and the indices\n"
        "/positions: the account owner's positions\n"
        "/holdings: the account owner's holdings\n"
        "/orders: today's orders, when they are shared with you\n"
        "/portfolio: the portfolios shared with you\n"
        "/quiet on or /quiet off: quiet hours for the daily summary\n"
        "/language en or /language gu: English or Gujarati\n"
        "/stop: unlink this chat",
        "/status: બજાર અને ઇન્ડેક્સ\n"
        "/positions: એકાઉન્ટ માલિકની પોઝિશન\n"
        "/holdings: એકાઉન્ટ માલિકના હોલ્ડિંગ્સ\n"
        "/orders: આજના ઓર્ડર, જો તમારી સાથે શેર કરેલા હોય\n"
        "/portfolio: તમારી સાથે શેર કરેલા પોર્ટફોલિયો\n"
        "/quiet on અથવા /quiet off: રોજના સારાંશ માટે શાંત સમય\n"
        "/language en અથવા /language gu: અંગ્રેજી કે ગુજરાતી\n"
        "/stop: આ ચેટનું જોડાણ દૂર કરો",
    ),
    "paused": (
        "The account owner has paused your family account. Nothing is shown until it is resumed.",
        "એકાઉન્ટ માલિકે તમારું ફેમિલી એકાઉન્ટ અટકાવ્યું છે. ફરી શરૂ થાય ત્યાં સુધી કંઈ બતાવાશે નહીં.",
    ),
    # ---- what a book answer can say instead of figures ----
    "not_shared": ("Not shared with you.", "તમારી સાથે શેર કરેલું નથી."),
    "orders_hidden": ("Orders are not shared with you.", "ઓર્ડર તમારી સાથે શેર કરેલા નથી."),
    "not_ready": (
        "This part of the family view is not ready yet.",
        "ફેમિલી વ્યૂનો આ ભાગ હજી તૈયાર નથી.",
    ),
    "unreachable": (
        "The account cannot be read right now. Try again in a minute.",
        "અત્યારે એકાઉન્ટ વાંચી શકાતું નથી. એક મિનિટ પછી ફરી પ્રયત્ન કરો.",
    ),
    "portfolio_off": (
        "The portfolio service is switched off on the server.",
        "સર્વર પર પોર્ટફોલિયો સેવા બંધ છે.",
    ),
    "figures_from": ("Figures from {time}.", "{time} ના આંકડા."),
    "closed_from": ("Market closed · figures from {time}.", "બજાર બંધ · {time} ના આંકડા."),
    "budget_from": (
        "Figures from {time}. They refresh in a minute.",
        "{time} ના આંકડા. એક મિનિટમાં તાજા થશે.",
    ),
    "no_figures": (
        "No figures yet. They refresh in a minute.",
        "હજી આંકડા નથી. એક મિનિટમાં આવશે.",
    ),
    # ---- the book answers ----
    "positions": ("Positions", "પોઝિશન"),
    "positions_none": ("No open positions.", "કોઈ ખુલ્લી પોઝિશન નથી."),
    "holdings": ("Holdings", "હોલ્ડિંગ્સ"),
    "holdings_none": ("No holdings.", "કોઈ હોલ્ડિંગ નથી."),
    "orders": ("Orders today", "આજના ઓર્ડર"),
    "orders_none": ("No orders today.", "આજે કોઈ ઓર્ડર નથી."),
    "portfolio": ("Shared portfolios", "શેર કરેલા પોર્ટફોલિયો"),
    "portfolio_none": (
        "No portfolio is shared with you yet. Ask the account owner.",
        "હજી કોઈ પોર્ટફોલિયો તમારી સાથે શેર કરેલો નથી. એકાઉન્ટ માલિકને પૂછો.",
    ),
    "more_rows": (
        "and {count} more: open the app to see them all.",
        "અને બીજા {count}: બધા જોવા એપ ખોલો.",
    ),
    "long": ("Long", "લોંગ"),
    "short": ("Short", "શોર્ટ"),
    "closed": ("Closed", "બંધ"),
    "buy": ("Buy", "ખરીદી"),
    "sell": ("Sell", "વેચાણ"),
    "qty": ("Qty {qty}", "જથ્થો {qty}"),
    "pnl": ("P&L {amount}", "નફો/નુકસાન {amount}"),
    "value": ("Value {amount}", "મૂલ્ય {amount}"),
    "today": ("Today {amount}", "આજે {amount}"),
    # ---- /status ----
    "realtime": ("Real-time", "રિયલ-ટાઇમ"),
    "reconnecting": ("Reconnecting", "ફરી જોડાઈ રહ્યું છે"),
    "market_open": ("Market open", "બજાર ખુલ્લું"),
    "market_closed": ("Market closed", "બજાર બંધ"),
    "indices_unavailable": (
        "Index prices are not available right now.",
        "અત્યારે ઇન્ડેક્સના ભાવ ઉપલબ્ધ નથી.",
    ),
    "next_day": ("Next trading day: {day}", "આગળનો ટ્રેડિંગ દિવસ: {day}"),
    # ---- /quiet and /language ----
    "quiet_on": (
        "Quiet hours are on: {start} to {end}. Sign-in notices still arrive.",
        "શાંત સમય ચાલુ છે: {start} થી {end}. સાઇન-ઇન સૂચનાઓ તો પણ આવશે.",
    ),
    "quiet_off": (
        "Quiet hours are off. The daily summary arrives when it is ready.",
        "શાંત સમય બંધ છે. રોજનો સારાંશ તૈયાર થાય ત્યારે આવશે.",
    ),
    "quiet_usage": ("Send /quiet on or /quiet off.", "/quiet on અથવા /quiet off મોકલો."),
    "language_set": (
        "Messages from this bot are now in English.",
        "આ બોટના સંદેશા હવે ગુજરાતીમાં આવશે.",
    ),
    "language_usage": (
        "Send /language en for English or /language gu for Gujarati.",
        "અંગ્રેજી માટે /language en અથવા ગુજરાતી માટે /language gu મોકલો.",
    ),
    # ---- the daily summary (F10) ----
    "digest_title": ("Today's summary · {day}", "આજનો સારાંશ · {day}"),
    "digest_positions": (
        "Positions: {open} open · {long} long · {short} short",
        "પોઝિશન: {open} ખુલ્લી · {long} લોંગ · {short} શોર્ટ",
    ),
    "digest_positions_none": ("Positions: none open", "પોઝિશન: કોઈ ખુલ્લી નથી"),
    "digest_holdings": ("Holdings today: {amount}", "હોલ્ડિંગ્સ આજે: {amount}"),
    "digest_trades": ("Trades today: {count}", "આજના ટ્રેડ: {count}"),
    "digest_won_lost": ("won {won} · lost {lost}", "નફામાં {won} · નુકસાનમાં {lost}"),
    # ---- sign-in notices to the member (never quiet) ----
    "notice_new_device": (
        "A new phone signed in to your family account: {device}, {time}. Not you? Sign out "
        "everywhere in Settings and tell the account owner.",
        "તમારા ફેમિલી એકાઉન્ટમાં નવા ફોનથી સાઇન-ઇન થયું: {device}, {time}. તમે નથી? Settings માં "
        "Sign out everywhere દબાવો અને એકાઉન્ટ માલિકને જણાવો.",
    ),
    "notice_lockout": (
        "Your family sign-in was locked after {attempts} wrong attempts. Try again at {time}. "
        "Not you? Tell the account owner.",
        "{attempts} ખોટા પ્રયત્નો પછી તમારું ફેમિલી સાઇન-ઇન અટકાવાયું છે. {time} પછી ફરી પ્રયત્ન "
        "કરો. તમે નથી? એકાઉન્ટ માલિકને જણાવો.",
    ),
    # ---- the test send (F10) ----
    "test_push": (
        "This is a test. Notifications from the family view reach this phone.",
        "આ એક ટેસ્ટ છે. ફેમિલી વ્યૂની સૂચનાઓ આ ફોન પર આવે છે.",
    ),
    "test_telegram": (
        "This is a test. Messages from the family view reach this chat.",
        "આ એક ટેસ્ટ છે. ફેમિલી વ્યૂના સંદેશા આ ચેટમાં આવે છે.",
    ),
}

DAYS = (
    ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    ("સોમ", "મંગળ", "બુધ", "ગુરુ", "શુક્ર", "શનિ", "રવિ"),
)
MONTHS = (
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
    ("જાન્યુ", "ફેબ્રુ", "માર્ચ", "એપ્રિલ", "મે", "જૂન", "જુલાઈ", "ઑગસ્ટ", "સપ્ટે", "ઑક્ટો", "નવે",
     "ડિસે"),
)  # fmt: skip


def column(language: str | None) -> int:
    """0 for English, 1 for Gujarati; anything else reads English."""
    return 1 if language == "gu" else 0


def say(line: str, language: str | None = "en", **facts: object) -> str:
    """Line ``line`` in the member's language, with ``facts`` put in."""
    text = LINES[line][column(language)]
    return text.format(**facts) if facts else text


def both(line: str, **facts: object) -> str:
    """Line ``line`` in English, then Gujarati: for a chat whose member is not known yet."""
    return "\n".join(text.format(**facts) if facts else text for text in LINES[line])


# ---- numbers, money, times and days --------------------------------------------------------------
def grouped(number: int) -> str:
    """Indian digit grouping of a whole number: 3,993; 1,00,000; 24,19,200."""
    digits = str(abs(int(number)))
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while len(head) > 2:
        head, groups = head[:-2], [head[-2:], *groups]
    text = ",".join([part for part in (head, *groups) if part] + [tail])
    return (MINUS if number < 0 else "") + text


def _rupees(paise: int) -> str:
    whole, part = divmod(abs(int(paise)), 100)
    return f"₹{grouped(whole)}.{part:02d}"


def money(paise: int) -> str:
    """A signed amount of money: +₹1,234.50, −₹1,234.50, ₹0.00."""
    sign = "+" if paise > 0 else MINUS if paise < 0 else ""
    return sign + _rupees(paise)


def _half_away(numerator: int, denominator: int) -> int:
    whole, rest = divmod(abs(numerator), denominator)
    if 2 * rest >= denominator:
        whole += 1
    return whole if numerator >= 0 else -whole


def price(price_u: int) -> str:
    """A price (rupees x 10^4, the portfolio's ``*_price_u``) to the paisa: ₹1,010.05."""
    return _rupees(_half_away(int(price_u), 100))


def quantity(qty_u: int) -> str:
    """A quantity (units x 10^6, the portfolio's ``qty_u``) without its sign: 100, 109.459."""
    whole, part = divmod(abs(int(qty_u)), 1_000_000)
    if not part:
        return grouped(whole)
    return f"{grouped(whole)}.{f'{part:06d}'.rstrip('0')}"


def percent(value: float) -> str:
    """A signed percentage to two places: +0.42%, −1.05%."""
    sign = "+" if value > 0 else MINUS if value < 0 else ""
    return f"{sign}{abs(value):.2f}%"


def level(value: float) -> str:
    """An index level or a price given in rupees: 24,310.55."""
    hundredths = round(abs(float(value)) * 100)
    whole, part = divmod(hundredths, 100)
    return (MINUS if value < 0 else "") + f"{grouped(whole)}.{part:02d}"


def clock(at_ms: int) -> str:
    """An instant as a member reads it in India: 15:40:12."""
    return datetime.fromtimestamp(at_ms / 1000, UTC).astimezone(IST).strftime("%H:%M:%S")


def minutes(at_ms: int) -> str:
    """An instant to the minute: 19:05."""
    return datetime.fromtimestamp(at_ms / 1000, UTC).astimezone(IST).strftime("%H:%M")


def day_words(day: date, language: str | None = "en") -> str:
    """A day as the summary names it: Mon 16 Nov, સોમ 16 નવે."""
    side = column(language)
    return f"{DAYS[side][day.weekday()]} {day.day} {MONTHS[side][day.month - 1]}"
