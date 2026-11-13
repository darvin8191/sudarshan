"""The weekly equity-curve picture (JB8; design 6.5, 8.2 Renderer): its size, and that its
labels are really drawn, as text, with their sign."""

from __future__ import annotations

import io
import os
import time

import pytest
from PIL import Image
from render_samples import week_equity

from sudarshan.journal.core import equity
from sudarshan.journal.render import png, style

TITLE, LABEL = "Week in review", "Mon 2 Nov - 8 Nov 2026"


def _open(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def _differing(im: Image.Image, box, background) -> int:
    """Pixels inside ``box`` (shrunk past its edge) that are not the box's own fill."""
    x0, y0, x1, y1 = box
    crop = im.convert("RGB").crop((x0 + 4, y0 + 4, x1 - 3, y1 - 3))
    return sum(
        1
        for px in crop.getdata()
        if sum(abs(a - b) for a, b in zip(px, background, strict=True)) > 90
    )


def test_the_picture_is_a_png_of_the_stated_size():
    out = png.draw_equity_png(week_equity(), TITLE, LABEL)
    assert out.png[:8] == b"\x89PNG\r\n\x1a\n"
    im = _open(out.png)
    assert im.format == "PNG" and im.mode == "RGB"
    assert im.size == (png.WIDTH, png.HEIGHT) == (out.width, out.height) == (1080, 600)
    assert png.render_equity_png(week_equity(), TITLE, LABEL)[:4] == b"\x89PNG"


def test_the_last_value_and_the_drawdown_are_written_in_boxes_that_hold_text_pixels():
    out = png.draw_equity_png(week_equity(), TITLE, LABEL)
    im = _open(out.png)
    last = out.boxes["last_value"]
    dd = out.boxes["max_drawdown"]
    assert last[2] <= dd[0], "the two labels do not overlap"
    # The last value is white text on its fill (a loss week here): many white pixels in the box.
    assert _differing(im, last, style.LOSS) > 400
    # The drawdown is ink on a tint inside a loss-coloured edge.
    assert _differing(im, dd, style.LOSS_TINT) > 400
    assert im.getpixel((dd[0], (dd[1] + dd[3]) // 2)) == style.LOSS  # the edge, not colour alone
    # The value is also written beside the dot.
    assert _differing(im, out.boxes["last_point"], style.CARD) > 60


def test_the_labels_change_when_the_figures_change():
    a = png.draw_equity_png(week_equity(), TITLE, LABEL)
    days = [20261102, 20261103]
    trips = [
        {"close_day": 20261102, "net_paise": 500000},
        {"close_day": 20261103, "net_paise": -100},
    ]
    b = png.draw_equity_png(equity.equity_curve(trips, days), TITLE, LABEL)
    box = a.boxes["last_value"]
    crop = lambda out: _open(out.png).crop(box).tobytes()  # noqa: E731
    assert crop(a) != crop(b)
    assert a.png != b.png


def test_a_profit_week_labels_its_last_value_with_the_profit_fill_and_word():
    days = [20261102, 20261103]
    trips = [
        {"close_day": 20261102, "net_paise": 250000},
        {"close_day": 20261103, "net_paise": 1000},
    ]
    out = png.draw_equity_png(equity.equity_curve(trips, days), TITLE, LABEL)
    im = _open(out.png)
    x0, y0, x1, y1 = out.boxes["last_value"]
    assert im.getpixel((x0 + 2, y0 + 2)) == style.PROFIT
    assert _differing(im, out.boxes["last_value"], style.PROFIT) > 400
    # No drawdown: the label still stands and says so, in a neutral box.
    assert _differing(im, out.boxes["max_drawdown"], style.CANVAS) > 250


def test_the_wordmark_and_the_brand_word_are_in_the_header():
    out = png.draw_equity_png(week_equity(), TITLE, LABEL)
    im = _open(out.png).convert("RGB")
    ink = sum(1 for px in im.crop((28, 16, 28 + 70, 50)).getdata() if px[0] < 90 and px[1] < 90)
    brand = sum(1 for px in im.crop((120, 20, 330, 60)).getdata() if px == style.BRAND)
    assert ink > 150, "the wordmark outline is drawn"
    assert brand > 200, "SUDARSHAN is drawn in the brand colour"


def test_no_trading_day_still_gives_a_picture_that_says_so():
    out = png.draw_equity_png(equity.equity_curve([], []), TITLE, LABEL)
    im = _open(out.png)
    assert im.size == (1080, 600)
    assert out.boxes == {}
    assert _differing(im, (28, 100, 400, 140), style.CARD) > 100


def test_a_long_run_of_days_is_thinned_but_still_drawn():
    days = [20260101 + i for i in range(28)]
    trips = [{"close_day": d, "net_paise": (-1) ** i * 10000 * (i + 1)} for i, d in enumerate(days)]
    out = png.draw_equity_png(equity.equity_curve(trips, days, 10000000), TITLE, LABEL)
    assert _open(out.png).size == (1080, 600)
    assert out.boxes["last_value"][3] > out.boxes["last_value"][1]


def test_text_the_fonts_lack_is_a_question_mark_in_a_title():
    out = png.draw_equity_png(week_equity(), "सु", LABEL)
    assert _open(out.png).size == (1080, 600)


def _soft_timing_ok() -> bool:
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        return False
    return os.getloadavg()[0] < (os.cpu_count() or 1)


def test_the_picture_builds_in_under_100_ms():
    eq = week_equity()
    png.render_equity_png(eq, TITLE, LABEL)  # warm: fonts and the wordmark mask
    best = 1.0
    for _ in range(9):
        t = time.perf_counter()
        png.render_equity_png(eq, TITLE, LABEL)
        best = min(best, time.perf_counter() - t)
    if not _soft_timing_ok():
        pytest.skip(f"timing not asserted on a shared or CI machine (best {best * 1000:.0f} ms)")
    assert best < 0.1, f"{best * 1000:.0f} ms"
