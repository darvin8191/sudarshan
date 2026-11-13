"""The wordmark outline as drawing commands (JB8).

``lockup_paths.WORDMARK_D`` is SVG path data made of absolute M, L, H, V, Q and Z only. This
module reads it once into commands the PDF draws as vector paths and the PNG flattens into
polygons, so both print the outline the app draws. A command this reader does not know raises
``ValueError``: a changed ``marks.ts`` fails here, never as a wrong picture.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache

from sudarshan.journal.render.lockup_paths import WORDMARK_D, WORDMARK_VIEWBOX

Command = tuple  # ("M", x, y) | ("L", x, y) | ("C", x1, y1, x2, y2, x, y) | ("Z",)
Point = tuple[float, float]

_TOKEN = re.compile(r"([MLHVQZ])|(-?\d+(?:\.\d+)?)")
_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "Q": 4, "Z": 0}


def parse(d: str) -> list[Command]:
    """Path data as absolute commands; a quadratic curve comes back as the equal cubic."""
    if _TOKEN.sub("", d).strip():
        raise ValueError("the path data holds something this reader does not know")
    out: list[Command] = []
    x = y = sx = sy = 0.0
    groups: list[tuple[str, list[float]]] = []
    for letter, number in _TOKEN.findall(d):
        if letter:
            groups.append((letter, []))
        elif groups:
            groups[-1][1].append(float(number))
        else:
            raise ValueError("a number outside any command")
    for letter, nums in groups:
        n = _ARGS[letter]
        if n == 0:
            if nums:
                raise ValueError("Z takes no numbers")
            out.append(("Z",))
            x, y = sx, sy
            continue
        if not nums or len(nums) % n:
            raise ValueError(f"command {letter} has {len(nums)} numbers, not a multiple of {n}")
        for i in range(0, len(nums), n):
            a = nums[i : i + n]
            if letter == "M" and i == 0:
                x, y = a
                sx, sy = x, y
                out.append(("M", x, y))
            elif letter in ("M", "L"):  # pairs after a move are lines
                x, y = a
                out.append(("L", x, y))
            elif letter == "H":
                x = a[0]
                out.append(("L", x, y))
            elif letter == "V":
                y = a[0]
                out.append(("L", x, y))
            else:  # Q: lift to the equal cubic
                qx, qy, ex, ey = a
                out.append(
                    (
                        "C",
                        x + 2 / 3 * (qx - x),
                        y + 2 / 3 * (qy - y),
                        ex + 2 / 3 * (qx - ex),
                        ey + 2 / 3 * (qy - ey),
                        ex,
                        ey,
                    )
                )
                x, y = ex, ey
    return out


@lru_cache(maxsize=1)
def wordmark() -> tuple[tuple[float, float], tuple[Command, ...]]:
    """The wordmark's width and height in its own units, and its commands."""
    _, _, width, height = WORDMARK_VIEWBOX
    return (float(width), float(height)), tuple(parse(WORDMARK_D))


def transform(commands: Sequence[Command], scale: float, dx: float, dy: float) -> list[Command]:
    """The commands scaled by ``scale`` and moved by (dx, dy)."""
    out: list[Command] = []
    for c in commands:
        if c[0] == "Z":
            out.append(c)
            continue
        vals = c[1:]
        moved = tuple(v * scale + (dx if i % 2 == 0 else dy) for i, v in enumerate(vals))
        out.append((c[0], *moved))
    return out


def flatten(commands: Sequence[Command], steps: int = 6) -> list[list[Point]]:
    """The outline as closed polygons, each curve cut into ``steps`` straight pieces."""
    polys: list[list[Point]] = []
    cur: list[Point] = []
    for c in commands:
        kind = c[0]
        if kind == "M":
            if len(cur) > 2:
                polys.append(cur)
            cur = [(c[1], c[2])]
        elif kind == "L":
            cur.append((c[1], c[2]))
        elif kind == "C":
            x0, y0 = cur[-1]
            _, x1, y1, x2, y2, x3, y3 = c
            for i in range(1, steps + 1):
                t = i / steps
                u = 1 - t
                cur.append(
                    (
                        u**3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t**3 * x3,
                        u**3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t**3 * y3,
                    )
                )
        elif kind == "Z" and len(cur) > 2:
            polys.append(cur)
            cur = []
    if len(cur) > 2:
        polys.append(cur)
    return polys


def area(poly: Sequence[Point]) -> float:
    """The signed area of a polygon (shoelace)."""
    return (
        sum(
            poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
            for i in range(len(poly))
        )
        / 2
    )
