"""What the owner reads when a favourite or a view preference is refused (M8.5, M8.7, W10). Pure."""

from __future__ import annotations

#: A write named a favourites version the owner has moved past (another screen changed it first).
CONFLICT = "Changed on another screen. Your favourites are shown again."
SEVEN_AT_MOST = "Seven favourites at most. Remove one first."
SLOT_BAD = "Favourites go in the slots 1 to 7."
SLOT_TWICE = "Two favourites cannot share a slot."
REF_BAD = "SUDARSHAN could not understand that favourite, so nothing was changed. Update the app."
REF_TWICE = "A list can be a favourite once."
GONE = "That list is no longer there, so it cannot be a favourite."
PREF_NOTHING = "Give a sort, a direction, what to change from, or the holdings line."
PREF_GONE = "That list is no longer there."
