"""A stub of OpenAlgo's sealed members route (the contract in market/members.py), in memory."""

from __future__ import annotations

from sudarshan.alerts.market import groups
from sudarshan.alerts.market.members import Answer, Unavailable


def key(n: int) -> str:
    return f"NSE_EQ|INE{n:03d}A01020"


def row(n: int, **more) -> dict:
    return {"instrument_key": key(n), "symbol": f"S{n}", "exchange": "NSE", "tick_size": "0.05",
            "expiry": None} | more  # fmt: skip


class StubMembers:
    """Lists by ref; ``state`` is the whole feature's (ok or off); ``gone`` holds deleted refs;
    ``down`` makes every call unavailable. ``calls`` is what was asked, in order."""

    def __init__(self, lists: dict[str, list[dict]] | None = None, version: str = "v1"):
        self.lists = lists or {}
        self.left_out: dict[str, list[dict]] = {}
        self.state, self.gone, self.down, self.current = groups.OK, set(), False, version
        self.calls: list[tuple[str, ...]] = []

    async def members(self, ref: str) -> Answer:
        self.calls.append(("members", ref))
        self._check()
        if self.state == groups.OFF:
            return Answer(groups.OFF)
        if ref in self.gone:
            return Answer(groups.GONE)
        rows = tuple(groups.Row.of(r) for r in self.lists.get(ref, ()))
        return Answer(groups.OK, self.current, rows, tuple(self.left_out.get(ref, ())))

    async def version(self) -> Answer:
        self.calls.append(("version",))
        self._check()
        return Answer(self.state, self.current if self.state == groups.OK else None)

    def _check(self) -> None:
        if self.down:
            raise Unavailable("not answering")
