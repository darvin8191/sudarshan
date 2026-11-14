"""Every sentence the family service answers with (design 6.7), by the keys of ``WORD_KEYS``.

The readers are family members on a phone and the account owner on his Family page: a sentence
names what happened and what to do, never a status code, an exception or a protocol word. One
sentence answers a wrong sign-in name, a wrong password and a wrong code alike, on purpose: an
answer that told them apart would say which part an attacker already has right. A member is never
named in a sentence.
"""

from __future__ import annotations

from .contract import LIMITS

_LIM = LIMITS

WORDS: dict[str, str] = {
    # ---- the error codes' own sentences ----
    "invalid": "That was not accepted. Check what you typed and try again.",
    "code_mismatch": "That code did not match. Codes change every 30 seconds; try the next one.",
    "signin_failed": "That sign-in name, password or code did not match.",
    "signed_out": "You were signed out on this device.",
    "csrf": "This page is out of date. Reload it and try again.",
    "forbidden": (
        "The family service could not confirm that this request came from this server. If the "
        "server's keys were changed, the next deploy run makes the family's keys again."
    ),
    "not_shared": "Not shared with you.",
    "not_found": "This is no longer there; it may have been removed on another screen.",
    "conflict": "This was changed on another screen. Reload and try again.",
    "invite_gone": (
        "This invite link has expired or was already used. Ask the account owner for a new one."
    ),
    "too_large": "That is too large to send.",
    "limit": "That is more than the family view allows.",
    "locked": "Too many wrong attempts. Try again at {time}.",
    "failed": "Something went wrong on the server. Try again in a minute.",
    "not_ready": "This part of the family view is not ready yet.",
    "off": "The family view is switched off on the server.",
    "store_newer": (
        "The family data was written by a newer release. Install that release again; nothing "
        "was changed."
    ),
    "busy": "The family view is busy. Wait a minute, then try again.",
    "unavailable": (
        "The family service is not answering. Nothing was changed; try again in a minute."
    ),
    "not_confirmed": "Not confirmed: open the Family page again to see whether it was done.",
    # ---- the rest ----
    "expired": "Your sign-in has expired. Sign in again.",
    "removed": "This member was removed by the account owner.",
    "suspended": "The account owner has paused this sign-in. Ask them to resume it.",
    "handle_rule": (
        f"A sign-in name is {_LIM['handle_min']} to {_LIM['handle_max']} small letters, digits "
        "or underscores."
    ),
    "handle_taken": "That sign-in name is taken. Choose another.",
    "password_rule": (
        f"A password is {_LIM['password_min']} to {_LIM['password_max']} characters. A sentence "
        "you will remember works best."
    ),
    "password_current": "Your current password or the code did not match.",
    "name_rule": f"A display name is 1 to {_LIM['display_name_max']} characters.",
    "label_rule": f"A device name is 1 to {_LIM['device_label_max']} characters.",
    "member_limit": (
        f"The family view has room for {_LIM['max_members']} members. Remove one first."
    ),
    "member_gone": "This member is no longer there. Reload the Family page.",
    "already_joined": "This member has already joined. Use Reset sign-in to send a new invite.",
    # "Live" is the trading-mode chip's word and never the feed's (review L7 of v0.12.0)
    "privacy_rule": (
        "P&L that moves with prices is worked out from quantities. Show quantities to show P&L."
    ),
    "ws_signed_out": "Signed out on this device.",
    "password_changed": "Your password was changed. Your other devices are signed out.",
    "signed_out_everywhere": "You were signed out on every device.",
    "store_failed": (
        "The family data cannot be read on the server just now. Check that the server has free "
        "disk space; the family view tries again at its next start."
    ),
    # What a family member is told while the store is off (review L4): the two sentences above
    # are the owner's, and a member can act on neither.
    "viewer_unavailable": "The family view is not available right now. Ask the account owner.",
    # F10 (feat/family-t2): design 6.7, word for word
    "push_failing": (
        "Notifications may not arrive on this phone. Turn them off and on again in Settings."
    ),
    # v0.12.0 review H1: a share of a part of the books was kept and never read, so it is refused.
    # The Family page shows this sentence as it is (``BOOK_SHARE_REFUSED`` below). It names where the
    # control is (verification N4): the Family page's Shares tab, "Portfolio tabs"; the Portfolio
    # page has no share control.
    "book_share": (
        "Which books a member can open is set by sharing the Upstox tab under Portfolio tabs "
        "on the Family page, and by that member's Orders and Funds switches."
    ),
}

#: The one sentence for a refused share of a part of the books (review H1 of v0.12.0): the family
#: service's ``share_set`` and OpenAlgo's owner bridge both answer it, and the Family page shows it.
BOOK_SHARE_REFUSED = WORDS["book_share"]


def say(key: str, **facts: str) -> str:
    """The sentence ``key``, with ``facts`` put in (``locked`` takes ``time``, as 19:05)."""
    text = WORDS[key]
    return text.format(**facts) if facts else text
