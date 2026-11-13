"""The ``Members`` record of a ref (M8.14, W23): the contracts it holds today and the rows it cannot
give. Pure: the ``View`` record (or its parts) comes in, a dict leaves; no I/O, no clock, no log.

``members`` are the ``Contract`` records of the rows the master lists; a row it does not list (an
expired contract) is a cut with ``why`` ``missing``. A follow row on the at-the-money or a fixed
strike is a cut with ``why`` ``atm`` or ``strike``: it is a different contract during the day, so
an alert cannot hold it. A future row that resolved is a member like any other. Nothing here reads
a flag or a note: the record is built from ``contract`` alone.
"""

from __future__ import annotations

from typing import Any

from sudarshan.watchlists.words import members as words


def cut(token: str, why: str, reason: str) -> dict:
    """A row the record leaves out: ``token`` names it, ``reason`` is words for the owner."""
    return {"token": token, "why": why, "reason": reason}


def follow_cut(follow: dict) -> dict | None:
    """The cut for a follow row an alert cannot hold, or None for a month future (a contract)."""
    if follow["kind"] == "month_future":
        return None
    at_the_money = follow.get("strike_rule") == "atm"
    return cut(
        follow["underlying"],
        "atm" if at_the_money else "strike",
        words.FOLLOW_AT_THE_MONEY if at_the_money else words.FOLLOW_STRIKE,
    )


def from_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """``(members, cuts)`` of the ``Row`` records of a view, in their order."""
    members, cuts = [], []
    for row in rows:
        c = row["contract"]
        if row["expired"]:
            cuts.append(cut(c["key"], "missing", words.NOT_LISTED))
        else:
            members.append(c)
    return members, cuts


def record(ref: str, version: str, members: list[dict], cuts: list[dict],
           valid_until: int | None = None, **more: Any) -> dict:  # fmt: skip
    """The record of the contract's ``Members``; ``more`` adds what only a kind has."""
    return {"ref": ref, "version": version, "members": members, "valid_until": valid_until,
            "left_out": cuts, **more}  # fmt: skip
