"""Make the two static Noto Sans files kept in fonts/ from Google's variable font.

The variable file (about 2 MB) is not kept: only the rupee sign and Latin text are needed
(Y39), so two static instances, Regular and Bold, are cut from it and subset. Noto Sans is
SIL OFL 1.1 without a reserved name, which allows the modification; OFL-NotoSans.txt stays
beside the result.

Source: https://github.com/google/fonts/tree/main/ofl/notosans (NotoSans[wdth,wght].ttf).
Run: uv run python sudarshan/journal/render/tools/make_noto_subset.py <variable.ttf> <out dir>
"""

from __future__ import annotations

import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

UNICODES = (
    list(range(0x20, 0x7F))
    + list(range(0xA0, 0x100))
    + [0x2013, 0x2014, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2026, 0x20B9, 0x2212]
)


def cut(source: Path, out_dir: Path, weight: int, name: str) -> Path:
    font = TTFont(source)
    inst = instancer.instantiateVariableFont(font, {"wght": weight, "wdth": 100})
    opts = subset.Options()
    opts.layout_features = ["kern", "liga"]
    opts.notdef_outline = True
    sub = subset.Subsetter(opts)
    sub.populate(unicodes=UNICODES)
    sub.subset(inst)
    path = out_dir / name
    inst.save(path)
    return path


def main() -> None:
    source, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    for weight, name in ((400, "NotoSans-Regular.ttf"), (700, "NotoSans-Bold.ttf")):
        print(cut(source, out_dir, weight, name))


if __name__ == "__main__":
    main()
