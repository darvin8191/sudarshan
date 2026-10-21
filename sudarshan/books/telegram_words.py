"""What Telegram says about an Exit all or a Cancel all, in the app's own words (M6.9).

Two places speak: the bot's /closeall reply (services/telegram_bot_service.py), which calls the
app through the openalgo SDK, and the order alert (services/telegram_alert_service.py), which
formats the app's own answer. A trader reads both on a phone and must never see a status code, an
SDK's wrapping or a JSON body; and a title must not say "Position Closed" or "All Orders
Cancelled" over a result that did not do it all.
"""

from __future__ import annotations

import json
import re
from typing import Any

EXIT_ALL_NOT_ALL_CLOSED = "Exit all did not close every position"
EXIT_ALL_NOT_CONFIRMED_TITLE = "Exit all not confirmed"
EXIT_ALL_NOT_SENT_TITLE = "Exit all not sent"
CANCEL_ALL_NOT_ALL_CANCELLED = "Cancel all did not cancel every order"
NO_OPEN_ORDERS = "No open orders to cancel"

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
_WRAPPED = re.compile(r"\AHTTP \d{3}: (.*)\Z", re.S)
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
            body = json.loads(wrapped.group(1))
        except ValueError:
            body = None
        inner = body.get("message") if isinstance(body, dict) else None
        if isinstance(inner, str) and inner.strip():
            return EXIT_ALL_NOT_ALL_CLOSED, inner.strip()
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
    if order_type == "closeposition" and not succeeded:
        title = "Position not closed" if order_data.get("symbol") else EXIT_ALL_NOT_ALL_CLOSED
        return f"*{title}*\n{{details}}"
    if order_type == "cancelallorder":
        if not succeeded or _count(response.get("failed_cancellations")):
            return f"*{CANCEL_ALL_NOT_ALL_CANCELLED}*\n{{details}}"
        if not _count(response.get("canceled_orders")):
            return f"*{NO_OPEN_ORDERS}*\n{{details}}"
    return template


def close_failed_line(order_data: dict[str, Any], response: dict[str, Any]) -> str:
    """The alert's line for a close that did not succeed: Exit all's own sentence, no "Error:"."""
    message = str(response.get("message") or "").strip()
    if order_data.get("symbol"):
        return markdown_safe(f"Error: {message or 'Failed'}")
    return markdown_safe(without_lead(message, EXIT_ALL_NOT_ALL_CLOSED) or EXIT_ALL_NOT_CONFIRMED)


def cancel_all_unsure_line(response: dict[str, Any]) -> str | None:
    """A line for the cancels that got no usable answer, when there were any."""
    unsure = _count(response.get("not_confirmed_cancellations"))
    if not unsure:
        return None
    return (
        f"Not confirmed: {unsure} {'order' if unsure == 1 else 'orders'} (may have been "
        "cancelled; check their status before cancelling again)"
    )
