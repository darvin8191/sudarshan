"""DL1: the sandbox's daily loss figure is marked before it is read.

The sandbox marks a position's P&L column only when something reads its positions with live
prices; its background loop never runs inside the app. Seen on 9 Nov: the card read +8,601.80
at 11:20:42, 11:22:00 and 11:22:26 while the option moved, and became right after the Positions
page was opened. The gate's check and the card read the same stored column, so both were stale.
"""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from sudarshan.guard import readers


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter_by(self, **kwargs):
        return self

    def filter(self, *args):
        return self

    def populate_existing(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


class _Model:
    def __init__(self, rows):
        self.query = _Query(rows)
        self.user_id = self.quantity = 0


@pytest.fixture(autouse=True)
def _fresh_marks():
    readers._sandbox_marked.clear()
    yield
    readers._sandbox_marked.clear()


def _world(monkeypatch, manager):
    """One open option position whose stored P&L is stale until the manager marks it."""
    row = SimpleNamespace(
        product="NRML", exchange="NFO", quantity=65, pnl=Decimal("8601.80"),
        today_realized_pnl=Decimal("0"),
    )  # fmt: skip
    db = SimpleNamespace(
        SandboxFunds=_Model([SimpleNamespace(today_realized_pnl=Decimal("0"))]),
        SandboxPositions=_Model([row]),
    )
    monkeypatch.setattr(readers, "_sandbox", lambda: db)
    module = types.ModuleType("sandbox.position_manager")
    module.PositionManager = lambda user_id: manager(row, user_id)
    monkeypatch.setitem(sys.modules, "sandbox.position_manager", module)
    return row


class _Marker:
    """Stands for the sandbox's PositionManager: marking moves the stored column."""

    calls: list = []

    def __init__(self, row, user_id, ok=True):
        self.row, self.user_id, self.ok = row, user_id, ok

    def get_open_positions(self, update_mtm=True):
        _Marker.calls.append((self.user_id, update_mtm))
        if not self.ok:
            return False, {"status": "error"}, 500
        self.row.pnl = Decimal("-1500.00")  # the option moved
        return True, {"status": "success"}, 200


def test_the_figure_is_read_after_the_positions_are_marked(monkeypatch):
    _Marker.calls = []
    _world(monkeypatch, _Marker)
    before = datetime.now(UTC)
    figure = readers.sandbox_day("owner")
    assert _Marker.calls == [("owner", True)]
    assert figure.unrealised == -1500.0  # not the stale +8,601.80
    assert figure.marked_at is not None and figure.marked_at >= before


def test_two_reads_within_a_second_share_one_marking(monkeypatch):
    _Marker.calls = []
    _world(monkeypatch, _Marker)
    first = readers.sandbox_day("owner")
    second = readers.sandbox_day("owner")
    assert len(_Marker.calls) == 1
    assert second.marked_at == first.marked_at


def test_positions_that_cannot_be_read_make_the_figure_unreadable(monkeypatch):
    _world(monkeypatch, lambda row, user: _Marker(row, user, ok=False))
    with pytest.raises(readers.Unreadable):
        readers.sandbox_day("owner")


def test_a_marking_that_raises_makes_the_figure_unreadable_not_stale(monkeypatch):
    class Broken:
        def __init__(self, row, user):
            pass

        def get_open_positions(self, update_mtm=True):
            raise RuntimeError("quotes down")

    _world(monkeypatch, Broken)
    with pytest.raises(readers.Unreadable):
        readers.sandbox_day("owner")
