"""The sentences the option chain bridge answers with (M11.1, O2), written for a trader."""

from __future__ import annotations

SIGNIN_TODAY = "Today's Upstox login is needed to read the option chain."
OTHER_BROKER = "The option chain on the phone reads from Upstox. Sign in to Upstox to use it."
WAIT = "Upstox asked us to wait. The chain will be read again shortly."
FAILED = "The option chain could not be read."
NOT_LOADED = (
    "Today's contract list is not loaded yet. It loads after the day's login; "
    "open the option chain again then."
)
NO_MCX = "Upstox has no option chain for MCX. Use the wide option chain for it."
PICK_AGAIN = "Pick the underlying and the expiry again: one of them did not come with the request."
SESSION = "Sign in to SUDARSHAN again to read the option chain."
OWNER_ONLY = "The option chain is for the owner's sign-in only."
BUSY = "Too many reads at once. Try again in a moment."


def not_listed(underlying: str, expiry: str) -> str:
    return f"No options are listed for {underlying} expiring on {expiry}."
