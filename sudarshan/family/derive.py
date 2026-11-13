"""``python -m sudarshan.family.derive``: the family account's seal keys, for the deploy tool to write.

The family service runs as its own account, which cannot read OpenAlgo's ``.env`` and so cannot
derive a key from the pepper (P6 2.1, cross-design Y1). Root, which runs the deploy tool, never
runs the app's Python (Y2): the tool runs this module as ``sudarshan``, which can read the pepper,
checks every line it is handed and writes each key into ``/etc/sudarshan-family/seal-<name>.key``
itself.

On stdout, and nothing else: one line per family purpose, ``<name> <key>``, in this order::

    admin             FAMILY_ADMIN_INFO           OpenAlgo's owner bridge to the family's admin ops
    nudge             FAMILY_NUDGE_INFO           the alerts' "orders changed" nudges to the family
    internal          FAMILY_INTERNAL_INFO        the family's one internal route into OpenAlgo
    portfolio-family  PORTFOLIO_FAMILY_API_INFO   the family's read-only portfolio calls
    journal-family    JOURNAL_FAMILY_INFO         the family's read of the journal's family line

Each key is ``derive_key(pepper, info)`` (32 bytes, HKDF-SHA256, ``sudarshan.vault.keys``) as
urlsafe base64 with its padding: 44 characters. A derived key reveals nothing about the pepper or
about the keys of other purposes.

The pepper is read as the other ring services read it: OpenAlgo's ``.env`` (``SUDARSHAN_OPENALGO_DIR``
and ``SUDARSHAN_OPENALGO_ENV_FILE`` move it), with a value in the process environment winning over
the file. Exit 0 with all five lines; exit 2, with nothing on stdout and one line in words on stderr,
when the pepper cannot be read. Nothing is logged and nothing is written anywhere.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO

from dotenv import dotenv_values

from sudarshan.vault.keys import (
    FAMILY_ADMIN_INFO,
    FAMILY_INTERNAL_INFO,
    FAMILY_NUDGE_INFO,
    JOURNAL_FAMILY_INFO,
    PORTFOLIO_FAMILY_API_INFO,
    VaultKeyError,
    derive_key,
    pepper_from,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
#: The names the deploy tool writes as ``seal-<name>.key``, with their purposes, in output order.
PURPOSES: tuple[tuple[str, bytes], ...] = (
    ("admin", FAMILY_ADMIN_INFO),
    ("nudge", FAMILY_NUDGE_INFO),
    ("internal", FAMILY_INTERNAL_INFO),
    ("portfolio-family", PORTFOLIO_FAMILY_API_INFO),
    ("journal-family", JOURNAL_FAMILY_INFO),
)


class PepperError(Exception):
    """The pepper cannot be read; the message says where to look."""


def read_pepper(env: Mapping[str, str]) -> str:
    """OpenAlgo's ``API_KEY_PEPPER``: the process environment over OpenAlgo's ``.env``."""
    app_dir = Path(env.get("SUDARSHAN_OPENALGO_DIR") or REPO_ROOT)
    env_file = Path(env.get("SUDARSHAN_OPENALGO_ENV_FILE") or app_dir / ".env")
    try:
        with env_file.open(encoding="utf-8") as handle:
            values = dotenv_values(stream=handle)
    except OSError as exc:
        raise PepperError(f"cannot read {env_file}") from exc
    merged = {key: (value or "").strip() for key, value in values.items() if key}
    merged.update({key: value for key, value in env.items() if value != ""})
    try:
        return pepper_from(merged)
    except VaultKeyError as exc:
        raise PepperError(str(exc)) from exc


def lines(pepper: str) -> str:
    """The five lines, each ``<name> <key>`` and a newline."""
    return "".join(
        f"{name} {base64.urlsafe_b64encode(derive_key(pepper, info)).decode('ascii')}\n"
        for name, info in PURPOSES
    )


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sudarshan.family.derive",
        description="Print the family account's seal keys, one 'name key' line each.",
    )
    parser.parse_args(argv)
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    try:
        text = lines(read_pepper(os.environ if env is None else env))
    except PepperError as exc:
        print(f"The family keys were not made: {exc}.", file=err)
        return 2
    out.write(text)  # one write, all or nothing: the deploy tool takes every line or none
    out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
