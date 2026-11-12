"""What the owner reads when the bridge refuses or cannot do something (M8.4). Pure."""

from __future__ import annotations

#: One sentence per error code of the contract. An op may give its own for ``invalid``, ``limit``,
#: ``conflict``, ``gone`` and ``signin``; these are what the bridge says when it has nothing more
#: exact. None of them names a status, a class, a protocol or a route.
MESSAGES = {
    "invalid": "SUDARSHAN could not understand that change, so nothing was changed. Update the "
    "app and try again.",
    "conflict": "This list was changed on another screen. It is up to date now; try again.",
    "gone": "That is no longer in your lists.",
    "limit": "That is more than a list can hold.",
    "off": "Watchlists v2 is switched off on this server. Your lists are as before.",
    "record": "This server's watchlist record cannot be used right now. Your lists themselves are "
    "safe. Ask for help if this stays.",
    "signin": "Sign in to Upstox for today, then try again.",
    "busy": "That was too many requests in a minute. Wait a moment and try again.",
    "failed": "SUDARSHAN could not do that just now. Your lists are kept; try again in a minute.",
    "session": "You are signed out. Sign in again to see your lists.",
    "owner_only": "Watchlists are the owner's. This sign-in can view the shared lists only.",
}
TOO_BIG = "That was too much to take in one go, so nothing was changed."
