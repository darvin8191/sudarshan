"""Today's P&L and the daily loss limit (M5.14): golden vectors from Upstox's raw rows, the
decision through services/risk, and the day and session a figure is looked at in."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from sudarshan.guard import daily

VECTORS = json.loads((Path(__file__).parent / "daily_vectors.json").read_text())


@pytest.mark.parametrize("case", VECTORS["figures"], ids=lambda case: case["name"])
def test_today_s_figure_from_upstox_s_raw_rows(case):
    figure = daily.live_figure(case["rows"])
    assert figure.realised == pytest.approx(case["realised"])
    assert figure.unrealised == pytest.approx(case["unrealised"])
    assert figure.approximate is case["approximate"]


@pytest.mark.parametrize("case", VECTORS["limits"], ids=lambda case: case["name"])
def test_whether_the_limit_is_reached(case):
    figure = daily.DayFigure(case["realised"], case["unrealised"])
    assert daily.reached(case["limit"], figure) is case["reached"]


def test_the_decision_is_the_shared_core_s(monkeypatch):
    """services.risk decides; daily.py only translates (CLAUDE.md: never reimplemented)."""
    seen = []
    real = daily.evaluate_aggregate

    def watch(risk, realised, unrealised):
        seen.append((risk.combined_stoploss, realised, unrealised))
        return real(risk, realised, unrealised)

    monkeypatch.setattr(daily, "evaluate_aggregate", watch)
    assert daily.reached(4000, daily.DayFigure(-4000.0, 0.0))
    assert seen == [(4000.0, -4000.0, 0.0)]


def test_the_day_is_india_s_date():
    assert daily.day_of(datetime(2026, 10, 27, 18, 29, tzinfo=UTC)) == date(2026, 10, 27)
    assert daily.day_of(datetime(2026, 10, 27, 18, 31, tzinfo=UTC)) == date(2026, 10, 28)


def test_a_figure_is_looked_at_only_in_its_segment_s_session():
    window = {
        "start_ms": int(datetime(2026, 10, 27, 3, 45, tzinfo=UTC).timestamp() * 1000),  # 09:15
        "end_ms": int(datetime(2026, 10, 27, 10, 10, tzinfo=UTC).timestamp() * 1000),  # 15:40
    }
    assert daily.in_session(window, datetime(2026, 10, 27, 10, 5, tzinfo=UTC))  # 15:35 F&O
    assert daily.in_session(window, datetime(2026, 10, 27, 10, 10, tzinfo=UTC))
    assert not daily.in_session(window, datetime(2026, 10, 27, 10, 11, tzinfo=UTC))
    assert not daily.in_session(window, datetime(2026, 10, 27, 3, 44, tzinfo=UTC))
    assert not daily.in_session(None, datetime(2026, 10, 27, 6, 0, tzinfo=UTC))  # a holiday
