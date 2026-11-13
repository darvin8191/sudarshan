"""The D look as the renderer needs it: colours and the words a report file is named by (JB8).

The values are the app's tokens (``frontend/src/sudarshan/theme.css``), light only. Gold is
decoration: a rule or a tint, never text on white. Meaning never rides on colour alone: a
profit or loss is written with its sign, a side or result in a filled box with its word.
"""

from __future__ import annotations

from typing import Final

RGB = tuple[int, int, int]


def _hex(value: str) -> RGB:
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


BRAND: Final = _hex("#0E5A73")
BRAND_TINT: Final = _hex("#E4F1F4")
GOLD: Final = _hex("#E3A21A")
GOLD_TINT: Final = _hex("#FDF3DC")
WARNING_TEXT: Final = _hex("#8A5A00")
INK: Final = _hex("#0F1A24")
INK_2: Final = _hex("#475569")
INK_3: Final = _hex("#5B6676")
CANVAS: Final = _hex("#F4F6F8")
CARD: Final = _hex("#FFFFFF")
BORDER: Final = _hex("#E3E7EC")
DIVIDER: Final = _hex("#EDF0F3")
PROFIT: Final = _hex("#067A45")
PROFIT_TINT: Final = _hex("#E6F5ED")
LOSS: Final = _hex("#C3282E")
LOSS_TINT: Final = _hex("#FCEBEC")
BUY: Final = _hex("#1C65C8")
SELL: Final = _hex("#D02F14")
WHITE: Final = _hex("#FFFFFF")

#: Fill of a chip by its word. Every chip is a filled box with the word inside it, in white.
CHIP_FILL: Final = {
    "PROFIT": PROFIT,
    "LOSS": LOSS,
    "FLAT": INK_2,
    "LONG": BUY,
    "SHORT": SELL,
}

#: How each report kind is named in a file name (ASCII only, no spaces).
FILE_KIND: Final = {
    "brief": "Morning-brief",
    "eod": "Day-report",
    "weekly": "Week-in-review",
    "monthly": "Monthly-statement",
    "fy": "Tax-pack",
}

BRAND_WORD: Final = "SUDARSHAN"
#: The wordmark is set beside SUDARSHAN in Khand 600, tracked 0.4 em, in the brand colour.
TRACKING_EM: Final = 0.4
