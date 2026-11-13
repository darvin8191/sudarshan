"""The weekly equity-curve image (JB8, M15.3): Pillow, no browser.

The image is sent as a photo with the week's text, so it has to be read at phone size and in
black and white. Never colour alone (design 6.5): the last value and the deepest drawdown are
written as text labels with their sign, and the last value carries a PROFIT, LOSS or FLAT word
in a filled box.
"""

from __future__ import annotations

import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from sudarshan.journal.core import words
from sudarshan.journal.render import fonts, paths, style
from sudarshan.journal.render.pdf import clean

WIDTH = 1080
HEIGHT = 600
_SS = 3  # supersampling of the curve layer
_LOGO_SS = 4

Box = tuple[int, int, int, int]


@dataclass(frozen=True)
class EquityPng:
    """The picture's bytes and where its text labels were drawn (for a test to look at)."""

    png: bytes
    width: int
    height: int
    boxes: Mapping[str, Box] = field(default_factory=dict)


@lru_cache(maxsize=32)
def _font(key: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(fonts.font_path(key)), size)


def _text_w(font: ImageFont.FreeTypeFont, text: str) -> float:
    return font.getlength(text)


def _tracked(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: style.RGB,
    tracking: float,
) -> float:
    """Draw ``text`` one letter at a time with extra space between; returns the end x."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill, anchor="ls")
        x += font.getlength(ch) + tracking
    return x - tracking


@lru_cache(maxsize=4)
def _wordmark_mask(height: int) -> Image.Image:
    """The wordmark outline as an antialiased mask ``height`` px tall (built once)."""
    (vw, vh), cmds = paths.wordmark()
    scale = height * _LOGO_SS / vh
    w = round(vw * scale)
    h = round(vh * scale)
    polys = paths.flatten(paths.transform(cmds, scale, 0, 0), steps=8)
    outer_sign = 1 if paths.area(max(polys, key=lambda p: abs(paths.area(p)))) > 0 else -1
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    for poly in polys:  # an outer contour paints, a counter (hole) clears
        md.polygon(poly, fill=255 if paths.area(poly) * outer_sign > 0 else 0)
    return mask.reduce(_LOGO_SS)


def _paste_wordmark(canvas: Image.Image, x: int, y: int, height: int) -> int:
    """The wordmark outline in ink at (x, y), ``height`` px tall; returns its width."""
    mask = _wordmark_mask(height)
    canvas.paste(Image.new("RGB", mask.size, style.INK), (x, y), mask)
    return mask.size[0]


def _rupees_axis(paise: float) -> str:
    """An axis label: whole rupees with the sign, no paise."""
    rupees = int(round(paise / 100))
    text = f"{words.RUPEE}{words.group_en_in(str(abs(rupees)))}"
    return ("+" if rupees > 0 else words.MINUS if rupees < 0 else "") + text


def _ticks(lo: float, hi: float) -> list[float]:
    """Round gridline values (in paise) that cover ``lo`` to ``hi`` and include zero."""
    span = max(hi - lo, 1)
    raw = span / 4
    step = 100.0  # never finer than one rupee
    while step < raw:
        for mult in (2, 2.5, 2):
            step *= mult
            if step >= raw:
                break
        else:
            continue
        break
    first = int(lo // step)
    last = -int(-hi // step)
    return [k * step for k in range(first, last + 1)]


def _pct(text: str | None) -> str:
    return f" ({words.signed_pct(text)})" if text is not None else ""


def _label(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: style.RGB,
    ink: style.RGB,
    edge: style.RGB | None = None,
    pad: int = 10,
) -> Box:
    """A text label in a box; returns the box. Text is drawn in the box, whatever its colours."""
    x, y = xy
    w = int(_text_w(font, text)) + 2 * pad
    h = font.size + 2 * pad - 2
    draw.rectangle((x, y, x + w, y + h), fill=fill, outline=edge, width=2 if edge else 0)
    draw.text((x + pad, y + h - pad + 1), text, font=font, fill=ink, anchor="ls")
    return (x, y, x + w, y + h)


def draw_equity_png(equity: Mapping[str, Any], title: str, label: str) -> EquityPng:
    """The equity curve of ``core.equity.equity_curve`` as a picture with its labels."""
    points: Sequence[Mapping[str, int]] = equity["points"]
    dd: Mapping[str, Any] = equity["drawdown"]
    canvas = Image.new("RGB", (WIDTH, HEIGHT), style.CARD)
    draw = ImageDraw.Draw(canvas)
    boxes: dict[str, Box] = {}

    # Header: the wordmark outline, SUDARSHAN tracked in Khand, the report's name and period.
    mark_w = _paste_wordmark(canvas, 28, 16, 34)
    khand = _font("khand_semibold", 26)
    _tracked(
        draw, (28 + mark_w + 12, 52), style.BRAND_WORD, khand, style.BRAND, 26 * style.TRACKING_EM
    )
    f_title = _font("barlow_semibold", 24)
    f_sub = _font("barlow", 18)
    t = clean(title)
    draw.text((WIDTH - 28 - _text_w(f_title, t), 38), t, font=f_title, fill=style.INK, anchor="ls")
    s = clean(label)
    draw.text((WIDTH - 28 - _text_w(f_sub, s), 62), s, font=f_sub, fill=style.INK_2, anchor="ls")
    draw.rectangle((28, 78, WIDTH - 28, 79), fill=style.DIVIDER)
    draw.rectangle((28, 77, 28 + 90, 80), fill=style.GOLD)

    f_note = _font("barlow", 20)
    if not points:
        draw.text((28, 130), "No trading day this week.", font=f_note, fill=style.INK, anchor="ls")
        return _finish(canvas, boxes)

    last = int(points[-1]["equity_paise"])
    result = "PROFIT" if last > 0 else "LOSS" if last < 0 else "FLAT"
    colour = {"PROFIT": style.PROFIT, "LOSS": style.LOSS, "FLAT": style.INK_2}[result]

    # The two written labels, side by side above the plot.
    f_lab = _font("noto", 19)
    f_lab_b = _font("noto_bold", 19)
    text_last = f"Last value {words.rupees(last, signed=True)}  {result}"
    boxes["last_value"] = _label(draw, (28, 96), text_last, f_lab_b, colour, style.WHITE)
    if dd.get("max_drawdown_paise"):
        text_dd = (
            f"Max drawdown {words.rupees(dd['max_drawdown_paise'], signed=True)}"
            f"{_pct(dd.get('max_drawdown_pct'))}"
        )
        edge = style.LOSS
        fill = style.LOSS_TINT
    else:
        text_dd = "Max drawdown none"
        edge = style.INK_2
        fill = style.CANVAS
    x2 = boxes["last_value"][2] + 14
    boxes["max_drawdown"] = _label(draw, (x2, 96), text_dd, f_lab, fill, style.INK, edge)

    # The plot area.
    left, top, right, bottom = 118, 160, WIDTH - 36, HEIGHT - 52
    eq = [int(p["equity_paise"]) for p in points]
    lo, hi = min(0, *eq), max(0, *eq)
    if hi == lo:
        lo, hi = -10000, 10000
    ticks = _ticks(lo, hi)
    lo, hi = ticks[0], ticks[-1]

    def y_of(v: float) -> float:
        return bottom - (v - lo) / (hi - lo) * (bottom - top)

    n = len(points)
    step = (right - left) / max(n, 1)

    def x_of(i: int) -> float:
        return left + step * (i + 0.5)

    f_axis = _font("noto", 15)
    for v in ticks:  # gridlines at round rupee values, each with its signed label
        yy = y_of(v)
        draw.line((left, yy, right, yy), fill=style.DIVIDER, width=1)
        lab = _rupees_axis(v)
        draw.text((left - 10, yy), lab, font=f_axis, fill=style.INK_3, anchor="rm")
    zero = y_of(0)
    for x in range(left, right, 12):  # the zero line, dashed
        draw.line((x, zero, min(x + 6, right), zero), fill=style.INK_2, width=2)
    f_day = _font("barlow", 16)
    every = 1 if n <= 8 else -(-n // 8)
    for i, p in enumerate(points):
        if i % every and i != n - 1:
            continue
        d = (
            words.day_label(int(p["day"])).split(" ", 1)[1]
            if n > 8
            else words.day_label(int(p["day"]))
        )
        draw.text((x_of(i) - _text_w(f_day, d) / 2, bottom + 28), d, font=f_day, fill=style.INK_2)

    # The curve and its fill, drawn large and brought down for smooth edges.
    pw, ph = right - left, bottom - top
    layer = Image.new("RGBA", (pw * _SS, ph * _SS), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)

    def to_layer(i: int, v: float) -> tuple[float, float]:
        return (x_of(i) - left) * _SS, (y_of(v) - top) * _SS

    line = [to_layer(i, v) for i, v in enumerate(eq)]
    base_y = (zero - top) * _SS
    area = [(line[0][0], base_y), *line, (line[-1][0], base_y)]
    ld.polygon(area, fill=(*style.BRAND_TINT, 255))
    ld.line(line, fill=(*style.BRAND, 255), width=3 * _SS, joint="curve")
    if dd.get("max_drawdown_paise") and dd.get("peak_day") and dd.get("trough_day"):
        days = [int(p["day"]) for p in points]
        if dd["peak_day"] in days and dd["trough_day"] in days:
            a, b = days.index(dd["peak_day"]), days.index(dd["trough_day"])
            ld.line(line[a : b + 1], fill=(*style.LOSS, 255), width=5 * _SS, joint="curve")
    r = 6 * _SS
    lx, ly = line[-1]
    ld.ellipse(
        (lx - r, ly - r, lx + r, ly + r),
        fill=(*colour, 255),
        outline=(255, 255, 255, 255),
        width=2 * _SS,
    )
    layer = layer.reduce(_SS)
    canvas.paste(layer, (left, top), layer)

    # The last value written beside its dot, kept inside the plot.
    f_pt = _font("noto_bold", 17)
    txt = words.rupees(last, signed=True)
    tw = _text_w(f_pt, txt)
    px = min(x_of(n - 1) + 12, right - tw)
    py = y_of(last) - 12
    py = max(top + 18, min(py, bottom - 6))
    draw.text((px, py), txt, font=f_pt, fill=colour, anchor="ls")
    boxes["last_point"] = (int(px), int(py - 17), int(px + tw), int(py + 4))
    return _finish(canvas, boxes)


def _finish(canvas: Image.Image, boxes: dict[str, Box]) -> EquityPng:
    buf = io.BytesIO()
    canvas.save(buf, format="PNG", compress_level=3)
    return EquityPng(buf.getvalue(), canvas.width, canvas.height, dict(boxes))


def render_equity_png(equity: Mapping[str, Any], title: str, label: str) -> bytes:
    """The weekly equity-curve picture as PNG bytes."""
    return draw_equity_png(equity, title, label).png
