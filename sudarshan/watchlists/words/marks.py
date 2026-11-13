"""What the owner reads for notes, flags and flag lists (M8.6, W9). Pure."""

from __future__ import annotations

#: A write named a version the symbol, or the flag names, have moved past.
CONFLICT = "Changed on another screen. The list is shown again."
NOTE_LONG = "A note can be up to 500 characters."
FLAG_BAD = "Pick one of the five flags, or none."
KEY_BAD = "That symbol could not be understood. Open it from a list and try again."
NOTHING = "Give a flag or a note to change."
NAMES_NONE = "Give at least one flag a name."
NAME_EMPTY = "Give the flag a name."
NAME_LONG = "Flag names can be up to 24 letters."
NAME_TWICE = "Each flag can be named once."
FLAG_LIST = "Flagged across your lists. Flag or unflag a symbol from its sheet."
NOT_LOADED = (
    "The contract list is not loaded right now, so nothing was cleared. Try again in a minute."
)
NONE_EXPIRED = "There are no expired contracts to clear."


def cleared(count: int) -> str:
    return f"Cleared {count} expired contract{'' if count == 1 else 's'}."
