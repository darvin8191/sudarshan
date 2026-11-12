"""What the owner reads when a section change is refused (M8.4, W7). Pure."""

from __future__ import annotations

#: A write named a version the list has moved past (another screen changed it first).
CONFLICT = "Changed on another screen. The list is shown again."
NAME_EMPTY = "Give the section a name."
NAME_LONG = "Section names can be up to 40 letters."
NAME_TAKEN = "This list already has a section with that name."
MARK_BAD = "Pick one of the five marks, or none."
NOTHING = "Give a new name or a mark to change."
SECTIONS_FULL = "A list can have 20 sections."
SECTION_GONE = "That section is no longer in this list."
