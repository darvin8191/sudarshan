"""What Telegram says about an exit, an Exit all or a Cancel all, in the app's own words (M6.9).

Two places speak: the bot's /closeall reply (services/telegram_bot_service.py), which calls the
app through the openalgo SDK, and the order alert (services/telegram_alert_service.py), which
formats the app's own answer. A trader reads both on a phone and must never see a status code, an
SDK's wrapping or a JSON body; and a title must not say "Position Closed" or "All Orders
Cancelled" over a result that did not do it all.

An exit is a smart order to a position size of 0, and the request carries placeholders for the
side and quantity ("BUY", "0"): the order that went is sized from the book when it is sent. The
alert says the side and quantity sent (``sent`` in the answer, written where the order is sized:
broker/upstox/api/order_api.py and services/sandbox_service.py), or "to close the position" when
that is not known, never the placeholders; and "nothing sent", with the app's reason, when nothing
went. Only the app's own Exit (its strategy name) speaks in the app's Exit words.

Every answer that is not a success either says some of it may have gone (``not_confirmed``) or is
a refusal before anything was sent, and the titles say which ("not sent").
"""

from __future__ import annotations

import json
import re
from typing import Any

EXIT_ALL_NOT_ALL_CLOSED = "Exit all did not close every position"
EXIT_ALL_NOT_CONFIRMED_TITLE = "Exit all not confirmed"
EXIT_ALL_NOT_SENT_TITLE = "Exit all not sent"
#: One position's close that is not the app's Exit (Flow's "Close Positions" node with a symbol).
POSITION_NOT_CLOSED = "Position not closed"
POSITION_CLOSE_NOT_CONFIRMED = "Position close not confirmed"
CANCEL_ALL_NOT_ALL_CANCELLED = "Cancel all did not cancel every order"
NO_OPEN_ORDERS = "No open orders to cancel"

#: The strategy name blueprints/orders.py gives the app's own Exit of one position, in both modes.
APP_EXIT_STRATEGY = "UI Exit Position"
EXIT_SENT = "Exit sent"
EXIT_NOT_SENT = "Exit not sent"
EXIT_NOT_CONFIRMED_TITLE = "Exit not confirmed"
NO_EXIT_SENT = "No exit sent"
TO_CLOSE = "to close the position"
NOTHING_WAS_OPEN = "no position was open, so nothing was sent."
EXIT_NOT_SENT_REASON = "Nothing was sent. Check Positions and try again."

EXIT_ALL_NO_ANSWER = (
    "No answer came for Exit all: its exits may still be going through. Check Positions and "
    "Orders before sending again."
)
EXIT_ALL_UNREACHABLE = "The app could not be reached, so nothing was sent. Try again in a moment."
EXIT_ALL_NOT_CONFIRMED = (
    "Exit all is not confirmed: some exits may have gone through. Check Positions and Orders "
    "before sending it again."
)

# The SDK's own shape for every answer that is not a 200: "HTTP <code>: <body>".
_WRAPPED = re.compile(r"\AHTTP (\d{3}): (.*)\Z", re.S)
_MARKDOWN = re.compile(r"([_*`\[])")


def markdown_safe(text: str) -> str:
    """``text`` for a Telegram Markdown message, its own underscores, stars and brackets kept."""
    return _MARKDOWN.sub(r"\\\1", text)


def without_lead(sentence: str, lead: str) -> str:
    """``sentence`` without a first sentence that only repeats ``lead``, the title above it."""
    if sentence.startswith(lead + "."):
        return sentence[len(lead) + 1 :].strip() or sentence
    return sentence


def sdk_sentence(answer: Any) -> tuple[str, str]:
    """What an SDK answer to Exit all says, as (title, sentence).

    The app's own sentence when it answered; when no answer came (a timeout), when nothing reached
    it (no connection), or when something else answered for it (a page that is not the app's
    JSON), one of ours, never the transport's words.
    """
    if not isinstance(answer, dict):
        return EXIT_ALL_NOT_CONFIRMED_TITLE, EXIT_ALL_NOT_CONFIRMED
    kind = answer.get("error_type")
    if kind == "timeout_error":
        return EXIT_ALL_NOT_CONFIRMED_TITLE, EXIT_ALL_NO_ANSWER
    if kind == "connection_error":
        return EXIT_ALL_NOT_SENT_TITLE, EXIT_ALL_UNREACHABLE
    message = answer.get("message")
    message = message.strip() if isinstance(message, str) else ""
    wrapped = _WRAPPED.match(message)
    if wrapped:
        try:
            body = json.loads(wrapped.group(2))
        except ValueError:
            body = None
        inner = body.get("message") if isinstance(body, dict) else None
        if isinstance(inner, str) and inner.strip():
            # Some exits went (the app says so), none did (a refusal: a 4xx), or it is not known.
            if body.get("not_confirmed"):
                return EXIT_ALL_NOT_ALL_CLOSED, inner.strip()
            if wrapped.group(1).startswith("4"):
                return EXIT_ALL_NOT_SENT_TITLE, inner.strip()
            return EXIT_ALL_NOT_CONFIRMED_TITLE, inner.strip()
        return EXIT_ALL_NOT_CONFIRMED_TITLE, EXIT_ALL_NOT_CONFIRMED
    if not message or kind in ("http_error", "json_error", "unknown_error"):
        return EXIT_ALL_NOT_CONFIRMED_TITLE, EXIT_ALL_NOT_CONFIRMED
    return EXIT_ALL_NOT_ALL_CLOSED, message


def exit_all_failed_reply(answer: Any) -> str:
    """The bot's whole reply, in Markdown, for an Exit all that did not answer success."""
    title, sentence = sdk_sentence(answer)
    return f"*{title}*\n\n{markdown_safe(without_lead(sentence, title))}"


def exit_all_failed_line(answer: Any) -> str:
    """One Markdown line for a reply that says more than Exit all's result."""
    title, sentence = sdk_sentence(answer)
    return markdown_safe(f"{title}: {without_lead(sentence, title)}")


def _count(value: Any) -> int:
    return len(value) if isinstance(value, (list, tuple)) else 0


def alert_template(
    order_type: str, order_data: dict[str, Any], response: dict[str, Any], template: str
) -> str:
    """The order alert's title line over ``{details}``: its own words for what did not do it all."""
    succeeded = response.get("status") == "success"
    if is_app_exit(order_type, order_data):
        return f"*{app_exit_title(response)}*\n{{details}}"
    if order_type == "closeposition" and not succeeded:
        # Some exits may have gone (not_confirmed), or none did: every other answer that is
        # not a success is a refusal before anything was sent.
        went = bool(response.get("not_confirmed"))
        if order_data.get("symbol"):  # one position, closed by another surface (Flow's node)
            title = POSITION_CLOSE_NOT_CONFIRMED if went else POSITION_NOT_CLOSED
        else:  # an Exit all
            title = EXIT_ALL_NOT_ALL_CLOSED if went else EXIT_ALL_NOT_SENT_TITLE
        return f"*{title}*\n{{details}}"
    if order_type == "cancelallorder":
        if not succeeded or _count(response.get("failed_cancellations")):
            return f"*{CANCEL_ALL_NOT_ALL_CANCELLED}*\n{{details}}"
        if not _count(response.get("canceled_orders")):
            return f"*{NO_OPEN_ORDERS}*\n{{details}}"
    return template


def close_failed_line(order_data: dict[str, Any], response: dict[str, Any]) -> str:
    """The alert's line for an Exit all that did not succeed: its own sentence, no "Error:".

    One position's exit has its own lines (``app_exit_lines``).
    """
    message = str(response.get("message") or "").strip()
    return markdown_safe(without_lead(message, EXIT_ALL_NOT_ALL_CLOSED) or EXIT_ALL_NOT_CONFIRMED)


def cancel_all_failed_line(response: dict[str, Any]) -> str:
    """The alert's line for a Cancel all that did not go through: the app's own reason."""
    message = str(response.get("message") or "").strip()
    reason = without_lead(message, CANCEL_ALL_NOT_ALL_CANCELLED) if message else ""
    return markdown_safe(reason or "Nothing was cancelled. Check Orders and try again.")


def cancel_all_unsure_line(response: dict[str, Any]) -> str | None:
    """A line for the cancels that got no usable answer, when there were any."""
    unsure = _count(response.get("not_confirmed_cancellations"))
    if not unsure:
        return None
    return (
        f"Not confirmed: {unsure} {'order' if unsure == 1 else 'orders'} (may have been "
        "cancelled; check their status before cancelling again)"
    )


def is_app_exit(order_type: str, order_data: dict[str, Any]) -> bool:
    """Whether an alert is for the app's own Exit of one position (live or Analyze mode).

    Live, the close route publishes it as a ``closeposition``; in Analyze mode it is the
    sandbox's smart order. Both carry the strategy name the route gives it, and only that
    name: a ``closeposition`` from anything else is an Exit all, or Flow's close node.
    """
    if order_data.get("strategy") != APP_EXIT_STRATEGY:
        return False
    return order_type in ("closeposition", "placesmartorder")


def _sent(response: dict[str, Any]) -> dict[str, Any] | None:
    """The side and quantity the order that went was sized to, when the answer says."""
    sent = response.get("sent")
    if not isinstance(sent, dict):
        return None
    action = str(sent.get("action") or "").strip().upper()
    try:
        quantity = int(sent.get("quantity"))
    except (TypeError, ValueError):
        return None
    if action not in ("BUY", "SELL") or quantity <= 0:
        return None
    return {"action": action, "quantity": quantity, "closes": bool(sent.get("closes"))}


def _order_ids_line(response: dict[str, Any]) -> str:
    """The order it placed, or every slice of it when the broker sliced it."""
    ids = [str(each) for each in response.get("orderids") or [] if str(each).strip()]
    if len(ids) > 1:
        return "Order IDs: " + ", ".join(f"`{each}`" for each in ids[:5])
    return f"Order ID: `{response.get('orderid', 'N/A')}`"


def app_exit_title(response: dict[str, Any]) -> str:
    """What became of the app's own Exit, as the alert's title."""
    if response.get("status") != "success":
        return EXIT_NOT_CONFIRMED_TITLE if response.get("not_confirmed") else EXIT_NOT_SENT
    return EXIT_SENT if response.get("orderid") else NO_EXIT_SENT


def app_exit_lines(order_data: dict[str, Any], response: dict[str, Any]) -> list[str]:
    """The alert's lines for the app's own Exit: the position, what was sent, and its order or
    the app's own reason (the words the phone showed)."""
    symbol = markdown_safe(str(order_data.get("symbol") or "").strip())
    # Live it is "product"; the sandbox's smart order calls it "product_type".
    product = order_data.get("product") or order_data.get("product_type")
    where = [str(part or "").strip() for part in (order_data.get("exchange"), product)]
    where = [part for part in where if part]
    position = f"{symbol} ({', '.join(where)})" if where else symbol
    succeeded = response.get("status") == "success"
    if succeeded and not response.get("orderid"):
        return [f"{position}: {NOTHING_WAS_OPEN}"]
    sent = _sent(response)
    if not succeeded and not response.get("not_confirmed"):
        reason = str(response.get("message") or "").strip()
        return [position, markdown_safe(reason or EXIT_NOT_SENT_REASON)]
    how = f"{sent['action'].lower()} {sent['quantity']} to close" if sent else TO_CLOSE
    if succeeded:
        return [f"{position}, {how}", _order_ids_line(response)]
    reason = str(response.get("message") or "").strip()
    return [f"{position}, {how}", markdown_safe(reason or EXIT_NOT_SENT_REASON)]


def smart_order_action_lines(order_data: dict[str, Any], response: dict[str, Any]) -> list[str]:
    """A smart order alert's lines for what it does, between its symbol and its exchange.

    A position size of 0 says the side and quantity sent ("SELL 65 to close"), or "to close the
    position" when that is not known, never the request's placeholders; nothing sent says so, with
    the app's reason. Every other smart order keeps upstream's three lines.
    """
    try:
        target = int(str(order_data.get("position_size", "")).strip())
    except ValueError:
        target = None
    if target != 0:
        return [
            f"Action: {order_data.get('action', 'N/A')}",
            f"Quantity: {order_data.get('quantity', 'N/A')}",
            f"Position Size: {order_data.get('position_size', 'N/A')}",
        ]
    succeeded = response.get("status") == "success"
    reason = str(response.get("message") or "").strip()
    because = [markdown_safe(reason)] if reason else []
    if (succeeded and not response.get("orderid")) or (
        not succeeded and not response.get("not_confirmed")
    ):
        # Nothing was sent: no position to close, a refusal, or its exit already on its way.
        return ["Action: nothing sent", *because]
    sent = _sent(response)
    if sent is None:
        return [f"Action: {TO_CLOSE}", *([] if succeeded else because)]
    to_close = " to close" if sent["closes"] else ""
    return [
        f"Action: {sent['action']} {sent['quantity']}{to_close}",
        *([] if succeeded else because),
    ]
