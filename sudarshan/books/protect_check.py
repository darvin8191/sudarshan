"""What the server checks of a Protect request before any GTT is placed or changed (M5.6).

Protect is one SINGLE sell GTT for delivery on a cash holding, sent by the Holdings page through
OpenAlgo's own GTT service (frontend/src/sudarshan/books/protect). The owner confirms a sentence
on his phone: "Sell 50 X if the price falls to 1,240". Two things were then decided after that
sentence, on the server, and neither was the owner's (v0.14.0 review, M5):

- **the side.** The Upstox mapping picked BELOW, ABOVE or IMMEDIATE from the server's own last
  price and ignored which side the page had sent. With a price that had moved since the sheet, a
  stop under the price was placed as "rises to", or sold at once. Now the request's side is the
  one placed, and when the server's last price has already reached or crossed the trigger the
  request is refused with a sentence. The server never chooses a direction for a Protect.
- **the book.** At stage ``sandbox`` a Protect goes to the sandbox whatever the app's mode is at
  that moment: a sheet opened in Analyze mode can no longer place a real GTT because the app
  went Live before Send was tapped. This can only ever move a request to the sandbox.

A Protect request is known by its label, ``strategy: "Protect"``, which the page always sends
(``STRATEGY`` in protect.ts; a test pins the two together). The side is stated by which of the two
trigger fields is filled: ``triggerprice_sl`` for "falls to", ``triggerprice_tg`` for "rises to".

No I/O here except the flag's read of the environment. Nothing is sent from here.
"""

from __future__ import annotations

from typing import Any

from . import protect_flag

STRATEGY = "Protect"
FALLS, RISES = "falls", "rises"
#: How a refusal answers (never shown): the request was understood and not placed.
REFUSED_STATUS = 409

NOT_ONE_SELL = (
    "Protect is one sell order for delivery at one price. This request is not, so nothing was "
    "placed."
)
NO_SIDE = (
    "This Protect does not say whether the price must fall or rise to its price, so nothing was "
    "placed. Open the holding again and set it once more."
)
NO_PRICE = (
    "The last price could not be read just now, so Protect was not placed. Try again in a moment."
)


def _number(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if number == number and number > 0 else 0.0  # NaN reads as not given


def is_protect(data: Any) -> bool:
    """Whether a GTT request is the page's Protect."""
    return isinstance(data, dict) and str(data.get("strategy") or "").strip() == STRATEGY


def stated_side(data: dict) -> tuple[str, float] | None:
    """The side the request states and its price: ``("falls", 1240.0)`` or ``("rises", ...)``.
    ``None`` when it states none, or both."""
    falls, rises = _number(data.get("triggerprice_sl")), _number(data.get("triggerprice_tg"))
    if falls and not rises:
        return FALLS, falls
    if rises and not falls:
        return RISES, rises
    return None


def direction(data: Any) -> str | None:
    """Upstox's ``trigger_type`` for a Protect, from the side the request states: BELOW for
    "falls to", ABOVE for "rises to". ``None`` for anything that is not a Protect stating one
    side, which the mapping then handles as it always did."""
    if not is_protect(data):
        return None
    side = stated_side(data)
    return None if side is None else ("BELOW" if side[0] == FALLS else "ABOVE")


def _money(value: float) -> str:
    return f"{value:,.2f}"


def refusal(data: Any, last_price: Any) -> str | None:
    """Why a Protect request is not placed, as a sentence the owner reads; ``None`` when it may
    go, and always ``None`` for a request that is not a Protect.

    ``last_price`` is the server's own, read for this request. A price that has reached the
    trigger, or passed it, since the owner confirmed is a refusal: placing it would sell at once,
    or wait for the price to come back the other way, and he confirmed neither.
    """
    if not is_protect(data):
        return None
    if (
        str(data.get("trigger_type") or "").upper() != "SINGLE"
        or str(data.get("action") or "").upper() != "SELL"
        or str(data.get("product") or "").upper() != "CNC"
    ):
        return NOT_ONE_SELL
    side = stated_side(data)
    if side is None:
        return NO_SIDE
    last = _number(last_price)
    if not last:
        return NO_PRICE
    way, trigger = side
    if way == FALLS and last <= trigger:
        return (
            f"The last price is {_money(last)}, already at or below {_money(trigger)}, so this "
            "would sell at once. Protect was not placed. Choose a price below the last price, "
            "or sell from the card."
        )
    if way == RISES and last >= trigger:
        return (
            f"The last price is {_money(last)}, already at or above {_money(trigger)}, so this "
            "would sell at once. Protect was not placed. Choose a price above the last price, "
            "or sell from the card."
        )
    return None


def sandbox_only(data: Any) -> bool:
    """Whether this request goes to the sandbox whatever the app's mode: a Protect while the
    switch is at ``sandbox``. True can only move a request to the sandbox, never to Upstox."""
    return is_protect(data) and protect_flag.stage() == protect_flag.SANDBOX


def owner_api_key() -> str | None:
    """The owner's API key, which the sandbox needs to know whose book it is, for a Protect the
    page changed through its session while the app was not in Analyze mode. Read inside the
    process; never stored, logged or sent anywhere."""
    from database.auth_db import get_api_key_for_tradingview
    from database.user_db import find_user_by_username

    admin = find_user_by_username()
    return get_api_key_for_tradingview(admin.username) if admin is not None else None
