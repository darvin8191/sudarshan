"""The vendored fonts (JB8, Y39).

Barlow carries the body, Khand the titles and the single big figures, Noto Sans the rupee sign
(Barlow has none) and every number in a column (its figures are tabular by default). All are
SIL OFL 1.1 with the licence file beside them, in ``fonts/``. A file that is missing, is not a
font, or lacks a glyph the reports print raises ``FontError``: the renderer never falls back to
another font, because a silent fallback prints a different report than the one that was tested.
"""

from __future__ import annotations

from functools import cache, lru_cache
from pathlib import Path

FONT_DIR = Path(__file__).resolve().parent / "fonts"

#: key -> file name
FONTS = {
    "barlow": "Barlow-Regular.ttf",
    "barlow_semibold": "Barlow-SemiBold.ttf",
    "barlow_bold": "Barlow-Bold.ttf",
    "khand_semibold": "Khand-SemiBold.ttf",
    "noto": "NotoSans-Regular.ttf",
    "noto_bold": "NotoSans-Bold.ttf",
}
#: family -> licence file kept beside its fonts
LICENCES = {
    "Barlow": "OFL-Barlow.txt",
    "Khand": "OFL-Khand.txt",
    "Noto Sans": "OFL-NotoSans.txt",
}
RUPEE = "₹"
MINUS = "−"
#: Characters every report may print: the rupee sign, the true minus, the middle dot and a dash.
REQUIRED = RUPEE + MINUS + "·–+%,.:;()/&0123456789"
#: Fonts that must carry the rupee sign (the ones a money figure is set in).
NEED_RUPEE = ("khand_semibold", "noto", "noto_bold")


class FontError(RuntimeError):
    """A vendored font is missing or cannot do its job."""


def font_path(key: str) -> Path:
    """The path of a vendored font; raises ``FontError`` when it is not there."""
    try:
        name = FONTS[key]
    except KeyError:
        raise FontError(f"no font called {key!r}") from None
    path = FONT_DIR / name
    if not path.is_file() or path.stat().st_size < 1000:
        raise FontError(f"the vendored font {name} is missing from {FONT_DIR}")
    return path


def coverage(key: str) -> frozenset[int]:
    """The code points a vendored font draws."""
    return _coverage(font_path(key))


@cache
def _coverage(path: Path) -> frozenset[int]:
    from fontTools.ttLib import TTFont

    try:
        with TTFont(path, lazy=True) as font:
            return frozenset(font.getBestCmap())
    except Exception as exc:  # noqa: BLE001 - any parse failure is the same loud answer
        raise FontError(f"the vendored font {path.name} cannot be read") from exc


def verify() -> None:
    """Check every font file, every licence file and the glyphs the reports need."""
    for family, licence in LICENCES.items():
        if not (FONT_DIR / licence).is_file():
            raise FontError(f"the licence file {licence} for {family} is missing")
    for key in FONTS:
        cmap = coverage(key)
        needed = REQUIRED if key in NEED_RUPEE else REQUIRED.replace(RUPEE, "")
        lacking = [c for c in needed if ord(c) not in cmap]
        if lacking:
            raise FontError(f"{FONTS[key]} has no glyph for {''.join(lacking)!r}")


def can_draw(text: str, *keys: str) -> bool:
    """True when every character of ``text`` is in at least one of the named fonts."""
    cmaps = [coverage(k) for k in keys]
    return all(c in "\n" or any(ord(c) in cm for cm in cmaps) for c in text)
