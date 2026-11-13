"""What the owner reads when arranging, copying or moving symbols is refused or half done
(M8.4, W8). Pure."""

from __future__ import annotations

from sudarshan.watchlists import contract

#: The overlay was saved and OpenAlgo's order was not (a retry names the same version).
ARRANGE_PARTIAL = (
    "Saved here, but the order on /trading is unchanged until you arrange the list again."
)
ARRANGE_SECTIONS = "That order does not match this list's sections. The list is shown again."
ARRANGE_ITEMS = "A symbol was put in a section this list does not have. The list is shown again."
NEED_VERSION = "Say which version of the list the section was picked on. Update the app."
NAME_EMPTY = "Give the new list a name."
NAME_LONG = "List names can be up to 64 letters."
PICK_NOTHING = "Pick the symbols to copy, or one section."
PICK_ONE_WAY = "Pick symbols or a section, not both."
SAME_LIST = "That is the same list. Pick another list."
NEW_LIST_SECTION = "A new list has no sections yet. Copy it first, then add a section."
LISTS_FULL = (
    f"You have {contract.LIMITS['lists']} lists, the most there can be. "
    "Delete one to make a new list."
)


def symbols(n: int) -> str:
    return f"{n} symbol" if n == 1 else f"{n} symbols"


def room(name: str, left: int, picked: int, from_section: bool) -> str:
    """The target cannot take what was picked (``left`` is the room, ``picked`` the pick)."""
    what = "this section has" if from_section else "you picked"
    more = "room for 0 more symbols" if left <= 0 else f"room for {symbols(left)} more"
    more = more.replace(" symbols more", " more symbols").replace(" symbol more", " more symbol")
    return f"{name} has {more}; {what} {picked}. Make room in {name} or pick another list."


def too_many(picked: int) -> str:
    return f"A list holds {contract.LIMITS['list_items']} symbols and you picked {picked}."


def name_taken(name: str) -> str:
    return f"You already have a list called {name}. Pick another name."


#: Why symbols the owner put in a section went to No section instead (the copy's section step,
#: which runs after the symbols are in the list): another screen changed the list first, or the
#: section rows could not be written.
STALE, LOST = "stale", "lost"


def where(section: str | None) -> str:
    """Where added symbols are when the section picked was not set; empty when it was."""
    if section == STALE:
        return ", in No section, because the list was changed on another screen meanwhile"
    if section == LOST:
        return ", in No section, because the section could not be set"
    return ""


def placed_elsewhere(n: int, target: str, section: str, moved: bool) -> str:
    """Every symbol went into ``target`` (and, for a move, out of the source), but not into the
    section picked."""
    again = " The list is shown again." if section == STALE else ""
    verb = "Moved" if moved else "Added"
    return f"{verb} {symbols(n)} to {target}{where(section)}.{again}"


def added_some(
    done: int, total: int, name: str, section: str | None = None, source: str | None = None
) -> str:
    """The adds stopped part way. ``section`` says where the added ones are when they did not go
    into the section picked; ``source`` is named for a move, which then takes nothing out."""
    kept = f" and nothing was taken out of {source}" if source else ""
    if done == 0:
        return f"Nothing was added to {name}{kept}. Try again."
    rest = f"The rest were not added{',' + kept if kept else ''}; try again."
    return f"Added {done} of {symbols(total)} to {name}{where(section)}. {rest}"


def not_removed(n: int, target: str, source: str, section: str | None = None) -> str:
    return (
        f"Copied to {target}{where(section)}, but {symbols(n)} could not be removed from "
        f"{source}. Remove them there."
    )
