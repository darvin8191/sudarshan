"""Whether an order may be sent on today's fills against Upstox's positions row:
``SUDARSHAN_REVERSAL_FROM_FILLS`` (M6.9, GA1.2).

A smart order to a position size other than 0 that Upstox's positions row already shows used to
answer "No action needed" on the row alone. The row can show a fill a minute late: long 65,
reversed to short 65, and reversed back to long 65 while the row still shows +65, the order read
as nothing to do and the account stayed short 65.

Such an order is now judged on the row corrected by the order book's fills
(``sudarshan.books.fills``), as every order that reduces or crosses a position is. What happens
when the corrected figure is not the size asked for is this switch:

- ``off`` (the default): nothing is sent, as before, and the answer says so truthfully
  (``exit_guard.fills_not_shown_yet``) where it used to say the position matched.
- ``on``: an order that reduces or crosses the corrected position (the reversal back) is sent,
  sized from the corrected figure, under the claim, hold and working-order checks every reversal
  goes through. One that would only add is still not sent, with the same sentence.

Sending is behind the switch because it sends an order that was not sent before
(.claude/rules/sudarshan.md, "Feature flags"). It is read on every call; any other value reads as
``off``, with one warning in the log.
"""

from __future__ import annotations

import os

from utils.logging import get_logger

logger = get_logger(__name__)

ENV = "SUDARSHAN_REVERSAL_FROM_FILLS"
OFF, ON = "off", "on"

_warned_about: str | None = None


def sends() -> bool:
    """Whether a reversal the positions row hides may be sent from the corrected figure."""
    global _warned_about
    raw = os.environ.get(ENV)
    if raw is None:
        return False
    value = raw.strip().strip("'\"").lower()
    if value == ON:
        return True
    if value and value != OFF and raw != _warned_about:
        _warned_about = raw
        logger.warning(f"{ENV} has a value it does not know; it stays off")
    return False
