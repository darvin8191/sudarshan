"""The restart page's nginx lines, written from the page itself (M1.17; review M2).

``python -m sudarshan.ops.restart_page --write`` rewrites
``sudarshan/ops/nginx/sudarshan-restart.conf`` from ``frontend/public/images/restarting.html``;
``--check`` says whether the two still agree (a test runs it, so a change to the page that
forgets the lines fails the build).

Why the page is inside the lines and not a file nginx reads: an ``error_page`` that reads a file
answers **404** when the file is missing or unreadable, for every 502 and 504, an order's
included. A phone reads a 404 on an order as "refused"; TradingView reads "not found". With the
page in the lines themselves there is no file to lose: each of the two answers has a named
location that returns its own status with the page as the body.

nginx reads no parameter longer than 4096 bytes, so the page is given in pieces (``set``) and
put together in the ``return``. A ``$`` would be read as a variable there, so a page that holds
one is refused here.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PAGE = REPO / "frontend" / "public" / "images" / "restarting.html"
TARGET = REPO / "sudarshan" / "ops" / "nginx" / "sudarshan-restart.conf"
#: The two answers nginx gives for an app that is down, each with its named location.
STATUSES = (502, 504)
#: Bytes of the page in one piece; well under nginx's 4096 for one parameter, escapes included.
PIECE = 1800

HEAD = """\
# सुदर्शन / SUDARSHAN: the page shown while the app restarts (M1.17).
#
# WRITTEN BY `python -m sudarshan.ops.restart_page --write` from
# frontend/public/images/restarting.html. Do not edit by hand: change the page and write again.
#
# Installed by the owner, once, as /etc/nginx/snippets/sudarshan-restart.conf and included with one
# line inside SUDARSHAN's own server { } block and nowhere else (sudarshan/README.md, "The restart
# page"). After any change: `nginx -t`, then reload nginx; never restart it.
#
# During a deploy the app is down for some seconds, and nginx answers for it: "502 Bad Gateway"
# when nothing listens, "504 Gateway Time-out" when it did not answer in time. With these lines
# those two answers keep their status and carry SUDARSHAN's own page, which says the app is
# restarting and tries the same address again by itself. Nothing else changes: an answer the app
# itself gives is passed on as it is, and the family view's own lines for its API stay in charge
# there (a location's own error_page wins over this one).
#
# The page is inside these lines on purpose, in pieces nginx puts together. Read from a file, a
# missing or unreadable file would turn every 502 and 504 into "404 Not Found", an order's
# answer included. Here there is no file to lose, and the status is written beside the page.
# The two locations are named ones: nothing from outside can ask for them, and the family view's
# check of what a family phone may open is not asked again on the way in, so a family phone on
# the family view gets this page too.
"""


class PageRefused(ValueError):
    """The page holds something nginx would not give back as written."""


def escaped(text: str) -> str:
    """``text`` as it stands inside a single-quoted nginx parameter."""
    if "$" in text:
        raise PageRefused("the restart page holds a $ sign, which nginx would read as a variable")
    return text.replace("\\", "\\\\").replace("'", "\\'")


def pieces(page: str, size: int = PIECE) -> list[str]:
    """The page cut into runs of at most ``size`` bytes, never inside a character."""
    out, run, held = [], [], 0
    for char in page:
        width = len(char.encode("utf-8"))
        if run and held + width > size:
            out.append("".join(run))
            run, held = [], 0
        run.append(char)
        held += width
    if run:
        out.append("".join(run))
    return out


def render(page: str) -> str:
    """The whole file for ``page``."""
    cut = pieces(page)
    names = [f"$sudarshan_restart_{n}" for n in range(1, len(cut) + 1)]
    lines = [HEAD, ""]
    lines += [f"error_page {status} @sudarshan_restarting_{status};" for status in STATUSES]
    for status in STATUSES:
        lines += ["", f"location @sudarshan_restarting_{status} {{", "    types { }",
                  '    default_type "text/html; charset=utf-8";',
                  '    add_header Cache-Control "no-store" always;']  # fmt: skip
        lines += [
            f"    set {name} '{escaped(piece)}';" for name, piece in zip(names, cut, strict=True)
        ]
        lines += [f"    return {status} {''.join(names)};", "}"]
    return "\n".join(lines) + "\n"


def check(page: Path = PAGE, target: Path = TARGET) -> bool:
    return target.is_file() and target.read_text(encoding="utf-8") == render(
        page.read_text(encoding="utf-8")
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sudarshan.ops.restart_page")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="write the lines from the page")
    group.add_argument(
        "--check", action="store_true", help="say whether they agree; change nothing"
    )
    args = parser.parse_args(argv)
    if args.write:
        TARGET.write_text(render(PAGE.read_text(encoding="utf-8")), encoding="utf-8")
        sys.stdout.write(f"wrote {TARGET.relative_to(REPO)}\n")
        return 0
    if check():
        sys.stdout.write("the restart page and its nginx lines agree\n")
        return 0
    sys.stdout.write(
        "the nginx lines are not the restart page's: run "
        "python -m sudarshan.ops.restart_page --write\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
