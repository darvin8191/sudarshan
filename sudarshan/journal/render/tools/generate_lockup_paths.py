"""Generate ``render/lockup_paths.py`` from ``frontend/src/sudarshan/marks.ts`` (JB8, M15.6).

The PDF and the PNG draw the wordmark with the same outline the app draws, so the outline is
read from the one place it is kept and never typed again. ``lockup_paths.py`` is generated;
``test_journal_render_lockup.py`` regenerates it in memory and compares, so a change to
``marks.ts`` that skips this script fails a test.

Run from the repository root: ``uv run python -m sudarshan.journal.render.tools.generate_lockup_paths``
(add ``--check`` to compare without writing).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
MARKS_TS = ROOT / "frontend" / "src" / "sudarshan" / "marks.ts"
TARGET = Path(__file__).resolve().parents[1] / "lockup_paths.py"

_WORDMARK = re.compile(
    r"export const WORDMARK = \{\s*viewBox:\s*'([^']+)',\s*d:\s*'([^']+)',?\s*\} as const",
    re.S,
)
_LINE_WIDTH = 92


def read_wordmark(ts_text: str) -> tuple[tuple[int, int, int, int], str]:
    """The wordmark's viewBox and path data out of the text of ``marks.ts``."""
    found = _WORDMARK.search(ts_text)
    if not found:
        raise ValueError("marks.ts has no WORDMARK with a viewBox and a path")
    box = tuple(int(v) for v in found.group(1).split())
    if len(box) != 4:
        raise ValueError("the WORDMARK viewBox does not hold four numbers")
    return box, found.group(2)  # type: ignore[return-value]


def _chunks(d: str) -> list[str]:
    """Cut the path before a command letter, so each line stays short and no number is split."""
    parts = re.findall(r"[MLHVCQZ][^MLHVCQZ]*", d)
    if "".join(parts) != d:
        raise ValueError("the wordmark path holds a command this generator does not know")
    lines: list[str] = []
    cur = ""
    for part in parts:
        if cur and len(cur) + len(part) > _LINE_WIDTH:
            lines.append(cur)
            cur = ""
        cur += part
    if cur:
        lines.append(cur)
    return lines


def render_module(ts_text: str) -> str:
    """The text of ``lockup_paths.py`` for the given ``marks.ts``."""
    box, d = read_wordmark(ts_text)
    body = "\n".join(f'    "{line}"' for line in _chunks(d))
    return (
        '"""The wordmark outline, generated from frontend/src/sudarshan/marks.ts. Do not edit.\n'
        "\n"
        "Regenerate with ``uv run python -m sudarshan.journal.render.tools.generate_lockup_paths``.\n"
        "Outline: Tiro Devanagari Sanskrit 400 (SIL OFL 1.1, The Indigo Project Authors), shaped\n"
        'with HarfBuzz and drawn as paths, so no font and no shaping is needed to print it."""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        f"WORDMARK_VIEWBOX = {box!r}\n"
        "WORDMARK_D = (\n"
        f"{body}\n"
        ")\n"
    )


def main(argv: list[str]) -> int:
    text = render_module(MARKS_TS.read_text(encoding="utf-8"))
    if "--check" in argv:
        same = TARGET.exists() and TARGET.read_text(encoding="utf-8") == text
        print("lockup_paths.py is current" if same else "lockup_paths.py is stale")
        return 0 if same else 1
    TARGET.write_text(text, encoding="utf-8")
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
