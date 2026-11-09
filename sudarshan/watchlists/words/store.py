"""What the owner is told when the watchlist record cannot be used (M8.4). Pure."""

from __future__ import annotations

#: The file was written by a newer SUDARSHAN release than the one running (design 2.3).
RECORD_NEWER = (
    "This server's watchlist record is from a newer SUDARSHAN release. Install that release "
    "again, or ask for help; your lists themselves are safe."
)
#: The file could not be opened or read at all.
RECORD_UNREADABLE = (
    "This server's watchlist record cannot be opened right now. Your lists themselves are "
    "safe. Ask for help if this stays."
)
