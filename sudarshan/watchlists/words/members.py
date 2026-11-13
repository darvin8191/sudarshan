"""What the list alerts and the owner read about a list's members (M8.14, W23). Pure.

The sealed members route answers these in ``left_out`` for a row an alert cannot follow; the alerts
service counts them and the owner reads its own sentence on the alert's card.
"""

from __future__ import annotations

#: A follow row on the at-the-money strike or on a fixed strike is a different contract once the
#: expiry or the price moves, which is a normal part of a day: an alert holds one contract.
FOLLOW_AT_THE_MONEY = (
    "This row follows the at-the-money strike, which changes contract during the day, "
    "so an alert cannot follow it."
)
FOLLOW_STRIKE = (
    "This row follows a strike by its expiry, which changes contract during the day, "
    "so an alert cannot follow it."
)
#: A symbol the list holds that today's contract list does not (an expired contract).
NOT_LISTED = "It is not in today's contract list, so it is left out."
NOT_LOADED = "Today's contract list is not loaded yet, so this list cannot be read."
#: A future row of the list that cannot be worked out today.
NO_CONTRACT = "No contract can be found for this row today, so it is left out."

#: What a ranked list is refused with (the alerts service has the same sentence at creation).
BAD_REF = "That is not a list an alert can follow. Pick one of your lists or a built-in list."
